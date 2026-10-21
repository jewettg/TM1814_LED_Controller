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

    esmtp_features = {"auth": "LOGIN PLAIN XOAUTH2"}
    user = password = None

    def ehlo_or_helo_if_needed(self):
        pass

    def auth_plain(self, challenge=None):
        return ""

    auth_login = auth_plain

    def auth(self, mechanism, authobject, initial_response_ok=True):
        self.calls.append(("login", self.user, self.password))

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


def test_send_detailed_can_target_one_channel_and_explains_failures(smtp, monkeypatch):
    cfg = {**EMAIL, "alert_webhook": "http://127.0.0.1:1/x"}
    delivered, failed = alerts.send_detailed(cfg, "s", "b", only="email")
    assert delivered == ["email"] and failed == {} and len(smtp.instances) == 1          # the webhook was not even tried
    delivered, failed = alerts.send_detailed(cfg, "s", "b", only="webhook")
    assert delivered == [] and "webhook" in failed and len(smtp.instances) == 1
    delivered, failed = alerts.send_detailed(cfg, "s", "b")
    assert delivered == ["email"] and list(failed) == ["webhook"]


def test_failure_reasons_are_human_readable(monkeypatch):
    import smtplib as sm
    class Refuse(FakeSMTP):
        def auth(self, mechanism, authobject, initial_response_ok=True):
            raise sm.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted. Learn more at https://support.google.com/mail")
    monkeypatch.setattr(alerts.smtplib, "SMTP", Refuse)
    _, failed = alerts.send_detailed(EMAIL, "s", "b")
    assert failed["email"].startswith("the mail server says the user name or password is wrong (535): 5.7.8 Username and Password not accepted")
    class Slow(FakeSMTP):
        def __init__(self, *a, **k):
            raise TimeoutError("timed out")
    monkeypatch.setattr(alerts.smtplib, "SMTP", Slow)
    _, failed = alerts.send_detailed(EMAIL, "s", "b")
    assert "did not answer in time" in failed["email"]


def test_gmail_asking_for_an_app_password_is_explained_plainly(monkeypatch):
    import smtplib as sm
    class Gmail(FakeSMTP):
        def auth(self, mechanism, authobject, initial_response_ok=True):
            assert mechanism == "PLAIN"                              # only ONE method is tried, so the real refusal is shown
            raise sm.SMTPAuthenticationError(534, b"5.7.9 Application-specific password required. Learn more")
    monkeypatch.setattr(alerts.smtplib, "SMTP", Gmail)
    _, failed = alerts.send_detailed(EMAIL, "s", "b")
    assert "APP PASSWORD" in failed["email"] and "apppasswords" in failed["email"] and "2-Step Verification" in failed["email"]


def test_a_wrong_password_is_reported_as_such(monkeypatch):
    import smtplib as sm
    class Strict(FakeSMTP):
        def auth(self, mechanism, authobject, initial_response_ok=True):
            raise sm.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    monkeypatch.setattr(alerts.smtplib, "SMTP", Strict)
    _, failed = alerts.send_detailed(EMAIL, "s", "b")
    assert "user name or password is wrong (535)" in failed["email"]
