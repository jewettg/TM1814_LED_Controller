"""Network client for the controller.

`Controller` owns one persistent TCP connection (the firmware dislikes many short connections), paces every
command (flooding stalls the firmware), reconnects on errors, and can stream frames efficiently by sending only
the pixels that changed. `SimulatedController` has the same interface without any network, for development,
demos and tests.
"""
from __future__ import annotations

import logging
import select
import socket
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import protocol as P
from .protocol import RGB, Pixel, State

log = logging.getLogger("tm1814.controller")


class ControllerError(Exception):
    """The controller could not be reached or a send failed."""


def quantize(rgb: Pixel, step: int) -> Pixel:
    """Round each channel to a multiple of `step` (0 stays 0, nonzero never rounds down to 0).

    A white-LED pixel (0, 0, 0, level) is never rounded: there are only a handful of white levels in practice."""
    if step <= 1 or len(rgb) == 4:
        return rgb
    out = []
    for c in rgb:
        if c <= 0:
            out.append(0)
        else:
            q = int(round(c / step)) * step
            out.append(min(255, q if q > 0 else step))
    return (out[0], out[1], out[2])


def group_by_color(items: Dict[int, Pixel], max_groups: int) -> Dict[Pixel, List[int]]:
    """Group pixel indices by color, coarsening the quantization until at most `max_groups` colors remain.

    Every distinct color costs one paint command, and commands are the scarce resource, so busy frames get
    slightly coarser colors instead of slowing the animation down.
    """
    groups: Dict[Pixel, List[int]] = {}
    for step in (1, 4, 8, 16, 32, 48, 64, 96):
        groups = {}
        for idx, rgb in items.items():
            groups.setdefault(quantize(rgb, step), []).append(idx)
        if len(groups) <= max_groups:
            break
    return groups


