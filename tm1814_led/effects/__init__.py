"""Effect engine.

An *effect* is a kind (code) plus parameters (data). Each kind declares its parameters with `Param`, and the web
UI builds its editor forms from that schema, so adding a kind with new parameters needs no UI work.

Two families:
  * native effects run on the controller by themselves (one command; they keep running even if this program stops)
  * streamed effects are rendered here, frame by frame, and painted onto the strip (needs this program running)
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type

from ..protocol import RGB, parse_hex, to_hex


class ParamError(ValueError):
    """A parameter value is invalid."""


class Param:
    """One editable parameter. type is one of: int, float, bool, choice, color, colors, frames."""

    def __init__(self, key: str, label: str, type: str, default: Any, *, min: Optional[float] = None,
                 max: Optional[float] = None, step: Optional[float] = None,
                 choices: Optional[Sequence[Tuple[Any, str]]] = None, help: str = "", unit: str = ""):
        self.key, self.label, self.type, self.default = key, label, type, default
        self.min, self.max, self.step = min, max, step
        self.choices = list(choices) if choices else None
        self.help, self.unit = help, unit

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"key": self.key, "label": self.label, "type": self.type, "default": self.default,
                             "help": self.help, "unit": self.unit}
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        if self.step is not None:
            d["step"] = self.step
        if self.choices:
            d["choices"] = [{"value": v, "label": l} for v, l in self.choices]
        return d

    def clean(self, value: Any) -> Any:
        t = self.type
        try:
            if t == "int":
                v = int(round(float(value)))
            elif t == "float":
                v = float(value)
            elif t == "bool":
                v = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
                return v
            elif t == "choice":
                valid = [c[0] for c in (self.choices or [])]
                if value not in valid:
                    # JSON numbers may arrive as strings for integer choices
                    for c in valid:
                        if str(c) == str(value):
                            return c
                    raise ParamError(f"{self.label}: {value!r} is not one of {valid}")
                return value
            elif t == "color":
                return to_hex(parse_hex(str(value)))
            elif t == "colors":
                if not isinstance(value, (list, tuple)):
                    raise ParamError(f"{self.label}: expected a list of colors")
                v = [to_hex(parse_hex(str(c))) for c in value]
                lo = int(self.min) if self.min is not None else 1
                hi = int(self.max) if self.max is not None else 64
                if not lo <= len(v) <= hi:
                    raise ParamError(f"{self.label}: needs {lo} to {hi} colors")
                return v
            else:
                return value  # "frames" and other structured types are validated by the effect class
        except ParamError:
            raise
        except (TypeError, ValueError) as exc:
            raise ParamError(f"{self.label}: {exc}") from exc
        if self.min is not None:
            v = max(type(v)(self.min), v)
        if self.max is not None:
            v = min(type(v)(self.max), v)
        return v


class Effect:
    kind = ""
    label = ""
    description = ""
    category = ""          # shown as a group in the UI
    native = False         # True: runs on the controller itself
    previewable = True     # False: cannot be simulated here (shown without a preview)
    params: Sequence[Param] = ()

    def __init__(self, params: Optional[dict], led_count: int, seed: Optional[int] = None):
        self.p = self.clean(params or {})
        self.n = led_count
        self.rng = random.Random(seed)
        self._last_t: Optional[float] = None
        self.start()

    # ---- schema / validation
    @classmethod
    def schema(cls) -> dict:
        return {"kind": cls.kind, "label": cls.label, "description": cls.description, "category": cls.category,
                "native": cls.native, "previewable": cls.previewable, "params": [p.to_dict() for p in cls.params]}

    @classmethod
    def clean(cls, params: dict) -> dict:
        out: Dict[str, Any] = {}
        for p in cls.params:
            out[p.key] = p.clean(params[p.key]) if p.key in params and params[p.key] is not None else p.default
        return cls.clean_extra(out)

    @classmethod
    def clean_extra(cls, params: dict) -> dict:
        return params

    # ---- behavior
    def start(self) -> None:
        """Called once when the effect begins (override to initialize state)."""

    def dt(self, t: float) -> float:
        d = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        return d

    def color(self, key: str) -> RGB:
        return parse_hex(self.p[key])

    def colors(self, key: str) -> List[RGB]:
        return [parse_hex(c) for c in self.p[key]]

    def frame(self, t: float) -> List[RGB]:  # streamed effects
        raise NotImplementedError

    def apply(self, controller) -> None:  # native effects
        raise NotImplementedError


REGISTRY: Dict[str, Type[Effect]] = {}


def register(cls: Type[Effect]) -> Type[Effect]:
    REGISTRY[cls.kind] = cls
    return cls


def get_kind(kind: str) -> Type[Effect]:
    if kind not in REGISTRY:
        raise ParamError(f"unknown effect kind {kind!r}")
    return REGISTRY[kind]


def build(kind: str, params: Optional[dict], led_count: int, seed: Optional[int] = None) -> Effect:
    return get_kind(kind)(params, led_count, seed)


def kinds_schema() -> List[dict]:
    return [cls.schema() for cls in REGISTRY.values()]


def render_preview(kind: str, params: Optional[dict], led_count: int, seconds: float, fps: float,
                   seed: int = 1) -> List[str]:
    """Render frames as hex strings (6 hex digits per LED) for the browser preview."""
    cls = get_kind(kind)
    if not cls.previewable:
        raise ParamError(f"{cls.label} runs on the controller and cannot be previewed here")
    eff = cls(params, led_count, seed)
    frames = []
    count = max(1, min(600, int(seconds * fps)))
    for k in range(count):
        px = eff.frame(k / fps)
        frames.append("".join("%02x%02x%02x" % (int(c[0]) & 255, int(c[1]) & 255, int(c[2]) & 255) for c in px))
    return frames


from . import native, streamed, frames  # noqa: E402,F401  (import registers the kinds)
