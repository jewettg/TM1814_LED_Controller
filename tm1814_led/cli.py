"""Command line for tm1814-led (`tm1814-led ...` or `python3 -m tm1814_led ...`).

Three kinds of commands:
  * run the program:      serve, install, uninstall, service
  * talk to the service:  status, effects, runlists, schedules, play, stop, next, pause, resume, brightness, logs
                          (they sign in with the same password as the web page, found automatically on the
                          machine where the service is installed)
  * talk to the controller directly:  discover, state, on, off, color, test

Every command except serve/install asks for the web page's password (no echo) and remembers the sign-in for the
rest of the terminal session; `tm1814-led logout` forgets it. TM1814_PASSWORD may supply it for scripts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import __version__
from . import diagnostics
from . import protocol as P
from .client import LoginRequired, ServiceClient, ServiceError, SessionCache, connect, prompt_login, service_url
from .config import DEFAULT_HOST, DEFAULT_PORT, default_data_dir

# ---------------------------------------------------------------- colors and output

NAMED_COLORS: Dict[str, Tuple[int, int, int]] = {
    "red": (255, 0, 0), "orange": (255, 90, 0), "amber": (255, 140, 0), "yellow": (255, 200, 0),
    "green": (0, 255, 0), "teal": (0, 200, 150), "cyan": (0, 220, 255), "blue": (0, 0, 255),
    "purple": (140, 0, 255), "magenta": (255, 0, 200), "pink": (255, 80, 140), "white": (255, 255, 255),
    "warm": (255, 180, 70), "cool": (200, 220, 255),
}
ANSI = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36"}


def parse_color(text: str) -> Tuple[int, int, int]:
    """'#ff5a00', 'ff5a00', '255,90,0' or a name such as 'orange'."""
    t = text.strip().lower()
    if t in NAMED_COLORS:
        return NAMED_COLORS[t]
    if "," in t:
        parts = [int(x) for x in t.split(",")]
        if len(parts) == 3 and all(0 <= x <= 255 for x in parts):
            return (parts[0], parts[1], parts[2])
        raise ValueError(f"not a valid r,g,b color: {text!r}")
    try:
        return P.parse_hex(t)
    except ValueError:
        names = ", ".join(sorted(NAMED_COLORS))
        raise ValueError(f"unknown color {text!r}: use #rrggbb, r,g,b or one of: {names}") from None


class Out:
    """Colored (when a terminal) text, aligned tables and JSON output."""

    def __init__(self, color: bool = True, as_json: bool = False):
        self.color = color and sys.stdout.isatty() and "NO_COLOR" not in os.environ
        self.as_json = as_json

    def p(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "\033[" + ";".join(ANSI[s] for s in styles) + "m" + text + "\033[0m"

    def say(self, text: str = "") -> None:
        print(text)

    def ok(self, text: str) -> None:
        print(self.p("✔ ", "green") + text if self.color else "OK: " + text)

    def fail(self, text: str) -> None:
        print((self.p("✖ ", "red") if self.color else "Error: ") + text, file=sys.stderr)

    def json(self, obj: Any) -> None:
        print(json.dumps(obj, indent=2, default=str))

    def table(self, headers: Sequence[str], rows: List[Sequence[Any]]) -> None:
        cells = [[str(c) for c in r] for r in rows]
        widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
        print(self.p("  ".join(h.ljust(w) for h, w in zip(headers, widths)), "bold"))
        for r in cells:
            print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())

    def kv(self, rows: List[Tuple[str, str]]) -> None:
        width = max(len(k) for k, _ in rows)
        for k, v in rows:
            print(self.p(k.ljust(width), "dim") + "  " + v)


def fmt_secs(s: Optional[float]) -> str:
    if s is None:
        return "until stopped"
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def fmt_when(iso: str) -> str:
    try:
        return dt.datetime.fromisoformat(iso).astimezone().strftime("%a %b %d  %I:%M %p").replace(" 0", " ")
    except ValueError:
        return iso


# ---------------------------------------------------------------- argument parsing

def _common() -> Tuple[argparse.ArgumentParser, argparse.ArgumentParser, argparse.ArgumentParser]:
    base = argparse.ArgumentParser(add_help=False)
    base.add_argument("--no-color", action="store_true", help="plain output (also honors NO_COLOR)")
    base.add_argument("--json", action="store_true", help="machine-readable JSON output")
    svc = argparse.ArgumentParser(add_help=False, parents=[base])
    svc.add_argument("--url", default=None, help="service address (default: the service installed on this machine)")
    hw = argparse.ArgumentParser(add_help=False, parents=[svc])
    hw.add_argument("--controller-host", default=os.environ.get("TM1814_CONTROLLER_HOST"),
                    help="controller IP or name (default: the one saved in the web page's Settings)")
    return base, svc, hw


EPILOG = """\
examples:
  tm1814-led --install                   install as a background service (see --install --help)
  tm1814-led login                       sign in (any command asks for the password once per terminal session)
  tm1814-led status                      what is playing, controller state, next schedule
  tm1814-led play "Halloween - Embers"   play an effect (or a run list: tm1814-led play "Halloween show")
  tm1814-led stop                        stop and turn the lights off
  tm1814-led logs --since 6h --level warning     what went wrong in the last six hours
  tm1814-led effects ghost               search the catalog
  tm1814-led color orange                show a solid color directly on the controller
  tm1814-led test halves                 light the strip to find where the second half starts

