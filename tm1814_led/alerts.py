"""Alerts: email (SMTP) and/or a webhook. A no-op until at least one is configured; sending never raises.

Channels (Settings -> Self-healing and alerts):
  * email    alert_email (recipients), smtp_host/smtp_port, optional smtp_user/smtp_password, smtp_tls, smtp_from
  * webhook  alert_webhook: an http(s) URL that gets the message as a plain-text POST (the subject is sent in a `Title`
             header), which is what ntfy.sh, Home Assistant webhooks and most push services accept
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("tm1814.alerts")
SUBJECT_PREFIX = "[LED Controller] "
MAX_BODY = 3500            # ntfy.sh accepts messages up to 4096 bytes

# What each kind of alert is, for the budget and for push priority (ntfy: min, low, default, high, max).
CRITICAL = {"down", "recovered", "watchdog", "watchdog_recovered"}   # the only kinds always allowed (within the cap)
LOOK = {                                                             # kind -> (ntfy priority, ntfy tag/emoji)
    "down": ("high", "warning"), "reminder": ("default", "hourglass_flowing_sand"), "recovered": ("default", "white_check_mark"),
    "watchdog": ("high", "rotating_light"), "watchdog_recovered": ("default", "white_check_mark"),
    "moved": ("low", "arrows_counterclockwise"), "test": ("default", "bell"),
}


def channels(cfg: Dict[str, Any]) -> List[str]:
    out = []
    if str(cfg.get("alert_email") or "").strip() and str(cfg.get("smtp_host") or "").strip():
        out.append("email")
    if str(cfg.get("alert_webhook") or "").strip():
        out.append("webhook")
    return out


def _send_email(cfg: Dict[str, Any], subject: str, body: str, kind: str = "") -> None:
    to = [a.strip() for a in str(cfg["alert_email"]).replace(";", ",").split(",") if a.strip()]
    msg = EmailMessage()
    msg["Subject"] = SUBJECT_PREFIX + subject
    msg["From"] = str(cfg.get("smtp_from") or "").strip() or "tm1814-led@localhost"
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    host, port = str(cfg["smtp_host"]).strip(), int(cfg.get("smtp_port") or 25)
    if port == 465:
        smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(host, port, timeout=15)
    with smtp:
        if port != 465 and cfg.get("smtp_tls", True):
            try:
                smtp.starttls(context=ssl.create_default_context())
            except smtplib.SMTPNotSupportedError:
                pass                                  # a plain relay on the home network: carry on without it
        if cfg.get("smtp_user"):
            smtp.login(str(cfg["smtp_user"]), str(cfg.get("smtp_password") or ""))
        smtp.send_message(msg)


def _send_webhook(cfg: Dict[str, Any], subject: str, body: str, kind: str = "") -> None:
    headers = {"Content-Type": "text/plain; charset=utf-8",
               "Title": (SUBJECT_PREFIX + subject).encode("ascii", "replace").decode()}
    if kind in LOOK:                                  # ntfy understands these; other webhooks simply ignore them
        headers["Priority"], headers["Tags"] = LOOK[kind]
    req = urllib.request.Request(str(cfg["alert_webhook"]).strip(), data=body[:MAX_BODY].encode("utf-8"), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("the push service says its message limit has been reached (HTTP 429)") from exc
        raise


def send(cfg: Dict[str, Any], subject: str, body: str, kind: str = "") -> Tuple[bool, List[str]]:
    """Send through every configured channel. Returns (anything delivered, names of the channels that worked).
    `kind` (see LOOK) sets the push priority and tag; use Budget to decide whether an alert may be sent at all."""
    delivered: List[str] = []
    for name in channels(cfg):
        try:
            (_send_email if name == "email" else _send_webhook)(cfg, subject, body, kind)
            delivered.append(name)
        except Exception as exc:  # noqa: BLE001  (an alert problem must never become a second problem)
            log.warning("ALERT delivery via %s failed: %s", name, exc)
    return bool(delivered), delivered


DAY = 86400.0


class Budget:
    """Keeps the number of alerts small, so a free push service (ntfy.sh allows 250 messages a day) is never at risk.

    `state` is a plain dict the caller persists (the program keeps it in the database, the watchdog in its state file).
      * critical alerts only: down / recovered / the watchdog's; "controller moved" and reminders are the
        non-critical kinds and may only use the first 70% of the daily limit
      * a hard cap of `limit` alerts in any 24 hours (tests sent by hand are always allowed but still counted)
      * a new "down" alert waits `min_gap_sec` after the previous one, so a flapping controller cannot alert in a loop
      * after a failed delivery the next attempt waits 5 min, then 10, 20 ... up to an hour
    """

    def __init__(self, state: Dict[str, Any], limit: int = 20, min_gap_sec: float = 3600.0):
        self.s, self.limit, self.min_gap = state, max(1, int(limit)), float(min_gap_sec)

    def _prune(self, now: float) -> None:
        self.s["sent"] = [x for x in self.s.get("sent", []) if now - x[0] < DAY]

    def used(self, now: float) -> int:
        self._prune(now)
        return len(self.s["sent"])

    def check(self, kind: str, now: float) -> Optional[str]:
        """None if this alert may be sent now, else the reason it may not."""
        if kind == "test":
            return None
        if now < self.s.get("next_try", 0.0):
            return "a recent delivery failed; waiting before trying again"
        used = self.used(now)
        if used >= self.limit:
            return f"the limit of {self.limit} alerts per 24 hours has been reached"
        if kind not in CRITICAL and used >= int(self.limit * 0.7):
            return "keeping the rest of the daily limit for critical alerts"
        if kind in ("down", "watchdog") and now - self.s.get("last_down", 0.0) < self.min_gap:
            return f"another down alert was sent less than {int(self.min_gap // 60)} minutes ago (the connection is flapping)"
        return None

    def record_sent(self, kind: str, now: float) -> None:
        self._prune(now)
        self.s["sent"].append([now, kind])
        self.s["fails"], self.s["next_try"] = 0, 0.0
        if kind in ("down", "watchdog"):
            self.s["last_down"] = now

    def record_failure(self, now: float) -> None:
        self.s["fails"] = self.s.get("fails", 0) + 1
        self.s["next_try"] = now + min(3600.0, 300.0 * 2 ** (self.s["fails"] - 1))

    def summary(self, now: float) -> Dict[str, Any]:
        return {"sent_24h": self.used(now), "limit": self.limit, "min_gap_minutes": int(self.min_gap // 60),
                "retry_at": self.s.get("next_try") or None, "last_down_alert": self.s.get("last_down") or None}
