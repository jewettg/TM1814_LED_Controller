"""Native effects: one command and the controller animates by itself (no streaming, runs even if we stop)."""
from __future__ import annotations

from typing import List

from .. import protocol as P
from ..protocol import RGB
from . import Effect, Param, register

PATTERN_CHOICES = [(pid, name) for pid, name in sorted(P.BUILTIN_PATTERNS.items())]


@register
class Solid(Effect):
    kind = "solid"
    label = "Solid color"
    category = "Controller-native"
    native = True
    description = "One color across the whole strip. Held by the controller."
    params = (
        Param("color", "Color", "color", "#ff0000"),
        Param("use_white", "Use the white LED instead", "bool", False,
              help="RGBW pixels have a dedicated white LED; this lights only that channel."),
        Param("white_level", "White level", "int", 100, min=0, max=100, unit="%"),
    )

    def frame(self, t: float) -> List[RGB]:
        if self.p["use_white"]:
            w = int(255 * self.p["white_level"] / 100)
            return [(w, w, w)] * self.n
        return [self.color("color")] * self.n

    def apply(self, controller) -> None:
        if self.p["use_white"]:
            controller.solid((0, 0, 0), white=self.p["white_level"])
        else:
            controller.solid(self.color("color"))


@register
class Segments(Effect):
    kind = "segments"
    label = "Static segments"
    category = "Controller-native"
    native = True
    description = ("The strip is split into 20 equal segments. Give 1 to 20 colors; they repeat to fill all 20. "
                   "Held by the controller.")
    params = (Param("colors", "Segment colors", "colors", ["#ff0000", "#00ff00", "#0000ff"], min=1, max=20),)

    def _twenty(self) -> List[RGB]:
        cols = self.colors("colors")
        return [cols[i % len(cols)] for i in range(P.SEGMENTS)]

    def frame(self, t: float) -> List[RGB]:
        seg = self._twenty()
        return [seg[min(P.SEGMENTS - 1, i * P.SEGMENTS // self.n)] for i in range(self.n)]

    def apply(self, controller) -> None:
        controller.segments(self._twenty())


@register
class BuiltIn(Effect):
    kind = "builtin"
    label = "Built-in animation"
    category = "Controller-native"
    native = True
    previewable = False
    description = ("One of the controller's own animations (Wave, Meteor, Horse Racing, Twinkling Stars...). "
                   "Smooth at any speed and keeps running without this program.")
    params = (
        Param("pattern", "Animation", "choice", 8, choices=PATTERN_CHOICES),
        Param("colors", "Colors (1 to 8)", "colors", ["#ff0000", "#00ff00", "#0000ff"], min=1, max=8),
        Param("speed", "Speed", "int", 50, min=0, max=100),
        Param("density", "Density", "int", 50, min=0, max=100, help="Pattern spacing; effect depends on the animation."),
        Param("direction", "Direction", "choice", 1, choices=[(1, "Left to right"), (2, "Right to left")]),
        Param("option", "Variant", "choice", 0, choices=[(0, "Default"), (1, "Variant 1"), (2, "Variant 2")],
              help="Meaning varies by animation (for example static vs. color change)."),
    )

    def apply(self, controller) -> None:
        controller.builtin(self.p["pattern"], self.colors("colors"), self.p["speed"], self.p["density"],
                           self.p["direction"], self.p["option"])