'service' commands need the service running; 'direct' commands (discover, state, on, off, color, test) talk to
the controller themselves, so run `tm1814-led stop` first if the service is playing something.
"""


def build_parser() -> argparse.ArgumentParser:
    base, svc, hw = _common()
    p = argparse.ArgumentParser(
        prog="tm1814-led", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Control a TM1814 / Zengge (Surplife, Magic Home) Wi-Fi LED controller: a password-protected web "
                    "page with effects, run lists and schedules, plus this command line.", epilog=EPILOG)
    p.add_argument("--version", action="version", version=f"tm1814-led {__version__}")
    sub = p.add_subparsers(dest="command", title="commands", metavar="<command>")

    def add(name: str, help: str, parents, group: str = ""):
        return sub.add_parser(name, help=(f"[{group}] " if group else "") + help, parents=parents,
                              description=help)

    s = add("serve", "run the web page and background engine", [], "run")
    s.add_argument("--host", default=os.environ.get("TM1814_HOST", DEFAULT_HOST), help="address to listen on")
    s.add_argument("--port", type=int, default=int(os.environ.get("TM1814_PORT", DEFAULT_PORT)))
    s.add_argument("--data-dir", default=None, help="where the database lives (default: per-user data folder)")
    s.add_argument("--controller-host", default=os.environ.get("TM1814_CONTROLLER_HOST"),
                   help="controller IP (only used the first time; change it later in Settings)")
    s.add_argument("--simulate", action="store_true", help="no hardware: simulate the controller")
    s.add_argument("--password", default=os.environ.get("TM1814_PASSWORD"),
                   help="password for the web page (required unless --simulate); also TM1814_PASSWORD")
    s.add_argument("--env-file", default=None, help="file of KEY=VALUE settings (used by the installed service)")
    s.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    add("install", "install as a background service (same as --install; try: install --help)", [], "run")
    add("uninstall", "stop and remove the service (same as --uninstall)", [], "run")
    add("service", "show whether the service is running and its addresses (same as --service-status)", [], "run")
    w = add("watchdog", "one independent health check of the service; restarts it if stuck (run by the system scheduler)", [], "run")
    w.add_argument("--env-file", default=None, help="file of KEY=VALUE settings (used by the installed job)")

    add("status", "what is playing, the controller, and the next scheduled runs", [svc], "service")
    add("health", "the connection to the controller: state, diagnosis and recent incidents", [svc], "service")
    add("find", "search the network for the controller now and follow it if its address changed", [svc], "service")
    e = add("effects", "list the effects in the catalog", [svc], "service")
    e.add_argument("search", nargs="?", default="", help="only names containing this text")
    e.add_argument("--native", action="store_true", help="only controller-native effects")
    e.add_argument("--fixed", action="store_true", help="only fixed effects (still pictures)")
    e.add_argument("--animated", action="store_true", help="only animated effects")
    add("runlists", "list run lists", [svc], "service")
    add("schedules", "list schedules with their next start", [svc], "service")
    pl = add("play", "play an effect or run list by name (or id)", [svc], "service")
    pl.add_argument("name", help="name, part of a name, or id")
    pl.add_argument("--effect", action="store_true", help="the name is an effect")
    pl.add_argument("--runlist", action="store_true", help="the name is a run list")
    pl.add_argument("--for", dest="seconds", type=float, default=None, help="effects only: stop after this many seconds")
    add("stop", "stop playing and apply the 'when idle' action (default: lights off)", [svc], "service")
    add("next", "skip to the next effect in the run list", [svc], "service")
    add("pause", "pause the current effect", [svc], "service")
    add("resume", "resume after pause", [svc], "service")
    b = add("brightness", "set master brightness", [svc], "service")
    b.add_argument("value", type=int, help="0-100")
    lg = add("logs", "show the log (kept for the retention period set in Settings)", [svc], "service")
    lg.add_argument("-n", "--lines", type=int, default=50, help="most lines to show (the newest ones; default 50)")
    lg.add_argument("--since", metavar="WHEN", help="start of the range: 30m, 6h, 2d, 1w, today, yesterday, 2026-10-13 or '2026-10-13 18:00'")
    lg.add_argument("--until", metavar="WHEN", help="end of the range (same forms; default: now)")
    lg.add_argument("--level", choices=["info", "warning", "error"], help="this level and above (default: everything)")
    lg.add_argument("--grep", metavar="TEXT", help="only lines containing this text")
    lg.add_argument("-f", "--follow", action="store_true", help="keep printing new lines (Ctrl-C to stop)")
    add("login", "sign in now (every command asks for the password once per terminal session)", [svc], "session")
    add("logout", "forget this terminal session's sign-in", [svc], "session")
    add("password", "change the web page / command line password", [svc], "session")
    add("discover", "find controllers on the network", [svc], "direct")
    add("state", "show the controller's state", [hw], "direct")
    add("on", "power on", [hw], "direct")
    add("off", "power off (shows black; the supply stays on)", [hw], "direct")
    c = add("color", "show a solid color", [hw], "direct")
    c.add_argument("color", help="#rrggbb, r,g,b, or a name: " + ", ".join(sorted(NAMED_COLORS)))
    c.add_argument("--white", type=int, metavar="LEVEL", default=None, help="use the white LED at this level 0-100 instead")
    t = add("test", "light a walk-around test pattern to learn how the strip is laid out", [hw], "direct")
    t.add_argument("pattern", nargs="?", choices=sorted(diagnostics.PATTERNS), help="which pattern (omit to list them)")
    t.add_argument("value", nargs="?", type=int, help="for fillto / single: the LED number")
    t.add_argument("--delay", type=float, default=0.25, help="chase: seconds per LED")
    return p


# ---------------------------------------------------------------- shared helpers

def load_env_file(path: str) -> None:
    """Read KEY=VALUE lines into os.environ without overriding variables that are already set."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as exc:
        sys.exit(f"cannot read settings file {path}: {exc}")
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _controller(args, out: Out):
    from .controller import Controller
    cl = _service(args, out)                          # sign in first: no password, no lights
    host = args.controller_host or cl.get("/api/settings").get("controller_host", "")
    if not host:
        raise ValueError("No controller address. Use --controller-host IP, or save one in the web page's Settings. "
                         "Find it with: tm1814-led discover")
    port = P.TCP_PORT
    if host.count(":") == 1:                          # host:port is allowed
        host, _, port_text = host.partition(":")
        port = int(port_text)
    return Controller(host, port)


