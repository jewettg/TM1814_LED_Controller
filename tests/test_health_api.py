import time

import pytest
from fastapi.testclient import TestClient

from tm1814_led import alerts as alerts_mod
from tm1814_led import api as api_module
from tm1814_led.api import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_module.time, "sleep", lambda s: None)
    app = create_app(data_dir=":memory:", simulate=True, password="s3cretpw", start_background=False)
    with TestClient(app, follow_redirects=False) as c:
        assert c.post("/api/login", json={"password": "s3cretpw"}).status_code == 200
        c.app = app
        yield c


def test_liveness_is_public_secret_free_and_reports_heartbeats(client):
    anon = TestClient(client.app, follow_redirects=False)
    assert anon.get("/api/status").status_code == 401                      # everything else needs the password
    r = anon.get("/api/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and set(body["heartbeat"]) == {"runner_sec", "schedules_sec", "monitor_sec"}
    assert set(body) == {"ok", "version", "heartbeat", "uptime_sec"}       # nothing about the controller or settings


def test_liveness_says_not_ok_when_a_thread_is_silent(client):
    client.app.state.runner.beat = time.monotonic() - 1000
    assert TestClient(client.app).get("/api/health/live").json()["ok"] is False


def test_health_and_incidents_need_a_session(client):
    anon = TestClient(client.app, follow_redirects=False)
    for path in ("/api/health", "/api/incidents"):
        assert anon.get(path).status_code == 401
    h = client.get("/api/health").json()
    assert h["state"] == "unknown" and h["incidents"] == [] and h["alert_channels"] == []
    assert "health" in client.get("/api/status").json() and client.get("/api/incidents").json() == []


def test_recover_endpoint_runs_a_search(client):
    r = client.post("/api/health/recover").json()
    assert set(r) == {"moved", "host", "was", "state", "mac_known"} and r["moved"] is False


def test_test_alert_needs_a_channel_and_reports_delivery(client, monkeypatch):
    assert client.post("/api/health/test-alert").status_code == 422
    client.put("/api/settings", json={"alert_webhook": "http://example/hook"})
    monkeypatch.setattr(alerts_mod, "send", lambda cfg, s, b, kind="": (True, ["webhook"]))
    assert client.post("/api/health/test-alert").json() == {"sent": True, "channels": ["webhook"]}
    monkeypatch.setattr(alerts_mod, "send", lambda cfg, s, b, kind="": (False, []))
    r = client.post("/api/health/test-alert")
    assert r.status_code == 422 and "could not be delivered" in r.json()["detail"]


def test_the_smtp_password_is_write_only(client):
    client.put("/api/settings", json={"smtp_host": "mail", "smtp_user": "me", "smtp_password": "hunter2hunter2"})
    got = client.get("/api/settings").json()
    assert got["smtp_password_set"] is True and "smtp_password" not in got and "hunter2" not in str(got)
    client.put("/api/settings", json={"smtp_port": 587})                    # not sent: the saved one is kept
    assert client.app.state.db.settings()["smtp_password"] == "hunter2hunter2"
    client.put("/api/settings", json={"smtp_password": None})               # null: also kept
    assert client.app.state.db.settings()["smtp_password"] == "hunter2hunter2"
    client.put("/api/settings", json={"smtp_password": ""})                 # an empty string clears it
    assert client.get("/api/settings").json()["smtp_password_set"] is False


def test_changing_the_controller_address_forgets_the_old_mac(client):
    client.put("/api/settings", json={"controller_host": "192.168.1.50", "controller_mac": "aa:bb:cc:dd:ee:ff"})
    assert client.get("/api/settings").json()["controller_mac"] == "AABBCCDDEEFF"
    client.put("/api/settings", json={"controller_host": "192.168.1.50", "fps": 6})              # same address: keep it
    assert client.get("/api/settings").json()["controller_mac"] == "AABBCCDDEEFF"
    client.put("/api/settings", json={"controller_host": "192.168.1.60"})                          # a different device
    assert client.get("/api/settings").json()["controller_mac"] == ""
    client.put("/api/settings", json={"controller_host": "192.168.1.70", "controller_mac": "112233445566"})
    assert client.get("/api/settings").json()["controller_mac"] == "112233445566"                  # explicit wins


def test_new_settings_are_validated(client):
    assert client.put("/api/settings", json={"controller_mac": "not-a-mac"}).status_code == 422
    assert client.put("/api/settings", json={"alert_webhook": "ftp://x"}).status_code == 422
    s = client.put("/api/settings", json={"health_check_sec": 1, "alert_after_minutes": 0, "alert_reminder_hours": 9999}).json()
    assert (s["health_check_sec"], s["alert_after_minutes"], s["alert_reminder_hours"]) == (10, 1, 168)


def test_the_status_carries_a_short_health_summary(client):
    client.put("/api/settings", json={"controller_host": "simulated"})
    client.app.state.monitor.tick()
    h = client.get("/api/status").json()["health"]
    assert h["state"] == "online" and set(h) >= {"state", "since", "last_ok", "failures", "unreachable_for_sec", "diagnosis", "host"}
