"""Client for a running tm1814-led service, used by the command line. Standard library only.

Sign-in works exactly like the web page: POST the password to /api/login and keep the session cookie. The password
is never stored; only the signed session token is cached, in a private file that belongs to the current terminal
session (so closing the terminal, or `tm1814-led logout`, ends it) and that also expires after a few hours.
"""
from __future__ import annotations

import getpass
import http.cookiejar
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from .auth import COOKIE
from .config import DEFAULT_PORT

CACHE_HOURS = 12


class ServiceError(Exception):
    """The service could not be reached, refused the password, or rejected the request."""


class LoginRequired(ServiceError):
    """Not signed in (or the cached session is no longer valid)."""


def installed_port() -> str:
    """The port of an installed service on this machine (not a secret); falls back to the default."""
    from .installer import build_paths, detect_os
    os_name = detect_os()
    for mode in ("user", "system") if os_name == "linux" else ("user",):
        try:
            text = Path(build_paths(os_name, mode).env_file).read_text(encoding="utf-8")
        except (OSError, Exception):
            continue
        for line in text.splitlines():
            if line.startswith("TM1814_PORT="):
                return line.split("=", 1)[1].strip()
    return str(DEFAULT_PORT)


def service_url(url: Optional[str]) -> str:
    return (url or os.environ.get("TM1814_URL") or f"http://127.0.0.1:{os.environ.get('TM1814_PORT') or installed_port()}").rstrip("/")


# ---------------------------------------------------------------- the per-terminal-session cache

class SessionCache:
    def __init__(self, directory: Optional[Path] = None):
        self.dir = directory or self._default_dir()

    @staticmethod
    def _default_dir() -> Path:
        system = platform.system()
        if system == "Darwin":
            return Path.home() / "Library" / "Caches" / "tm1814-led"
        if system == "Windows":
            return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "tm1814-led" / "cache"
        return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tm1814-led"

    @staticmethod
    def session_id() -> str:
        """Identifies this terminal session: the POSIX session id (one per terminal window), else the parent process."""
        try:
            return str(os.getsid(0))
        except (AttributeError, OSError):
            return str(os.getppid())

    @property
    def path(self) -> Path:
        return self.dir / f"session-{self.session_id()}.json"

    def load(self, url: str) -> Optional[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("url") != url or time.time() - float(data.get("saved", 0)) > CACHE_HOURS * 3600:
            return None
        return data.get("token") or None

    def save(self, url: str, token: str) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dir, 0o700) if os.name == "posix" else None
            for old in self.dir.glob("session-*.json"):          # tidy up sessions of closed terminals
                if time.time() - old.stat().st_mtime > CACHE_HOURS * 3600:
                    old.unlink(missing_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"url": url, "token": token, "saved": time.time()}), encoding="utf-8")
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            tmp.replace(self.path)
        except OSError:
            pass  # not being able to cache only means asking again next time

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- the client

class ServiceClient:
    def __init__(self, url: str, token: Optional[str] = None, timeout: float = 20.0):
        self.url, self.token, self.timeout = url, token, timeout
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))

    def _raw(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if self.token:
            headers["Cookie"] = f"{COOKIE}={self.token}"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
                return json.loads(text) if text else None
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("detail", "")
            except Exception:
                pass
            if exc.code == 401 and path != "/api/login":
                raise LoginRequired(detail or "sign-in required") from exc
            raise ServiceError(detail or f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ServiceError(f"cannot reach the service at {self.url}: {getattr(exc, 'reason', exc)}") from exc

    def is_signed_in(self) -> bool:
        try:
            return bool(self._raw("GET", "/api/auth").get("authenticated"))
        except LoginRequired:
            return False

    def login(self, password: str) -> str:
        """Sign in; returns the session token (also used for later requests)."""
        self.token = None
        self._raw("POST", "/api/login", {"password": password})
        for cookie in self._jar:
            if cookie.name == COOKIE:
                self.token = cookie.value
        if not self.token:
            raise ServiceError("the service did not start a session")
        return self.token

    def logout(self) -> None:
        try:
            self._raw("POST", "/api/logout")
        except ServiceError:
            pass
        self.token = None

    def get(self, path: str) -> Any:
        return self._raw("GET", path)

    def post(self, path: str, body: Any = None) -> Any:
        return self._raw("POST", path, {} if body is None else body)


def prompt_login(client: ServiceClient, cache: SessionCache, attempts: int = 3) -> None:
    """Ask for the password (no echo) until it is accepted; cache the session. TM1814_PASSWORD may supply it."""
    env_pw = os.environ.get("TM1814_PASSWORD")
    for attempt in range(attempts):
        pw = env_pw if (env_pw and attempt == 0) else None
        if pw is None:
            if not sys.stdin.isatty():
                raise ServiceError("a password is needed but there is no terminal to ask on "
                                   "(set TM1814_PASSWORD for non-interactive use)")
            pw = getpass.getpass(f"Password for the LED controller at {client.url}: ")
        try:
            cache.save(client.url, client.login(pw))
            return
        except ServiceError as exc:
            if "cannot reach" in str(exc):
                raise
            print(f"  {exc}", file=sys.stderr)
    raise ServiceError("too many wrong passwords")


def connect(url: Optional[str], cache: Optional[SessionCache] = None) -> ServiceClient:
    """A signed-in client: reuse this terminal's cached session, or ask for the password."""
    cache = cache or SessionCache()
    u = service_url(url)
    client = ServiceClient(u, token=cache.load(u))
    if client.token and client.is_signed_in():
        return client
    client.token = None
    if client.is_signed_in():            # the service has no password (simulation mode)
        return client
    prompt_login(client, cache)
    return client
