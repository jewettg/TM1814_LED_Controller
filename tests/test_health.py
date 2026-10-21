import threading
import time

import pytest

from tm1814_led import alerts, health, netscan
from tm1814_led.controller import ControllerError, SimulatedController
from tm1814_led.db import Database
from tm1814_led.health import ConnectionMonitor
from tm1814_led.protocol import State

OLD, NEW, MAC = "10.0.0.5", "10.0.0.9", "AABBCCDDEEFF"
STATE = State(power=True, model=0xB6, version=21, pattern=0x61, speed=1, rgb_mode=True, hue=0, saturation=0, value=0,
              led_count=250)


class FakeController:
    """Stands in for the real client: 'up_at' is the address it currently answers on (None = nowhere)."""
    simulated = False

    def __init__(self, up_at=OLD):
        self.host, self.up_at, self.last_ok, self.last_error = OLD, up_at, 0.0, ""
        self.queries, self.closed, self.addresses = 0, 0, []

    def query_state(self, timeout=2.5):
        self.queries += 1
        if self.up_at != self.host:
            raise ControllerError("connect failed: timed out")
        self.last_ok = time.time()
        return STATE

    def close(self):
        self.closed += 1

    def set_address(self, host, port=None):
        self.host = host
        self.addresses.append(host)

    def invalidate(self):
        pass


class FakeRunner:
    def __init__(self, ctrl):
        self.ctrl, self.plan_active, self.reapplied, self.settings_applied = ctrl, False, 0, 0
        self.beat, self._thread, self.restarts = time.monotonic(), None, 0

    def request_reapply(self):
        self.reapplied += 1

    def apply_settings(self):
        self.settings_applied += 1

    def restart_worker(self):
        self.restarts += 1


class Rig:
    def __init__(self, **settings):
        self.db = Database(":memory:")
        self.db.update_settings({"controller_host": OLD, "controller_mac": MAC, "health_check_sec": 30, **settings})
        self.ctrl = FakeController()
        self.runner = FakeRunner(self.ctrl)
        self.t = [1_000_000.0]
        self.found = None                       # what the network search returns
        self.searches, self.exits, self.diagnoses = [], [], 0
        self.m = ConnectionMonitor(self.db, self.ctrl, self.runner, finder=self._find, prober=lambda h: None,
                                   diagnoser=self._diagnose, clock=lambda: self.t[0], exit_fn=self.exits.append)

    def _find(self, mac, near=None):
        self.searches.append((mac, near))
        return self.found

    def _diagnose(self, host, mac):
        self.diagnoses += 1
        return {"summary": "the router is reachable but nothing answers at the controller's address"}

    def tick(self, advance=10):
        self.t[0] += advance
        self.m.tick()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.db.close()


def test_a_healthy_controller_is_online_and_opens_no_incident(rig):
    rig.tick()
    assert rig.m.state == "online" and rig.m.failures == 0 and rig.m.last_ok
    assert rig.db.list_incidents() == [] and rig.ctrl.queries == 1
    assert rig.ctrl.closed == 1                           # idle: the connection is not held open


def test_unconfigured_does_nothing(rig):
    rig.db.update_settings({"controller_host": ""})
    rig.tick()
    assert rig.m.state == "unconfigured" and rig.ctrl.queries == 0


def test_failures_step_through_degraded_to_offline_and_open_one_incident(rig):
    rig.ctrl.up_at = None
    states = []
    for _ in range(4):
        rig.tick(); states.append(rig.m.state)
    assert states == ["degraded", "degraded", "offline", "offline"]
    incidents = rig.db.list_incidents()
    assert len(incidents) == 1 and incidents[0]["open"] and incidents[0]["kind"] == "controller_offline"
    assert incidents[0]["started"] == 1_000_010.0          # the FIRST failed check, not the third
    assert "the router is reachable" in incidents[0]["detail"]["diagnosis"] and rig.diagnoses == 1


def test_recovery_closes_the_incident_and_resends_the_effect(rig):
    rig.ctrl.up_at = None
    for _ in range(4):
        rig.tick()
    rig.ctrl.up_at = OLD
    rig.tick(60)
    assert rig.m.state == "online" and rig.m.failures == 0 and rig.runner.reapplied == 1
    inc = rig.db.list_incidents()[0]
    assert not inc["open"] and inc["resolution"] == "reconnected on its own" and inc["duration_sec"] == 90.0


def test_a_blip_that_never_reaches_offline_leaves_no_incident(rig):
    rig.ctrl.up_at = None
    rig.tick(); rig.tick()
    rig.ctrl.up_at = OLD
    rig.tick()
    assert rig.m.state == "online" and rig.db.list_incidents() == [] and rig.runner.reapplied == 1


