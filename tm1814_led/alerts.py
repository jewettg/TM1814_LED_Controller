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
from typing import Any, Dict, List, Tuple

log = logging.getLogger("tm1814.alerts")
SUBJECT_PREFIX = "[LED Controller] "


def channels(cfg: Dict[str, Any]) -> List[str]:
    out = []
    if str(cfg.get("alert_email") or "").strip() and str(cfg.get("smtp_host") or "").strip():
        out.append("email")
    if str(cfg.get("alert_webhook") or "").strip():
        out.append("webhook")
    return out


def _send_email(cfg: Dict[str, Any], subject: str, body: str) -> None:
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


def _send_webhook(cfg: Dict[str, Any], subject: str, body: str) -> None:
    req = urllib.request.Request(str(cfg["alert_webhook"]).strip(), data=body.encode("utf-8"), method="POST",
                                 headers={"Content-Type": "text/plain; charset=utf-8",
                                          "Title": (SUBJECT_PREFIX + subject).encode("ascii", "replace").decode()})
    with urllib.request.urlopen(req, timeout=15):
        pass


def send(cfg: Dict[str, Any], subject: str, body: str) -> Tuple[bool, List[str]]:
    """Send through every configured channel. Returns (anything delivered, names of the channels that worked)."""
    delivered: List[str] = []
    for name in channels(cfg):
        try:
            (_send_email if name == "email" else _send_webhook)(cfg, subject, body)
            delivered.append(name)
        except Exception as exc:  # noqa: BLE001  (an alert problem must never become a second problem)
            log.warning("alert via %s failed: %s", name, exc)
    return bool(delivered), delivered
