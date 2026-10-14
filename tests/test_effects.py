import pytest

from tm1814_led import effects as fx

N = 250


def test_every_kind_is_registered_with_a_schema():
    schema = {s["kind"]: s for s in fx.kinds_schema()}
    assert {"solid", "segments", "builtin", "embers", "wave", "comet", "lightning", "twinkle", "rainbow",
            "breathe", "chase", "wipe", "gradient", "frames"} <= set(schema)
    for s in schema.values():
        assert s["label"] and s["category"] and isinstance(s["params"], list)
        assert s["family"] in ("native", "fixed", "animated") and s["category"] in ("Controller-native", "Fixed", "Animated")
        for p in s["params"]:
            assert p["key"] and p["type"] in ("int", "float", "bool", "choice", "color", "colors", "frames")


@pytest.mark.parametrize("kind", [k for k, c in fx.REGISTRY.items() if c.previewable])
def test_every_previewable_effect_renders_valid_frames(kind):
    eff = fx.build(kind, {}, N, seed=7)
    for t in (0.0, 0.2, 1.3, 9.9):
        px = eff.frame(t)
        assert len(px) == N
        for pixel in px:
            assert len(pixel) in (3, 4) and all(0 <= v <= 255 for v in pixel)


@pytest.mark.parametrize("kind", ["embers", "lightning", "twinkle"])
def test_random_effects_are_repeatable_with_a_seed(kind):
    a = fx.render_preview(kind, {}, N, 3, 5, seed=3)
    b = fx.render_preview(kind, {}, N, 3, 5, seed=3)
    assert a == b


def test_render_preview_format_and_limits():
    frames = fx.render_preview("rainbow", {}, N, 2, 5)
    assert len(frames) == 10 and all(len(f) == N * 6 for f in frames)
    assert len(fx.render_preview("rainbow", {}, N, 1000, 15)) == 600   # capped
    with pytest.raises(fx.ParamError):
        fx.render_preview("builtin", {}, N, 2, 5)


def test_param_cleaning_and_clamping():
    e = fx.build("lightning", {"bolts_per_sec": 9999, "min_width": -5, "base": "#ABC"}, N)
    assert e.p["bolts_per_sec"] == 20 and e.p["min_width"] == 0 and e.p["base"] == "#aabbcc"
    assert fx.get_kind("embers").clean({})["block"] == 5          # defaults filled in
    assert "bogus" not in fx.get_kind("embers").clean({"bogus": 1})


@pytest.mark.parametrize("kind,params", [
    ("embers", {"color": "red"}), ("wave", {"direction": "sideways"}), ("builtin", {"pattern": 99}),
    ("segments", {"colors": []}), ("builtin", {"colors": ["#fff"] * 9}), ("nonsense", {}),
])
def test_bad_params_are_rejected(kind, params):
    with pytest.raises(fx.ParamError):
        fx.build(kind, params, N)


def test_choice_accepts_numeric_strings():
    assert fx.get_kind("builtin").clean({"pattern": "8"})["pattern"] == 8


def test_solid_and_segments_frames():
    assert set(fx.build("solid", {"color": "#102030"}, N).frame(0)) == {(16, 32, 48)}
    seg = fx.build("segments", {"colors": ["#ff0000", "#0000ff"]}, N).frame(0)
    assert seg[0] == (255, 0, 0) and seg[13] == (0, 0, 255) and seg[25] == (255, 0, 0)


def test_comet_moves_and_bounce_reverses():
    e = fx.build("comet", {"tail": 5, "speed": 100, "gap": 0, "background": "#000000", "color": "#ffffff"}, 100, seed=1)
    head = lambda t: max(i for i, c in enumerate(e.frame(t)) if c != (0, 0, 0))  # noqa: E731
    assert head(0.5) < head(0.8)
    b = fx.build("comet", {"tail": 5, "speed": 100, "gap": 0, "bounce": True, "background": "#000000",
                           "color": "#ffffff"}, 100, seed=1)
    bright = lambda t: [i for i, c in enumerate(b.frame(t)) if c == max(b.frame(t))][0]  # noqa: E731
    assert bright(1.5) > bright(1.9) or bright(1.5) != bright(1.9)


def test_chase_direction_and_period():
    e = fx.build("chase", {"colors": ["#ff0000", "#0000ff"], "block": 2, "gap": 0, "speed": 1}, 8)
    assert e.frame(0) == [(255, 0, 0)] * 2 + [(0, 0, 255)] * 2 + [(255, 0, 0)] * 2 + [(0, 0, 255)] * 2


