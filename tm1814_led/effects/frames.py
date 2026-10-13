"""Custom frame-by-frame animation, created in the web editor.

Data model (the `frames` parameter):

    {"background": "#000000", "loop": "loop" | "pingpong" | "once",
     "frames": [ {"duration": 0.5, "transition": "cut" | "fade",
                  "segments": [ {"start": 0, "end": 49, "color": "#ff0000", "color_end": "#0000ff"} ]} ]}

Each frame starts from the background and paints its segments in order (later segments win). A segment with a
`color_end` is a gradient. LED indices are 0-based and inclusive. "fade" blends this frame into the next one over
its duration; "cut" holds it and then switches.
"""
from __future__ import annotations

from typing import Any, List

from ..protocol import RGB, parse_hex, to_hex
from . import Effect, Param, ParamError, register
from .util import lerp

MAX_FRAMES = 500
MAX_SEGMENTS = 500
DEFAULT_DATA = {
    "background": "#000000", "loop": "loop",
    "frames": [
        {"duration": 0.6, "transition": "cut", "segments": [{"start": 0, "end": 124, "color": "#ff0000"},
                                                             {"start": 125, "end": 249, "color": "#0000ff"}]},
        {"duration": 0.6, "transition": "cut", "segments": [{"start": 0, "end": 124, "color": "#0000ff"},
                                                             {"start": 125, "end": 249, "color": "#ff0000"}]},
    ],
}


def _clean_data(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ParamError("animation data must be an object")
    bg = to_hex(parse_hex(str(value.get("background", "#000000"))))
    loop = value.get("loop", "loop")
    if loop not in ("loop", "pingpong", "once"):
        raise ParamError("loop must be loop, pingpong or once")
    frames_in = value.get("frames")
    if not isinstance(frames_in, list) or not 1 <= len(frames_in) <= MAX_FRAMES:
        raise ParamError(f"an animation needs 1 to {MAX_FRAMES} frames")
    frames = []
    for fi, fr in enumerate(frames_in):
        if not isinstance(fr, dict):
            raise ParamError(f"frame {fi + 1} must be an object")
        try:
            duration = float(fr.get("duration", 0.5))
        except (TypeError, ValueError) as exc:
            raise ParamError(f"frame {fi + 1}: bad duration") from exc
        transition = fr.get("transition", "cut")
        if transition not in ("cut", "fade"):
            raise ParamError(f"frame {fi + 1}: transition must be cut or fade")
        segs_in = fr.get("segments", [])
        if not isinstance(segs_in, list) or len(segs_in) > MAX_SEGMENTS:
            raise ParamError(f"frame {fi + 1}: too many segments (max {MAX_SEGMENTS})")
        segs = []
        for s in segs_in:
            try:
                start, end = int(s["start"]), int(s["end"])
                seg = {"start": min(start, end), "end": max(start, end), "color": to_hex(parse_hex(str(s["color"])))}
                if s.get("color_end"):
                    seg["color_end"] = to_hex(parse_hex(str(s["color_end"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise ParamError(f"frame {fi + 1}: bad segment {s!r}") from exc
            if seg["start"] < 0:
                raise ParamError(f"frame {fi + 1}: LED indices start at 0")
            segs.append(seg)
        frames.append({"duration": max(0.05, min(3600.0, duration)), "transition": transition, "segments": segs})
    return {"background": bg, "loop": loop, "frames": frames}


@register
class CustomFrames(Effect):
    kind = "frames"
    label = "Custom animation (frames)"
    category = "Streamed"
    description = ("Your own animation: a list of frames, each made of colored (or gradient) ranges of LEDs, "
                   "with per-frame durations and optional fades. Built in the frame editor.")
    params = (Param("data", "Animation", "frames", DEFAULT_DATA),)

    @classmethod
    def clean_extra(cls, params: dict) -> dict:
        params["data"] = _clean_data(params["data"])
        return params

    def start(self) -> None:
        d = self.p["data"]
        self._bg = parse_hex(d["background"])
        self._frames = d["frames"]
        self._loop = d["loop"]
        self._durations = [f["duration"] for f in self._frames]
        self._cache: dict = {}
        self._seq = self._timeline()

    def _render(self, index: int) -> List[RGB]:
        if index in self._cache:
            return self._cache[index]
        px = [self._bg] * self.n
        px = list(px)
        for seg in self._frames[index]["segments"]:
            a, b = seg["start"], min(seg["end"], self.n - 1)
            c1 = parse_hex(seg["color"])
            c2 = parse_hex(seg["color_end"]) if "color_end" in seg else None
            span = max(1, b - a)
            for i in range(a, b + 1):
                px[i] = c1 if c2 is None else lerp(c1, c2, (i - a) / span)
        if len(self._cache) < 64:
            self._cache[index] = px
        return px

    def _timeline(self):
        """Playback order as (frame index, duration). Ping-pong goes 0..n-1 then back down to 1 (ends not repeated)."""
        seq = [(i, d) for i, d in enumerate(self._durations)]
        if self._loop == "pingpong":
            seq += [(i, self._durations[i]) for i in range(len(self._frames) - 2, 0, -1)]
        return seq

    def _locate(self, t: float):
        """Return (position in the timeline, progress 0-1 within that frame) for time t."""
        seq = self._seq
        total = sum(d for _, d in seq)
        t = min(t, total - 1e-9) if self._loop == "once" else t % total
        acc = 0.0
        for pos, (_, d) in enumerate(seq):
            if t < acc + d:
                return pos, (t - acc) / d
            acc += d
        return len(seq) - 1, 1.0

    def frame(self, t: float) -> List[RGB]:
        pos, progress = self._locate(t)
        index = self._seq[pos][0]
        cur = self._render(index)
        if self._frames[index]["transition"] == "fade":
            last = pos + 1 >= len(self._seq)
            nxt_pos = pos if (last and self._loop == "once") else (pos + 1) % len(self._seq)
            nxt = self._render(self._seq[nxt_pos][0])
            return [lerp(a, b, progress) for a, b in zip(cur, nxt)]
        return cur