_CLIENT: Dict[str, ServiceClient] = {}


def _service(args, out: Out) -> ServiceClient:
    """A signed-in client for the service: asks for the password once per terminal session."""
    key = str(getattr(args, "url", None))
    if key not in _CLIENT:
        _CLIENT[key] = connect(getattr(args, "url", None))
    return _CLIENT[key]


def _guard(fn: Callable[[], int], out: Out) -> int:
    from .controller import ControllerError
    try:
        return fn()
    except LoginRequired:
        SessionCache().clear()
        out.fail("Your sign-in has ended. Run the command again to sign in.")
        return 1
    except ServiceError as exc:
        out.fail(str(exc))
        if "cannot reach" in str(exc):
            out.say("  Is the service running? Check with: tm1814-led service")
        return 1
    except ControllerError as exc:
        out.fail(f"controller: {exc}")
        return 1
    except ValueError as exc:
        out.fail(str(exc))
        return 2


# ---------------------------------------------------------------- commands: service

def cmd_status(a, out: Out) -> int:
    st = _service(a, out).get("/api/status")
    if a.json:
        out.json(st)
        return 0
    c = st["controller"]
    dot = out.p("●", "red") if c["last_error"] else out.p("●", "green" if c["host"] or c["simulated"] else "yellow")
    if c["simulated"]:
        link = "simulated controller"
    elif c["connected"]:
        extras = [f"{c['led_count']} LEDs"]
        if c.get("firmware") is not None:
            extras.append(f"firmware {c['firmware']}")
        if c.get("power") is not None:
            extras.append("power " + ("on" if c["power"] else "off"))
        link = f"connected to {c['host']} ({', '.join(extras)})"
    elif c["last_error"]:
        link = f"cannot reach {c['host']}: {c['last_error']}"
    elif c["host"]:
        link = f"{c['host']} (ready; connects when something is played)"
    else:
        link = "no address set"
    rows = [("Controller", f"{dot} {link}")]
    if st["state"] == "idle":
        rows.append(("Playing", out.p("nothing", "dim")))
    else:
        it = st.get("item") or {}
        text = out.p(st["plan"], "bold")
        if it.get("name") and it["name"] != st["plan"]:
            text += f"  ›  {it['name']}"
        detail = [f"{it['index'] + 1} of {it['count']}"] if it.get("count", 0) > 1 else []
        if it:
            detail.append(f"{fmt_secs(it.get('elapsed'))} / {fmt_secs(it.get('duration'))}")
        detail.append("paused" if st["state"] == "paused" else ("scheduled" if (st["source"] or "").startswith("schedule") else "manual"))
        rows.append(("Playing", text + "  " + out.p("(" + ", ".join(detail) + ")", "dim")))
        if st.get("fps_actual"):
            rows.append(("Streaming", f"{st['fps_actual']} frames/s, {c['commands_per_second']} commands/s"))
    if st.get("active_schedule"):
        rows.append(("Schedule", "active: " + st["active_schedule"]["name"]))
    for i, w in enumerate(st.get("upcoming", [])):
        rows.append(("Next" if i == 0 else "", f"{w['name']}  —  {fmt_when(w['start'])}"))
    if st.get("needs_setup"):
        rows.append(("Setup", out.p("no controller address yet: open the web page's Settings", "yellow")))
    out.say(out.p(f"LED Controller {st['version']}", "bold") + out.p(f"   {_service(a, out).url}", "dim"))
    out.kv(rows)
    return 0


