"""Command line entry point: `tm1814-led` (or `python -m tm1814_led`)."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional

from . import __version__
from . import protocol as P
from .config import DEFAULT_HOST, DEFAULT_PORT, default_data_dir

COMMANDS = ("serve", "discover", "state", "on", "off", "color")


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


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tm1814-led",
        description="Control a TM1814 / Zengge (Surplife, Magic Home) Wi-Fi LED controller: web UI, effects, "
                    "run lists and schedules. With no command, runs the web UI (serve).",
        epilog="Install as a background service:  tm1814-led --install   (see: tm1814-led --install --help)")
    p.add_argument("--version", action="version", version=f"tm1814-led {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("serve", help="run the web UI and background engine (default)")
    s.add_argument("--host", default=os.environ.get("TM1814_HOST", DEFAULT_HOST), help="address to listen on")
    s.add_argument("--port", type=int, default=int(os.environ.get("TM1814_PORT", DEFAULT_PORT)))
    s.add_argument("--data-dir", default=None, help="where the database lives (default: per-user data folder)")
    s.add_argument("--controller-host", default=os.environ.get("TM1814_CONTROLLER_HOST"),
                   help="controller IP (only used the first time; change it later in Settings)")
    s.add_argument("--simulate", action="store_true", help="no hardware: simulate the controller")
    s.add_argument("--password", default=os.environ.get("TM1814_PASSWORD"),
                   help="require this password (HTTP Basic, any user name); also TM1814_PASSWORD")
    s.add_argument("--env-file", default=None, help="file of KEY=VALUE settings (used by the installed service)")
    s.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    for name, helptext in (("discover", "find controllers on the network"), ("state", "show the controller's state"),
                           ("on", "power on"), ("off", "power off (black; supply stays on)"),
                           ("color", "show a solid color")):
        c = sub.add_parser(name, help=helptext)
        if name != "discover":
            c.add_argument("--controller-host", default=os.environ.get("TM1814_CONTROLLER_HOST"), required=False,
                           help="controller IP or name")
        if name == "color":
            c.add_argument("hex", help="color such as #ff5a00")
    return p


def _controller(host: Optional[str]):
    from .controller import Controller
    if not host:
        from .db import Database
        db = Database(str(default_data_dir() / "tm1814.db"))
        host = db.settings()["controller_host"]
    if not host:
        sys.exit("No controller address: pass --controller-host or set it in the web UI first.")
    return Controller(host)


def _serve(a) -> int:
    import uvicorn
    from .api import create_app
    logging.basicConfig(level=getattr(logging, a.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data_dir = a.data_dir or str(default_data_dir())
    app = create_app(data_dir=data_dir, controller_host=a.controller_host, simulate=a.simulate, password=a.password)
    if not a.password and a.host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger("tm1814").warning(
            "No password set and listening on %s: anyone on your network can control the lights.", a.host)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(x in argv for x in ("--install", "--uninstall", "--service-status")):
        from .installer import main as installer_main
        return installer_main(argv)
    for i, tok in enumerate(argv):  # settings file must be loaded before the parser reads its defaults
        if tok == "--env-file" and i + 1 < len(argv):
            load_env_file(argv[i + 1])
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv = ["serve"] + argv
    a = _parser().parse_args(argv)
    if a.command == "serve":
        return _serve(a)
    if a.command == "discover":
        from .controller import discover
        found = discover()
        for d in found:
            print(f"{d['ip']:16} {d['mac']}  {d['model']}")
        if not found:
            print("No controllers answered. Check that this computer is on the same network.")
        return 0 if found else 1
    ctrl = _controller(a.controller_host)
    if a.command == "state":
        st = ctrl.query_state()
        print(st.to_dict() if st else "no answer")
        return 0 if st else 1
    if a.command in ("on", "off"):
        ctrl.power(a.command == "on")
    elif a.command == "color":
        ctrl.power(True)
        ctrl.solid(P.parse_hex(a.hex))
    return 0
