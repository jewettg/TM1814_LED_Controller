"""Connection health: detect that the controller stopped answering, heal it where possible, and tell someone.

Layer 1 of the self-healing (the independent watchdog in watchdog.py is layer 2). A `ConnectionMonitor` thread:

  * checks the controller every `health_check_sec` (every 10 s while something is wrong). A running effect's own
    successful traffic counts as proof of life, so a healthy system is hardly probed at all;
  * follows the controller when its IP address changes (DHCP): it is recognized by its MAC address, which is learned
    automatically the first time the controller is healthy, and found again with a network search;
  * keeps an incident record of each outage (when, why, how it ended), de-duplicated alerts (one when it has been
    down `alert_after_minutes`, reminders, and a "recovered" message) and a plain-English diagnosis;
  * re-sends the current effect once the controller is back (it may have restarted and forgotten it);
  * supervises this program's own worker threads and restarts a stalled one (or exits so the system restarts us).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import alerts, netscan
from .controller import ControllerError
from .db import Database

log = logging.getLogger("tm1814.health")

OFFLINE_AFTER_FAILURES = 3        # consecutive failed checks before the controller counts as offline
FAST_CHECK_SEC = 10.0             # how often to check while something is wrong
SEARCH_EVERY_SEC = 60.0           # at most one network search this often while the controller is missing
MAC_RETRY_SEC = 600.0             # how often to retry learning the controller's MAC while it is unknown
DIAGNOSE_EVERY_SEC = 300.0
STALL_SEC = 120.0                 # a worker thread silent this long (while it has work) is stuck
RESTART_LIMIT = (3, 900.0)        # this many worker restarts within this many seconds -> restart the whole program
KIND = "controller_offline"


def human(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds} s"


class ConnectionMonitor:
    def __init__(self, db: Database, controller, runner=None, manager=None, *,
                 finder: Optional[Callable[..., Optional[dict]]] = None,
                 prober: Optional[Callable[..., Optional[dict]]] = None,
                 diagnoser: Optional[Callable[..., dict]] = None,
                 clock: Callable[[], float] = time.time,
                 exit_fn: Callable[[int], Any] = os._exit):
        self.db, self.ctrl, self.runner, self.manager = db, controller, runner, manager
        self.finder = finder or (lambda mac, near=None: netscan.find_by_mac(mac, near=near))
        self.prober = prober or netscan.probe
        self.diagnoser = diagnoser or netscan.diagnose
        self.clock = clock
        self.exit_fn = exit_fn
        self.state = "unknown"            # unknown | unconfigured | online | degraded | offline
        self.since = clock()
        self.last_ok: Optional[float] = None
        self.last_probe: Optional[float] = None
        self.failures = 0
        self.first_failure: Optional[float] = None
        self.last_search = 0.0
        self.last_diagnosis_at = 0.0
        self.diagnosis: Dict[str, Any] = {}
        self.last_error = ""
        self.searches = 0
        self.moved_from: Optional[str] = None
        self.incident_id: Optional[int] = None
        self._last_mac_try = 0.0
        self._restarts: List[float] = []
        self.beat = time.monotonic()
        self._stop = threading.Event()
        self._wake = threading.Event()        # set to make the next check happen now instead of at the next interval
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="health", daemon=True)
        self._thread.start()

    def nudge(self) -> None:
        """Something noticed trouble (for example a command to the controller failed): check right away."""
        self._wake.set()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("connection check failed")
            interval = self.db.settings()["health_check_sec"] if self.state in ("online", "unconfigured") else FAST_CHECK_SEC
            end = time.monotonic() + interval
            while not self._stop.is_set() and time.monotonic() < end:     # wait in slices so the heartbeat stays fresh
                self.beat = time.monotonic()
                if self._wake.wait(min(5.0, max(0.1, end - time.monotonic()))):
                    self._wake.clear()
                    break

    # ------------------------------------------------------------ one cycle
    def tick(self) -> None:
        with self._lock:
            self.beat = time.monotonic()
            cfg = self.db.settings()
            self._supervise_threads()
            host = cfg["controller_host"]
            if not host:
                self._set_state("unconfigured")
                return
            ok = self._probe(cfg)
            (self._on_ok if ok else self._on_fail)(cfg)
            self._alerts(cfg)

    def _probe(self, cfg: dict) -> bool:
        now = self.clock()
        self.last_probe = now
        # a running effect's own successful traffic already proves the controller is answering
        if (self.runner is not None and self.runner.plan_active and self.ctrl.last_ok
                and now - self.ctrl.last_ok < cfg["health_check_sec"] and not self.ctrl.last_error):
            return True
        try:
            st = self.ctrl.query_state(timeout=3.0)
        except ControllerError as exc:
            self.last_error = str(exc)
            return False
        if st is None:
            self.last_error = "the controller did not answer the state query"
            return False
        if self.runner is not None and not self.runner.plan_active and not self.ctrl.simulated:
            self.ctrl.close()                 # do not hold a connection open while idle (the controller drops idle ones)
        return True

    # ------------------------------------------------------------ results
    def _set_state(self, state: str) -> None:
        if state != self.state:
            log.info("controller connection: %s -> %s", self.state, state)
            self.state, self.since = state, self.clock()

    def _on_ok(self, cfg: dict) -> None:
        now = self.clock()
        was_down = self.failures > 0 or self.state in ("degraded", "offline")
        self.last_ok, self.last_error = now, ""
        if was_down:
            self._recovered(cfg, now)
        self.failures, self.first_failure = 0, None
        self._set_state("online")
        if not cfg["controller_mac"] and not self.ctrl.simulated and now - self._last_mac_try >= MAC_RETRY_SEC:
            self._learn_mac(cfg["controller_host"])

    def _on_fail(self, cfg: dict) -> None:
        now = self.clock()
        self.failures += 1
        if self.first_failure is None:
            self.first_failure = now
            log.warning("the controller at %s is not answering (%s)", cfg["controller_host"], self.last_error)
        self._set_state("offline" if self.failures >= OFFLINE_AFTER_FAILURES else "degraded")
        # 1) has it moved? Look for its MAC address on the network (the first check right away, then every minute)
        if now - self.last_search >= SEARCH_EVERY_SEC:
            if self.search(cfg):
                return
        # 2) once it counts as offline: open the incident and explain why
        if self.state == "offline":
            self._open_incident(cfg, self.first_failure or now)      # it started when it FIRST stopped answering
            if now - self.last_diagnosis_at >= DIAGNOSE_EVERY_SEC:
                self._diagnose(cfg, now)

    # ------------------------------------------------------------ finding it again
    def _learn_mac(self, host: str) -> None:
        self._last_mac_try = self.clock()
        entry = None
        try:
            entry = self.prober(host)
        except Exception:  # noqa: BLE001
            entry = None
        mac = netscan.normalize_mac((entry or {}).get("mac")) if entry and entry.get("ip") == host else ""
        if not mac:
            mac = netscan.arp_mac(host)
        if mac:
            self.db.update_settings({"controller_mac": mac})
            log.info("learned the controller's ID (MAC address %s); it will be followed if its IP address changes", mac)

    def search(self, cfg: Optional[dict] = None) -> bool:
        """Look for the controller by MAC address. Returns True if it was found at a NEW address and followed."""
        cfg = cfg or self.db.settings()
        self.last_search, self.searches = self.clock(), self.searches + 1
        mac, host = cfg["controller_mac"], cfg["controller_host"]
        if not mac:
            log.warning("cannot search for the controller: its ID (MAC address) is not known yet "
                        "(it is learned the first time it answers)")
            return False
        try:
            entry = self.finder(mac, near=host)
        except Exception as exc:  # noqa: BLE001
            log.warning("network search failed: %s", exc)
            return False
        if not entry:
            self.diagnosis = {**self.diagnosis, "last_search": "not found on the network"}
            return False
        if entry["ip"] == host:
            self.diagnosis = {**self.diagnosis, "last_search": "it answers discovery at its usual address"}
            return False
        self._adopt(cfg, entry["ip"])
        return True

    def _adopt(self, cfg: dict, new_ip: str) -> None:
        old = cfg["controller_host"]
        log.warning("the controller moved from %s to %s (same MAC address %s); following it", old, new_ip, cfg["controller_mac"])
        self.db.update_settings({"controller_host": new_ip})
        if self.runner is not None:
            self.runner.apply_settings()
        self.ctrl.set_address(new_ip)
        self.moved_from = old
        self._open_incident(cfg, self.first_failure or self.clock())
        if self.incident_id:
            self.db.update_incident(self.incident_id, summary=f"The controller moved from {old} to {new_ip}",
                                    detail={"moved_from": old, "moved_to": new_ip})
        self._send(cfg, f"Controller moved to {new_ip}",
                   f"The controller's IP address changed from {old} to {new_ip} (same MAC address, {cfg['controller_mac']}).\n"
                   "The program found it again and is using the new address; nothing else is needed.\n\n"
                   "Tip: give the controller a fixed address (a DHCP reservation in your router) so this cannot happen.")
        try:                                    # confirm right away rather than waiting for the next cycle
            if self.ctrl.query_state(timeout=3.0):
                self.last_error = ""
        except ControllerError:
            pass

    def recover_now(self) -> Dict[str, Any]:
        """Search right now (the 'find it' button / command). Returns what happened."""
        with self._lock:
            cfg = self.db.settings()
            before = cfg["controller_host"]
            if not cfg["controller_mac"] and cfg["controller_host"] and not self.ctrl.simulated:
                self._learn_mac(cfg["controller_host"])
                cfg = self.db.settings()
            moved = self.search(cfg)
            if cfg["controller_host"]:
                self.tick()
            return {"moved": moved, "host": self.db.settings()["controller_host"], "was": before, "state": self.state,
                    "mac_known": bool(self.db.settings()["controller_mac"])}

    # ------------------------------------------------------------ incidents and alerts
    def _open_incident(self, cfg: dict, started: float) -> None:
        if self.incident_id is not None:
            return
        existing = self.db.open_incident_of(KIND)
        if existing:
            self.incident_id = existing["id"]
            return
        self.incident_id = self.db.open_incident(
            KIND, f"The controller at {cfg['controller_host']} stopped answering",
            {"host": cfg["controller_host"], "mac": cfg["controller_mac"], "error": self.last_error}, started=started)
        log.warning("incident opened: the controller at %s has been unreachable since %s", cfg["controller_host"],
                    time.strftime("%H:%M:%S", time.localtime(started)))

    def _diagnose(self, cfg: dict, now: float) -> None:
        self.last_diagnosis_at = now
        try:
            d = self.diagnoser(cfg["controller_host"], cfg["controller_mac"])
        except Exception as exc:  # noqa: BLE001
            d = {"summary": f"could not diagnose ({exc})"}
        self.diagnosis = {**self.diagnosis, **d}
        log.warning("diagnosis: %s", d.get("summary"))
        if self.incident_id:
            self.db.update_incident(self.incident_id, detail={"diagnosis": d.get("summary"), "error": self.last_error})

    def _recovered(self, cfg: dict, now: float) -> None:
        began = self.first_failure or now
        took = human(now - began)
        moved = self.moved_from
        if moved:
            how = f"found at its new address {cfg['controller_host']} (was {moved})"
        else:
            how = "reconnected on its own"
        log.info("the controller is reachable again after %s: %s", took, how)
        if self.incident_id:
            self.db.close_incident(self.incident_id, how, ended=now)
        if self.runner is not None:
            self.runner.request_reapply()           # it may have restarted and forgotten what it was showing
        st = self.db.get_state("alert", {})
        if st.get("sent") and st.get("incident") == self.incident_id:
            self._send(cfg, "Recovered: the controller is reachable again",
                       f"The controller is answering again after {took} ({how}).\n"
                       f"Outage began: {time.strftime('%a %b %d %H:%M:%S', time.localtime(began))}.")
        self.db.set_state("alert", {})
        self.incident_id, self.moved_from, self.diagnosis = None, None, {}
        self.last_search, self.searches = 0.0, 0

    def _alerts(self, cfg: dict) -> None:
        if self.state != "offline" or self.incident_id is None or not alerts.channels(cfg):
            return
        incident = self.db.get_incident(self.incident_id)
        if not incident:
            return
        now = self.clock()
        down = now - incident["started"]
        st = self.db.get_state("alert", {})
        due_first = not st.get("sent") and down >= cfg["alert_after_minutes"] * 60
        due_reminder = st.get("sent") and now - st.get("last", 0) >= cfg["alert_reminder_hours"] * 3600
        if not (due_first or due_reminder):
            return
        facts = self.diagnosis.get("summary") or self.last_error or "no answer"
        body = (f"The program cannot reach the LED controller at {cfg['controller_host']}.\n\n"
                f"  Down for: {human(down)} (since {time.strftime('%a %b %d %H:%M:%S', time.localtime(incident['started']))})\n"
                f"  Likely reason: {facts}\n"
                f"  The program keeps checking, searches the network for the controller's MAC address "
                f"({cfg['controller_mac'] or 'not learned yet'}) every minute, and will follow it if its IP address changed.\n"
                "  Lights keep showing whatever the controller last had; schedules resume automatically when it is back.")
        ok, _ = alerts.send(cfg, ("[reminder] " if due_reminder else "") + f"Controller not responding ({human(down)})", body)
        if ok:
            self.db.set_state("alert", {"incident": self.incident_id, "sent": True, "last": now})
            log.warning("alert sent: the controller has been down for %s", human(down))

    def _send(self, cfg: dict, subject: str, body: str) -> None:
        if alerts.channels(cfg):
            ok, via = alerts.send(cfg, subject, body)
            if ok:
                log.info("alert sent (%s): %s", ", ".join(via), subject)

    # ------------------------------------------------------------ keeping this program's own threads alive
    def _supervise_threads(self) -> None:
        r, m = self.runner, self.manager
        now = time.monotonic()
        problem = None
        if r is not None and r._thread is not None:
            if not r._thread.is_alive():
                problem = "the worker thread has died"
            elif r.plan_active and now - r.beat > STALL_SEC:
                problem = f"the worker thread has not responded for {int(now - r.beat)} s"
        if problem:
            self._worker_problem(problem)
        if m is not None and m._thread is not None and not m._thread.is_alive() and not getattr(m, "_stop").is_set():
            log.error("the schedule thread has died; restarting it")
            m.start()

    def _worker_problem(self, problem: str) -> None:
        now = self.clock()
        self._restarts = [t for t in self._restarts if now - t < RESTART_LIMIT[1]] + [now]
        log.error("%s; restarting it (%d in the last %d min)", problem, len(self._restarts), RESTART_LIMIT[1] // 60)
        if len(self._restarts) >= RESTART_LIMIT[0]:
            log.critical("the worker keeps stalling; exiting so the system service manager restarts the whole program")
            self.exit_fn(70)
            return
        self.runner.restart_worker()

    # ------------------------------------------------------------ reporting
    def snapshot(self) -> Dict[str, Any]:
        cfg = self.db.settings()
        now = self.clock()
        incident = self.db.get_incident(self.incident_id) if self.incident_id else None
        mono = time.monotonic()
        return {
            "state": self.state, "since": self.since, "last_ok": self.last_ok, "last_probe": self.last_probe,
            "failures": self.failures, "unreachable_for_sec": (now - self.first_failure) if self.first_failure else None,
            "error": self.last_error, "diagnosis": self.diagnosis, "searches": self.searches, "last_search": self.last_search or None,
            "host": cfg["controller_host"], "mac": cfg["controller_mac"], "mac_known": bool(cfg["controller_mac"]),
            "incident": incident, "alert_channels": alerts.channels(cfg),
            "heartbeat": {"runner_sec": round(mono - self.runner.beat, 1) if self.runner else None,
                          "schedules_sec": round(mono - self.manager.beat, 1) if self.manager else None,
                          "monitor_sec": round(mono - self.beat, 1)},
        }