def _ago(ts: Optional[float]) -> str:
    return fmt_secs(max(0.0, time.time() - ts)) + " ago" if ts else "never"


def cmd_health(a, out: Out) -> int:
    h = _service(a, out).get("/api/health")
    if a.json:
        out.json(h)
        return 0
    state = h["state"]
    dot = out.p("●", {"online": "green", "degraded": "yellow", "offline": "red"}.get(state, "yellow"))
    rows = [("Connection", f"{dot} {state}" + (f" for {fmt_secs(time.time() - h['since'])}" if h.get("since") else "")),
            ("Controller", f"{h['host'] or '(no address set)'}   ID {h['mac'] or 'not learned yet'}"),
            ("Last answered", _ago(h["last_ok"]))]
    if h["failures"]:
        rows.append(("Not answering", f"{h['failures']} checks in a row, {fmt_secs(h['unreachable_for_sec'] or 0)}: {h['error']}"))
    if h["diagnosis"].get("summary"):
        rows.append(("Likely reason", h["diagnosis"]["summary"]))
    rows.append(("Alerts", ", ".join(h["alert_channels"]) or out.p("none set up (Settings > Self-healing and alerts)", "dim")))
    hb = h["heartbeat"]
    rows.append(("Heartbeat", f"worker {hb['runner_sec']} s, schedules {hb['schedules_sec']} s, monitor {hb['monitor_sec']} s"))
    out.kv(rows)
    if h["incidents"]:
        out.say()
        out.table(("STARTED", "LASTED", "WHAT HAPPENED"),
                  [(fmt_when(dt.datetime.fromtimestamp(i["started"]).astimezone().isoformat()),
                    "still going" if i["open"] else fmt_secs(i["duration_sec"]),
                    i["resolution"] or i["summary"]) for i in h["incidents"][:10]])
    return 0


