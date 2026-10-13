"""Walk-around test patterns for `tm1814-led test`: find out how the strip is laid out by looking at it.

Each pattern is a function returning either one frame (a list of (r, g, b) per LED, shown until Ctrl-C) or a
generator of (frame, label) pairs for animated patterns.
"""
from __future__ import annotations

import colorsys
from typing import Callable, Dict, Iterator, List, Tuple, Union

RGB = Tuple[int, int, int]
Frame = List[RGB]
OFF: RGB = (0, 0, 0)
WHITE: RGB = (255, 255, 255)
RED, GREEN, BLUE, YELLOW, CYAN = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 200, 0), (0, 255, 255)


def _blank(n: int) -> Frame:
    return [OFF] * n


def halves(n: int) -> Tuple[Frame, str]:
    half = n // 2
    return [RED] * half + [BLUE] * (n - half), f"red = LEDs 0-{half - 1}, blue = {half}-{n - 1}"


def ruler(n: int) -> Tuple[Frame, str]:
    f = _blank(n)
    for i in range(0, n, 10):
        f[i] = WHITE
    for i in range(0, n, 50):
        f[i] = RED
    f[n - 1] = BLUE
    return f, "white tick every 10 LEDs, red every 50, blue on the last LED"


def zones(n: int) -> Tuple[Frame, str]:
    cols = [RED, YELLOW, GREEN, CYAN, BLUE]
    size = max(1, n // 5)
    return [cols[min(4, i // size)] for i in range(n)], "five zones in order: red, yellow, green, cyan, blue"


def blocks(n: int) -> Tuple[Frame, str]:
    cols = [RED, GREEN, BLUE, WHITE]
    return [cols[(i // 25) % 4] for i in range(n)], "blocks of 25 LEDs: red, green, blue, white, repeating"


def colororder(n: int) -> Tuple[Frame, str]:
    f = _blank(n)
    for lo, col in ((0, RED), (10, GREEN), (20, BLUE), (30, WHITE)):
        for i in range(lo, min(n, lo + 10)):
            f[i] = col
    return f, "LEDs 0-9 should look RED, 10-19 GREEN, 20-29 BLUE, 30-39 WHITE"


def fillto(n: int, count: int) -> Tuple[Frame, str]:
    count = max(0, min(n, count))
    return [GREEN] * count + [OFF] * (n - count), f"LEDs 0-{count - 1} are green; where does the green stop?"


def single(n: int, index: int) -> Tuple[Frame, str]:
    f = _blank(n)
    f[max(0, min(n - 1, index))] = WHITE
    return f, f"only LED {index} is lit: how many white spots can you count on the house?"


def gradient(n: int) -> Tuple[Frame, str]:
    return [tuple(int(c * 255) for c in colorsys.hsv_to_rgb(i / n, 1, 1)) for i in range(n)], "rainbow across the whole strip"


def chase(n: int) -> Iterator[Tuple[Frame, str]]:
    for i in range(n):
        f = _blank(n)
        f[i] = WHITE
        yield f, f"LED {i}"


PATTERNS: Dict[str, Tuple[Callable, str]] = {
    "halves": (halves, "red first half, blue second half: where does the second half start?"),
    "ruler": (ruler, "tick marks every 10/50 LEDs to count along the strip"),
    "zones": (zones, "five colored zones to see the order and where the strip ends"),
    "blocks": (blocks, "blocks of 25 LEDs in four colors"),
    "colororder": (colororder, "pure red, green, blue, white: reveals swapped color channels"),
    "fillto": (fillto, "fillto N: light LEDs 0..N-1 green to find where the lit run ends"),
    "single": (single, "single N: light only LED N and count the spots"),
    "gradient": (gradient, "a rainbow over the whole strip"),
    "chase": (chase, "one LED travels from 0 to the end (chase --delay 0.3)"),
}
