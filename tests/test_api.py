import time

import pytest
from fastapi.testclient import TestClient

from tm1814_led.api import create_app


@pytest.fixture
def client():
    app = create_app(data_dir=":memory:", simulate=True, start_background=True)
    app.state.db.update_settings({"min_gap_ms": 5, "fps": 15})
    app.state.runner.apply_settings()
    with TestClient(app) as c:
        c.app = app
        yield c


def ids(client):
    return {e["name"]: e["id"] for e in client.get("/api/effects").json()}


def test_status_meta_and_schema(client):
    s = client.get("/api/status").json()
    assert s["state"] == "idle" and s["controller"]["simulated"] is True and s["needs_setup"] is False
    meta = client.get("/api/meta").json()
    assert meta["led_count"] == 250 and "America/Chicago" in meta["timezones"] and len(meta["builtin_patterns"]) == 24
    kinds = client.get("/api/effect-kinds").json()
    assert {k["kind"] for k in kinds} >= {"embers", "lightning", "frames", "builtin"}


def test_effect_lifecycle(client):
    emb = ids(client)["Halloween - Embers"]
    copy = client.post(f"/api/effects/{emb}/duplicate", json={"name": "Hot embers"})
    assert copy.status_code == 201
    cid = copy.json()["id"]
    r = client.put(f"/api/effects/{cid}", json={"name": "Hotter embers", "params": {"flicker": 95, "color": "#ff0000"}})
    assert r.json()["name"] == "Hotter embers" and r.json()["params"]["flicker"] == 95
    assert client.put(f"/api/effects/{cid}", json={"params": {"color": "zzzzzz"}}).status_code == 422
    assert client.put(f"/api/effects/{cid}", json={"name": "Solid - Red"}).status_code == 409
    new = client.post("/api/effects", json={"name": "Brand new", "kind": "wave", "params": {"period": 9}})
    assert new.status_code == 201 and new.json()["params"]["period"] == 9
    assert client.delete(f"/api/effects/{cid}").status_code == 204
    assert client.get(f"/api/effects/{cid}").status_code == 404


def test_preview_endpoint(client):
    r = client.post("/api/preview", json={"kind": "lightning", "params": {}, "seconds": 3, "fps": 5}).json()
    assert len(r["frames"]) == 15 and len(r["frames"][0]) == 250 * 6
    assert client.post("/api/preview", json={"kind": "builtin", "params": {}}).status_code == 422


def test_custom_frames_effect_roundtrip_and_preview(client):
    data = {"background": "#000000", "loop": "pingpong", "frames": [
        {"duration": 0.4, "transition": "fade", "segments": [{"start": 0, "end": 99, "color": "#ff0000", "color_end": "#0000ff"}]},
        {"duration": 0.4, "transition": "cut", "segments": [{"start": 100, "end": 249, "color": "#00ff00"}]},
        {"duration": 0.4, "transition": "cut", "segments": []}]}
    r = client.post("/api/effects", json={"name": "Studio", "kind": "frames", "params": {"data": data}})
    assert r.status_code == 201 and r.json()["params"]["data"]["loop"] == "pingpong"
    pv = client.post("/api/preview", json={"kind": "frames", "params": {"data": data}, "seconds": 2, "fps": 5}).json()
    assert len(pv["frames"]) == 10
    bad = {**data, "frames": []}
    assert client.post("/api/effects", json={"name": "Bad", "kind": "frames", "params": {"data": bad}}).status_code == 422


def test_runlist_and_schedule_lifecycle(client):
    i = ids(client)
    rl = client.post("/api/runlists", json={"name": "Night", "items": [
        {"effect_id": i["Solid - Red"], "duration_sec": 10}, {"effect_id": i["Rainbow"], "duration_sec": 20}]})
    assert rl.status_code == 201 and rl.json()["total_sec"] == 30
    rid = rl.json()["id"]
    sp = {"start_time": "sunset", "end_time": "23:00", "repeat": {"type": "weekly", "weekdays": [4, 5]}}
    sc = client.post("/api/schedules", json={"name": "Weekends", "runlist_id": rid, "spec": sp})
    assert sc.status_code == 201 and "Fri" in sc.json()["summary"]
    prev = client.post("/api/schedules/preview", json={"spec": sp, "count": 4}).json()
    assert len(prev["windows"]) == 4 and prev["sun_estimated"] is True
    client.put("/api/settings", json={"latitude": 30.27, "longitude": -97.74, "timezone": "America/Chicago"})
    assert client.post("/api/schedules/preview", json={"spec": sp}).json()["sun_estimated"] is False
    assert client.post("/api/schedules/preview", json={"spec": {"repeat": {"type": "weekly"}}}).status_code == 422
    assert client.delete(f"/api/runlists/{rid}").status_code == 409          # in use by the schedule
    dup = client.post(f"/api/schedules/{sc.json()['id']}/duplicate").json()
    assert dup["name"] == "Copy of Weekends" and dup["enabled"] is False
    assert client.delete(f"/api/schedules/{dup['id']}").status_code == 204


