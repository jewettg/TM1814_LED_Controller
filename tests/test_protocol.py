import pytest

from tm1814_led import protocol as P

# Byte strings below were captured from / verified on a real 0xB6 controller (see TECHNICAL_README.md).
REAL_STATE = bytes.fromhex("ea810100b61523256550f0b464643c1d0500fa0000002002000003")
REAL_WRAPPED_STATE = bytes.fromhex("b0b1b2b300010269001bea810100b61523256550f0b464643c1d0500fa00000020020000036a")


def test_checksum_and_power_commands():
    assert P.power_command(False).hex() == "71240fa4"
    assert P.power_command(True).hex() == "71230fa3"


def test_envelope_matches_verified_green_command():
    inner = bytes.fromhex("e00100a13c6464000000001400" + "00")
    assert P.wrap(inner, 0).hex() == "b0b1b2b300010200000ee00100a13c64640000000014000071"


def test_unwrap_real_reply_and_state():
    envs, rest = P.unwrap(REAL_WRAPPED_STATE)
    assert rest == b"" and len(envs) == 1
    version, counter, inner = envs[0]
    assert (version, counter) == (2, 0x69)
    assert P.parse_state(inner).led_count == 250


def test_unwrap_ignores_bad_checksum_and_keeps_partial():
    bad = bytearray(REAL_WRAPPED_STATE)
    bad[-1] ^= 0xFF
    assert P.unwrap(bytes(bad))[0] == []
    envs, rest = P.unwrap(REAL_WRAPPED_STATE + REAL_WRAPPED_STATE[:12])
    assert len(envs) == 1 and rest.startswith(P.ENVELOPE_MAGIC)


def test_parse_state_real_frame():
    st = P.parse_state(REAL_STATE)
    assert st.power is True and st.model == 0xB6 and st.version == 21
    assert (st.hue, st.saturation, st.value) == (360, 100, 100)
    assert st.led_count == 250 and st.rgb_mode is True


def test_parse_state_rejects_garbage():
    assert P.parse_state(b"") is None
    assert P.parse_state(b"\xea\x81\x01") is None


def test_solid_message_layout():
    m = P.solid_message((255, 0, 0))
    assert m[:7] == bytes([0xE1, 0x22, 0, 0, 0, 0, 20]) and len(m) == 7 + 5 * 20
    assert m[7:12] == bytes([0, 100, 100, 0, 0])
    w = P.solid_message(white=50)
    assert w[7:12] == bytes([0, 100, 0, 0, 50])


def test_segments_needs_twenty():
    with pytest.raises(ValueError):
        P.segments_message([(0, 0, 0)] * 19)
    assert len(P.segments_message([(1, 2, 3)] * 20)) == 7 + 100


def test_builtin_effect_layout():
    m = P.builtin_effect_message(8, [(255, 0, 0), (0, 0, 255)], speed=40, density=60, direction=2, option=1)
    assert m[:2] == bytes([0xE1, 0x21]) and m[4] == 8 and m[5] == 1 and m[6] == 2
    assert m[7] == 60 and m[8] == 40 and m[15] == 2 and len(m) == 16 + 10
    with pytest.raises(ValueError):
        P.builtin_effect_message(8, [], 1, 1)


def test_scribble_init_and_paint():
    init = P.scribble_init_message(250)
    assert init[:9] == bytes([0xE1, 0x23, 1, 0, 1, 0x50, 0x64, 0, 250]) and len(init) == 9 + 7 * 250
    paint = P.scribble_paint_message([0, 2, 249], 250, rgb=(0, 0, 255))
    assert paint[:2] == bytes([0xE1, 0x26]) and len(paint) == 13 + 32
    assert paint[13] == 0b10100000 and paint[13 + 31] == 0b01000000
    with pytest.raises(ValueError):
        P.scribble_init_message(256)
    with pytest.raises(ValueError):
        P.scribble_paint_message([250], 250, rgb=(0, 0, 0))


def test_hsv_bytes():
    assert P.hsv_bytes((255, 0, 0)) == [0, 100, 100, 0, 0]
    assert P.hsv_bytes((0, 255, 0))[0] == 60
    assert P.hsv_bytes((0, 0, 255))[0] == 120
    assert P.hsv_bytes((90, 10, 0)) == [3, 100, 35, 0, 0]


def test_color_helpers():
    assert P.parse_hex("#ff5a00") == (255, 90, 0) and P.parse_hex("f00") == (255, 0, 0)
    assert P.to_hex((255, 90, 0)) == "#ff5a00"
    with pytest.raises(ValueError):
        P.parse_hex("#12345")
    assert P.scale_rgb((200, 100, 50), 50) == (100, 50, 25)


def test_forbidden_commands():
    assert P.is_forbidden(bytes([0x62, 1, 2]))
    assert P.is_forbidden(bytes([0xE0, 0x43, 1]))
    assert not P.is_forbidden(P.solid_message((1, 2, 3)))


def test_discovery_reply():
    d = P.parse_discovery_reply(b"192.168.1.50,AABBCCDDEEFF,AK001-ZJ21413,abc")
    assert d == {"ip": "192.168.1.50", "mac": "AABBCCDDEEFF", "model": "AK001-ZJ21413", "token": "abc"}
    assert P.parse_discovery_reply(b"junk") is None
