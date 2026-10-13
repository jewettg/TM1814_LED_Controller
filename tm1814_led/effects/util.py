"""Small color helpers shared by effects."""
from __future__ import annotations

import colorsys
from typing import Sequence

from ..protocol import RGB


def scale(rgb: RGB, k: float) -> RGB:
    k = max(0.0, min(1.0, k))
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


def lerp(a: RGB, b: RGB, k: float) -> RGB:
    k = max(0.0, min(1.0, k))
    return (int(a[0] + (b[0] - a[0]) * k), int(a[1] + (b[1] - a[1]) * k), int(a[2] + (b[2] - a[2]) * k))


def add(a: RGB, b: RGB) -> RGB:
    return (min(255, a[0] + b[0]), min(255, a[1] + b[1]), min(255, a[2] + b[2]))


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return (int(r * 255), int(g * 255), int(b * 255))


def palette(colors: Sequence[RGB], pos: float) -> RGB:
    """Smoothly interpolate around a cyclic palette; pos in [0,1)."""
    n = len(colors)
    if n == 1:
        return colors[0]
    x = (pos % 1.0) * n
    i = int(x)
    return lerp(colors[i % n], colors[(i + 1) % n], x - i)
