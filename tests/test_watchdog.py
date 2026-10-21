import http.server
import json
import threading
import urllib.error
from pathlib import Path

import pytest

from tm1814_led import watchdog as W
from tm1814_led.db import Database


class Rig:
    def __init__(self, tmp_path):
        self.state = tmp_path / "watchdog_state.json"
        self.db_path = tmp_path / "tm1814.db"
        db = Database(str(self.db_path))
        db.update_settings({"alert_webhook": "http://example/hook"})
        db.close()
        self.t = [5_000_000.0]
        self.live = {"ok": True, "heartbeat": {"runner_sec": 1.0}}
        self.restarts, self.sent = [], []
        self.restart_ok = True

    def fetch(self, url):
        if isinstance(self.live, Exception):
            raise self.live
        return self.live

    def restart(self):
        self.restarts.append(self.t[0])
        return self.restart_ok, "launchctl kickstart -k gui/501/x"

    def send(self, cfg, subject, body, kind=""):
        self.sent.append((cfg.get("alert_webhook"), subject, body))
        return True, ["webhook"]

    def run(self, advance=180):
        self.t[0] += advance
        return W.run_once(url="http://x/live", state_path=self.state, db_path=self.db_path, fetch=self.fetch,
                          restart=self.restart, send=self.send, now=lambda: self.t[0])


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


DOWN = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))


def test_a_healthy_program_needs_nothing(rig):
    r = rig.run()
    assert r == {"ok": True, "problem": "", "actions": []} and rig.restarts == [] and rig.sent == []


def test_the_first_bad_check_waits_the_second_restarts_and_alerts(rig):
    rig.live = DOWN
    first = rig.run()
    assert not first["ok"] and "not answering" in first["problem"] and rig.restarts == [] and rig.sent == []
    second = rig.run()
    assert len(rig.restarts) == 1 and second["restarted"][0] is True
    assert len(rig.sent) == 1 and rig.sent[0][1] == "The LED Controller program is not healthy"
    assert rig.sent[0][0] == "http://example/hook"                         # the alert settings came from the database file
    assert "restarted the service" in rig.sent[0][2] and "even when the program itself is dead" in rig.sent[0][2]


def test_a_stuck_but_answering_program_is_also_a_problem(rig):
    rig.live = {"ok": False, "heartbeat": {"runner_sec": 400.0}}
    rig.run(); r = rig.run()
    assert "a worker is stuck" in r["problem"] and "400.0" in r["problem"] and len(rig.restarts) == 1


def test_alert_is_not_repeated_until_the_reminder_period(rig):
    rig.live = DOWN
    rig.run(); rig.run()
    for _ in range(5):
        rig.run()
    assert len(rig.sent) == 1                                              # one alert, however long it stays down
    rig.run(6 * 3600)
    assert len(rig.sent) == 2 and rig.sent[1][1].startswith("[reminder]")


def test_restarts_are_limited_to_three_an_hour(rig):
    rig.live = DOWN
    for _ in range(8):
        rig.run(180)                                                       # 24 minutes of constant trouble
    assert len(rig.restarts) == 3
    rig.run(3600)
    assert len(rig.restarts) == 4                                          # an hour later it may try again


def test_recovery_sends_one_message_and_resets(rig):
    rig.live = DOWN
    rig.run(); rig.run()
    rig.live = {"ok": True, "heartbeat": {}}
    r = rig.run()
    assert r["ok"] and "recovery message" in r["actions"][0]
    assert rig.sent[-1][1].startswith("Recovered")
    rig.run()
    assert len(rig.sent) == 2                                              # no repeated "recovered"
    rig.live = DOWN
    rig.run()
    assert len(rig.restarts) == 1                                          # a new problem starts counting from zero


def test_a_blip_that_fixes_itself_sends_nothing(rig):
    rig.live = DOWN
    rig.run()
    rig.live = {"ok": True, "heartbeat": {}}
    rig.run()
    assert rig.restarts == [] and rig.sent == []


def test_a_failed_restart_is_reported(rig):
    rig.live, rig.restart_ok = DOWN, False
    rig.run(); rig.run()
    assert "failed" in rig.sent[0][2]


def test_unreadable_database_still_runs_without_alerting(tmp_path):
    rig = Rig(tmp_path)
    rig.db_path.unlink()
    rig.live = DOWN
    rig.run(); rig.run()
    assert len(rig.restarts) == 1 and rig.sent[0][0] is None               # restarted; alert settings empty, nothing delivered


def test_alert_settings_are_read_without_opening_the_database_for_writing(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    db.update_settings({"alert_email": "me@example.com", "smtp_host": "mail", "smtp_password": "secret", "fps": 9})
    cfg = W.read_alert_settings(tmp_path / "x.db")
    assert cfg["alert_email"] == "me@example.com" and cfg["smtp_password"] == "secret" and "fps" not in cfg
    assert W.read_alert_settings(tmp_path / "missing.db") == {}


class Live(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "version": "t"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_fetch_live_reads_the_endpoint():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Live)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert W.fetch_live(f"http://127.0.0.1:{srv.server_port}/api/health/live")["ok"] is True
    finally:
        srv.shutdown()
    with pytest.raises(urllib.error.URLError):
        W.fetch_live("http://127.0.0.1:1/api/health/live", timeout=1)


class Done:
    returncode = 0


def test_restart_commands_per_operating_system(monkeypatch):
    calls = []
    runner = lambda cmd, **k: (calls.append(cmd), Done())[1]               # noqa: E731
    ok, what = W.restart_service(runner, "macos")
    assert ok and calls[-1][:3] == ["launchctl", "kickstart", "-k"] and calls[-1][3].endswith("/com.jewettg.tm1814-led")
    import tm1814_led.installer as I
    monkeypatch.setattr(I, "has_systemd", lambda: True)
    monkeypatch.setattr(Path, "exists", lambda self: False)                # no system-wide unit: it is a user service
    W.restart_service(runner, "linux")
    assert calls[-1] == ["systemctl", "--user", "restart", "tm1814-led.service"]
    monkeypatch.setattr(Path, "exists", lambda self: True)
    W.restart_service(runner, "linux")
    assert calls[-1] == ["systemctl", "restart", "tm1814-led.service"]
    W.restart_service(runner, "windows")
    assert calls[-2][:3] == ["schtasks", "/End", "/TN"] and calls[-1][:3] == ["schtasks", "/Run", "/TN"]
    assert W.restart_service(runner, "plan9") == (False, "no service manager to restart it with")


def test_the_watchdog_alerts_at_most_the_daily_limit_and_not_every_flap(tmp_path):
    rig = Rig(tmp_path)
    db = Database(str(rig.db_path)); db.update_settings({"alert_daily_limit": 4, "alert_min_gap_minutes": 60}); db.close()
    for _ in range(30):                                       # thirty separate outages, each fixed after a restart
        rig.live = DOWN
        rig.run(180); rig.run(180)
        rig.live = {"ok": True, "heartbeat": {}}
        rig.run(180)
    assert 1 <= len(rig.sent) <= 4                            # the limit holds
    downs = [s for s in rig.sent if "not healthy" in s[1]]
    assert len(downs) <= 4


def test_the_watchdog_keeps_its_budget_in_its_own_state_file(rig):
    rig.live = DOWN
    rig.run(); rig.run()
    saved = json.loads(rig.state.read_text())
    assert [k for _, k in saved["budget"]["sent"]] == ["watchdog"] and saved["budget"]["last_down"] > 0
