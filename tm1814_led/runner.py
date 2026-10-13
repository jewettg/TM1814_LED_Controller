"""Background engine: plays run lists on the controller and obeys the schedules.

`Runner` owns one worker thread that plays one *plan* at a time (a run list, or a single effect).
`ScheduleManager` owns a second thread that every few seconds decides which schedule should be active and
tells the runner to start or stop it. Manual plays always win over schedules until they end or are stopped.
"""
from __future__ import annotations

import collections
import datetime as dt
import logging
import random
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from . import effects as fx
from . import scheduler as sch
from .controller import Controller, ControllerError
from .db import Database
from .protocol import parse_hex

log = logging.getLogger("tm1814.runner")
HEALTH_INTERVAL = 30.0
SCRIBBLE_PATTERN = 0x66  # state "pattern" byte while the strip is in per-pixel mode


class LogBuffer(logging.Handler):
    """Keeps the last few hundred log lines for the web UI."""

    def __init__(self, size: int = 500):
        super().__init__(logging.INFO)
        self.entries: Deque[dict] = collections.deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        self.entries.append({"ts": record.created, "level": record.levelname, "msg": record.getMessage()})

    def tail(self, count: int = 200) -> List[dict]:
        return list(self.entries)[-count:]


class Runner:
    def __init__(self, db: Database, controller: Controller):
        self.db = db
        self.ctrl = controller
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.on_end: Optional[Callable[[dict, str], None]] = None  # (plan, reason) when a plan ends or is stopped
        self._plan: Optional[dict] = None
        self._gen = 0
        self._skip = False
        self._paused = False
        self._reapply = False
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._fps = 5
        self._idle = {"action": "off", "color": "#ff5a00"}
        self._last_err = ""
        self.started_at = time.time()
        self.item_status: Dict[str, Any] = {}
        self.achieved_fps = 0.0
        self.apply_settings()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="runner", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop = True
        self.wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.ctrl.close()

    def apply_settings(self) -> None:
        s = self.db.settings()
        c = self.ctrl
        if not type(c).__name__.startswith("Simulated"):
            wanted = (s["controller_host"], s["controller_port"])
            if (c.host, c.port) != wanted:
                c.host, c.port = wanted
                c.close()
        c.min_gap = s["min_gap_ms"] / 1000.0
        c.max_groups = s["max_groups"]
        c.configured_led_count = s["led_count"]
        if c.brightness != s["brightness"]:
            c.brightness = s["brightness"]
            c.invalidate()
            self._reapply = True
        self._fps = s["fps"]
        self._idle = {"action": s["idle_action"], "color": s["idle_color"]}

    # ------------------------------------------------------------ commands (called from the web API)
    def _make_item(self, effect: Dict[str, Any], duration: Optional[float]) -> dict:
        return {"effect_id": effect.get("id"), "name": effect["name"], "kind": effect["kind"],
                "params": effect["params"], "duration": duration}

    def play_runlist(self, runlist_id: int, source: str = "manual", window: Optional[str] = None) -> None:
        rl = self.db.get_runlist(runlist_id)
        items = []
        for it in rl["items"]:
            items.append(self._make_item(self.db.get_effect(it["effect_id"]), it["duration_sec"]))
        if not items:
            raise ValueError("this run list has no effects in it")
        self._set_plan({"source": source, "name": rl["name"], "runlist_id": runlist_id, "loop": rl["loop"],
                        "shuffle": rl["shuffle"], "items": items, "window": window, "sig": rl["updated"]})

    def play_effect(self, effect: Dict[str, Any], duration: Optional[float] = None, source: str = "manual") -> None:
        self._set_plan({"source": source, "name": effect["name"], "runlist_id": None, "loop": True,
                        "shuffle": False, "items": [self._make_item(effect, duration)], "window": None, "sig": 0})

    def _set_plan(self, plan: dict) -> None:
        with self.lock:
            old = self._plan
            self._plan = plan
            self._gen += 1
            self._paused = False
            self._skip = False
            self.item_status = {}      # never show the previous plan's item for the new plan
        if old and self.on_end:
            self.on_end(old, "replaced")
        self.wake.set()
        log.info("playing %s (%s)", plan["name"], plan["source"])

    def stop(self, idle: bool = True, by_user: bool = True) -> None:
        with self.lock:
            old = self._plan
            self._plan = None
            self._gen += 1
            self._paused = False
        if old and self.on_end:
            self.on_end(old, "stopped" if by_user else "ended")
        if old:
            log.info("stopped %s", old["name"])
        self.wake.set()
        if idle:
            self._apply_idle()

    def next(self) -> None:
        self._skip = True

    def pause(self, paused: bool = True) -> None:
        self._paused = paused

    # ------------------------------------------------------------ status
    def plan_info(self) -> Optional[dict]:
        with self.lock:
            return dict(self._plan) if self._plan else None

    def status(self) -> Dict[str, Any]:
        with self.lock:
            plan = self._plan
        c = self.ctrl
        st = c.last_state
        return {
            "state": "idle" if plan is None else ("paused" if self._paused else "running"),
            "source": plan["source"] if plan else None,
            "plan": plan["name"] if plan else None,
            "loop": plan["loop"] if plan else None,
            "item": (dict(self.item_status) or None) if plan else None,
            "fps_target": self._fps,
            "fps_actual": self.achieved_fps,
            "controller": {
                "host": c.host, "port": c.port, "connected": c.connected, "led_count": c.led_count,
                "last_error": c.last_error or self._last_err,
                "commands_per_second": c.commands_per_second(),
                "commands_sent": c.commands_sent, "brightness": c.brightness,
                "power": st.power if st else None, "model": st.model if st else None,
                "firmware": st.version if st else None,
                "simulated": type(c).__name__.startswith("Simulated"),
            },
            "uptime_sec": int(time.time() - self.started_at),
        }

    # ------------------------------------------------------------ worker
    def _alive(self, gen: int) -> bool:
        return not self._stop and gen == self._gen

    def _loop(self) -> None:
        while not self._stop:
            with self.lock:
                plan, gen = self._plan, self._gen
            if plan is None:
                self.item_status = {}
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            try:
                self._run_plan(plan, gen)
            except Exception:  # never let the worker die
                log.exception("unexpected error while playing %s", plan["name"])
                time.sleep(2)

    def _sequence(self, plan: dict):
        n = len(plan["items"])
        while True:
            order = list(range(n))
            if plan["shuffle"]:
                random.shuffle(order)
            for i in order:
                yield i
            if not plan["loop"]:
                return

    def _run_plan(self, plan: dict, gen: int) -> None:
        self._powered_on = False
        for idx in self._sequence(plan):
            if not self._alive(gen):
                return
            self._play_item(plan, plan["items"][idx], idx, gen)
            if not self._alive(gen):
                return
        # played to the end (run list with loop off)
        with self.lock:
            if gen != self._gen:
                return
            self._plan = None
            self._gen += 1
        if self.on_end:
            self.on_end(plan, "finished")
        log.info("finished %s", plan["name"])
        self._apply_idle()

    def _guard(self, what: str, fn: Callable[[], Any], gen: int) -> bool:
        """Run a controller action; on failure log, back off briefly and report False."""
        try:
            fn()
            if self._last_err:
                log.info("controller reachable again")
            self._last_err = ""
            return True
        except ControllerError as exc:
            if str(exc) != self._last_err:
                log.warning("%s: %s", what, exc)
            self._last_err = str(exc)
            end = time.monotonic() + 3.0
            while time.monotonic() < end and self._alive(gen):
                time.sleep(0.1)
            return False

    def _ensure_power(self, gen: int) -> None:
        while self._alive(gen) and not self._powered_on:
            if self._guard("power on", lambda: self.ctrl.power(True), gen):
                self._powered_on = True
                time.sleep(0.4)

    def _play_item(self, plan: dict, item: dict, idx: int, gen: int) -> None:
        try:
            eff = fx.build(item["kind"], item["params"], self.ctrl.led_count)
        except fx.ParamError as exc:
            log.error("skipping %s: %s", item["name"], exc)
            time.sleep(1)
            return
        self._skip = False
        duration = item["duration"]
        self.item_status = {"index": idx, "count": len(plan["items"]), "name": item["name"], "kind": item["kind"],
                            "family": eff.family(),
                            "effect_id": item.get("effect_id"),
                            "native": eff.native, "duration": duration, "elapsed": 0.0}
        log.info("effect: %s", item["name"])
        self._ensure_power(gen)
        elapsed, last = 0.0, time.monotonic()
        last_health = last
        applied = False
        in_frames = False
        period = 1.0 / max(1, self._fps)
        frames_done, fps_mark = 0, time.monotonic()
        while self._alive(gen):
            if self._skip:
                self._skip = False
                return
            if duration is not None and elapsed >= duration:
                return
            now = time.monotonic()
            if self._paused:
                last = now
                time.sleep(0.1)
                continue
            elapsed += now - last
            last = now
            self.item_status["elapsed"] = round(elapsed, 1)
            if self._reapply:
                self._reapply = False
                applied = in_frames = False
                period = 1.0 / max(1, self._fps)
            if eff.native:
                if not applied:
                    applied = self._guard("apply effect", lambda: eff.apply(self.ctrl), gen)
                    continue
                if now - last_health >= HEALTH_INTERVAL:
                    last_health = now
                    self._health(eff, gen, native=True)
                    applied = self._powered_on and applied and self._state_ok
                time.sleep(0.2)
                continue
            if eff.static:   # fixed effect: paint the picture once, then only watch that it stays
                if not applied:
                    applied = self._guard("paint picture", lambda: self._paint_once(eff), gen)
                    continue
                if now - last_health >= HEALTH_INTERVAL:
                    last_health = now
                    self._health(eff, gen, native=False)
                    applied = applied and self._state_ok
                time.sleep(0.2)
                continue
            # animated effect
            if not in_frames:
                in_frames = self._guard("start frames", self.ctrl.start_frames, gen)
                if not in_frames:
                    continue
            t0 = time.monotonic()
            ok = self._guard("send frame", lambda: self.ctrl.show_frame(eff.frame(elapsed)), gen)
            if not ok:
                in_frames = False
                self.ctrl.stop_frames()
                continue
            frames_done += 1
            if time.monotonic() - fps_mark >= 3:
                self.achieved_fps = round(frames_done / (time.monotonic() - fps_mark), 1)
                frames_done, fps_mark = 0, time.monotonic()
            if now - last_health >= HEALTH_INTERVAL:
                last_health = now
                self._health(eff, gen, native=False)
                if not self._state_ok:
                    in_frames = False
            spare = period - (time.monotonic() - t0)
            if spare > 0:
                time.sleep(spare)

    def _paint_once(self, eff: fx.Effect) -> None:
        self.ctrl.start_frames()
        self.ctrl.show_frame(eff.frame(0.0), force_full=True)

    _state_ok = True

    def _health(self, eff: fx.Effect, gen: int, native: bool) -> None:
        """Every so often check the controller still shows what we think (it may have lost power and restarted)."""
        self._state_ok = True
        try:
            st = self.ctrl.query_state()
        except ControllerError:
            return  # the next send reports the problem
        if st is None:
            return
        if not st.power:
            log.warning("controller reports power off while playing; turning it back on")
            self._powered_on = False
            self._ensure_power(gen)
            self._state_ok = False
        elif not native and st.pattern != SCRIBBLE_PATTERN:
            log.warning("controller left per-pixel mode (restarted?); re-entering it")
            self._state_ok = False

    def _apply_idle(self) -> None:
        action = self._idle["action"]
        self.item_status = {}

        def do():
            if action == "off":
                self.ctrl.power(False)
            elif action == "solid":
                self.ctrl.power(True)
                self.ctrl.solid(parse_hex(self._idle["color"]))
        if action == "leave":
            self.ctrl.stop_frames()
            return
        try:
            do()
        except ControllerError as exc:
            log.warning("could not apply idle action: %s", exc)