class Controller:
    simulated = False

    def __init__(self, host: str = "", port: int = P.TCP_PORT, *, min_gap: float = 0.03, timeout: float = 4.0,
                 led_count: int = 0, max_groups: int = 10, refresh_interval: float = 20.0):
        self.host = host
        self.port = port
        self.min_gap = min_gap
        self.timeout = timeout
        self.configured_led_count = led_count      # 0 = use what the controller reports
        self.detected_led_count = 0
        self.max_groups = max_groups
        self.refresh_interval = refresh_interval
        self.brightness = 100                      # master brightness 0-100, applied to everything we send
        self.last_error = ""
        self.last_state: Optional[State] = None
        self.commands_sent = 0
        self.frames_sent = 0
        self.last_ok = 0.0                         # time.time() of the last successful send or state reply
        self._sock: Optional[socket.socket] = None
        self._lock = threading.RLock()
        self._counter = 0
        self._last_send = 0.0
        self._since_drain = 0
        self._shown: Optional[List[Pixel]] = None  # what the strip currently shows (for diffing)
        self._in_scribble = False
        self._last_full = 0.0
        self._rate_window: List[float] = []

    def set_address(self, host: str, port: Optional[int] = None) -> None:
        """Point at a new address (the controller moved): drops the old connection and the cached picture."""
        with self._lock:
            self.host = host
            if port:
                self.port = port
            self.close()

    # ------------------------------------------------------------ connection
    @property
    def led_count(self) -> int:
        n = self.configured_led_count or self.detected_led_count or 250
        return max(1, min(P.MAX_SCRIBBLE_LEDS, n))

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def _connect(self) -> None:
        if not self.host:
            raise ControllerError("no controller address configured")
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            self.last_error = f"connect failed: {exc}"
            raise ControllerError(self.last_error) from exc
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._in_scribble = False
        self._shown = None
        self.last_error = ""
        log.info("connected to %s:%s", self.host, self.port)

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = None
            self._in_scribble = False
            self._shown = None

    def _ensure(self) -> None:
        if self._sock is None:
            self._connect()

    # ------------------------------------------------------------ low level send/receive
    def _pace(self) -> None:
        wait = self.min_gap - (time.monotonic() - self._last_send)
        if wait > 0:
            time.sleep(wait)

    def _transmit(self, data: bytes) -> None:
        assert self._sock is not None
        self._sock.settimeout(self.timeout)
        self._sock.sendall(data)

    def _drain(self) -> bytes:
        """Read and discard anything the controller pushed (state updates), so buffers never fill."""
        out = b""
        if self._sock is None:
            return out
        try:
            while select.select([self._sock], [], [], 0)[0]:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise OSError("connection closed by controller")
                out += chunk
        except OSError as exc:
            self.close()
            self.last_error = f"connection lost: {exc}"
            raise ControllerError(self.last_error) from exc
        return out

    def send_plain(self, data: bytes) -> None:
        with self._lock:
            self._send(data)

    def send_inner(self, inner: bytes) -> None:
        if P.is_forbidden(inner):
            raise ControllerError("refusing to send a configuration-changing command")
        with self._lock:
            self._counter = (self._counter + 1) & 0xFF
            self._send(P.wrap(inner, self._counter))

    def _check_alive(self) -> None:
        """The controller closes idle connections. If it did, drop ours now so the send below starts on a fresh one."""
        if self._sock is None:
            return
        try:
            while select.select([self._sock], [], [], 0)[0]:
                chunk = self._sock.recv(4096)
                if not chunk:                       # the controller closed the connection
                    log.info("the controller closed the idle connection; reconnecting")
                    self.close()
                    return
        except OSError:
            self.close()

    def _send(self, data: bytes) -> None:
        for attempt in (1, 2):
            self._ensure()
            self._check_alive()
            if self._sock is None:                  # closed by the check: connect again
                self._ensure()
            self._pace()
            try:
                self._transmit(data)
                break
            except OSError as exc:
                self.close()
                if attempt == 2:
                    self.last_error = f"send failed: {exc}"
                    raise ControllerError(self.last_error) from exc
                log.info("send failed (%s); reconnecting and trying once more", exc)
        now = time.monotonic()
        self._last_send = now
        self.last_ok = time.time()
        self.commands_sent += 1
        self._rate_window = [t for t in self._rate_window if now - t < 5.0] + [now]
        self._since_drain += 1
        if self._since_drain >= 20:
            self._since_drain = 0
            self._drain()

    def invalidate(self) -> None:
        """Forget what the strip shows so the next frame is painted in full (e.g. after a brightness change)."""
        with self._lock:
            self._shown = None

    def commands_per_second(self) -> float:
        now = time.monotonic()
        recent = [t for t in self._rate_window if now - t < 5.0]
        return round(len(recent) / 5.0, 1)

    # ------------------------------------------------------------ queries
    def query_state(self, timeout: float = 2.5) -> Optional[State]:
        """Ask for the 27-byte state frame. Returns None if the controller does not answer in time."""
        with self._lock:
            self._ensure()
            self._drain()
            self._send(P.STATE_QUERY)
            deadline = time.monotonic() + timeout
            buf = b""
            while time.monotonic() < deadline:
                if self._sock is None:
                    break
                ready = select.select([self._sock], [], [], 0.2)[0]
                if not ready:
                    continue
                try:
                    chunk = self._sock.recv(4096)
                except OSError as exc:
                    self.close()
                    raise ControllerError(f"connection lost: {exc}") from exc
                if not chunk:
                    self.close()
                    raise ControllerError("connection closed by controller")
                buf += chunk
                state = P.parse_state(buf)
                if state:
                    self.last_state = state
                    self.last_ok = time.time()
                    self.detected_led_count = state.led_count
                    return state
            return None

    # ------------------------------------------------------------ high level
    def _scale(self, px: Pixel) -> Pixel:
        return px if self.brightness >= 100 else P.scale_pixel(px, self.brightness)

    def power(self, on: bool) -> None:
        with self._lock:
            self._send(P.power_command(on))
            if not on:
                self._shown = None
                self._in_scribble = False

    def solid(self, rgb: RGB, white: Optional[int] = None) -> None:
        with self._lock:
            self._shown = None
            self._in_scribble = False
            if white is not None:
                self.send_inner(P.solid_message(white=int(white * self.brightness / 100)))
            else:
                self.send_inner(P.solid_message(self._scale(rgb)))

    def segments(self, colors: Sequence[RGB]) -> None:
        with self._lock:
            self._shown = None
            self._in_scribble = False
            self.send_inner(P.segments_message([self._scale(c) for c in colors]))

    def builtin(self, pattern: int, colors: Sequence[RGB], speed: int = 50, density: int = 50,
                direction: int = P.DIRECTION_LEFT_TO_RIGHT, option: int = 0) -> None:
        with self._lock:
            self._shown = None
            self._in_scribble = False
            self.send_inner(P.builtin_effect_message(pattern, [self._scale(c) for c in colors],
                                                     speed, density, direction, option))

    # ---- streamed frames
    def start_frames(self) -> None:
        """Enter per-pixel mode and forget what is on the strip, so the next frame is painted in full."""
        with self._lock:
            self.send_inner(P.scribble_init_message(self.led_count))
            time.sleep(0.4)  # the controller needs a moment to switch modes before it accepts paints
            self._in_scribble = True
            self._shown = None

    def show_frame(self, colors: Sequence[Pixel], force_full: bool = False) -> int:
        """Display one frame (one pixel per LED: (r, g, b), or (0, 0, 0, level) for the white LED). Sends only changed pixels. Returns commands sent."""
        n = self.led_count
        if len(colors) < n:
            colors = list(colors) + [(0, 0, 0)] * (n - len(colors))
        frame = [self._scale(tuple(c)) for c in colors[:n]]
        sent = 0
        with self._lock:
            now = time.monotonic()
            if not self._in_scribble:
                self.start_frames()
            full = force_full or self._shown is None or (now - self._last_full) >= self.refresh_interval
            if full:
                changed = {i: c for i, c in enumerate(frame)}
                self._last_full = now
            else:
                shown = self._shown or []
                changed = {i: c for i, c in enumerate(frame) if shown[i] != c}
            if changed:
                for px, idx in group_by_color(changed, self.max_groups).items():
                    if len(px) == 4:
                        self.send_inner(P.scribble_paint_message(idx, n, white=px[3]))
                    else:
                        self.send_inner(P.scribble_paint_message(idx, n, rgb=px))
                    sent += 1
                # Remember what the strip really shows (including quantization is unnecessary: we diff against
                # the requested frame, and the periodic full refresh corrects any rounding drift).
                self._shown = frame
            self.frames_sent += 1
        return sent

    def stop_frames(self) -> None:
        with self._lock:
            self._in_scribble = False
            self._shown = None


