"""Wire protocol for Zengge / Surplife "symphony_line" controllers (model byte 0xB6, TM1814 RGBW).

Everything here is pure: functions take values and return bytes (or parse bytes). No sockets.
See TECHNICAL_README.md for the full specification and the confidence level of each command.

Commands are sent on TCP port 5577. Most are wrapped in an "envelope":

    b0 b1 b2 b3 00 01 <version> <counter> <length16> <inner...> <checksum>

where checksum = sum of all preceding bytes & 0xFF. This firmware only answers envelope version 0x02.
The two plain (un-wrapped) commands we use are the state query and power on/off.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

ENVELOPE_MAGIC = bytes([0xB0, 0xB1, 0xB2, 0xB3, 0x00, 0x01])
ENVELOPE_VERSION = 0x02
STATE_QUERY = bytes([0x81, 0x8A, 0x8B, 0x96])
TCP_PORT = 5577
DISCOVERY_PORT = 48899
DISCOVERY_MESSAGE = b"HF-A11ASSISTHREAD"
MAX_SCRIBBLE_LEDS = 255  # the E1 23 / E1 26 commands carry the LED count in one byte
SEGMENTS = 20            # E1 22 splits the strip into 20 equal segments

# Built-in animation ids for E1 21 (names from the open-source flux_led project, PR #540).
BUILTIN_PATTERNS: Dict[int, str] = {
    1: "Wave", 2: "Meteor", 3: "Streamer", 4: "Building Blocks", 5: "Flowing Water", 6: "Chase",
    7: "Horse Racing", 8: "Cycle", 9: "Breathe", 10: "Jump", 11: "Strobe", 12: "Twinkling Stars",
    13: "Stars Wink", 14: "Warning", 15: "Collision", 16: "Fireworks", 17: "Comet",
    18: "Gradient Meteor", 19: "Volcano", 20: "Superluminal", 21: "Rainbow Bridge",
    22: "Gradient Overlay", 101: "Static Gradient", 102: "Static Fill",
}
DIRECTION_LEFT_TO_RIGHT = 0x01
DIRECTION_RIGHT_TO_LEFT = 0x02

# Commands that this project deliberately never sends (they change or erase stored configuration).
FORBIDDEN_PREFIXES = (bytes([0x62]), bytes([0xE0, 0x43]))


def checksum(data: Iterable[int]) -> int:
    return sum(data) & 0xFF


def with_checksum(data: Sequence[int]) -> bytes:
    data = bytes(data)
    return data + bytes([checksum(data)])


def wrap(inner: bytes, counter: int, version: int = ENVELOPE_VERSION) -> bytes:
    """Wrap an inner message in the b0 b1 b2 b3 envelope."""
    length = len(inner)
    body = ENVELOPE_MAGIC + bytes([version, counter & 0xFF, length >> 8, length & 0xFF]) + inner
    return with_checksum(body)


def unwrap(buffer: bytes) -> Tuple[List[Tuple[int, int, bytes]], bytes]:
    """Split a receive buffer into envelopes.

    Returns ([(version, counter, inner), ...], leftover_bytes). Envelopes with a bad checksum are skipped.
    """
    out: List[Tuple[int, int, bytes]] = []
    buf = bytes(buffer)
    while True:
        start = buf.find(ENVELOPE_MAGIC)
        if start < 0:
            return out, buf[-5:] if buf else b""
        buf = buf[start:]
        if len(buf) < 10:
            return out, buf
        length = (buf[8] << 8) | buf[9]
        total = 10 + length + 1
        if len(buf) < total:
            return out, buf
        frame = buf[:total]
        if checksum(frame[:-1]) == frame[-1]:
            out.append((frame[6], frame[7], frame[10:10 + length]))
        buf = buf[total:]


# ---------------------------------------------------------------- colors

def hsv_bytes(rgb: RGB, white: int = 0) -> List[int]:
    """5-byte pixel record used by the E1 commands: [hue/2 (0-180), saturation, value (0-100), 0, white]."""
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return [int(round(h * 180)) % 181, int(round(s * 100)), int(round(v * 100)), 0x00, max(0, min(100, int(white)))]


def scale_rgb(rgb: RGB, percent: float) -> RGB:
    k = max(0.0, min(100.0, percent)) / 100.0
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


def parse_hex(color: str) -> RGB:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ValueError(f"not a #rrggbb color: {color!r}")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def to_hex(rgb: RGB) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


# ---------------------------------------------------------------- plain commands

def power_command(on: bool) -> bytes:
    """Power on/off. Plain (un-wrapped) command; 'off' makes the controller send black, the 24 V supply stays on."""
    return with_checksum([0x71, 0x23 if on else 0x24, 0x0F])


# ---------------------------------------------------------------- inner messages (to be wrapped)

def solid_message(rgb: Optional[RGB] = None, white: Optional[int] = None) -> bytes:
    """E1 22 uniform fill: all 20 segments the same color (or a white level 0-100)."""
    if white is not None:
        seg = [0x00, 0x64, 0x00, 0x00, max(0, min(100, int(white)))]
    else:
        seg = hsv_bytes(rgb or (0, 0, 0))
    return bytes([0xE1, 0x22, 0x00, 0x00, 0x00, 0x00, SEGMENTS] + seg * SEGMENTS)


def segments_message(colors: Sequence[RGB]) -> bytes:
    """E1 22 with 20 individual segment colors."""
    if len(colors) != SEGMENTS:
        raise ValueError(f"need exactly {SEGMENTS} segment colors, got {len(colors)}")
    out = [0xE1, 0x22, 0x00, 0x00, 0x00, 0x00, SEGMENTS]
    for c in colors:
        out += hsv_bytes(c)
    return bytes(out)


def builtin_effect_message(pattern: int, colors: Sequence[RGB], speed: int = 50, density: int = 50,
                           direction: int = DIRECTION_LEFT_TO_RIGHT, option: int = 0) -> bytes:
    """E1 21 built-in animation that runs on the controller by itself (1-8 colors)."""
    if not 1 <= len(colors) <= 8:
        raise ValueError("a built-in effect takes 1 to 8 colors")
    msg = [0xE1, 0x21, 0x01, 0x64, pattern & 0xFF, option & 0xFF, direction & 0xFF,
           max(0, min(100, int(density))), max(0, min(100, int(speed))),
           0, 0, 0, 0, 0, 0, len(colors)]
    for c in colors:
        msg += hsv_bytes(c)
    return bytes(msg)


def scribble_init_message(count: int) -> bytes:
    """E1 23: switch to per-LED ("scribble") mode with `count` LEDs. Does not render by itself."""
    if not 1 <= count <= MAX_SCRIBBLE_LEDS:
        raise ValueError(f"LED count must be 1..{MAX_SCRIBBLE_LEDS}")
    return bytes([0xE1, 0x23, 0x01, 0x00, 0x01, 0x50, 0x64, 0x00, count]) + bytes.fromhex("00000000000064") * count


def bitmap(indices: Iterable[int], count: int) -> bytes:
    """MSB-first bitmap of ceil(count/8) bytes with the bit set for each selected LED index."""
    data = bytearray((count + 7) // 8)
    for i in indices:
        if not 0 <= i < count:
            raise ValueError(f"LED index {i} out of range 0..{count - 1}")
        data[i // 8] |= 0x80 >> (i % 8)
    return bytes(data)


def scribble_paint_message(indices: Iterable[int], count: int, rgb: Optional[RGB] = None,
                           white: Optional[int] = None, effect: int = 0, direction: int = 1,
                           density: int = 0x50, speed: int = 0x64, blink: int = 0, blink_speed: int = 0x64) -> bytes:
    """E1 26: paint one color (or white level) onto the selected LEDs. Unselected LEDs keep their color."""
    if white is not None:
        h2, s, v, w = 0, 0, 0, max(0, min(100, int(white)))
    else:
        h2, s, v, _, w = hsv_bytes(rgb or (0, 0, 0))
    header = [0xE1, 0x26, effect & 0xFF, direction & 0xFF, density & 0xFF, speed & 0xFF, blink & 0xFF,
              h2, s, v, 0x00, w, blink_speed & 0xFF]
    return bytes(header) + bitmap(indices, count)


# ---------------------------------------------------------------- state parsing

@dataclass
class State:
    power: bool
    model: int
    version: int
    pattern: int
    speed: int
    rgb_mode: bool
    hue: int          # degrees 0-360
    saturation: int   # 0-100
    value: int        # 0-100
    led_count: int
    raw: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        return {"power": self.power, "model": self.model, "version": self.version, "pattern": self.pattern,
                "speed": self.speed, "rgb_mode": self.rgb_mode, "hue": self.hue, "saturation": self.saturation,
                "value": self.value, "led_count": self.led_count}


def parse_state(frame: bytes) -> Optional[State]:
    """Parse a 27-byte `ea 81 ...` state frame (found anywhere in `frame`)."""
    idx = frame.find(bytes([0xEA, 0x81]))
    if idx < 0 or len(frame) - idx < 20:
        return None
    f = frame[idx:]
    return State(power=(f[6] == 0x23), model=f[4], version=f[5], pattern=f[7], speed=f[9],
                 rgb_mode=(f[10] == 0xF0), hue=f[11] * 2, saturation=f[12], value=f[13],
                 led_count=f[18], raw=f.hex())


def is_forbidden(inner: bytes) -> bool:
    """True for messages that would change stored configuration (never sent by this project)."""
    return any(inner.startswith(p) for p in FORBIDDEN_PREFIXES)


def parse_discovery_reply(data: bytes) -> Optional[dict]:
    """'192.168.1.50,AABBCCDDEEFF,AK001-ZJ21413,<token>' -> dict."""
    try:
        parts = data.decode("ascii", "replace").strip().split(",")
    except Exception:
        return None
    if len(parts) < 3 or parts[0].count(".") != 3:
        return None
    return {"ip": parts[0], "mac": parts[1], "model": parts[2], "token": parts[3] if len(parts) > 3 else ""}