class ScheduleManager:
    """Starts and stops run lists according to the schedules."""

    def __init__(self, db: Database, runner: Runner, interval: float = 5.0):
        self.db, self.runner, self.interval = db, runner, interval
        self._finished: Set[Tuple[int, str]] = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.active_id: Optional[int] = None
        runner.on_end = self._plan_ended

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="schedules", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _plan_ended(self, plan: dict, reason: str) -> None:
        """A scheduled run that finished or was stopped is not restarted within the same window."""
        if plan["source"].startswith("schedule:") and plan.get("window") and reason in ("finished", "stopped"):
            self._finished.add((int(plan["source"].split(":")[1]), plan["window"]))

    def suppress_active(self, now: Optional[dt.datetime] = None) -> None:
        """Called when the user presses Stop: stay off until the next window instead of restarting right away."""
        now = now or dt.datetime.now(dt.timezone.utc)
        cand = self._pick(now)
        if cand:
            self._finished.add((cand.id, cand.start.isoformat()))

    def _rows(self) -> List[dict]:
        return self.db.list_schedules()

    def _pick(self, now: dt.datetime):
        place = self.db.place()
        rows = [{"id": r["id"], "enabled": r["enabled"], "priority": r["priority"],
                 "spec": sch.validate_spec(r["spec"])} for r in self._rows()]
        return sch.pick_active(rows, now, place)

    def evaluate(self, now: Optional[dt.datetime] = None) -> None:
        now = now or dt.datetime.now(dt.timezone.utc)
        plan = self.runner.plan_info()
        if plan and plan["source"] == "manual":
            return
        cand = self._pick(now)
        self.active_id = cand.id if cand else None
        if cand is None:
            if plan and plan["source"].startswith("schedule:"):
                self.runner.stop(idle=True, by_user=False)
            return
        window = cand.start.isoformat()
        source = f"schedule:{cand.id}"
        if (cand.id, window) in self._finished:
            return
        sched = self.db.get_schedule(cand.id)
        if plan and plan["source"] == source and plan.get("window") == window:
            if plan.get("sig") == self.db.get_runlist(sched["runlist_id"])["updated"]:
                return
        try:
            self.runner.play_runlist(sched["runlist_id"], source=source, window=window)
            log.info("schedule %r is active until %s", sched["name"], cand.end.strftime("%a %H:%M"))
        except (ValueError, LookupError) as exc:
            log.error("schedule %r cannot start: %s", sched["name"], exc)
            self._finished.add((cand.id, window))

    def upcoming(self, now: Optional[dt.datetime] = None, count: int = 5) -> List[dict]:
        now = now or dt.datetime.now(dt.timezone.utc)
        place = self.db.place()
        out = []
        for r in self._rows():
            if not r["enabled"]:
                continue
            spec = sch.validate_spec(r["spec"])
            for start, end in sch.next_windows(spec, now, place, 1):
                out.append({"schedule_id": r["id"], "name": r["name"], "runlist": r["runlist_name"],
                            "start": start.isoformat(), "end": end.isoformat()})
        out.sort(key=lambda w: w["start"])
        return out[:count]

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.evaluate()
            except Exception:
                log.exception("schedule evaluation failed")
            self._stop.wait(self.interval)
