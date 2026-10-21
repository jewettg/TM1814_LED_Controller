"""`tm1814-led --install`: install this program as a background service on the current operating system.

  Linux with systemd  root  -> system service  (/opt/tm1814-led, user "tm1814", /etc/systemd/system)   <- best for a Raspberry Pi
  Linux with systemd  user  -> user service    (~/.local/share/tm1814-led, ~/.config/systemd/user, lingering enabled)
  Linux without systemd     -> @reboot cron entry (user crontab)
  macOS                     -> LaunchAgent     (~/Library/LaunchAgents), starts at login
  Windows                   -> Scheduled Task  (runs at logon, or at startup when run as administrator)

The program is installed into its own virtual environment, so nothing touches the system Python. Use --dry-run to
see exactly what would be done without doing it.
"""
from __future__ import annotations

import argparse
import getpass
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __version__
from .config import DEFAULT_PORT

SERVICE_NAME = "tm1814-led"
LAUNCHD_LABEL = "com.jewettg.tm1814-led"
WINDOWS_TASK = "TM1814 LED Controller"
WATCHDOG_LABEL = "com.jewettg.tm1814-led-watchdog"
WINDOWS_WATCHDOG_TASK = "TM1814 LED Watchdog"
WATCHDOG_MINUTES = 3
SYSTEM_USER = "tm1814"


class InstallError(Exception):
    """The installation cannot continue (message is shown to the user)."""


@dataclass
class Paths:
    os_name: str                 # linux | macos | windows
    mode: str                    # system | user
    venv: Path
    data_dir: Path
    env_file: Path
    service_file: Optional[Path]
    log_dir: Optional[Path]

    @property
    def exe(self) -> Path:
        scripts = "Scripts" if self.os_name == "windows" else "bin"
        return self.venv / scripts / ("tm1814-led.exe" if self.os_name == "windows" else "tm1814-led")

    @property
    def python(self) -> Path:
        scripts = "Scripts" if self.os_name == "windows" else "bin"
        return self.venv / scripts / ("pythonw.exe" if self.os_name == "windows" else "python")