def cmd_find(a, out: Out) -> int:
    r = _service(a, out).post("/api/health/recover")
    if a.json:
        out.json(r)
    elif r["moved"]:
        out.ok(f"The controller had moved: {r['was']} -> {out.p(r['host'], 'bold')}. The program is following it.")
    elif r["state"] == "online":
        out.ok(f"The controller is answering at {r['host']}; nothing needed fixing.")
    else:
        out.fail("The controller was not found on the network." + ("" if r["mac_known"] else
                 " Its ID (MAC address) is not known yet, so it cannot be recognized if it moved; it is learned the first time it answers."))
    return 0 if (r["moved"] or r["state"] == "online") else 1


def cmd_watchdog(a, out: Out) -> int:
    from .watchdog import run_once
    r = run_once()
    if r["ok"]:
        out.say("watchdog: OK" + ("; " + "; ".join(r["actions"]) if r["actions"] else ""))
        return 0
    out.say("watchdog: PROBLEM - " + r["problem"] + ("; " + "; ".join(r["actions"]) if r["actions"] else ""))
    return 1


def cmd_effects(a, out: Out) -> int:
    cl = _service(a, out)
    effects, kinds = cl.get("/api/effects"), {k["kind"]: k for k in cl.get("/api/effect-kinds")}
    wanted = {f for f, on in (("native", a.native), ("fixed", a.fixed), ("animated", a.animated)) if on}
    names = {"native": "Controller-native", "fixed": "Fixed", "animated": "Animated"}
    rows = []
    for e in effects:
        k = kinds.get(e["kind"], {})
        family = k.get("family", "animated")
        if a.search and a.search.lower() not in e["name"].lower():
            continue
        if wanted and family not in wanted:
            continue
        rows.append((e["id"], e["name"], k.get("label", e["kind"]), family))
    if a.json:
        out.json([dict(zip(("id", "name", "kind", "family"), r)) for r in rows])
    else:
        out.table(("ID", "NAME", "KIND", "TYPE"), [(i, n, k, names[f]) for i, n, k, f in rows])
        out.say(out.p(f"{len(rows)} effect(s). Play one with: tm1814-led play \"name\"", "dim"))
    return 0


def cmd_runlists(a, out: Out) -> int:
    lists = _service(a, out).get("/api/runlists")
    if a.json:
        out.json(lists)
        return 0
    out.table(("ID", "NAME", "ITEMS", "LENGTH", "REPEAT"),
              [(r["id"], r["name"], len(r["items"]), fmt_secs(r["total_sec"]),
                ("loop" if r["loop"] else "once") + (", shuffle" if r["shuffle"] else "")) for r in lists])
    return 0


def cmd_schedules(a, out: Out) -> int:
    cl = _service(a, out)
    rows, raw = [], cl.get("/api/schedules")
    for s in raw:
        nxt = ""
        if s["enabled"]:
            wins = cl.get(f"/api/schedules/{s['id']}/occurrences?count=1")["windows"]
            nxt = fmt_when(wins[0]["start"]) if wins else "never"
        rows.append((s["id"], s["name"], "on" if s["enabled"] else "off", s["runlist_name"], s["summary"], nxt))
    if a.json:
        out.json(raw)
    else:
        out.table(("ID", "NAME", "ENABLED", "RUN LIST", "WHEN", "NEXT START"), rows)
    return 0