def test_wipe_fills_and_clears():
    e = fx.build("wipe", {"period": 10, "color": "#ffffff", "background": "#000000"}, 100)
    assert e.frame(0.0).count((255, 255, 255)) == 0
    assert e.frame(4.7).count((255, 255, 255)) == 100
    assert e.frame(9.9).count((255, 255, 255)) == 0


def test_lightning_black_sky_only_flashes_from_its_colors():
    e = fx.build("lightning", {"base": "#000000", "flash_colors": ["#ffffff"], "big_colors": ["#ffffff"],
                               "big_per_minute": 0}, N, seed=2)
    seen = set()
    for k in range(60):
        seen |= set(e.frame(k * 0.2))
    assert seen <= {(0, 0, 0), (255, 255, 255)} and (255, 255, 255) in seen


# ---- custom frames
DATA = {"background": "#000000", "loop": "loop", "frames": [
    {"duration": 1.0, "transition": "cut", "segments": [{"start": 0, "end": 4, "color": "#ff0000"}]},
    {"duration": 1.0, "transition": "cut", "segments": [{"start": 5, "end": 9, "color": "#0000ff"}]},
]}


def frames_effect(data, n=10):
    return fx.build("frames", {"data": data}, n)


def test_frames_cut_and_loop():
    e = frames_effect(DATA)
    assert e.frame(0.5)[:5] == [(255, 0, 0)] * 5 and e.frame(0.5)[5:] == [(0, 0, 0)] * 5
    assert e.frame(1.5)[5:] == [(0, 0, 255)] * 5
    assert e.frame(2.5)[:5] == [(255, 0, 0)] * 5            # looped


def test_frames_fade_blends_toward_next_frame():
    d = {**DATA, "frames": [{**DATA["frames"][0], "transition": "fade"}, DATA["frames"][1]]}
    mid = frames_effect(d).frame(0.5)
    assert mid[0] == (127, 0, 0) and mid[5] == (0, 0, 127)


def test_frames_gradient_segment_and_once_and_pingpong():
    g = {"background": "#000000", "loop": "once", "frames": [
        {"duration": 1, "transition": "cut", "segments": [{"start": 0, "end": 9, "color": "#000000", "color_end": "#ffffff"}]}]}
    px = frames_effect(g).frame(100)
    assert px[0] == (0, 0, 0) and px[9] == (255, 255, 255) and 100 < px[5][0] < 160
    pp = {**DATA, "loop": "pingpong", "frames": DATA["frames"] + [
        {"duration": 1.0, "transition": "cut", "segments": [{"start": 0, "end": 9, "color": "#00ff00"}]}]}
    e = frames_effect(pp)
    assert e.frame(0.5)[0] == (255, 0, 0) and e.frame(2.5)[0] == (0, 255, 0)
    assert e.frame(3.5)[5] == (0, 0, 255)                   # on the way back through frame 2 (blue)
    assert e.frame(4.5)[0] == (255, 0, 0)                   # and the cycle restarts at frame 1 (the ends are not repeated)


@pytest.mark.parametrize("bad", [
    {"frames": []}, {"frames": [{"duration": 1, "segments": [{"start": -1, "end": 2, "color": "#fff"}]}]},
    {"loop": "sideways", "frames": [{"duration": 1, "segments": []}]},
    {"frames": [{"duration": 1, "transition": "wobble", "segments": []}]},
    {"frames": [{"duration": 1, "segments": [{"start": 0, "end": 2, "color": "blue"}]}]},
])
def test_frames_validation(bad):
    with pytest.raises(fx.ParamError):
        frames_effect(bad)


def test_frames_segment_past_strip_end_is_clipped():
    e = frames_effect({"frames": [{"duration": 1, "segments": [{"start": 8, "end": 400, "color": "#ffffff"}]}]})
    assert len(e.frame(0)) == 10 and e.frame(0)[9] == (255, 255, 255)


# ---- families, fixed and stepping effects
def test_families_and_categories():
    fams = {k: c.family() for k, c in fx.REGISTRY.items()}
    assert fams["solid"] == fams["segments"] == fams["builtin"] == "native"
    assert fams["nth"] == "fixed"
    assert fams["embers"] == fams["lightning"] == fams["frames"] == fams["nth_step"] == "animated"
    assert {c.category for c in fx.REGISTRY.values()} == {"Controller-native", "Fixed", "Animated"}


@pytest.mark.parametrize("every", [3, 4, 5, 6])
def test_every_nth_lights_exactly_those_pixels_with_the_white_led(every):
    px = fx.build("nth", {"every": every}, N).frame(0)
    lit = [i for i, p in enumerate(px) if len(p) == 4]
    assert lit == list(range(0, N, every))
    assert all(p == (0, 0, 0, 100) for p in px if len(p) == 4)
    assert all(p == (0, 0, 0) for p in px if len(p) == 3)
    assert fx.build("nth", {"every": every}, N).frame(99.0) == px            # fixed: the same picture at any time


