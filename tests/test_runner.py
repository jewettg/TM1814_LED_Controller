import datetime as dt
import time
from zoneinfo import ZoneInfo

import pytest

from tm1814_led.controller import SimulatedController
from tm1814_led.db import Database
from tm1814_led.runner import Runner, ScheduleManager
from tm1814_led import protocol as P

TZ = ZoneInfo("America/Chicago")


@pytest.fixture
def rig():
    db = Database(":memory:")
    db.update_settings({"min_gap_ms": 5, "fps": 15, "timezone": "America/Chicago", "latitude": 30.27, "longitude": -97.74})
    ctrl = SimulatedController("sim")
    runner = Runner(db, ctrl)
    manager = ScheduleManager(db, runner)
    runner.start()
    yield db, ctrl, runner, manager
    runner.shutdown()
    db.close()


def wait_for(cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def make_list(db, loop=True, durations=(1.0, 1.0)):
    solid = db.create_effect("Red", "solid", {"color": "#ff0000"})
    emb = db.create_effect("Embers", "embers", {})
    return db.create_runlist("RL", loop=loop, items=[{"effect_id": solid["id"], "duration_sec": durations[0]},
                                                      {"effect_id": emb["id"], "duration_sec": durations[1]}])


def test_plays_items_in_order_and_loops(rig):
    db, ctrl, runner, _ = rig
    rl = make_list(db)
    runner.play_runlist(rl["id"])
    assert wait_for(lambda: runner.status()["item"] and runner.status()["item"]["name"] == "Red")
    assert wait_for(lambda: runner.status()["item"] and runner.status()["item"]["name"] == "Embers", 4)
    assert wait_for(lambda: runner.status()["item"] and runner.status()["item"]["name"] == "Red", 4)   # looped
    assert runner.status()["state"] == "running" and runner.status()["source"] == "manual"
    assert P.power_command(True) in ctrl.sent


def test_native_effect_sends_one_command_streamed_sends_many(rig):
    db, ctrl, runner, _ = rig
    rl = make_list(db, durations=(1.5, 1.5))
    runner.play_runlist(rl["id"])
    assert wait_for(lambda: runner.status()["item"] and runner.status()["item"]["name"] == "Red")
    time.sleep(0.8)
    solid_cmds = [s for s in ctrl.sent if s[10:12] == bytes([0xE1, 0x22])]
    assert len(solid_cmds) == 1                          # held by the controller: not resent every frame
    assert wait_for(lambda: runner.status()["item"]["name"] == "Embers", 4)
    time.sleep(1.0)
    assert len([s for s in ctrl.sent if s[10:12] == bytes([0xE1, 0x26])]) > 3


def test_stop_applies_idle_action_off_and_leave(rig):
    db, ctrl, runner, _ = rig
    runner.play_runlist(make_list(db)["id"])
    assert wait_for(lambda: runner.status()["state"] == "running")
    runner.stop()
    assert runner.status()["state"] == "idle" and ctrl.is_on is False
    db.update_settings({"idle_action": "solid", "idle_color": "#00ff00"})
    runner.apply_settings()
    runner.play_effect(db.list_effects()[0])
    time.sleep(0.3)
    runner.stop()
    assert ctrl.is_on is True and ctrl.sent[-1][10:12] == bytes([0xE1, 0x22])


def test_next_pause_resume(rig):
    db, ctrl, runner, _ = rig
    runner.play_runlist(make_list(db, durations=(30, 30))["id"])
    assert wait_for(lambda: runner.status()["item"] and runner.status()["item"]["name"] == "Red")
    runner.next()
    assert wait_for(lambda: runner.status()["item"]["name"] == "Embers", 3)
    runner.pause(True)
    time.sleep(0.4)
    before = runner.status()["item"]["elapsed"]
    time.sleep(0.8)
    assert runner.status()["state"] == "paused" and runner.status()["item"]["elapsed"] == before
    runner.pause(False)
    assert wait_for(lambda: runner.status()["item"]["elapsed"] > before, 3)


def test_non_looping_run_list_ends_and_goes_idle(rig):
    db, ctrl, runner, _ = rig
    ended = []
    runner.on_end = lambda plan, reason: ended.append(reason)
    runner.play_runlist(make_list(db, loop=False, durations=(1, 1))["id"])
    assert wait_for(lambda: runner.status()["state"] == "idle", 8)
    assert "finished" in ended and ctrl.is_on is False


def test_new_play_replaces_current(rig):
    db, ctrl, runner, _ = rig
    rl = make_list(db, durations=(30, 30))
    runner.play_runlist(rl["id"])
    assert wait_for(lambda: runner.status()["state"] == "running")
    e = db.create_effect("Blue", "solid", {"color": "#0000ff"})
    runner.play_effect(e, None)
    assert wait_for(lambda: runner.status()["plan"] == "Blue")
    assert wait_for(lambda: (runner.status()["item"] or {}).get("name") == "Blue")
    assert runner.status()["item"]["duration"] is None


def test_empty_run_list_cannot_play(rig):
    db, _, runner, _ = rig
    empty = db.create_runlist("Empty")
    with pytest.raises(ValueError):
        runner.play_runlist(empty["id"])


# ---- schedules (the manager is driven with a fixed clock; no thread timing involved)
def sched(db, rl, **kw):
    spec = {"start_time": "18:00", "end_time": "23:00", "repeat": {"type": "daily"}}
    spec.update(kw.pop("spec", {}))
    return db.create_schedule(kw.pop("name", "S"), rl["id"], spec, **kw)


def now_at(h, m=0, day=13):
    return dt.datetime(2026, 10, day, h, m, tzinfo=TZ)


def test_schedule_starts_in_window_and_stops_after(rig):
    db, ctrl, runner, mgr = rig
    s = sched(db, make_list(db, durations=(60, 60)))
    mgr.evaluate(now_at(12))
    assert runner.status()["state"] == "idle"
    mgr.evaluate(now_at(19))
    assert runner.status()["source"] == f"schedule:{s['id']}"
    assert wait_for(lambda: runner.status()["item"] and runner.status()["item"]["name"] == "Red")
    mgr.evaluate(now_at(23, 5))
    assert runner.status()["state"] == "idle"


def test_user_stop_is_not_undone_by_the_schedule_within_the_same_window(rig):
    db, ctrl, runner, mgr = rig
    sched(db, make_list(db, durations=(60, 60)))
    mgr.evaluate(now_at(19))
    assert runner.status()["state"] == "running"
    mgr.suppress_active(now_at(19, 1))
    runner.stop()
    mgr.evaluate(now_at(19, 2))
    assert runner.status()["state"] == "idle"
    mgr.evaluate(now_at(19, 0, day=14))                 # next day's window starts normally
    assert runner.status()["state"] == "running"


def test_manual_play_is_not_preempted_by_a_schedule(rig):
    db, ctrl, runner, mgr = rig
    rl = make_list(db, durations=(60, 60))
    sched(db, rl)
    e = db.create_effect("Blue", "solid", {"color": "#0000ff"})
    runner.play_effect(e, None)
    mgr.evaluate(now_at(19))
    assert runner.status()["plan"] == "Blue" and runner.status()["source"] == "manual"


def test_higher_priority_schedule_wins_and_disabled_ignored(rig):
    db, ctrl, runner, mgr = rig
    a = make_list(db, durations=(60, 60))
    other = db.create_effect("Other", "solid", {"color": "#00ff00"})
    b = db.create_runlist("RL2", items=[{"effect_id": other["id"], "duration_sec": 60}])
    low = sched(db, a, name="low")
    high = sched(db, b, name="high", priority=5)
    off = sched(db, a, name="off", priority=99, enabled=False)
    mgr.evaluate(now_at(20))
    assert runner.status()["source"] == f"schedule:{high['id']}" and runner.status()["plan"] == "RL2"


def test_finished_schedule_run_is_not_restarted_in_same_window(rig):
    db, ctrl, runner, mgr = rig
    sched(db, make_list(db, loop=False, durations=(1, 1)))
    mgr.evaluate(now_at(19))
    assert wait_for(lambda: runner.status()["state"] == "idle", 8)
    mgr.evaluate(now_at(19, 30))
    assert runner.status()["state"] == "idle"


def test_editing_the_run_list_restarts_the_scheduled_run(rig):
    db, ctrl, runner, mgr = rig
    rl = make_list(db, durations=(60, 60))
    sched(db, rl)
    mgr.evaluate(now_at(19))
    first = runner.plan_info()["sig"]
    time.sleep(0.01)
    db.update_runlist(rl["id"], notes="changed")
    mgr.evaluate(now_at(19, 1))
    assert runner.plan_info()["sig"] != first


def test_upcoming_lists_next_windows(rig):
    db, ctrl, runner, mgr = rig
    sched(db, make_list(db), name="Nightly")
    up = mgr.upcoming(now_at(12))
    assert up and up[0]["name"] == "Nightly" and up[0]["start"].startswith("2026-10-13T18:00")