def _resolve_target(a, cl: ServiceClient) -> Tuple[str, dict]:
    """Find the effect or run list the user means: exact id or name first, then a unique partial name."""
    pools = []
    if not a.runlist:
        pools.append(("effect", cl.get("/api/effects")))
    if not a.effect:
        pools.append(("run list", cl.get("/api/runlists")))
    want = a.name.strip().lower()
    for finder in (lambda x: str(x["id"]) == want and a.name.strip().isdigit(),
                   lambda x: x["name"].lower() == want,
                   lambda x: want in x["name"].lower()):
        hits = [(kind, x) for kind, items in pools for x in items if finder(x)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            names = "\n".join(f"    {k}: {x['name']}  (id {x['id']})" for k, x in hits[:12])
            raise ValueError(f"{a.name!r} matches more than one:\n{names}\n  Be more specific, or add --effect / --runlist.")
    raise ValueError(f"nothing named {a.name!r}. See: tm1814-led effects, tm1814-led runlists")


def cmd_play(a, out: Out) -> int:
    cl = _service(a, out)
    kind, target = _resolve_target(a, cl)
    body = {"runlist_id": target["id"]} if kind == "run list" else {"effect_id": target["id"], "duration_sec": a.seconds}
    st = cl.post("/api/control/play", body)
    if a.json:
        out.json(st)
    else:
        out.ok(f"Playing {kind} {out.p(target['name'], 'bold')}" + (f" for {fmt_secs(a.seconds)}" if a.seconds else ""))
        out.say(out.p("  stop with: tm1814-led stop      see what's happening: tm1814-led status", "dim"))
    return 0


def cmd_simple(path: str, message: str) -> Callable:
    def run(a, out: Out) -> int:
        _service(a, out).post(path)
        out.ok(message) if not a.json else out.json({"ok": True})
        return 0
    return run


def cmd_brightness(a, out: Out) -> int:
    if not 0 <= a.value <= 100:
        raise ValueError("brightness is 0-100")
    r = _service(a, out).post("/api/control/brightness", {"value": a.value})
    out.json(r) if a.json else out.ok(f"Master brightness is now {r['brightness']}%")
    return 0


def parse_when(text: str, now: Optional[dt.datetime] = None) -> float:
    """'30m', '6h', '2d', '1w' (that long ago), 'today', 'yesterday', or a local date / date and time -> Unix time."""
    now = now or dt.datetime.now()
    t = text.strip().lower()
    if t == "now":
        return now.timestamp()
    if t in ("today", "yesterday"):
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (day - dt.timedelta(days=1 if t == "yesterday" else 0)).timestamp()
    if t and t[-1] in "smhdw" and t[:-1].replace(".", "", 1).isdigit():
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[t[-1]]
        return (now - dt.timedelta(seconds=float(t[:-1]) * seconds)).timestamp()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"cannot understand the time {text!r}: use 30m, 6h, 2d, 1w, today, yesterday, 2026-10-13 or '2026-10-13 18:00'")


def cmd_logs(a, out: Out) -> int:
    from urllib.parse import urlencode
    cl = _service(a, out)
    params: Dict[str, Any] = {"limit": max(1, a.lines)}
    if a.since:
        params["since"] = parse_when(a.since)
    if a.until:
        params["until"] = parse_when(a.until)
    if a.level:
        params["level"] = a.level.upper()
    if a.grep:
        params["q"] = a.grep
    level_style = {"WARNING": "yellow", "ERROR": "red", "CRITICAL": "red"}
    seen = 0.0
    first = True
    while True:
        res = cl.get("/api/logs/query?" + urlencode(params))
        for l in res["entries"]:
            if l["ts"] <= seen:
                continue
            if a.json:
                out.json(l)
            else:
                stamp = dt.datetime.fromtimestamp(l["ts"]).strftime("%b %d %H:%M:%S").replace(" 0", "  ")
                out.say(out.p(stamp, "dim") + "  " + out.p(l["level"][:4].ljust(4), level_style.get(l["level"], "dim")) + "  " + l["msg"])
            seen = max(seen, l["ts"])
        if first and not a.json:
            note = f"{len(res['entries'])} of {res['total']} matching line(s)"
            if res["truncated"]:
                note += f" — showing the newest {res['limit']}; use -n, --since or --grep to narrow"
            out.say(out.p(note + f". Logs are kept for {res['retention_days']} days.", "dim"))
        first = False
        if not a.follow:
            return 0
        try:
            time.sleep(2)
        except KeyboardInterrupt:
            return 0
        params["since"] = max(seen, params.get("since", 0.0))
        params["limit"] = 200


