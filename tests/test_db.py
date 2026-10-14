import pytest

from tm1814_led import effects as fx
from tm1814_led.db import Conflict, Database, NotFound
from tm1814_led.seed import seed_catalog


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def test_effect_crud_rename_and_duplicate(db):
    e = db.create_effect("Mine", "embers", {"flicker": 80}, "notes")
    assert e["params"]["flicker"] == 80 and e["params"]["block"] == 5
    renamed = db.update_effect(e["id"], name="Renamed")
    assert renamed["name"] == "Renamed" and renamed["params"]["flicker"] == 80
    c1 = db.duplicate_effect(e["id"])
    c2 = db.duplicate_effect(e["id"])
    assert c1["name"] == "Copy of Renamed" and c2["name"] == "Copy of Renamed (2)"
    assert db.duplicate_effect(e["id"], "Custom name")["name"] == "Custom name"
    db.delete_effect(c1["id"])
    with pytest.raises(NotFound):
        db.get_effect(c1["id"])


def test_names_are_unique_case_insensitively_and_required(db):
    db.create_effect("Alpha", "solid", {})
    with pytest.raises(Conflict):
        db.create_effect("ALPHA", "solid", {})
    with pytest.raises(ValueError):
        db.create_effect("   ", "solid", {})
    with pytest.raises(fx.ParamError):
        db.create_effect("Bad", "solid", {"color": "nope"})
    with pytest.raises(fx.ParamError):
        db.create_effect("Bad2", "nokind", {})


def test_runlist_items_order_and_validation(db):
    a, b = db.create_effect("A", "solid", {}), db.create_effect("B", "solid", {})
    rl = db.create_runlist("List", items=[{"effect_id": b["id"], "duration_sec": 30}, {"effect_id": a["id"], "duration_sec": 0.2}])
    assert [i["effect_name"] for i in rl["items"]] == ["B", "A"]
    assert rl["items"][1]["duration_sec"] == 1.0 and rl["total_sec"] == 31.0   # clamped to 1 second minimum
    rl2 = db.update_runlist(rl["id"], items=[{"effect_id": a["id"], "duration_sec": 5}], loop=False, name="List 2")
    assert [i["effect_name"] for i in rl2["items"]] == ["A"] and rl2["loop"] is False
    with pytest.raises(ValueError):
        db.create_runlist("Bad", items=[{"effect_id": 9999, "duration_sec": 5}])
    dup = db.duplicate_runlist(rl["id"])
    assert dup["items"] == rl2["items"] and dup["name"].startswith("Copy of")


def test_effect_in_use_cannot_be_deleted_and_runlist_in_use_cannot_be_deleted(db):
    e = db.create_effect("E", "solid", {})
    rl = db.create_runlist("RL", items=[{"effect_id": e["id"], "duration_sec": 10}])
    with pytest.raises(Conflict):
        db.delete_effect(e["id"])
    s = db.create_schedule("S", rl["id"], {"start_time": "18:00", "end_time": "23:00", "repeat": {"type": "daily"}})
    with pytest.raises(Conflict):
        db.delete_runlist(rl["id"])
    db.delete_schedule(s["id"])
    db.delete_runlist(rl["id"])
    db.delete_effect(e["id"])


def test_schedule_crud_validation_and_duplicate(db):
    e = db.create_effect("E", "solid", {})
    rl = db.create_runlist("RL", items=[{"effect_id": e["id"], "duration_sec": 10}])
    spec = {"start_date": "2026-10-24", "end_date": "2026-10-31", "start_time": "sunset", "end_time": "23:00",
            "repeat": {"type": "daily"}}
    s = db.create_schedule("Halloween", rl["id"], spec, priority=3)
    assert s["spec"]["start_date"] == "2026-10-24" and "sunset" in s["summary"] and s["runlist_name"] == "RL"
    upd = db.update_schedule(s["id"], name="Halloween nights", enabled=False)
    assert upd["name"] == "Halloween nights" and upd["enabled"] is False and upd["priority"] == 3
    with pytest.raises(ValueError):
        db.update_schedule(s["id"], spec={"start_time": "bad"})
    dup = db.duplicate_schedule(s["id"])
    assert dup["enabled"] is False and dup["spec"] == upd["spec"]
    with pytest.raises(NotFound):
        db.create_schedule("X", 999, spec)


def test_settings_defaults_validation_and_persistence(tmp_path):
    path = str(tmp_path / "t.db")
    d = Database(path)
    assert d.settings()["fps"] == 5
    out = d.update_settings({"fps": 99, "controller_host": " 10.0.0.5 ", "latitude": "30.27", "timezone": "America/Chicago",
                             "idle_color": "#F00"})
    assert out["fps"] == 15 and out["controller_host"] == "10.0.0.5" and out["latitude"] == 30.27
    assert out["idle_color"] == "#ff0000"
    for bad in ({"nope": 1}, {"idle_action": "explode"}, {"timezone": "Mars/Base"}, {"fps": "x"}):
        with pytest.raises(ValueError):
            d.update_settings(bad)
    d.close()
    assert Database(path).settings()["controller_host"] == "10.0.0.5"


def test_seed_is_idempotent_and_respects_deletions(db):
    seed_catalog(db)
    n = len(db.list_effects())
    assert n > 40 and {r["name"] for r in db.list_runlists()} >= {"Halloween show", "Built-in favorites"}
    seed_catalog(db)
    assert len(db.list_effects()) == n
    victim = next(e for e in db.list_effects() if e["name"] == "Solid - Red")
    db.delete_effect(victim["id"])
    seed_catalog(db)
    assert not any(e["name"] == "Solid - Red" for e in db.list_effects())     # deleted seeds stay deleted


def test_effects_saved_by_an_older_version_come_back_upgraded(db):
    import json
    e = db.create_effect("Hourly", "nth_step", {})
    old = {"first": 4, "last": 6, "change_minutes": 120, "follow_clock": True}
    db.conn.execute("UPDATE effects SET params = ? WHERE id = ?", (json.dumps(old), e["id"]))
    db.conn.commit()
    got = db.get_effect(e["id"])["params"]
    assert got["change_hours"] == 2 and "change_minutes" not in got
