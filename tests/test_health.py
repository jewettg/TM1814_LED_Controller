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

    def fake_send(cfg, subject, body, kind=""):
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


def test_moving_to_a_new_address_is_not_critical_so_it_only_alerts_if_asked(rig, sent):
    rig.db.update_settings({"alert_webhook": "http://x/y"})
    rig.ctrl.up_at = NEW
    rig.found = {"ip": NEW, "mac": MAC}
    rig.tick()
    assert sent == [] and rig.db.settings()["controller_host"] == NEW        # followed silently; recorded as an incident
    rig2 = Rig(alert_webhook="http://x/y", alert_on_move=True)
    rig2.ctrl.up_at, rig2.found = NEW, {"ip": NEW, "mac": MAC}
    rig2.tick()
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


# ---- keeping the number of alerts small (ntfy.sh's free tier allows 250 messages a day)
def outage(rig, minutes):
    """The controller is down for `minutes`, then comes back (checked every minute)."""
    rig.ctrl.up_at = None
    for _ in range(int(minutes)):
        rig.tick(60)
    rig.ctrl.up_at = OLD
    rig.tick(60)


def test_a_controller_that_flaps_all_day_cannot_flood_the_phone(rig, monkeypatch):
    stamps = []
    monkeypatch.setattr(alerts, "send", lambda cfg, s, b, kind="": (stamps.append((rig.t[0], kind)), (True, ["webhook"]))[1])
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 1, "alert_daily_limit": 20,
                            "alert_min_gap_minutes": 60})
    for _ in range(96):                                       # two days of 12-minute outages every 30 minutes
        outage(rig, 12)
        for _ in range(17):
            rig.tick(60)
    assert len(stamps) > 20                                   # it did keep alerting over the two days ...
    times = [t for t, _ in stamps]
    for i, t in enumerate(times):                             # ... but never more than 20 in ANY rolling 24 hours
        assert len([x for x in times if t - 86400 < x <= t]) <= 20
    downs = [t for t, kind in stamps if kind == "down"]
    assert all(b - a >= 3600 for a, b in zip(downs, downs[1:]))      # and never two "down" alerts within the hour


def test_only_critical_kinds_may_use_the_last_part_of_the_budget():
    b = alerts.Budget({}, limit=10, min_gap_sec=0)
    for _ in range(7):
        b.record_sent("down", 1000.0)
    assert b.check("reminder", 1001.0) and "critical" in b.check("moved", 1001.0)      # 70% used: non-critical stops
    assert b.check("recovered", 1001.0) is None and b.check("watchdog", 1001.0) is None  # critical still allowed
    for _ in range(3):
        b.record_sent("down", 1000.0)
    assert "limit of 10" in b.check("recovered", 1002.0) and b.check("test", 1002.0) is None   # a hand-sent test always works


def test_the_daily_limit_rolls_over_after_24_hours():
    b = alerts.Budget({}, limit=3, min_gap_sec=0)
    for i in range(3):
        b.record_sent("down", 1000.0 + i)
    assert b.check("down", 2000.0)
    later = 1002.0 + 86399                                    # the first two have aged out; the newest has not
    assert b.check("down", later) is None and b.used(later) == 1


def test_a_new_down_alert_waits_for_the_minimum_gap():
    b = alerts.Budget({}, limit=50, min_gap_sec=3600)
    b.record_sent("down", 1000.0)
    assert "flapping" in b.check("down", 1000.0 + 1800) and "flapping" in b.check("watchdog", 1000.0 + 1800)
    assert b.check("down", 1000.0 + 3601) is None and b.check("recovered", 1000.0 + 5) is None


def test_failed_deliveries_back_off_instead_of_retrying_every_check():
    b = alerts.Budget({}, limit=50, min_gap_sec=0)
    b.record_failure(1000.0)
    assert b.check("down", 1100.0) and b.check("down", 1000.0 + 301) is None
    b.record_failure(1400.0); b.record_failure(1500.0)
    assert b.s["next_try"] == 1500.0 + 1200.0                                       # 5, 10, 20 minutes ...
    for _ in range(10):
        b.record_failure(2000.0)
    assert b.s["next_try"] == 2000.0 + 3600.0                                       # ... but never more than an hour
    b.record_sent("down", 9000.0)
    assert b.s["next_try"] == 0.0 and b.s["fails"] == 0


def test_a_broken_channel_is_not_hammered_every_check(rig, monkeypatch):
    calls = []
    monkeypatch.setattr(alerts, "send", lambda cfg, s, b, kind="": (calls.append(kind), (False, []))[1])
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 1})
    rig.ctrl.up_at = None
    for _ in range(60):                                       # an hour of 10-second checks, all failing to deliver
        rig.tick(10)
    assert len(calls) <= 4                                    # first try, then 5 min, 10 min, 20 min back-off


def test_the_status_reports_how_much_of_the_budget_is_used(rig, sent):
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 1, "alert_daily_limit": 5})
    outage(rig, 3)
    snap = rig.m.snapshot()["alerts"]
    assert snap["limit"] == 5 and snap["sent_24h"] == 2 and snap["channels"] == ["webhook"] and snap["on_move"] is False