class SimulatedController(Controller):
    """Same interface, no network. Records what would be sent; useful for `--simulate`, demos and tests."""

    simulated = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent: List[bytes] = []
        self.is_on = True
        self.detected_led_count = 250
        self.host = self.host or "simulated"

    @property
    def connected(self) -> bool:
        return True

    def _connect(self) -> None:
        self._sock = object()  # type: ignore[assignment]
        self.last_error = ""

    def close(self) -> None:
        self._sock = None
        self._in_scribble = False
        self._shown = None

    def _transmit(self, data: bytes) -> None:
        self.sent.append(data)
        if data == P.power_command(True):
            self.is_on = True
        elif data == P.power_command(False):
            self.is_on = False

    def _drain(self) -> bytes:
        return b""

    def _check_alive(self) -> None:
        pass

    def query_state(self, timeout: float = 2.5) -> Optional[State]:
        with self._lock:
            self._ensure()
            self.last_state = State(power=self.is_on, model=0xB6, version=21, pattern=0x61, speed=80, rgb_mode=True,
                                    hue=0, saturation=100, value=100, led_count=self.detected_led_count)
            return self.last_state


def discover(timeout: float = 2.0, target: str = "255.255.255.255") -> List[dict]:
    """Find controllers on the local network with the UDP 48899 discovery broadcast."""
    found: Dict[str, dict] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.3)
        deadline = time.monotonic() + timeout
        next_send = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_send:
                try:
                    sock.sendto(P.DISCOVERY_MESSAGE, (target, P.DISCOVERY_PORT))
                except OSError:
                    break
                next_send = time.monotonic() + 0.8
            try:
                data, _addr = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            info = P.parse_discovery_reply(data)
            if info:
                found[info["ip"]] = info
    finally:
        sock.close()
    return list(found.values())