# ---------------------------------------------------------------- commands: direct to the controller

def cmd_login(a, out: Out) -> int:
    cache = SessionCache()
    client = ServiceClient(service_url(a.url))
    prompt_login(client, cache)
    out.ok(f"Signed in to {client.url} for this terminal session (end it with: tm1814-led logout)")
    return 0


def cmd_password(a, out: Out) -> int:
    cl = _service(a, out)
    if not sys.stdin.isatty():
        raise ValueError("changing the password needs a terminal to type it in")
    current = getpass.getpass("Current password: ")
    new = getpass.getpass("New password (at least 8 characters): ")
    if getpass.getpass("New password again: ") != new:
        raise ValueError("The two new passwords do not match.")
    cl.post("/api/password", {"current": current, "new": new})
    token = cl.adopt_session_cookie()
    if token:
        SessionCache().save(cl.url, token)         # this terminal stays signed in; every other session is signed out
    out.ok("Password changed. Every other browser and terminal must sign in again with the new password.")
    return 0


def cmd_logout(a, out: Out) -> int:
    cache = SessionCache()
    client = ServiceClient(service_url(a.url), token=cache.load(service_url(a.url)))
    if client.token:
        client.logout()
    cache.clear()
    out.ok("Signed out of this terminal session")
    return 0


def cmd_discover(a, out: Out) -> int:
    _service(a, out)                                  # sign in first
    from .controller import discover
    found = discover()
    if a.json:
        out.json(found)
    elif found:
        out.table(("ADDRESS", "MAC", "MODEL"), [(d["ip"], d["mac"], d["model"]) for d in found])
        out.say(out.p("Enter the address in the web page's Settings, or use --controller-host.", "dim"))
    else:
        out.fail("No controllers answered. Is this computer on the same network (not a guest Wi-Fi) as the controller?")
    return 0 if found else 1


def cmd_state(a, out: Out) -> int:
    ctrl = _controller(a, out)
    st = ctrl.query_state()
    if st is None:
        out.fail("The controller did not answer the state query.")
        return 1
    if a.json:
        out.json({**st.to_dict(), "host": ctrl.host})
        return 0
    showing = {0x66: "per-pixel picture (a fixed or animated effect)", 0x61: "a solid color"}.get(st.pattern, f"built-in animation/scene {st.pattern}")
    out.kv([("Controller", ctrl.host), ("Power", out.p("on", "green") if st.power else out.p("off", "dim")),
            ("Showing", showing if st.power else "-"), ("Color", f"hue {st.hue}°, saturation {st.saturation}%, brightness {st.value}%"),
            ("LEDs", str(st.led_count)), ("Model / firmware", f"{st.model} / {st.version}")])
    return 0


def cmd_power(on: bool) -> Callable:
    def run(a, out: Out) -> int:
        _controller(a, out).power(on)
        out.ok("Lights " + ("on" if on else "off (black; the power supply stays on)"))
        return 0
    return run


def cmd_color(a, out: Out) -> int:
    ctrl = _controller(a, out)
    ctrl.power(True)
    if a.white is not None:
        ctrl.solid((0, 0, 0), white=max(0, min(100, a.white)))
        out.ok(f"White LED at {a.white}%")
    else:
        rgb = parse_color(a.color)
        ctrl.solid(rgb)
        out.ok(f"Showing {P.to_hex(rgb)}")
    return 0