# ---- following the controller when its IP address changes
def test_the_controller_is_followed_to_its_new_address(rig):
    rig.ctrl.up_at = NEW                                   # it now lives at NEW; OLD is silent
    rig.found = {"ip": NEW, "mac": MAC}
    rig.tick()
    assert rig.db.settings()["controller_host"] == NEW and rig.ctrl.addresses == [NEW]
    assert rig.runner.settings_applied == 1 and rig.m.moved_from == OLD
    assert rig.searches == [(MAC, OLD)]                    # looked for its MAC, near the old address
    rig.tick()                                             # the next check succeeds at the new address
    assert rig.m.state == "online"
    inc = rig.db.list_incidents()[0]
    assert not inc["open"] and f"found at its new address {NEW}" in inc["resolution"]
    assert inc["detail"]["moved_from"] == OLD and inc["detail"]["moved_to"] == NEW
    assert rig.runner.reapplied == 1


def test_a_search_that_finds_it_at_the_same_address_changes_nothing(rig):
    rig.ctrl.up_at = None
    rig.found = {"ip": OLD, "mac": MAC}
    rig.tick()
    assert rig.db.settings()["controller_host"] == OLD and rig.ctrl.addresses == []


def test_a_search_that_finds_nothing_keeps_trying_once_a_minute(rig):
    rig.ctrl.up_at = None
    for _ in range(5):
        rig.tick(10)                                       # 50 seconds: one search, not five
    assert len(rig.searches) == 1
    rig.tick(20)                                           # now over a minute since the first
    assert len(rig.searches) == 2


def test_without_a_known_mac_it_cannot_follow_but_does_not_crash(rig):
    rig.db.update_settings({"controller_mac": ""})
    rig.ctrl.up_at = None
    rig.found = {"ip": NEW, "mac": "112233445566"}
    for _ in range(3):
        rig.tick()
    assert rig.searches == [] and rig.db.settings()["controller_host"] == OLD and rig.m.state == "offline"


def test_the_mac_is_learned_the_first_time_the_controller_is_healthy(rig):
    rig.db.update_settings({"controller_mac": ""})
    rig.m.prober = lambda host: {"ip": host, "mac": "aa:bb:cc:dd:ee:ff"}
    rig.tick()
    assert rig.db.settings()["controller_mac"] == MAC


def test_the_mac_falls_back_to_the_arp_table(rig, monkeypatch):
    rig.db.update_settings({"controller_mac": ""})
    monkeypatch.setattr(netscan, "arp_mac", lambda host: "AABBCCDDEEFF")
    rig.tick()
    assert rig.db.settings()["controller_mac"] == MAC


def test_a_simulated_controller_never_learns_a_mac():
    db = Database(":memory:")
    db.update_settings({"controller_host": "simulated"})
    sim = SimulatedController("simulated")
    called = []
    m = ConnectionMonitor(db, sim, None, prober=lambda h: called.append(h))
    m.tick()
    assert m.state == "online" and called == [] and db.settings()["controller_mac"] == ""


# ---- using what the running effect already proves
def test_a_playing_effect_counts_as_proof_of_life_so_no_probe_is_sent(rig):
    rig.runner.plan_active = True
    rig.ctrl.last_ok = rig.t[0] + 5                       # a command succeeded a moment ago
    rig.tick(10)
    assert rig.ctrl.queries == 0 and rig.m.state == "online" and rig.ctrl.closed == 0
    rig.ctrl.last_ok = rig.t[0] - 500                     # nothing succeeded for a long time: probe after all
    rig.tick(10)
    assert rig.ctrl.queries == 1


# ---- alerts
@pytest.fixture
def sent(monkeypatch):
    box = []

    def fake_send(cfg, subject, body):
        box.append((subject, body))
        return True, ["webhook"]
    monkeypatch.setattr(alerts, "send", fake_send)
    return box


def test_no_alert_goes_out_when_no_channel_is_configured(rig, sent):
    rig.ctrl.up_at = None
    for _ in range(100):
        rig.tick(30)
    assert sent == []


def test_alert_is_sent_once_after_the_threshold_then_reminders_then_recovery(rig, sent):
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 10, "alert_reminder_hours": 6})
    rig.ctrl.up_at = None
    for _ in range(3):
        rig.tick(10)                                       # offline, but only ~30 s: no alert yet
    assert sent == []
    rig.tick(600)                                          # ten minutes later
    assert len(sent) == 1 and sent[0][0].startswith("Controller not responding") and "router is reachable" in sent[0][1]
    for _ in range(10):
        rig.tick(60)                                       # ten more minutes: still just the one alert
    assert len(sent) == 1
    rig.tick(6 * 3600)
    assert len(sent) == 2 and sent[1][0].startswith("[reminder]")
    rig.ctrl.up_at = OLD
    rig.tick(10)
    assert len(sent) == 3 and sent[2][0].startswith("Recovered") and "reconnected on its own" in sent[2][1]
    rig.tick(10)
    assert len(sent) == 3                                  # and nothing more