def test_a_test_alert_always_goes_out_and_is_counted(rig, monkeypatch):
    got = []
    monkeypatch.setattr(alerts, "send", lambda cfg, s, b, kind="": (got.append(kind), (True, ["webhook"]))[1])
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_daily_limit": 1})
    assert rig.m.send_test() == ["webhook"] and rig.m.send_test() == ["webhook"]       # even past the limit
    assert got == ["test", "test"] and rig.m.snapshot()["alerts"]["sent_24h"] == 2
    rig.db.update_settings({"alert_webhook": ""})
    with pytest.raises(ValueError):
        rig.m.send_test()


def test_the_budget_survives_a_restart_of_the_program(rig, sent):
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 1})
    outage(rig, 3)
    again = ConnectionMonitor(rig.db, rig.ctrl, rig.runner, finder=rig._find, prober=lambda h: None, diagnoser=rig._diagnose,
                              clock=lambda: rig.t[0])
    assert again.snapshot()["alerts"]["sent_24h"] == 2


# ---- the log stays quiet unless something changes
def test_routine_health_checks_write_nothing_to_the_log(caplog):
    from test_controller import FakeController as TcpFake
    from tm1814_led.controller import Controller
    fake = TcpFake()
    db = Database(":memory:")
    db.update_settings({"controller_host": "127.0.0.1", "controller_port": fake.port, "controller_mac": MAC})
    ctrl = Controller("127.0.0.1", fake.port, min_gap=0)
    runner = FakeRunner(ctrl)
    t = [1_000_000.0]
    mon = ConnectionMonitor(db, ctrl, runner, clock=lambda: t[0])
    try:
        with caplog.at_level("INFO", logger="tm1814"):
            for _ in range(30):                                   # half an hour of once-a-minute checks, each opening a connection
                t[0] += 60
                mon.tick()
        assert mon.state == "online" and ctrl.commands_sent >= 30  # it really did talk to the controller each time
        lines = [r.getMessage() for r in caplog.records]
        assert lines == ["controller connection: unknown -> online"], lines     # and logged only the one change
    finally:
        fake.close(); db.close()


def test_a_change_of_health_is_logged_once_each(rig, caplog):
    with caplog.at_level("INFO", logger="tm1814"):
        rig.tick()
        rig.ctrl.up_at = None
        for _ in range(6):
            rig.tick(10)
        rig.ctrl.up_at = OLD
        for _ in range(4):
            rig.tick(10)
    changes = [r.getMessage() for r in caplog.records if "->" in r.getMessage()]
    assert changes == ["controller connection: unknown -> online", "controller connection: online -> degraded",
                       "controller connection: degraded -> offline", "controller connection: offline -> online"]


def test_every_alert_event_is_logged_with_the_word_alert(rig, monkeypatch, caplog):
    monkeypatch.setattr(alerts, "send", lambda cfg, s, b, kind="": (True, ["webhook"]))
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 1, "alert_daily_limit": 20})
    with caplog.at_level("INFO", logger="tm1814"):
        outage(rig, 3)
    alert_lines = [r for r in caplog.records if "ALERT" in r.getMessage()]
    msgs = [r.getMessage() for r in alert_lines]
    assert any("ALERT sent via webhook [down]" in m and "1 of 20 alerts used in the last 24 hours" in m for m in msgs)
    assert any("ALERT sent via webhook [recovered]" in m and "2 of 20" in m for m in msgs)
    assert all(r.levelname == "WARNING" for r in alert_lines)            # alerts stand out in a "warnings and errors" view


def test_a_held_back_alert_says_why_but_only_once_an_hour(rig, monkeypatch, caplog):
    monkeypatch.setattr(alerts, "send", lambda cfg, s, b, kind="": (True, ["webhook"]))
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 1, "alert_daily_limit": 1})
    with caplog.at_level("INFO", logger="tm1814"):
        outage(rig, 3)                                                    # the one allowed alert is used
        rig.ctrl.up_at = None
        for _ in range(40):
            rig.tick(60)                                                  # a second outage wants to alert: refused, every check
    held = [r.getMessage() for r in caplog.records if "ALERT held back" in r.getMessage()]
    assert held and len(held) <= 2 and "limit of 1 alerts per 24 hours" in held[0]


def test_a_missing_channel_is_mentioned_once_not_every_check(rig, caplog):
    rig.db.update_settings({"alert_after_minutes": 1})
    with caplog.at_level("INFO", logger="tm1814"):
        rig.ctrl.up_at = None
        for _ in range(30):
            rig.tick(30)
    lines = [r.getMessage() for r in caplog.records if "no alert channel is set up" in r.getMessage()]
    assert len(lines) == 1


def test_a_failed_delivery_is_logged_with_the_retry_time(rig, monkeypatch, caplog):
    monkeypatch.setattr(alerts, "send", lambda cfg, s, b, kind="": (False, []))
    rig.db.update_settings({"alert_webhook": "http://x/y", "alert_after_minutes": 1})
    with caplog.at_level("INFO", logger="tm1814"):
        rig.ctrl.up_at = None
        for _ in range(8):
            rig.tick(30)
    failed = [r.getMessage() for r in caplog.records if "ALERT could not be delivered" in r.getMessage()]
    assert len(failed) == 1 and "trying again at" in failed[0]
