"""Fixed effects: one picture painted once. Nothing moves; the lights stay as they are.

(They are painted by this program, then the controller holds them, so they keep showing even if this program stops.)
"""
from __future__ import annotations

import datetime as dt
from typing import List

from ..protocol import Pixel, white_pixel
from . import Effect, Param, register

LIT_PARAMS = (
    Param("use_white", "Use the white LED", "bool", True,
          help="RGBW pixels have a dedicated white LED. Turn this off to light the pixels with the color below."),
    Param("white_level", "White LED brightness", "int", 100, min=1, max=100, unit="%"),
    Param("color", "Color (when not using the white LED)", "color", "#ffb347"),
    Param("background", "Color of the other pixels", "color", "#000000", help="Black means off."),
)


def nth_frame(effect: Effect, every: int) -> List[Pixel]:
    on = white_pixel(effect.p["white_level"]) if effect.p["use_white"] else effect.color("color")
    off = effect.color("background")
    first = effect.p["offset"]
    return [on if i >= first and (i - first) % every == 0 else off for i in range(effect.n)]


@register
class EveryNth(Effect):
    kind = "nth"
    label = "Every Nth pixel"
    category = "Fixed"
    static = True
    description = ("Lights every 3rd, 4th, 5th... pixel and leaves the rest off. Not animated: it just stays. "
                   "A bigger number means fewer lights, so a dimmer glow overall.")
    params = (
        Param("every", "Light every Nth pixel", "int", 3, min=2, max=60, help="3 = every third pixel, 4 = every fourth..."),
        Param("offset", "First lit pixel", "int", 0, min=0, max=59, help="0 starts at the very first pixel."),
    ) + LIT_PARAMS

    def frame(self, t: float) -> List[Pixel]:
        return nth_frame(self, self.p["every"])
