"""The web API (FastAPI) and the static web UI."""
from __future__ import annotations

import datetime as dt
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from . import effects as fx
from . import protocol as P
from . import scheduler as sch
from .auth import COOKIE, MIN_PASSWORD_LENGTH, Auth, fingerprint, hash_password
from .config import default_data_dir
from .controller import Controller, ControllerError, SimulatedController, discover
from .db import Conflict, Database, NotFound
from .health import ConnectionMonitor, STALL_SEC
from .runner import DBLogHandler, Runner, ScheduleManager
from .seed import seed_catalog

WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger("tm1814.api")


# ---------------------------------------------------------------- request models
class EffectIn(BaseModel):
    name: str
    kind: str
    params: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class EffectPatch(BaseModel):
    name: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    description: Optional[str] = None


class DuplicateIn(BaseModel):
    name: Optional[str] = None


class ItemIn(BaseModel):
    effect_id: int
    duration_sec: float = 60.0


class RunlistIn(BaseModel):
    name: str
    loop: bool = True
    shuffle: bool = False
    notes: str = ""
    items: List[ItemIn] = Field(default_factory=list)


class RunlistPatch(BaseModel):
    name: Optional[str] = None
    loop: Optional[bool] = None
    shuffle: Optional[bool] = None
    notes: Optional[str] = None
    items: Optional[List[ItemIn]] = None


class ScheduleIn(BaseModel):
    name: str
    runlist_id: int
    spec: Dict[str, Any]
    enabled: bool = True
    priority: int = 0


class SchedulePatch(BaseModel):
    name: Optional[str] = None
    runlist_id: Optional[int] = None
    spec: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


class PlayIn(BaseModel):
    runlist_id: Optional[int] = None
    effect_id: Optional[int] = None
    kind: Optional[str] = None            # play an unsaved effect straight from the editor
    params: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    duration_sec: Optional[float] = None  # None = until stopped


class PreviewIn(BaseModel):
    kind: str
    params: Dict[str, Any] = Field(default_factory=dict)
    seconds: float = 20.0
    fps: float = 5.0


class OccurrencesIn(BaseModel):
    spec: Dict[str, Any]
    count: int = 6


class LoginIn(BaseModel):
    password: str = ""


class PasswordIn(BaseModel):
    current: str = ""
    new: str = ""


class PowerIn(BaseModel):
    on: bool


class ColorIn(BaseModel):
    color: str


class BrightnessIn(BaseModel):
    value: int