def test_every_nth_options():
    px = fx.build("nth", {"every": 4, "offset": 2, "use_white": False, "color": "#ff0000", "white_level": 30}, N).frame(0)
    assert [i for i, p in enumerate(px) if p == (255, 0, 0)] == list(range(2, N, 4))
    dim = fx.build("nth", {"every": 5, "white_level": 30}, N).frame(0)
    assert dim[0] == (0, 0, 0, 30) and dim[1] == (0, 0, 0)
    bg = fx.build("nth", {"every": 2, "background": "#101010"}, 10).frame(0)
    assert bg[1] == (16, 16, 16)
    with pytest.raises(fx.ParamError):
        fx.build("nth", {"every": "x"}, N)


def test_stepping_spacing_follows_the_clock_on_the_hour(monkeypatch):
    import datetime as dt
    e = fx.build("nth_step", {}, N)
    def at(h, m=0):
        monkeypatch.setattr(e, "now", lambda: dt.datetime(2026, 10, 13, h, m))
        return sorted(i for i, p in enumerate(e.frame(0)) if len(p) == 4)[:3]
    assert at(0) == [0, 4, 8] and at(0, 59) == [0, 4, 8]              # hour 0 -> every 4th
    assert at(1) == [0, 5, 10]                                        # the next hour -> every 5th
    assert at(2) == [0, 6, 12]
    assert at(3) == [0, 4, 8] and at(23) == [0, 6, 12]                # loops 4, 5, 6, 4, 5, 6 ...
    assert at(10) == [0, 5, 10]


def test_stepping_options_and_elapsed_time_mode():
    e = fx.build("nth_step", {"first": 3, "last": 5, "change_hours": 2, "follow_clock": False}, N)
    spacing = lambda t: [i for i, p in enumerate(e.frame(t)) if len(p) == 4][1]     # noqa: E731
    h = 3600
    assert [spacing(t) for t in (0, 2 * h - 1, 2 * h, 4 * h, 6 * h)] == [3, 3, 4, 5, 3]
    swapped = fx.build("nth_step", {"first": 6, "last": 4, "follow_clock": False}, N)
    assert [i for i, p in enumerate(swapped.frame(0)) if len(p) == 4][1] == 4        # a reversed range still works


def test_stepping_preview_is_fast_and_fixed_preview_is_one_frame():
    frames = fx.render_preview("nth_step", {}, N, 12, 5)
    assert len(frames) == 60 and len(set(frames)) == 3                # shows 4, 5 and 6 within seconds
    assert len(fx.render_preview("nth", {}, N, 20, 5)) == 1


def test_white_led_pixels_preview_as_warm_white():
    frame = fx.render_preview("nth", {"every": 2}, 4, 1, 5)[0]
    assert frame[:6] == "fff1de" and frame[6:12] == "000000"


def test_stepping_can_never_change_more_often_than_hourly():
    spec = next(p for p in fx.REGISTRY["nth_step"].params if p.key == "change_hours")
    assert spec.min == 1 and spec.default == 1 and spec.unit == "hours"
    assert fx.build("nth_step", {"change_hours": 0}, N).p["change_hours"] == 1          # clamped up to one hour
    assert fx.build("nth_step", {"change_hours": 99}, N).p["change_hours"] == 24


def test_two_hour_steps_change_on_even_hours(monkeypatch):
    import datetime as dt
    e = fx.build("nth_step", {"change_hours": 2}, N)
    def at(h):
        monkeypatch.setattr(e, "now", lambda: dt.datetime(2026, 10, 13, h, 30))
        return [i for i, p in enumerate(e.frame(0)) if len(p) == 4][1]
    assert [at(h) for h in (0, 1, 2, 3, 4, 5)] == [4, 4, 5, 5, 6, 6]


def test_effects_saved_with_minutes_are_converted_to_hours():
    assert fx.build("nth_step", {"change_minutes": 60}, N).p["change_hours"] == 1
    assert fx.build("nth_step", {"change_minutes": 180}, N).p["change_hours"] == 3
    assert fx.build("nth_step", {"change_minutes": 10}, N).p["change_hours"] == 1       # sub-hourly becomes hourly
    assert "change_minutes" not in fx.build("nth_step", {"change_minutes": 60}, N).p


def test_the_stepping_preview_explains_that_it_is_fast_forwarded():
    schema = {k["kind"]: k for k in fx.kinds_schema()}
    assert "fast-forwarded" in schema["nth_step"]["preview_note"] and schema["nth"]["preview_note"] == ""
