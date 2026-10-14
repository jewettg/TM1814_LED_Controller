import socket
import threading

import pytest

from tm1814_led import protocol as P
from tm1814_led.controller import Controller, ControllerError, SimulatedController, group_by_color, quantize

STATE = bytes.fromhex("ea810100b61523256550f0b464643c1d0500fa0000002002000003")


def test_quantize_keeps_zero_and_never_rounds_nonzero_to_zero():
    assert quantize((0, 3, 255), 16) == (0, 16, 255)
    assert quantize((10, 20, 30), 1) == (10, 20, 30)


def test_group_by_color_coarsens_until_limit():
    items = {i: (i, 0, 0) for i in range(200)}
    groups = group_by_color(items, 6)
    assert len(groups) <= 6
    assert sum(len(v) for v in groups.values()) == 200


def test_group_by_color_exact_when_few_colors():
    items = {0: (90, 10, 0), 1: (90, 10, 0), 2: (0, 0, 0)}
    groups = group_by_color(items, 10)
    assert groups == {(90, 10, 0): [0, 1], (0, 0, 0): [2]}


def make_sim(**kw):
    c = SimulatedController("sim", min_gap=0, **kw)
    return c


def test_first_frame_is_full_then_only_changes_are_sent():
    c = make_sim()
    frame = [(255, 0, 0)] * 250
    first = c.show_frame(frame)
    assert first == 1                                  # one color -> one paint command
    assert c.show_frame(frame) == 0                    # nothing changed -> nothing sent
    changed = list(frame)
    changed[10] = changed[11] = (0, 0, 255)
    assert c.show_frame(changed) == 1                  # only the two changed pixels, one command
    last = c.sent[-1]
    inner = last[10:-1]
    assert inner[:2] == bytes([0xE1, 0x26]) and inner[13] == 0b00000000 and inner[14] == 0b00110000


def test_scribble_init_sent_once_and_after_stop_frames():
    c = make_sim()
    c.show_frame([(1, 1, 1)] * 250)
    inits = [s for s in c.sent if s[10:12] == bytes([0xE1, 0x23])]
    assert len(inits) == 1
    c.stop_frames()
    c.show_frame([(1, 1, 1)] * 250)
    assert len([s for s in c.sent if s[10:12] == bytes([0xE1, 0x23])]) == 2


def test_brightness_scales_colors():
    c = make_sim()
    c.brightness = 50
    c.solid((200, 100, 0))
    inner = c.sent[-1][10:-1]
    assert inner[7:12] == bytes(P.hsv_bytes((100, 50, 0)))


def test_refuses_forbidden_inner_messages():
    c = make_sim()
    with pytest.raises(ControllerError):
        c.send_inner(bytes([0x62, 0x01]))


def test_led_count_uses_override_then_detected():
    c = make_sim()
    assert c.led_count == 250
    c.configured_led_count = 100
    assert c.led_count == 100
    c.configured_led_count = 0
    c.detected_led_count = 300
    assert c.led_count == 255  # the E1 commands carry the count in one byte


def test_unconfigured_controller_raises():
    with pytest.raises(ControllerError):
        Controller("").query_state()


class FakeController:
    """A tiny TCP server that answers the plain state query with a real state frame and records everything else."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]
        self.received = []
        self.threads = []
        t = threading.Thread(target=self._serve, daemon=True)
        t.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                self.received.append(data)
                if data.startswith(P.STATE_QUERY):
                    conn.sendall(STATE)
        except OSError:
            pass

    def close(self):
        self.srv.close()


def test_real_socket_state_query_and_commands():
    fake = FakeController()
    try:
        c = Controller("127.0.0.1", fake.port, min_gap=0)
        st = c.query_state()
        assert st and st.led_count == 250 and c.detected_led_count == 250
        c.power(False)
        c.solid((255, 0, 0))
        import time
        deadline = time.time() + 5
        while time.time() < deadline and not (P.power_command(False) in fake.received
                                              and any(r.startswith(P.ENVELOPE_MAGIC) for r in fake.received)):
            time.sleep(0.05)
        assert P.power_command(False) in fake.received
        assert any(r.startswith(P.ENVELOPE_MAGIC) for r in fake.received)
        c.close()
        assert not c.connected
    finally:
        fake.close()


def test_connection_refused_reports_error():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = Controller("127.0.0.1", port, timeout=1)
    with pytest.raises(ControllerError):
        c.query_state()
    assert "connect failed" in c.last_error


def test_white_led_pixels_are_painted_in_white_mode_and_cost_one_command():
    c = SimulatedController("sim", min_gap=0)
    frame = [(0, 0, 0, 100) if i % 3 == 0 else (0, 0, 0) for i in range(250)]
    assert c.show_frame(frame) == 2                                  # one command for the white pixels, one for black
    paints = [s[10:-1] for s in c.sent if s[10:12] == bytes([0xE1, 0x26])]
    white = next(p for p in paints if p[11] == 100)                  # the white-level byte of the E1 26 header
    assert white[7:10] == bytes([0, 0, 0]) and white[13] & 0b10000000   # color off, pixel 0 selected
    assert c.show_frame(frame) == 0                                  # nothing changed, nothing sent


def test_white_pixels_follow_master_brightness_and_are_never_quantized():
    c = SimulatedController("sim", min_gap=0)
    c.brightness = 50
    c.show_frame([(0, 0, 0, 100)] * 250)
    paint = [s[10:-1] for s in c.sent if s[10:12] == bytes([0xE1, 0x26])][-1]
    assert paint[11] == 50
    assert quantize((0, 0, 0, 37), 64) == (0, 0, 0, 37)


class DroppingController(FakeController):
    """Closes each client connection after `drop_after` bytes arrive, like a controller that drops idle connections."""

    def __init__(self):
        self.connections = 0
        self.conns = []
        super().__init__()

    def _client(self, conn):
        self.connections += 1
        self.conns.append(conn)
        super()._client(conn)

    def drop_all(self):
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR); c.close()
            except OSError:
                pass
        self.conns = []


def test_an_idle_connection_closed_by_the_controller_is_replaced_without_an_error():
    import time
    fake = DroppingController()
    try:
        c = Controller("127.0.0.1", fake.port, min_gap=0)
        c.power(True)
        deadline = time.time() + 3
        while time.time() < deadline and not fake.received:
            time.sleep(0.02)
        assert fake.connections == 1
        fake.received.clear()
        fake.drop_all()                              # the controller hangs up while we are idle
        time.sleep(0.3)
        for i in range(3):                           # several sends in a row, none may raise
            c.power(i % 2 == 0)
        deadline = time.time() + 5
        while time.time() < deadline and len(fake.received) < 3:
            time.sleep(0.05)
        assert fake.connections == 2                 # exactly one reconnect
        assert len(fake.received) == 3 and c.last_error == ""
    finally:
        fake.close()