# ---------------------------------------------------------------- app factory
def create_app(data_dir: Optional[str] = None, controller_host: Optional[str] = None, simulate: bool = False,
               password: Optional[str] = None, start_background: bool = True) -> FastAPI:
    data_path = Path(data_dir) if data_dir else default_data_dir()
    db = Database(":memory:" if data_dir == ":memory:" else str(data_path / "tm1814.db"))
    seed_catalog(db)
    if controller_host and not db.settings()["controller_host"]:
        db.update_settings({"controller_host": controller_host})
    s = db.settings()
    ctrl_cls = SimulatedController if simulate else Controller
    controller = ctrl_cls(s["controller_host"], s["controller_port"])
    db.prune_logs()                                   # at startup too, in case the program was off for a while
    logbuf = DBLogHandler(db)
    logging.getLogger("tm1814").setLevel(logging.INFO)
    logging.getLogger("tm1814").addHandler(logbuf)
    runner = Runner(db, controller)
    manager = ScheduleManager(db, runner)
    monitor = ConnectionMonitor(db, controller, runner, manager)
    runner.on_failure = monitor.nudge

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            runner.start()
            manager.start()
            monitor.start()
            log.info("tm1814-led %s started%s", __version__, " (simulated controller)" if simulate else "")
        yield
        if start_background:
            monitor.shutdown()
            manager.shutdown()
            runner.shutdown()
        logging.getLogger("tm1814").removeHandler(logbuf)

    app = FastAPI(title="tm1814-led", version=__version__, lifespan=lifespan)
    app.state.db, app.state.controller, app.state.runner, app.state.manager = db, controller, runner, manager
    app.state.monitor = monitor

    # ---- login page + session cookie (the web page is the only interface)
    env_fp = fingerprint(password)
    override = db.get_password_override()
    if override and override.get("env_fp") != env_fp:
        db.clear_password_override()      # the settings file's password was changed (installer): that one wins again
        override = None
    auth = Auth(password, db.auth_secret(), override=override)
    app.state.auth = auth
    PUBLIC = {"/login", "/api/login", "/api/auth", "/api/health/live", "/style.css", "/favicon.ico"}

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if not auth.enabled or path in PUBLIC or auth.valid_token(request.cookies.get(COOKIE)):
            response = await call_next(request)
        elif path.startswith("/api/"):
            response = JSONResponse({"detail": "login required"}, status_code=401)  # no WWW-Authenticate: no browser pop-up
        else:
            response = RedirectResponse("/login", status_code=302)
        # never let a browser reuse a page or answer without asking us: after Log out the next request must be redirected
        response.headers["Cache-Control"] = "no-store" if path.startswith("/api/") else "no-cache"
        return response

    @app.get("/login", include_in_schema=False)
    def login_page():
        if not auth.enabled:
            return RedirectResponse("/", status_code=302)
        return FileResponse(str(WEB_DIR / "login.html"))

    @app.post("/api/login")
    def login(body: LoginIn, request: Request):
        client = request.client.host if request.client else "unknown"
        wait = auth.blocked_for(client)
        if wait:
            return JSONResponse({"detail": f"Too many wrong passwords. Try again in {wait} seconds."}, status_code=429)
        if not auth.enabled or auth.check_password(body.password):
            auth.record_success(client)
            resp = JSONResponse({"ok": True})
            resp.set_cookie(COOKIE, auth.make_token(), max_age=auth.session_seconds, httponly=True,
                            samesite="lax", path="/")
            return resp
        auth.record_failure(client)
        time.sleep(0.6)  # slows down guessing
        return JSONResponse({"detail": "That is not the password."}, status_code=401)

    @app.post("/api/password")
    def change_password(body: PasswordIn, request: Request):
        client = request.client.host if request.client else "unknown"
        wait = auth.blocked_for(client)
        if wait:
            return JSONResponse({"detail": f"Too many wrong passwords. Try again in {wait} seconds."}, status_code=429)
        if not auth.check_password(body.current):
            auth.record_failure(client)
            time.sleep(0.6)
            return JSONResponse({"detail": "The current password is not right."}, status_code=401)
        new = body.new
        if len(new) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"The new password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if len(new) > 200:
            raise ValueError("The new password is too long (200 characters at most).")
        if new == body.current:
            raise ValueError("The new password is the same as the current one.")
        record = hash_password(new)
        db.set_password_override(record, env_fp)
        auth.set_override(record)           # every other browser and terminal is signed out from now on
        auth.record_success(client)
        log.info("the web page password was changed")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, auth.make_token(), max_age=auth.session_seconds, httponly=True, samesite="lax", path="/")
        return resp

    @app.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/auth")
    def auth_info(request: Request):
        ok = (not auth.enabled) or auth.valid_token(request.cookies.get(COOKIE))
        return {"required": auth.enabled, "authenticated": bool(ok)}

    # ---- error mapping
    @app.exception_handler(NotFound)
    async def _nf(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Conflict)
    async def _conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)  # includes ParamError and SpecError
    async def _bad(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ControllerError)
    async def _ctrl(request, exc):
        return JSONResponse({"detail": f"controller: {exc}"}, status_code=502)

    # ---------------------------------------------------------------- status / meta
    @app.get("/api/status")
    def status():
        st = runner.status()
        place = db.place()
        active = None
        if manager.active_id:
            try:
                active = {"id": manager.active_id, "name": db.get_schedule(manager.active_id)["name"]}
            except NotFound:
                pass
        h = monitor.snapshot()
        return {**st, "version": __version__, "active_schedule": active, "upcoming": manager.upcoming(count=3),
                "health": {k: h[k] for k in ("state", "since", "last_ok", "failures", "unreachable_for_sec", "error")}
                          | {"diagnosis": h["diagnosis"].get("summary", ""), "host": h["host"]},
                "needs_setup": not db.settings()["controller_host"] and not simulate,
                "has_location": place.has_location}

    @app.get("/api/meta")
    def meta():
        try:
            import zoneinfo
            zones = sorted(zoneinfo.available_timezones())
        except Exception:
            zones = []
        return {"version": __version__, "simulated": simulate, "timezones": zones, "weekdays": sch.WEEKDAYS,
                "repeat_types": list(sch.REPEAT_TYPES), "led_count": controller.led_count,
                "builtin_patterns": [{"id": k, "name": v} for k, v in P.BUILTIN_PATTERNS.items()]}

    @app.get("/api/effect-kinds")
    def effect_kinds():
        return fx.kinds_schema()

    @app.get("/api/logs")
    def logs(count: int = 200, since: Optional[float] = None, until: Optional[float] = None,
             level: Optional[str] = None, q: Optional[str] = None):
        """The newest `count` matching lines, oldest first. since/until are Unix times (seconds)."""
        return db.query_logs(since, until, level, q, count)["entries"]

    @app.get("/api/logs/query")
    def logs_query(since: Optional[float] = None, until: Optional[float] = None, level: Optional[str] = None,
                   q: Optional[str] = None, limit: int = 1000):
        """Like /api/logs but with the totals: {entries, total, limit, truncated, oldest, newest, stored, retention_days}."""
        return db.query_logs(since, until, level, q, limit)

    # ---------------------------------------------------------------- settings / controller
    @app.get("/api/settings")
    def get_settings():
        out = db.settings()
        out.pop("seeded_keys", None)
        out["smtp_password_set"] = bool(out.pop("smtp_password", ""))     # the password is write-only
        return out

    @app.put("/api/settings")
    def put_settings(changes: Dict[str, Any]):
        changes.pop("seeded_keys", None)
        changes.pop("smtp_password_set", None)
        if changes.get("smtp_password") is None:
            changes.pop("smtp_password", None)                            # not sent = keep the saved one
        before = db.settings()
        if ("controller_host" in changes and str(changes["controller_host"] or "").strip() != before["controller_host"]
                and "controller_mac" not in changes):
            changes["controller_mac"] = ""                                # a different address: learn its MAC again
        db.update_settings(changes)
        runner.apply_settings()
        return get_settings()

    # ---------------------------------------------------------------- health and self-healing
    @app.get("/api/health/live")
    def health_live():
        """Public and secret-free: lets the independent watchdog tell whether this program is alive and not wedged."""
        snap = monitor.snapshot()["heartbeat"]
        ages = [v for v in snap.values() if v is not None]
        return {"ok": all(a < STALL_SEC for a in ages), "version": __version__, "heartbeat": snap,
                "uptime_sec": int(time.time() - runner.started_at)}

    @app.get("/api/health")
    def health():
        return {**monitor.snapshot(), "incidents": db.list_incidents(20)}

    @app.get("/api/incidents")
    def incidents(limit: int = 50):
        return db.list_incidents(limit)

    @app.post("/api/health/recover")
    def health_recover():
        return monitor.recover_now()

    @app.post("/api/health/test-alert")
    def health_test_alert():
        from . import alerts as alerts_mod
        cfg = db.settings()
        if not alerts_mod.channels(cfg):
            raise ValueError("No alert channel is set up yet: enter an email address and mail server, or a webhook URL.")
        ok, via = alerts_mod.send(cfg, "Test alert", "This is a test from the LED Controller program. If you can read this, "
                                                      "alerts are working.")
        if not ok:
            raise ValueError("The test alert could not be delivered; see the Logs page for the reason.")
        return {"sent": True, "channels": via}

    @app.get("/api/controller/state")
    def controller_state():
        try:
            st = controller.query_state()
        except ControllerError as exc:
            return {"reachable": False, "error": str(exc)}
        return {"reachable": st is not None, "state": st.to_dict() if st else None,
                "error": "" if st else "no answer to the state query"}

    @app.post("/api/controller/discover")
    def controller_discover():
        return discover()

    # ---------------------------------------------------------------- effects
    @app.get("/api/effects")
    def list_effects():
        return db.list_effects()

    @app.post("/api/effects", status_code=201)
    def create_effect(body: EffectIn):
        return db.create_effect(body.name, body.kind, body.params, body.description)

    @app.get("/api/effects/{effect_id}")
    def get_effect(effect_id: int):
        return db.get_effect(effect_id)

    @app.put("/api/effects/{effect_id}")
    def update_effect(effect_id: int, body: EffectPatch):
        out = db.update_effect(effect_id, body.name, body.params, body.description)
        _refresh_if_playing()
        return out

    @app.post("/api/effects/{effect_id}/duplicate", status_code=201)
    def duplicate_effect(effect_id: int, body: DuplicateIn = DuplicateIn()):
        return db.duplicate_effect(effect_id, body.name)

    @app.delete("/api/effects/{effect_id}", status_code=204)
    def delete_effect(effect_id: int):
        db.delete_effect(effect_id)

    @app.post("/api/preview")
    def preview(body: PreviewIn):
        seconds = max(1.0, min(60.0, body.seconds))
        fps = max(1.0, min(15.0, body.fps))
        frames = fx.render_preview(body.kind, body.params, controller.led_count, seconds, fps)
        return {"fps": fps, "led_count": controller.led_count, "frames": frames}

    # ---------------------------------------------------------------- run lists
    @app.get("/api/runlists")
    def list_runlists():
        return db.list_runlists()

    @app.post("/api/runlists", status_code=201)
    def create_runlist(body: RunlistIn):
        return db.create_runlist(body.name, body.loop, body.shuffle, body.notes, [i.model_dump() for i in body.items])

    @app.get("/api/runlists/{runlist_id}")
    def get_runlist(runlist_id: int):
        return db.get_runlist(runlist_id)

    @app.put("/api/runlists/{runlist_id}")
    def update_runlist(runlist_id: int, body: RunlistPatch):
        items = [i.model_dump() for i in body.items] if body.items is not None else None
        out = db.update_runlist(runlist_id, body.name, body.loop, body.shuffle, body.notes, items)
        return out

    @app.post("/api/runlists/{runlist_id}/duplicate", status_code=201)
    def duplicate_runlist(runlist_id: int, body: DuplicateIn = DuplicateIn()):
        return db.duplicate_runlist(runlist_id, body.name)

    @app.delete("/api/runlists/{runlist_id}", status_code=204)
    def delete_runlist(runlist_id: int):
        db.delete_runlist(runlist_id)

    # ---------------------------------------------------------------- schedules
    @app.get("/api/schedules")
    def list_schedules():
        return db.list_schedules()

    @app.post("/api/schedules", status_code=201)
    def create_schedule(body: ScheduleIn):
        return db.create_schedule(body.name, body.runlist_id, body.spec, body.enabled, body.priority)

    @app.get("/api/schedules/{schedule_id}")
    def get_schedule(schedule_id: int):
        return db.get_schedule(schedule_id)

    @app.put("/api/schedules/{schedule_id}")
    def update_schedule(schedule_id: int, body: SchedulePatch):
        return db.update_schedule(schedule_id, body.name, body.runlist_id, body.spec, body.enabled, body.priority)

    @app.post("/api/schedules/{schedule_id}/duplicate", status_code=201)
    def duplicate_schedule(schedule_id: int, body: DuplicateIn = DuplicateIn()):
        return db.duplicate_schedule(schedule_id, body.name)

    @app.delete("/api/schedules/{schedule_id}", status_code=204)
    def delete_schedule(schedule_id: int):
        db.delete_schedule(schedule_id)

    def _occurrences(spec: dict, count: int):
        valid = sch.validate_spec(spec)
        place = db.place()
        now = dt.datetime.now(dt.timezone.utc)
        wins = sch.next_windows(valid, now, place, max(1, min(30, count)))
        estimate = (valid["start_time"] in sch.SUN_WORDS or valid["end_time"] in sch.SUN_WORDS) and not place.has_location
        return {"summary": sch.describe(valid), "sun_estimated": estimate,
                "windows": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in wins]}

    @app.post("/api/schedules/preview")
    def preview_schedule(body: OccurrencesIn):
        return _occurrences(body.spec, body.count)

    @app.get("/api/schedules/{schedule_id}/occurrences")
    def schedule_occurrences(schedule_id: int, count: int = 6):
        return _occurrences(db.get_schedule(schedule_id)["spec"], count)

    # ---------------------------------------------------------------- control
    def _refresh_if_playing():
        """If an edited effect is currently playing, restart the plan so the change shows up."""
        plan = runner.plan_info()
        if plan and plan["source"] == "manual" and plan.get("runlist_id") is None and plan["items"][0].get("effect_id"):
            try:
                runner.play_effect(db.get_effect(plan["items"][0]["effect_id"]), plan["items"][0]["duration"])
            except NotFound:
                pass

    @app.post("/api/control/play")
    def play(body: PlayIn):
        if body.runlist_id is not None:
            runner.play_runlist(body.runlist_id)
        elif body.effect_id is not None:
            runner.play_effect(db.get_effect(body.effect_id), body.duration_sec)
        elif body.kind:
            cleaned = fx.get_kind(body.kind).clean(body.params or {})
            runner.play_effect({"id": None, "name": body.name or "Editor preview", "kind": body.kind, "params": cleaned},
                               body.duration_sec)
        else:
            raise ValueError("give runlist_id, effect_id, or kind and params")
        return runner.status()

    @app.post("/api/control/stop")
    def stop():
        manager.suppress_active()
        runner.stop()
        return runner.status()

    @app.post("/api/control/next")
    def next_item():
        runner.next()
        return {"ok": True}

    @app.post("/api/control/pause")
    def pause():
        runner.pause(True)
        return {"ok": True}

    @app.post("/api/control/resume")
    def resume():
        runner.pause(False)
        return {"ok": True}

    @app.post("/api/control/power")
    def power(body: PowerIn):
        manager.suppress_active()
        runner.stop(idle=False)
        controller.power(body.on)
        return {"ok": True}

    @app.post("/api/control/color")
    def color(body: ColorIn):
        cleaned = fx.get_kind("solid").clean({"color": body.color})
        runner.play_effect({"id": None, "name": f"Solid {cleaned['color']}", "kind": "solid", "params": cleaned})
        return runner.status()

    @app.post("/api/control/brightness")
    def brightness(body: BrightnessIn):
        db.update_settings({"brightness": body.value})
        runner.apply_settings()
        return {"brightness": db.settings()["brightness"]}

    # ---------------------------------------------------------------- web UI
    if (WEB_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:
        @app.get("/")
        def placeholder():
            return PlainTextResponse("tm1814-led API is running; the web UI files are missing from this install.")
    return app
