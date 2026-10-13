"""Streamed effects: rendered here frame by frame and painted onto the strip (this program must be running)."""
from __future__ import annotations

import math
from typing import List

from ..protocol import RGB
from . import Effect, Param, register
from .util import add, hsv, lerp, palette, scale

DIRECTION = [("forward", "Forward"), ("backward", "Backward")]


def _flip(i: int, n: int, direction: str) -> int:
    return n - 1 - i if direction == "backward" else i


@register
class Embers(Effect):
    kind = "embers"
    label = "Embers"
    category = "Streamed"
    description = "Flickering glowing coals. Blocks of LEDs brighten and dim at random; every third block is hotter."
    params = (
        Param("color", "Ember color", "color", "#ff5a00"),
        Param("hot_color", "Hot block color", "color", "#ff1400"),
        Param("block", "Block size", "int", 5, min=1, max=40, unit="LEDs"),
        Param("min_level", "Dimmest level", "int", 15, min=0, max=100, unit="%"),
        Param("flicker", "Flicker amount", "int", 50, min=0, max=100, help="How much each block changes per frame."),
    )

    def start(self) -> None:
        self.levels = [self.rng.random() for _ in range(self.n // self.p["block"] + 1)]

    def frame(self, t: float) -> List[RGB]:
        floor = self.p["min_level"] / 100.0
        amount = self.p["flicker"] / 200.0
        for i in range(len(self.levels)):
            self.levels[i] = max(floor, min(1.0, self.levels[i] + self.rng.uniform(-amount, amount)))
        base, hot, blk = self.color("color"), self.color("hot_color"), self.p["block"]
        return [scale(hot if (i // blk) % 3 == 0 else base, self.levels[i // blk]) for i in range(self.n)]


@register
class Wave(Effect):
    kind = "wave"
    label = "Rolling wave"
    category = "Streamed"
    description = "Two colors flow into each other in a slow sine wave that rolls along the strip."
    params = (
        Param("color_a", "Color A", "color", "#8c00ff"),
        Param("color_b", "Color B", "color", "#ff5a00"),
        Param("wavelength", "Wavelength", "int", 88, min=8, max=500, unit="LEDs"),
        Param("period", "Seconds per cycle", "float", 4.0, min=0.5, max=120, step=0.5),
        Param("a_floor", "Color A minimum", "int", 35, min=0, max=100, unit="%"),
        Param("b_strength", "Color B strength", "int", 90, min=0, max=100, unit="%"),
        Param("direction", "Direction", "choice", "forward", choices=DIRECTION),
    )

    def frame(self, t: float) -> List[RGB]:
        a, b = self.color("color_a"), self.color("color_b")
        floor, bs = self.p["a_floor"] / 100.0, self.p["b_strength"] / 100.0
        sign = -1.0 if self.p["direction"] == "forward" else 1.0
        phase = sign * 2 * math.pi * t / self.p["period"]
        out = []
        for i in range(self.n):
            w = (math.sin(2 * math.pi * i / self.p["wavelength"] + phase) + 1) / 2
            out.append(add(scale(a, floor + (1 - floor) * w), scale(b, bs * (1 - w))))
        return out


@register
class Comet(Effect):
    kind = "comet"
    label = "Comet / ghost"
    category = "Streamed"
    description = "A bright head with a fading tail travels along the strip over a dim background."
    params = (
        Param("color", "Comet color", "color", "#00ff78"),
        Param("background", "Background color", "color", "#000a05"),
        Param("tail", "Tail length", "int", 14, min=1, max=120, unit="LEDs"),
        Param("speed", "Speed", "int", 55, min=1, max=400, unit="LEDs/s"),
        Param("gap", "Pause between passes", "int", 40, min=0, max=500, unit="LEDs"),
        Param("bounce", "Bounce back and forth", "bool", False),
        Param("direction", "Direction", "choice", "forward", choices=DIRECTION),
    )

    def frame(self, t: float) -> List[RGB]:
        n, tail = self.n, self.p["tail"]
        color, bg = self.color("color"), self.color("background")
        run = n + self.p["gap"]
        x = (t * self.p["speed"]) % (2 * run if self.p["bounce"] else run)
        reverse = False
        if self.p["bounce"] and x >= run:  # return pass: same motion, mirrored strip
            x, reverse = x - run, True
        reverse ^= self.p["direction"] == "backward"
        out: List[RGB] = [bg] * n
        for i in range(n):
            d = x - i
            if 0 <= d < tail * 4:
                out[i] = lerp(bg, color, math.exp(-d / tail))
        if reverse:
            out.reverse()
        return out


@register
class Lightning(Effect):
    kind = "lightning"
    label = "Lightning"
    category = "Streamed"
    description = ("Small flickering bolts at random spots over a base color, with an occasional full-strip flash. "
                   "Change the colors for a white storm, black sky, orange glow with purple flashes...")
    params = (
        Param("base", "Background color", "color", "#10001e"),
        Param("flash_colors", "Bolt colors", "colors", ["#ffffff", "#78a0ff"], min=1, max=8,
              help="Use the background color in the list to make dark 'holes' instead of flashes."),
        Param("big_colors", "Big flash colors", "colors", ["#ffffff", "#ffffff", "#ffffff", "#78a0ff"], min=1, max=8),
        Param("bolts_per_sec", "New bolts per second", "float", 2.0, min=0.1, max=20, step=0.1),
        Param("min_width", "Smallest bolt (each side)", "int", 1, min=0, max=60, unit="LEDs"),
        Param("max_width", "Largest bolt (each side)", "int", 4, min=0, max=60, unit="LEDs"),
        Param("min_life", "Shortest bolt life", "float", 0.5, min=0.1, max=10, step=0.1, unit="s"),
        Param("max_life", "Longest bolt life", "float", 1.5, min=0.1, max=10, step=0.1, unit="s"),
        Param("flicker", "Flicker", "int", 70, min=0, max=100, help="Chance each bolt pixel is lit in a frame."),
        Param("big_per_minute", "Big flashes per minute", "float", 4.0, min=0, max=60, step=0.5),
    )

    def start(self) -> None:
        self.bolts: List[dict] = []
        self.big_until = 0.0

    def frame(self, t: float) -> List[RGB]:
        p, rng, n = self.p, self.rng, self.n
        dt = self.dt(t) or (1 / 5)
        spawn = p["bolts_per_sec"] * dt
        count = int(spawn) + (1 if rng.random() < spawn - int(spawn) else 0)
        flash = self.colors("flash_colors")
        lo_w, hi_w = sorted((p["min_width"], p["max_width"]))
        lo_l, hi_l = sorted((p["min_life"], p["max_life"]))
        for _ in range(count):
            self.bolts.append({"c": rng.randrange(n), "w": rng.randint(lo_w, hi_w),
                               "until": t + rng.uniform(lo_l, hi_l), "col": rng.choice(flash)})
        if rng.random() < p["big_per_minute"] / 60.0 * dt:
            self.big_until = t + rng.uniform(0.15, 0.3)
        self.bolts = [b for b in self.bolts if b["until"] > t]
        if t < self.big_until:
            big = self.colors("big_colors")
            return [rng.choice(big) for _ in range(n)]
        out = [self.color("base")] * n
        for b in self.bolts:
            for i in range(max(0, b["c"] - b["w"]), min(n, b["c"] + b["w"] + 1)):
                if rng.random() < p["flicker"] / 100.0:
                    if abs(i - b["c"]) > b["w"] * 0.6 and rng.random() < 0.5:
                        continue
                    out[i] = b["col"] if rng.random() > 0.25 else rng.choice(flash)
        return out


@register
class Twinkle(Effect):
    kind = "twinkle"
    label = "Twinkle"
    category = "Streamed"
    description = "Single LEDs sparkle on and fade out over a base color."
    params = (
        Param("base", "Background color", "color", "#000000"),
        Param("colors", "Sparkle colors", "colors", ["#ffffff", "#ffd27a"], min=1, max=8),
        Param("per_second", "New sparkles per second", "float", 12.0, min=0.5, max=100, step=0.5),
        Param("life", "Sparkle life", "float", 0.8, min=0.1, max=10, step=0.1, unit="s"),
    )

    def start(self) -> None:
        self.sparks: List[dict] = []

    def frame(self, t: float) -> List[RGB]:
        dt = self.dt(t) or (1 / 5)
        spawn = self.p["per_second"] * dt
        for _ in range(int(spawn) + (1 if self.rng.random() < spawn - int(spawn) else 0)):
            self.sparks.append({"i": self.rng.randrange(self.n), "t0": t, "c": self.rng.choice(self.colors("colors"))})
        life = self.p["life"]
        self.sparks = [s for s in self.sparks if t - s["t0"] < life]
        base = self.color("base")
        out = [base] * self.n
        for s in self.sparks:
            k = (t - s["t0"]) / life
            out[s["i"]] = lerp(base, s["c"], 1 - k if k > 0.15 else k / 0.15)
        return out


@register
class Rainbow(Effect):
    kind = "rainbow"
    label = "Rainbow"
    category = "Streamed"
    description = "A smooth rainbow that scrolls along the strip."
    params = (
        Param("wavelength", "Rainbow length", "int", 120, min=10, max=500, unit="LEDs"),
        Param("period", "Seconds per cycle", "float", 8.0, min=0.5, max=120, step=0.5),
        Param("saturation", "Saturation", "int", 100, min=0, max=100, unit="%"),
        Param("brightness", "Brightness", "int", 100, min=1, max=100, unit="%"),
        Param("direction", "Direction", "choice", "forward", choices=DIRECTION),
    )

    def frame(self, t: float) -> List[RGB]:
        sign = 1 if self.p["direction"] == "forward" else -1
        shift = sign * t / self.p["period"]
        s, v = self.p["saturation"] / 100.0, self.p["brightness"] / 100.0
        return [hsv(i / self.p["wavelength"] - shift, s, v) for i in range(self.n)]


@register
class Breathe(Effect):
    kind = "breathe"
    label = "Breathe"
    category = "Streamed"
    description = "The whole strip slowly brightens and dims, stepping through your colors."
    params = (
        Param("colors", "Colors", "colors", ["#ff0000"], min=1, max=8),
        Param("period", "Seconds per breath", "float", 4.0, min=0.5, max=60, step=0.5),
        Param("min_level", "Dimmest level", "int", 5, min=0, max=100, unit="%"),
    )

    def frame(self, t: float) -> List[RGB]:
        cols = self.colors("colors")
        period = self.p["period"]
        color = cols[int(t / period) % len(cols)]
        floor = self.p["min_level"] / 100.0
        level = floor + (1 - floor) * (0.5 - 0.5 * math.cos(2 * math.pi * t / period))
        return [scale(color, level)] * self.n


@register
class Chase(Effect):
    kind = "chase"
    label = "Marching blocks"
    category = "Streamed"
    description = "Repeating blocks of color march along the strip (candy cane, Christmas, school colors...)."
    params = (
        Param("colors", "Block colors", "colors", ["#ff0000", "#ffffff"], min=1, max=8),
        Param("background", "Gap color", "color", "#000000"),
        Param("block", "Block size", "int", 6, min=1, max=60, unit="LEDs"),
        Param("gap", "Gap between blocks", "int", 0, min=0, max=60, unit="LEDs"),
        Param("speed", "Speed", "float", 12.0, min=0.5, max=200, step=0.5, unit="LEDs/s"),
        Param("direction", "Direction", "choice", "forward", choices=DIRECTION),
    )

    def frame(self, t: float) -> List[RGB]:
        cols, bg = self.colors("colors"), self.color("background")
        block, gap = self.p["block"], self.p["gap"]
        cell = block + gap
        period = cell * len(cols)
        offset = (t * self.p["speed"]) % period
        out = []
        for i in range(self.n):
            pos = (_flip(i, self.n, self.p["direction"]) - offset) % period
            idx, within = int(pos // cell), pos % cell
            out.append(cols[idx] if within < block else bg)
        return out


@register
class Wipe(Effect):
    kind = "wipe"
    label = "Color wipe"
    category = "Streamed"
    description = "A color fills the strip from one end, holds, then wipes back out."
    params = (
        Param("color", "Color", "color", "#00c8ff"),
        Param("background", "Background color", "color", "#000000"),
        Param("period", "Seconds per fill and wipe-out", "float", 6.0, min=1, max=120, step=0.5),
        Param("direction", "Direction", "choice", "forward", choices=DIRECTION),
    )

    def frame(self, t: float) -> List[RGB]:
        x = (t % self.p["period"]) / self.p["period"]
        fill = x / 0.45 if x < 0.45 else 1.0 if x < 0.5 else 1 - (x - 0.5) / 0.45 if x < 0.95 else 0.0
        lit = int(round(fill * self.n))
        color, bg = self.color("color"), self.color("background")
        out = [color if i < lit else bg for i in range(self.n)]
        return out[::-1] if self.p["direction"] == "backward" else out


@register
class ScrollGradient(Effect):
    kind = "gradient"
    label = "Scrolling gradient"
    category = "Streamed"
    description = "A smooth gradient through your colors that scrolls along the strip (sunset, ocean, fire...)."
    params = (
        Param("colors", "Gradient colors", "colors", ["#ff3c00", "#ff00a0", "#4b00ff"], min=2, max=12),
        Param("wavelength", "Gradient length", "int", 150, min=10, max=500, unit="LEDs"),
        Param("period", "Seconds per cycle", "float", 10.0, min=0.5, max=300, step=0.5),
        Param("direction", "Direction", "choice", "forward", choices=DIRECTION),
    )

    def frame(self, t: float) -> List[RGB]:
        cols = self.colors("colors")
        sign = 1 if self.p["direction"] == "forward" else -1
        shift = sign * t / self.p["period"]
        return [palette(cols, i / self.p["wavelength"] - shift) for i in range(self.n)]