def cmd_test(a, out: Out) -> int:
    if not a.pattern:
        out.table(("PATTERN", "WHAT IT SHOWS"), [(k, v[1]) for k, v in sorted(diagnostics.PATTERNS.items())])
        return 0
    ctrl = _controller(a, out)
    ctrl.query_state()
    n = ctrl.led_count
    fn = diagnostics.PATTERNS[a.pattern][0]
    needs_value = a.pattern in ("fillto", "single")
    if needs_value and a.value is None:
        raise ValueError(f"'{a.pattern}' needs a LED number, for example: tm1814-led test {a.pattern} 100")
    ctrl.power(True)
    ctrl.min_gap = max(ctrl.min_gap, 0.03)
    try:
        result = fn(n, a.value) if needs_value else fn(n)
        if a.pattern == "chase":
            out.say(out.p("Watch the strip; a single white LED moves from 0 to the end. Ctrl-C to stop.", "dim"))
            for frame, label in result:
                ctrl.show_frame(frame)
                print(f"\r{label}   ", end="", flush=True)
                time.sleep(a.delay)
            print()
        else:
            frame, text = result
            ctrl.show_frame(frame, force_full=True)
            out.ok(text)
            out.say(out.p("Ctrl-C to finish (the lights will turn off).", "dim"))
            while True:
                time.sleep(15)
                ctrl.show_frame(frame, force_full=True)   # keeps it on screen, heals any dropped command
    except KeyboardInterrupt:
        print()
    finally:
        try:
            ctrl.power(False)
        except Exception:
            pass
    out.say("Test finished; lights off.")
    return 0


# ---------------------------------------------------------------- run the program

def _serve(a) -> int:
    import logging
    import uvicorn
    from .api import create_app
    if not a.password and not a.simulate:
        sys.exit("A password is required to use the web page.\n"
                 "  Run the installer (it sets one):  tm1814-led --install\n"
                 "  or pass one:                      tm1814-led serve --password YOUR_PASSWORD   (or set TM1814_PASSWORD)")
    logging.basicConfig(level=getattr(logging, a.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data_dir = a.data_dir or str(default_data_dir())
    app = create_app(data_dir=data_dir, controller_host=a.controller_host, simulate=a.simulate, password=a.password)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


COMMANDS: Dict[str, Callable] = {
    "status": cmd_status, "health": cmd_health, "find": cmd_find, "effects": cmd_effects, "runlists": cmd_runlists, "schedules": cmd_schedules,
    "play": cmd_play, "stop": cmd_simple("/api/control/stop", "Stopped"),
    "next": cmd_simple("/api/control/next", "Skipped to the next effect"),
    "pause": cmd_simple("/api/control/pause", "Paused"), "resume": cmd_simple("/api/control/resume", "Resumed"),
    "brightness": cmd_brightness, "logs": cmd_logs,
    "login": cmd_login, "logout": cmd_logout, "password": cmd_password, "discover": cmd_discover, "state": cmd_state, "on": cmd_power(True), "off": cmd_power(False),
    "color": cmd_color, "test": cmd_test,
}
INSTALLER_ALIASES = {"install": "--install", "uninstall": "--uninstall", "service": "--service-status"}


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for i, tok in enumerate(argv):  # the settings file must be loaded before the parser reads its defaults
        if tok == "--env-file" and i + 1 < len(argv):
            load_env_file(argv[i + 1])
    if argv and argv[0] in INSTALLER_ALIASES:
        argv = [INSTALLER_ALIASES[argv[0]]] + argv[1:]
    if any(x in argv for x in ("--install", "--uninstall", "--service-status")):
        from .installer import main as installer_main
        return installer_main(argv)
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    a = parser.parse_args(argv)
    if a.command == "serve":
        return _serve(a)
    if a.command == "watchdog":
        return cmd_watchdog(a, Out(color=False))
    out = Out(color=not getattr(a, "no_color", False), as_json=getattr(a, "json", False))
    return _guard(lambda: COMMANDS[a.command](a, out), out)