def test_a_short_outage_that_never_alerted_sends_no_recovery_message(rig, sent):
    rig.db.update_settings({"alert_webhook": "http://x/y"})
    rig.ctrl.up_at = None
    for _ in range(4):
        rig.tick(10)
    rig.ctrl.up_at = OLD
    rig.tick(10)
    assert sent == []


def test_moving_to_a_new_address_alerts_immediately(rig, sent):
    rig.db.update_settings({"alert_webhook": "http://x/y"})
    rig.ctrl.up_at = NEW
    rig.found = {"ip": NEW, "mac": MAC}
    rig.tick()
    assert len(sent) == 1 and sent[0][0] == f"Controller moved to {NEW}" and "reservation" in sent[0][1]


def test_alert_state_survives_a_restart_of_the_program(rig, sent):
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 1})
    rig.ctrl.up_at = None
    for _ in range(3):
        rig.tick(30)
    rig.tick(60)
    assert len(sent) == 1
    again = ConnectionMonitor(rig.db, rig.ctrl, rig.runner, finder=rig._find, prober=lambda h: None,
                              diagnoser=rig._diagnose, clock=lambda: rig.t[0])
    again.tick()                                           # a fresh process takes over the same open incident
    again.tick()
    assert len(sent) == 1 and rig.db.list_incidents()[0]["open"] and len(rig.db.list_incidents()) == 1


# ---- keeping the program's own threads alive
class DeadThread:
    def is_alive(self):
        return False


def test_a_dead_worker_thread_is_replaced(rig):
    rig.runner._thread = DeadThread()
    rig.tick()
    assert rig.runner.restarts == 1 and rig.exits == []


def test_a_stuck_worker_is_replaced_but_an_idle_one_is_left_alone(rig):
    rig.runner._thread = threading.current_thread()
    rig.runner.plan_active = False
    rig.runner.beat = time.monotonic() - 1000
    rig.tick()
    assert rig.runner.restarts == 0                       # nothing to do, so silence is fine
    rig.runner.plan_active = True
    rig.runner.beat = time.monotonic() - 1000
    rig.tick()
    assert rig.runner.restarts == 1


def test_repeated_stalls_restart_the_whole_program(rig):
    rig.runner._thread = DeadThread()
    for _ in range(3):
        rig.tick(10)
    assert rig.runner.restarts == 2 and rig.exits == [70]  # two replacements, then give up and let the system restart us


def test_snapshot_describes_the_situation(rig):
    rig.ctrl.up_at = None
    for _ in range(3):
        rig.tick()
    snap = rig.m.snapshot()
    assert snap["state"] == "offline" and snap["failures"] == 3 and snap["host"] == OLD and snap["mac"] == MAC
    assert snap["incident"]["open"] and snap["unreachable_for_sec"] == 20.0 and "router" in snap["diagnosis"]["summary"]
    assert set(snap["heartbeat"]) == {"runner_sec", "schedules_sec", "monitor_sec"}


def test_recover_now_follows_a_moved_controller_on_demand(rig):
    rig.ctrl.up_at = NEW
    rig.found = {"ip": NEW, "mac": MAC}
    result = rig.m.recover_now()
    assert result["moved"] is True and result["host"] == NEW and result["was"] == OLD and result["mac_known"]
    rig.found = None
    assert rig.m.recover_now()["moved"] is False


def test_human_durations():
    assert [health.human(s) for s in (5, 75, 3725)] == ["5 s", "1 min 15 s", "1 h 2 min"]


def test_a_nudge_makes_the_monitor_check_immediately():
    import threading as th
    r = Rig(health_check_sec=600)                           # normally it would wait ten minutes
    r.m.tick = lambda: ticks.append(time.monotonic())       # count checks instead of doing them
    ticks = []
    r.m.start()
    deadline = time.time() + 3
    while not ticks and time.time() < deadline:
        time.sleep(0.02)
    assert len(ticks) == 1                                  # the first check at startup
    r.m.state = "online"
    r.m.nudge()
    deadline = time.time() + 3
    while len(ticks) < 2 and time.time() < deadline:
        time.sleep(0.02)
    r.m.shutdown()
    assert len(ticks) == 2                                  # the nudge did not wait for the 600 s interval


def test_the_runner_wakes_the_monitor_on_its_first_failed_command():
    from tm1814_led.runner import Runner
    db = Database(":memory:")
    ctrl = SimulatedController("sim")
    runner = Runner(db, ctrl)
    woken = []
    runner.on_failure = lambda: woken.append(1)
    def boom():
        raise ControllerError("connect failed: timed out")
    runner._guard("test", boom, runner._gen)
    runner._guard("test", boom, runner._gen)
    assert woken == [1]                                     # once per streak of failures, not once per attempt
