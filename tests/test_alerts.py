import http.server
import threading

import pytest

from tm1814_led import alerts


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.calls, self.sent = host, port, [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(alerts.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


EMAIL = {"alert_email": "me@example.com, you@example.com", "smtp_host": "mail.example.com", "smtp_port": 587,
         "smtp_user": "me", "smtp_password": "pw", "smtp_tls": True, "smtp_from": "led@example.com", "alert_webhook": ""}


def test_nothing_is_sent_until_a_channel_is_configured(smtp):
    assert alerts.channels({}) == [] and alerts.channels({"alert_email": "a@b.c"}) == []      # an address but no server
    assert alerts.send({}, "s", "b") == (False, []) and smtp.instances == []


def test_email_goes_to_every_recipient_with_starttls_and_login(smtp):
    ok, via = alerts.send(EMAIL, "Controller not responding", "details")
    assert ok and via == ["email"]
    m = smtp.instances[0]
    assert m.calls == ["starttls", ("login", "me", "pw")] and (m.host, m.port) == ("mail.example.com", 587)
    msg = m.sent[0]
    assert msg["Subject"] == "[LED Controller] Controller not responding" and msg["From"] == "led@example.com"
    assert msg["To"] == "me@example.com, you@example.com" and "details" in msg.get_content()


def test_a_plain_relay_needs_no_login(smtp):
    alerts.send({**EMAIL, "smtp_port": 25, "smtp_user": "", "smtp_tls": False}, "s", "b")
    assert smtp.instances[0].calls == [] and smtp.instances[0].sent


def test_a_failing_channel_never_raises_and_others_still_work(monkeypatch):
    class Boom(FakeSMTP):
        def send_message(self, msg):
            raise OSError("relay down")
    monkeypatch.setattr(alerts.smtplib, "SMTP", Boom)
    assert alerts.send(EMAIL, "s", "b") == (False, [])


class Hook(http.server.BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Hook.got.append((self.path, self.headers.get("Title"), body.decode()))
        self.send_response(200); self.end_headers()

    def log_message(self, *a):
        pass


def test_webhook_receives_the_message_with_a_title_header():
    Hook.got = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = {"alert_webhook": f"http://127.0.0.1:{srv.server_port}/lights"}
        assert alerts.send(cfg, "Controller moved", "now at 10.0.0.9") == (True, ["webhook"])
        assert Hook.got == [("/lights", "[LED Controller] Controller moved", "now at 10.0.0.9")]
        assert alerts.channels({**EMAIL, **cfg}) == ["email", "webhook"]
    finally:
        srv.shutdown()


def test_an_unreachable_webhook_is_not_an_error():
    assert alerts.send({"alert_webhook": "http://127.0.0.1:1/x"}, "s", "b") == (False, [])


def test_ntfy_gets_a_priority_and_a_tag_per_kind_and_a_bounded_body():
    Hook.got = []
    seen = []
    orig = Hook.do_POST
    def capture(self):
        seen.append({k: self.headers.get(k) for k in ("Title", "Priority", "Tags")})
        n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n); self.send_response(200); self.end_headers()
    Hook.do_POST = capture
    srv = http.server.HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = {"alert_webhook": f"http://127.0.0.1:{srv.server_port}/t"}
        alerts.send(cfg, "Controller not responding (10 min)", "x" * 10000, kind="down")
        alerts.send(cfg, "Recovered", "ok", kind="recovered")
        alerts.send(cfg, "plain", "ok")
        assert seen[0]["Priority"] == "high" and seen[0]["Tags"] == "warning"
        assert seen[1]["Priority"] == "default" and seen[1]["Tags"] == "white_check_mark"
        assert seen[2]["Priority"] is None                                      # no kind: no ntfy extras
    finally:
        srv.shutdown(); Hook.do_POST = orig


def test_a_push_service_that_says_slow_down_is_reported_not_retried(caplog):
    class Busy(http.server.BaseHTTPRequestHandler):
        hits = 0
        def do_POST(self):
            Busy.hits += 1
            self.send_response(429); self.end_headers()
        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), Busy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with caplog.at_level("WARNING"):
            assert alerts.send({"alert_webhook": f"http://127.0.0.1:{srv.server_port}/t"}, "s", "b", kind="down") == (False, [])
        assert Busy.hits == 1 and "message limit has been reached" in caplog.text
    finally:
        srv.shutdown()


def test_critical_kinds_are_exactly_the_ones_that_matter():
    assert alerts.CRITICAL == {"down", "recovered", "watchdog", "watchdog_recovered"}
    assert set(alerts.LOOK) >= alerts.CRITICAL | {"reminder", "moved", "test"}