def detect_os() -> str:
    return {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(platform.system(), "unknown")


def has_systemd() -> bool:
    return Path("/run/systemd/system").exists()


def is_admin(os_name: str) -> bool:
    if os_name == "windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def build_paths(os_name: str, mode: str, home: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> Paths:
    home = home or Path.home()
    env = env if env is not None else dict(os.environ)
    if os_name == "linux" and mode == "system":
        return Paths(os_name, mode, Path("/opt/tm1814-led/venv"), Path("/var/lib/tm1814-led"),
                     Path("/etc/tm1814-led/tm1814-led.env"), Path("/etc/systemd/system/tm1814-led.service"), None)
    if os_name == "linux":
        base = Path(env.get("XDG_DATA_HOME", str(home / ".local" / "share"))) / "tm1814-led"
        cfg = Path(env.get("XDG_CONFIG_HOME", str(home / ".config")))
        return Paths(os_name, "user", base / "venv", base / "data", cfg / "tm1814-led" / "tm1814-led.env",
                     cfg / "systemd" / "user" / "tm1814-led.service", base / "logs")
    if os_name == "macos":
        base = home / "Library" / "Application Support" / "tm1814-led"
        return Paths(os_name, "user", base / "venv", base / "data", base / "tm1814-led.env",
                     home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist", home / "Library" / "Logs" / "tm1814-led")
    if os_name == "windows":
        base = Path(env.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "tm1814-led"
        return Paths(os_name, "user", base / "venv", base / "data", base / "tm1814-led.env", None, base / "logs")
    raise InstallError(f"unsupported operating system: {platform.system()}")


# ---------------------------------------------------------------- file contents (pure functions, unit tested)

def render_env_file(data_dir: Path, host: str, port: int, password: Optional[str], controller_host: Optional[str]) -> str:
    lines = [f"TM1814_DATA_DIR={data_dir}", f"TM1814_HOST={host}", f"TM1814_PORT={port}"]
    if password:
        lines.append(f"TM1814_PASSWORD={password}")
    if controller_host:
        lines.append(f"TM1814_CONTROLLER_HOST={controller_host}")
    return "\n".join(lines) + "\n"


def render_systemd_unit(paths: Paths) -> str:
    system = paths.mode == "system"
    lines = [
        "[Unit]",
        "Description=TM1814 LED controller (web UI, effects and schedules)",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
    ]
    if system:
        lines += [f"User={SYSTEM_USER}", f"Group={SYSTEM_USER}"]
    lines += [
        f"EnvironmentFile={paths.env_file}",
        f"ExecStart={paths.exe} serve",
        "Restart=always",
        "RestartSec=5",
    ]
    if system:
        lines += ["NoNewPrivileges=true", "PrivateTmp=true", "ProtectSystem=strict", "ProtectHome=true",
                  f"ReadWritePaths={paths.data_dir}"]
    lines += ["", "[Install]", f"WantedBy={'multi-user.target' if system else 'default.target'}", ""]
    return "\n".join(lines)


def render_launchd_plist(paths: Paths) -> str:
    log_dir = paths.log_dir or Path("/tmp")
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key><string>{LAUNCHD_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{paths.exe}</string>
                <string>serve</string>
                <string>--env-file</string>
                <string>{paths.env_file}</string>
            </array>
            <key>RunAtLoad</key><true/>
            <key>KeepAlive</key><true/>
            <key>StandardOutPath</key><string>{log_dir}/tm1814-led.log</string>
            <key>StandardErrorPath</key><string>{log_dir}/tm1814-led.err.log</string>
        </dict>
        </plist>
        """)


def windows_task_command(paths: Paths, admin: bool) -> List[str]:
    run = f'"{paths.python}" -m tm1814_led serve --env-file "{paths.env_file}"'
    cmd = ["schtasks", "/Create", "/TN", WINDOWS_TASK, "/TR", run, "/F"]
    cmd += ["/SC", "ONSTART", "/RU", "SYSTEM"] if admin else ["/SC", "ONLOGON", "/RL", "LIMITED"]
    return cmd


def watchdog_files(paths: Paths) -> Dict[str, Path]:
    """Where the watchdog's own service files live (next to the main service's)."""
    base = paths.service_file
    if base is None:
        return {}
    if paths.os_name == "macos":
        return {"plist": base.with_name(f"{WATCHDOG_LABEL}.plist")}
    return {"service": base.with_name(f"{SERVICE_NAME}-watchdog.service"), "timer": base.with_name(f"{SERVICE_NAME}-watchdog.timer")}


def render_watchdog_plist(paths: Paths) -> str:
    log_dir = paths.log_dir or Path("/tmp")
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key><string>{WATCHDOG_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{paths.exe}</string>
                <string>watchdog</string>
                <string>--env-file</string>
                <string>{paths.env_file}</string>
            </array>
            <key>StartInterval</key><integer>{WATCHDOG_MINUTES * 60}</integer>
            <key>RunAtLoad</key><true/>
            <key>StandardOutPath</key><string>{log_dir}/watchdog.log</string>
            <key>StandardErrorPath</key><string>{log_dir}/watchdog.err.log</string>
        </dict>
        </plist>
        """)


def render_watchdog_units(paths: Paths) -> Tuple[str, str]:
    """(service, timer) systemd units. In a system install the check runs as root so it may restart the service."""
    system = paths.mode == "system"
    service = "\n".join(["[Unit]", "Description=TM1814 LED controller watchdog (independent health check)", "",
                         "[Service]", "Type=oneshot"] + (["User=root"] if system else []) +
                        [f"EnvironmentFile={paths.env_file}", f"ExecStart={paths.exe} watchdog", ""])
    timer = "\n".join(["[Unit]", "Description=Run the TM1814 LED controller watchdog every few minutes", "",
                       "[Timer]", "OnBootSec=3min", f"OnUnitActiveSec={WATCHDOG_MINUTES}min", "AccuracySec=30s", "",
                       "[Install]", "WantedBy=timers.target", ""])
    return service, timer


def windows_watchdog_command(paths: Paths, admin: bool) -> List[str]:
    run = f'"{paths.python}" -m tm1814_led watchdog --env-file "{paths.env_file}"'
    cmd = ["schtasks", "/Create", "/TN", WINDOWS_WATCHDOG_TASK, "/TR", run, "/SC", "MINUTE", "/MO", str(WATCHDOG_MINUTES), "/F"]
    return cmd + (["/RU", "SYSTEM"] if admin else [])


def watchdog_cron_line(paths: Paths) -> str:
    return (f"*/{WATCHDOG_MINUTES} * * * * {shlex.quote(str(paths.exe))} watchdog --env-file {shlex.quote(str(paths.env_file))} "
            f">> {shlex.quote(str(paths.data_dir))}/watchdog.log 2>&1  # {SERVICE_NAME}-watchdog")


def cron_line(paths: Paths) -> str:
    return f"@reboot {shlex.quote(str(paths.exe))} serve --env-file {shlex.quote(str(paths.env_file))} " \
           f">> {shlex.quote(str(paths.data_dir))}/service.log 2>&1  # {SERVICE_NAME}"


# ---------------------------------------------------------------- the installer

def parse_ipv4_addresses(text: str) -> List[str]:
    """Pull IPv4 addresses out of `ip addr`, `ifconfig` or `ipconfig` output (loopback and link-local skipped)."""
    found: List[str] = []
    for m in re.finditer(r"(?:inet\s+(?:addr:)?|IPv4[^\n:]*:\s*)(\d{1,3}(?:\.\d{1,3}){3})", text):
        ip = m.group(1)
        if not ip.startswith(("127.", "169.254.", "0.")) and ip not in found:
            found.append(ip)
    return found


def rank_addresses(ips: List[str], toward: Optional[str] = None) -> List[str]:
    """Best address first: same /24 as `toward` (the controller), then home-style ranges, then everything else.

    The address a computer would use to reach the internet is often a VPN's, which other devices on the home
    network cannot reach, so we never trust a single guess.
    """
    def key(ip: str):
        parts = ip.split(".")
        same_net = bool(toward) and ip.split(".")[:3] == str(toward).split(".")[:3]
        private = ip.startswith("192.168.") or ip.startswith("10.") or (
            parts[0] == "172" and 16 <= int(parts[1]) <= 31)
        return (0 if same_net else 1, 0 if ip.startswith("192.168.") else 1 if private else 2, ip)
    return sorted(dict.fromkeys(ips), key=key)


def local_ips(toward: Optional[str] = None) -> List[str]:
    """Every IPv4 address of this computer, best first (see rank_addresses)."""
    ips: List[str] = []
    if toward:  # the interface that routes to the controller is on the right network even when a VPN is up
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((toward, 1))
                ips.append(s.getsockname()[0])
        except OSError:
            pass
    for cmd in (["ip", "-o", "-4", "addr", "show"], ["ifconfig"], ["ipconfig"]):
        if shutil.which(cmd[0]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            ips += parse_ipv4_addresses(out)
            break
    if not ips:
        try:
            ips = [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
        except OSError:
            pass
    return rank_addresses([i for i in ips if not i.startswith("127.")], toward) or ["127.0.0.1"]


def access_urls(port: int, toward: Optional[str] = None) -> List[str]:
    return [f"http://{ip}:{port}/" for ip in local_ips(toward)]


class Installer:
    def __init__(self, args: argparse.Namespace, os_name: Optional[str] = None, runner=None):
        self.a = args
        self.os = os_name or detect_os()
        self.dry = bool(args.dry_run)
        self.runner = runner or subprocess.run
        self.admin = is_admin(self.os)
        self.mode = self._choose_mode()
        self.paths = build_paths(self.os, self.mode)
        self.log_lines: List[str] = []

    # ---- helpers
    def say(self, msg: str = "") -> None:
        print(msg)

    def _choose_mode(self) -> str:
        if self.os != "linux":
            if self.a.system:
                raise InstallError("--system is only available on Linux; this operating system uses a per-user service.")
            return "user"
        if self.a.system and self.a.user:
            raise InstallError("choose either --system or --user, not both")
        if self.a.system:
            if not self.admin and not self.dry:
                raise InstallError("--system needs root: run `sudo python3 -m tm1814_led --install --system`")
            return "system"
        if self.a.user:
            return "user"
        return "system" if self.admin else "user"

    def run(self, cmd: List[str], check: bool = True, quiet: bool = False) -> int:
        shown = " ".join(shlex.quote(c) for c in cmd)
        if self.dry:
            self.say(f"  [dry run] $ {shown}")
            return 0
        if not quiet:
            self.say(f"  $ {shown}")
        proc = self.runner(cmd, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise InstallError(f"command failed ({proc.returncode}): {shown}\n{(proc.stderr or proc.stdout or '').strip()}")
        return proc.returncode

    def write(self, path: Path, content: str, mode: int = 0o644, owner: Optional[str] = None) -> None:
        if self.dry:
            self.say(f"  [dry run] write {path} (mode {oct(mode)})")
            for line in content.rstrip().splitlines():
                self.say(f"      | {self._mask(line)}")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if self.os != "windows":
            os.chmod(path, mode)
        if owner and self.os == "linux":
            self.run(["chown", f"{owner}:{owner}", str(path)], check=False, quiet=True)
        self.say(f"  wrote {path}")

    @staticmethod
    def _mask(line: str) -> str:
        return "TM1814_PASSWORD=********" if line.startswith("TM1814_PASSWORD=") else line

    # ---- install
    def source_dir(self) -> Path:
        root = Path(__file__).resolve().parent.parent
        if not (root / "pyproject.toml").exists():
            raise InstallError("--install must be run from a checkout of the repository "
                               "(pyproject.toml was not found next to the tm1814_led package).")
        return root

    def password(self) -> str:
        """A password is always set: given on the command line, kept from a previous install, typed, or generated."""
        if self.a.password:
            return self.a.password
        if self.a.generate_password:
            self.generated = secrets.token_urlsafe(12)
            return self.generated
        existing = self._existing_password()
        if existing:
            self.say("  keeping the password already set in the settings file (use --generate-password for a new one)")
            return existing
        if sys.stdin.isatty() and not self.dry:
            while True:
                typed = getpass.getpass("Choose a password for the web page: ")
                if typed and getpass.getpass("Type it again: ") == typed:
                    return typed
                self.say("  The passwords were empty or did not match; try again.")
        self.generated = secrets.token_urlsafe(12)
        return self.generated

    generated: Optional[str] = None

    def _existing_password(self) -> Optional[str]:
        try:
            for line in self.paths.env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("TM1814_PASSWORD="):
                    return line.split("=", 1)[1].strip() or None
        except OSError:
            pass
        return None

    generated: Optional[str] = None

    def install(self) -> int:
        if sys.version_info < (3, 9):
            raise InstallError("Python 3.9 or newer is required")
        if self.os == "unknown":
            raise InstallError(f"unsupported operating system: {platform.system()}")
        if self.os == "linux" and not has_systemd() and not self.a.dry_run and shutil.which("crontab") is None:
            raise InstallError("this Linux has neither systemd nor cron; run `tm1814-led serve` yourself")
        p = self.paths
        source = self.source_dir()
        password = self.password()
        self.say(f"Installing tm1814-led {__version__} for {self.os} ({p.mode} service)"
                 + ("  [DRY RUN - nothing will be changed]" if self.dry else ""))
        self.say(f"  program : {p.venv}\n  data    : {p.data_dir}\n  settings: {p.env_file}")

        if self.os == "linux" and p.mode == "system":
            if self.run(["id", "-u", SYSTEM_USER], check=False, quiet=True) != 0:
                self.run(["useradd", "--system", "--home-dir", str(p.data_dir), "--shell", "/usr/sbin/nologin", SYSTEM_USER])
            if not self.dry:
                p.data_dir.mkdir(parents=True, exist_ok=True)
                self.run(["chown", "-R", f"{SYSTEM_USER}:{SYSTEM_USER}", str(p.data_dir)], quiet=True)
        elif not self.dry:
            p.data_dir.mkdir(parents=True, exist_ok=True)
            if p.log_dir:
                p.log_dir.mkdir(parents=True, exist_ok=True)

        self.say("Creating the Python environment and installing the program:")
        if not self.dry:
            p.venv.parent.mkdir(parents=True, exist_ok=True)
        self.run([sys.executable, "-m", "venv", str(p.venv)])
        py = str(p.venv / ("Scripts" if self.os == "windows" else "bin") / ("python.exe" if self.os == "windows" else "python"))
        self.run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=False)
        self.run([py, "-m", "pip", "install", "--quiet", "--upgrade", str(source)])
        # pip may skip a same-version reinstall of a local folder; force the program code itself to refresh
        self.run([py, "-m", "pip", "install", "--quiet", "--force-reinstall", "--no-deps", str(source)])

        self._link_cli()
        self.say("Writing settings and the service definition:")
        env_text = render_env_file(p.data_dir, self.a.host, self.a.port, password, self.a.controller_host)
        self.write(p.env_file, env_text, 0o640 if p.mode == "system" else 0o600,
                   owner=None)
        if self.os == "linux" and p.mode == "system" and not self.dry:
            self.run(["chgrp", SYSTEM_USER, str(p.env_file)], quiet=True)

        if self.os == "linux":
            self._install_linux()
        elif self.os == "macos":
            self._install_macos()
        else:
            self._install_windows()
        self._finish(password)
        return 0

    def cli_link_path(self) -> Optional[Path]:
        """Where the `tm1814-led` command is linked so it works from any terminal (not on Windows)."""
        if self.os == "windows":
            return None
        if self.os == "linux" and self.paths.mode == "system":
            return Path("/usr/local/bin/tm1814-led")
        return Path.home() / ".local" / "bin" / "tm1814-led"

    def _link_cli(self) -> None:
        link = self.cli_link_path()
        if link is None:
            return
        if self.dry:
            self.say(f"  [dry run] link {link} -> {self.paths.exe}")
            return
        try:
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(self.paths.exe)
        except OSError as exc:
            self.say(f"  note: could not create {link} ({exc}); run the program as {self.paths.exe}")
            return
        self.say(f"  linked {link}")
        if str(link.parent) not in os.environ.get("PATH", "").split(os.pathsep):
            self.say(f"  note: {link.parent} is not on your PATH. Add it (for zsh: echo 'export PATH=\"{link.parent}:$PATH\"' "
                     ">> ~/.zshrc) or run `python3 -m tm1814_led`.")

    def _install_linux(self) -> None:
        p = self.paths
        if not has_systemd() and not self.dry:
            self.say("No systemd found; using a cron @reboot entry.")
            self._cron(add=True)
            return
        self.write(p.service_file, render_systemd_unit(p))  # type: ignore[arg-type]
        ctl = ["systemctl"] if p.mode == "system" else ["systemctl", "--user"]
        self.run(ctl + ["daemon-reload"])
        self.run(ctl + ["enable", "--now", f"{SERVICE_NAME}.service"])
        wd = watchdog_files(p)
        unit, timer = render_watchdog_units(p)
        self.write(wd["service"], unit)
        self.write(wd["timer"], timer)
        self.run(ctl + ["daemon-reload"])
        self.run(ctl + ["enable", "--now", f"{SERVICE_NAME}-watchdog.timer"])
        if p.mode == "user":
            user = getpass.getuser()
            if self.run(["loginctl", "enable-linger", user], check=False) != 0:
                self.say("  note: could not enable lingering, so the service will only run while you are logged in.\n"
                         f"        Run `sudo loginctl enable-linger {user}` to keep it running after logout/reboot.")

    def _cron(self, add: bool) -> None:
        current = ""
        if not self.dry:
            proc = self.runner(["crontab", "-l"], capture_output=True, text=True)
            current = proc.stdout if proc.returncode == 0 else ""
        lines = [l for l in current.splitlines() if f"# {SERVICE_NAME}" not in l]
        if add:
            lines += [cron_line(self.paths), watchdog_cron_line(self.paths)]
        if self.dry:
            self.say(f"  [dry run] crontab += {cron_line(self.paths)}\n  [dry run] crontab += {watchdog_cron_line(self.paths)}")
            return
        proc = self.runner(["crontab", "-"], input="\n".join(lines) + "\n", text=True, capture_output=True)
        if proc.returncode != 0:
            raise InstallError(f"could not update crontab: {proc.stderr}")
        if add:  # start it now as well (cron only starts it at the next reboot)
            subprocess.Popen([str(self.paths.exe), "serve", "--env-file", str(self.paths.env_file)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def _install_macos(self) -> None:
        p = self.paths
        self.write(p.service_file, render_launchd_plist(p))  # type: ignore[arg-type]
        uid = os.getuid() if hasattr(os, "getuid") else 501
        domain = f"gui/{uid}"
        self.run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"], check=False, quiet=True)
        # bootout is asynchronous: right after it the old service may still be registered, so retry bootstrap
        for attempt in range(6):
            if self.run(["launchctl", "bootstrap", domain, str(p.service_file)], check=False) == 0:
                break
            time.sleep(1.0)
        else:
            raise InstallError("launchctl could not load the service; try `launchctl bootout "
                               f"{domain}/{LAUNCHD_LABEL}` and run the installer again")
        self.run(["launchctl", "kickstart", "-k", f"{domain}/{LAUNCHD_LABEL}"], check=False)
        wd = watchdog_files(p)["plist"]                      # the independent watchdog: a second launchd job
        self.write(wd, render_watchdog_plist(p))
        self.run(["launchctl", "bootout", f"{domain}/{WATCHDOG_LABEL}"], check=False, quiet=True)
        for attempt in range(6):
            if self.run(["launchctl", "bootstrap", domain, str(wd)], check=False) == 0:
                break
            time.sleep(1.0)
        else:
            self.say("  note: the watchdog job could not be loaded; the service itself is running")

    def _install_windows(self) -> None:
        self.run(windows_task_command(self.paths, self.admin))
        self.run(["schtasks", "/Run", "/TN", WINDOWS_TASK], check=False)
        self.run(windows_watchdog_command(self.paths, self.admin))

    def _finish(self, password: Optional[str]) -> None:
        self.say("")
        self.say("Done." if not self.dry else "Dry run complete; nothing was changed.")
        self._print_urls()
        if self.generated and self.dry:
            self.say("  A random password would be generated and shown once (any user name works with it).")
        elif self.generated:
            self.say(f"  Password (shown once, any user name): {self.generated}")
        if self.os == "windows":
            self.say("  You may need to allow the program through Windows Firewall to reach it from other devices.")

    def _print_urls(self) -> None:
        urls = access_urls(self.a.port, self._controller_hint())
        self.say("  Open the page in a browser (on this computer: http://localhost:%d/):" % self.a.port)
        for i, u in enumerate(urls):
            self.say(f"    {u}" + ("   <- most likely your home network" if i == 0 and len(urls) > 1 else ""))
        if len(urls) > 1:
            self.say("  Phones and other devices must use the address on your home network. If one belongs to a VPN, "
                     "ignore it.")
        self.say("  Then open Settings to enter or find your controller.")

    def _controller_hint(self) -> Optional[str]:
        """The controller's address (from the command line or the settings file), used to pick the right network."""
        if getattr(self.a, "controller_host", None):
            return self.a.controller_host
        try:
            for line in self.paths.env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("TM1814_CONTROLLER_HOST="):
                    return line.split("=", 1)[1].strip() or None
        except OSError:
            pass
        return None

    # ---- uninstall / status
    def uninstall(self) -> int:
        p = self.paths
        if not (self.a.yes or self.dry):
            answer = input(f"Remove the {SERVICE_NAME} service ({p.mode})"
                           + (" AND all data and settings" if self.a.purge else " (data is kept)") + "? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                self.say("Cancelled.")
                return 1
        self.say("Removing the service:")
        if self.os == "linux":
            ctl = ["systemctl"] if p.mode == "system" else ["systemctl", "--user"]
            if has_systemd():
                self.run(ctl + ["disable", "--now", f"{SERVICE_NAME}-watchdog.timer"], check=False)
                self.run(ctl + ["disable", "--now", f"{SERVICE_NAME}.service"], check=False)
                for f in watchdog_files(p).values():
                    self._remove(f)
                self._remove(p.service_file)
                self.run(ctl + ["daemon-reload"], check=False)
            else:
                self._cron(add=False)
        elif self.os == "macos":
            uid = os.getuid() if hasattr(os, "getuid") else 501
            self.run(["launchctl", "bootout", f"gui/{uid}/{WATCHDOG_LABEL}"], check=False)
            self.run(["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"], check=False)
            for f in watchdog_files(p).values():
                self._remove(f)
            self._remove(p.service_file)
        else:
            for task in (WINDOWS_WATCHDOG_TASK, WINDOWS_TASK):
                self.run(["schtasks", "/End", "/TN", task], check=False, quiet=True)
                self.run(["schtasks", "/Delete", "/TN", task, "/F"], check=False)
        self._remove(p.env_file)
        link = self.cli_link_path()
        if link is not None and (link.is_symlink() and os.readlink(link) == str(p.exe) or self.dry):
            self._remove(link)
        if self.a.purge:
            self._remove(p.venv, tree=True)
            self._remove(p.data_dir, tree=True)
            if self.os == "linux" and p.mode == "system":
                self.run(["userdel", SYSTEM_USER], check=False)
        else:
            self.say(f"  kept your data in {p.data_dir} (use --purge to delete it)")
        self.say("Done.")
        return 0

    def _remove(self, path: Optional[Path], tree: bool = False) -> None:
        if path is None:
            return
        if self.dry:
            self.say(f"  [dry run] remove {path}")
            return
        if path.is_dir() and tree and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists() or path.is_symlink():        # a link whose target is already gone still counts
            path.unlink()
        else:
            return
        self.say(f"  removed {path}")

    def service_status(self) -> int:
        self._print_urls()
        p = self.paths
        if self.os == "linux" and has_systemd():
            ctl = ["systemctl"] if p.mode == "system" else ["systemctl", "--user"]
            return self.run(ctl + ["status", "--no-pager", f"{SERVICE_NAME}.service"], check=False)
        if self.os == "macos":
            uid = os.getuid() if hasattr(os, "getuid") else 501
            return self.run(["launchctl", "print", f"gui/{uid}/{LAUNCHD_LABEL}"], check=False)
        if self.os == "windows":
            return self.run(["schtasks", "/Query", "/TN", WINDOWS_TASK, "/V", "/FO", "LIST"], check=False)
        self.say("No service manager found; check with: ps aux | grep tm1814")
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tm1814-led", description="Install tm1814-led as a background service.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true", help="install and start the service")
    g.add_argument("--uninstall", action="store_true", help="stop and remove the service")
    g.add_argument("--service-status", action="store_true", help="show whether the service is running")
    p.add_argument("--system", action="store_true", help="Linux: system-wide service (needs root; default when root)")
    p.add_argument("--user", action="store_true", help="Linux: per-user service (default when not root)")
    p.add_argument("--host", default="0.0.0.0", help="address the web page listens on (default: all)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"web page port (default {DEFAULT_PORT})")
    p.add_argument("--controller-host", default=None, help="controller IP (can also be set in the web page)")
    p.add_argument("--password", default=None, help="password for the web page (default: keep the current one, else ask)")
    p.add_argument("--generate-password", action="store_true", help="create a new random password and show it once")
    p.add_argument("--dry-run", action="store_true", help="show what would be done; change nothing")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation (uninstall)")
    p.add_argument("--purge", action="store_true", help="uninstall: also delete the data and the program folder")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inst = Installer(args)
        if args.install:
            return inst.install()
        if args.uninstall:
            return inst.uninstall()
        return inst.service_status()
    except InstallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
