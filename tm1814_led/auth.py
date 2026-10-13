"""Login sessions for the web UI.

The web page shows its own login form. A successful login sets an HttpOnly cookie holding an expiring,
HMAC-signed token (nothing is stored on the server, so sessions survive restarts, and changing the password
invalidates every session). The web page is the only interface: there is no other way in. Failed logins are
throttled per client address.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Dict, List, Optional

COOKIE = "tm1814_session"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 8          # failed logins allowed ...
FAILURE_WINDOW = 300.0    # ... per this many seconds, per client address


class Auth:
    def __init__(self, password: Optional[str], secret: str, session_seconds: int = SESSION_SECONDS):
        self.password = password or ""
        self.session_seconds = session_seconds
        # the signing key depends on the password, so changing the password signs everyone out
        self._key = hashlib.sha256((secret + "|" + hashlib.sha256(self.password.encode()).hexdigest()).encode()).digest()
        self._failures: Dict[str, List[float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    # ---- passwords
    def check_password(self, given: str) -> bool:
        return hmac.compare_digest(str(given).encode(), self.password.encode())

    # ---- session tokens
    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def make_token(self, now: Optional[float] = None) -> str:
        expires = int((now if now is not None else time.time()) + self.session_seconds)
        payload = f"v1.{expires}"
        return f"{payload}.{self._sign(payload)}"

    def valid_token(self, token: Optional[str], now: Optional[float] = None) -> bool:
        if not token:
            return False
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            return False
        try:
            expires = int(parts[1])
        except ValueError:
            return False
        if expires < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(parts[2], self._sign(f"{parts[0]}.{parts[1]}"))

    # ---- throttling
    def blocked_for(self, client: str, now: Optional[float] = None) -> int:
        """Seconds until `client` may try again (0 = allowed now)."""
        now = now if now is not None else time.time()
        recent = [t for t in self._failures.get(client, []) if now - t < FAILURE_WINDOW]
        self._failures[client] = recent
        if len(recent) < MAX_FAILURES:
            return 0
        return int(FAILURE_WINDOW - (now - recent[0])) + 1

    def record_failure(self, client: str, now: Optional[float] = None) -> None:
        self._failures.setdefault(client, []).append(now if now is not None else time.time())

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)
