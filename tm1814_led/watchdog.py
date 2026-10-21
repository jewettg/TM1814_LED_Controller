"""Layer 2 of the self-healing: an independent watchdog.

Run every few minutes by the operating system's own scheduler (a launchd job, a systemd timer, a Windows scheduled
task or cron), NOT by the program, so it still works when the program has died or hung. It asks the program's public
liveness endpoint (`/api/health/live`) whether it is answering and whether its worker threads are making progress.
If not, it restarts the service and, if that does not help, alerts.

Like the program's own monitor it de-duplicates (one alert, reminders, a "recovered" message), but its state lives in
a small file next to the database, and it reads the alert settings straight from the database (read-only), so it
still alerts while the program is down. Out of scope: total loss of this computer (the watchdog dies with it).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from . import alerts
from .config import DEFAULT_PORT, default_data_dir

RESTART_AFTER_CHECKS = 2          # consecutive bad checks before the service is restarted (about 3-6 minutes)
MAX_RESTARTS_PER_HOUR = 3
REMINDER_SEC = 6 * 3600
SETTING_KEYS = ("alert_email", "smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_tls", "smtp_from", "alert_webhook",
                "alert_daily_limit", "alert_min_gap_minutes")


def live_url() -> str:
    port = os.environ.get("TM1814_PORT") or str(DEFAULT_PORT)
    return f"http://127.0.0.1:{port}/api/health/live"


def data_dir() -> Path:
    return Path(os.environ.get("TM1814_DATA_DIR") or default_data_dir())


def fetch_live(url: str, timeout: float = 10.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_alert_settings(db_path: Path) -> Dict[str, Any]:
    """The alert settings, read straight from the database without opening it for writing."""
    cfg: Dict[str, Any] = {}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as con:
            for key, value in con.execute("SELECT key, value FROM settings"):
                if key in SETTING_KEYS:
                    cfg[key] = json.loads(value)
    except (sqlite3.Error, ValueError, OSError):
        pass
    return cfg


def restart_service(runner: Callable = subprocess.run, os_name: Optional[str] = None) -> Tuple[bool, str]:
    """Ask the system's service manager to restart the program. Returns (worked, what was done)."""
    from .installer import LAUNCHD_LABEL, SERVICE_NAME, WINDOWS_TASK, build_paths, detect_os, has_systemd
    os_name = os_name or detect_os()
    if os_name == "macos":
        cmd = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"]
    elif os_name == "linux" and has_systemd():
        system_unit = build_paths("linux", "system").service_file
        cmd = (["systemctl", "restart", f"{SERVICE_NAME}.service"] if system_unit and Path(system_unit).exists()
               else ["systemctl", "--user", "restart", f"{SERVICE_NAME}.service"])
    elif os_name == "windows":
        runner(["schtasks", "/End", "/TN", WINDOWS_TASK], capture_output=True, text=True, timeout=30)
        cmd = ["schtasks", "/Run", "/TN", WINDOWS_TASK]
    else:
        return False, "no service manager to restart it with"
    try:
        done = runner(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{' '.join(cmd)} failed: {exc}"
    return done.returncode == 0, " ".join(cmd) + ("" if done.returncode == 0 else f" (exit {done.returncode})")


def _load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"bad": 0, "since": None, "alerted": False, "last_alert": 0, "restarts": []}


def _save(path: Path, state: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def run_once(*, url: Optional[str] = None, state_path: Optional[Path] = None, db_path: Optional[Path] = None,
             fetch: Callable[[str], Dict[str, Any]] = fetch_live, restart: Callable[[], Tuple[bool, str]] = restart_service,
             send: Callable[..., Tuple[bool, list]] = alerts.send, now: Callable[[], float] = time.time) -> Dict[str, Any]:
    """One check. Returns {"ok": bool, "problem": str, "actions": [...]}."""
    url = url or live_url()
    base = data_dir()
    state_path = state_path or base / "watchdog_state.json"
    db_path = db_path or base / "tm1814.db"
    t = now()
    problem = ""
    try:
        live = fetch(url)
        if not live.get("ok"):
            problem = f"the program is running but a worker is stuck (heartbeat ages: {live.get('heartbeat')})"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        problem = f"the program is not answering at {url} ({getattr(exc, 'reason', exc)})"

    st = _load(state_path)
    actions = []
    cfg = read_alert_settings(db_path)
    budget = alerts.Budget(st.setdefault("budget", {}), int(cfg.get("alert_daily_limit", 20)),
                           float(cfg.get("alert_min_gap_minutes", 60)) * 60)

    def deliver(kind: str, subject: str, body: str) -> bool:
        """One alert through the budget: critical kinds only, a daily cap, a minimum gap, backoff after a failure."""
        if budget.check(kind, t):
            return False
        ok, _ = send(cfg, subject, body, kind=kind)
        budget.record_sent(kind, t) if ok else budget.record_failure(t)
        return ok

    if not problem:
        if st.get("alerted"):
            ok = deliver("watchdog_recovered", "Recovered: the LED Controller program is running again",
                         "The watchdog can reach the program again and its workers are making progress.")
            actions.append("sent a recovery message" if ok else "could not send a recovery message")
        _save(state_path, {"bad": 0, "since": None, "alerted": False, "last_alert": 0, "restarts": st.get("restarts", []),
                           "budget": budget.s})
        return {"ok": True, "problem": "", "actions": actions}

    st["bad"] = st.get("bad", 0) + 1
    st["since"] = st.get("since") or t
    st["restarts"] = [r for r in st.get("restarts", []) if t - r < 3600]
    restarted = None
    if st["bad"] >= RESTART_AFTER_CHECKS and len(st["restarts"]) < MAX_RESTARTS_PER_HOUR:
        worked, what = restart()
        st["restarts"].append(t)
        restarted = (worked, what)
        actions.append(f"restarted the service ({what})" if worked else f"tried to restart the service but it failed ({what})")
    if st["bad"] >= RESTART_AFTER_CHECKS:
        due = (not st.get("alerted")) or (t - st.get("last_alert", 0) >= REMINDER_SEC)
        if due:
            how = "; ".join(actions) or "the restart limit for this hour was reached"
            ok = deliver("watchdog", ("[reminder] " if st.get("alerted") else "") + "The LED Controller program is not healthy",
                         f"The independent watchdog found a problem: {problem}.\n\nDown for about {int((t - st['since']) / 60)} "
                         f"minutes. What it did: {how}.\n\n(This check runs from the system scheduler, so it works even "
                         "when the program itself is dead.)")
            if ok:
                st["alerted"], st["last_alert"] = True, t
    _save(state_path, st)
    return {"ok": False, "problem": problem, "actions": actions, "consecutive": st["bad"], "restarted": restarted}
