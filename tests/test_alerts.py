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