def test_control_play_stop_power_color_brightness(client):
    i = ids(client)
    rl = client.post("/api/runlists", json={"name": "L", "items": [{"effect_id": i["Rainbow"], "duration_sec": 30}]}).json()
    assert client.post("/api/control/play", json={"runlist_id": rl["id"]}).json()["state"] == "running"
    deadline = time.time() + 5
    while time.time() < deadline and not (client.get("/api/status").json()["item"] or {}).get("name"):
        time.sleep(0.05)
    assert client.get("/api/status").json()["item"]["name"] == "Rainbow"
    client.post("/api/control/pause")
    assert client.get("/api/status").json()["state"] == "paused"
    client.post("/api/control/resume")
    client.post("/api/control/stop")
    time.sleep(0.5)
    assert client.get("/api/status").json()["state"] == "idle"
    assert client.post("/api/control/color", json={"color": "#00ff00"}).json()["plan"] == "Solid #00ff00"
    assert client.post("/api/control/color", json={"color": "green"}).status_code == 422
    assert client.post("/api/control/brightness", json={"value": 40}).json()["brightness"] == 40
    assert client.app.state.controller.brightness == 40
    client.post("/api/control/power", json={"on": False})
    assert client.get("/api/status").json()["state"] == "idle" and client.app.state.controller.is_on is False
    assert client.post("/api/control/play", json={}).status_code == 422
    assert client.post("/api/control/play", json={"kind": "solid", "params": {"color": "#112233"}}).status_code == 200


def test_settings_roundtrip_and_validation(client):
    s = client.put("/api/settings", json={"fps": 9, "idle_action": "leave", "seeded_keys": ["hack"]}).json()
    assert s["fps"] == 9 and s["idle_action"] == "leave" and "seeded_keys" not in s
    assert client.put("/api/settings", json={"timezone": "Nowhere/Land"}).status_code == 422
    assert client.put("/api/settings", json={"unknown": 1}).status_code == 422


def test_controller_state_and_logs(client):
    assert client.get("/api/controller/state").json()["reachable"] is True
    client.post("/api/control/color", json={"color": "#ffffff"})
    time.sleep(0.4)
    assert any("playing" in l["msg"] for l in client.get("/api/logs").json())


def test_needs_setup_when_no_controller_configured():
    app = create_app(data_dir=":memory:", simulate=False, start_background=False)
    with TestClient(app) as c:
        assert c.get("/api/status").json()["needs_setup"] is True
        assert c.get("/api/controller/state").json()["reachable"] is False


def test_effect_sections(client):
    emb = ids(client)["Halloween - Embers"]
    assert client.get("/api/sections").json() == []
    assert client.get(f"/api/effects/{emb}").json()["section"] == ""
    assert client.post("/api/sections", json={"name": "  Halloween  "}).json() == ["Halloween"]
    # moving by a differently-cased name uses the stored spelling; a new name creates the section
    r = client.put(f"/api/effects/{emb}", json={"section": "halloween"}).json()
    assert r["section"] == "Halloween"
    assert client.put(f"/api/effects/{emb}", json={"section": "Fall"}).json()["section"] == "Fall"
    assert client.get("/api/sections").json() == ["Halloween", "Fall"]
    assert client.post(f"/api/effects/{emb}/duplicate", json={"name": "Copy"}).json()["section"] == "Fall"
    assert client.post("/api/sections/rename", json={"name": "Fall", "new_name": "Autumn"}).json() == ["Halloween", "Autumn"]
    assert client.get(f"/api/effects/{emb}").json()["section"] == "Autumn"
    assert client.post("/api/sections/rename", json={"name": "Autumn", "new_name": "halloween"}).status_code == 409
    assert client.post("/api/sections/delete", json={"name": "Autumn"}).json() == ["Halloween"]
    assert client.get(f"/api/effects/{emb}").json()["section"] == ""   # back to the built-in group, not deleted
    assert client.put(f"/api/effects/{emb}", json={"section": "x" * 61}).status_code == 422
