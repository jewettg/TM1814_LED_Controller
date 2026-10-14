import datetime as dt
import logging
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from tm1814_led import cli, db as db_module
from tm1814_led.api import create_app
from tm1814_led.db import Database
from tm1814_led.runner import DBLogHandler

DAY = 86400


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def fill(db, now):
    """A month and a half of history: one line a day for 45 days, with a warning and an error mixed in."""
    for age in range(45, 0, -1):
        level = "ERROR" if age == 10 else ("WARNING" if age == 20 else "INFO")
        db.add_log(now - age * DAY, level, "tm1814.test", f"day -{age} 100% done" if age == 5 else f"day -{age}")


def test_a_range_returns_only_that_range_oldest_first(db):
    now = time.time(); fill(db, now)
    week = db.query_logs(since=now - 7 * DAY)
    assert week["total"] == 7 and [e["msg"] for e in week["entries"]][0] == "day -7"
    assert [e["ts"] for e in week["entries"]] == sorted(e["ts"] for e in week["entries"])
    middle = db.query_logs(since=now - 30 * DAY, until=now - 25 * DAY)
    assert middle["total"] == 6 and not middle["truncated"]
    assert db.query_logs()["total"] == 45


def test_level_filter_means_this_level_and_above(db):
    now = time.time(); fill(db, now)
    assert [e["msg"] for e in db.query_logs(level="WARNING")["entries"]] == ["day -20", "day -10"]
    assert [e["msg"] for e in db.query_logs(level="error")["entries"]] == ["day -10"]
    with pytest.raises(ValueError):
        db.query_logs(level="LOUD")


def test_text_search_treats_percent_and_underscore_literally(db):
    now = time.time(); fill(db, now)
    assert [e["msg"] for e in db.query_logs(q="100%")["entries"]] == ["day -5 100% done"]
    assert db.query_logs(q="day -1")["total"] == 11                  # -1, -10 ... -19 contain "day -1"
    db.add_log(now, "INFO", "x", "a_b"); db.add_log(now, "INFO", "x", "aXb")
    assert [e["msg"] for e in db.query_logs(q="a_b")["entries"]] == ["a_b"]


def test_too_many_matches_returns_the_newest_and_says_so(db):
    now = time.time(); fill(db, now)
    r = db.query_logs(limit=5)
    assert r["truncated"] is True and r["total"] == 45 and r["limit"] == 5
    assert [e["msg"] for e in r["entries"]] == ["day -5 100% done", "day -4", "day -3", "day -2", "day -1"]
    assert r["oldest"] < r["newest"] and r["stored"] == 45


def test_oldest_lines_fall_off_after_the_retention_period(db):
    now = time.time(); fill(db, now)
    assert db.settings()["log_retention_days"] == 60
    assert db.prune_logs(now) == 0                                    # nothing is 60 days old yet
    db.update_settings({"log_retention_days": 31})
    assert db.prune_logs(now) == 14                                   # days 45..32 are older than 31 days
    left = db.query_logs()
    assert left["total"] == 31 and left["entries"][0]["msg"] == "day -31"
    assert db.prune_logs(now + 10 * DAY) == 10                        # ten days later, ten more fall off


def test_at_least_a_month_can_never_be_changed_to_less(db):
    assert db.update_settings({"log_retention_days": 5})["log_retention_days"] == 31
    assert db.update_settings({"log_retention_days": 90})["log_retention_days"] == 90
    assert db.update_settings({"log_retention_days": "abc" if False else 400})["log_retention_days"] == 400


def test_row_cap_is_a_safety_net_that_drops_the_oldest(db, monkeypatch):
    monkeypatch.setattr(db_module, "MAX_LOG_ROWS", 10)
    now = time.time()
    for i in range(25):
        db.add_log(now - 100 + i, "INFO", "x", f"line {i}")
    assert db.prune_logs(now) == 15
    assert [e["msg"] for e in db.query_logs()["entries"]][0] == "line 15"


def test_logs_survive_a_restart(tmp_path):
    path = str(tmp_path / "t.db")
    first = Database(path)
    first.add_log(time.time(), "INFO", "tm1814.x", "before restart")
    first.close()
    assert [e["msg"] for e in Database(path).query_logs()["entries"]] == ["before restart"]


def test_an_older_database_gets_the_logs_table(tmp_path):
    path = str(tmp_path / "old.db")
    d = Database(path); d.close()
    raw = sqlite3.connect(path)                                       # turn it back into a version 1 database
    raw.execute("DROP TABLE logs"); raw.execute("PRAGMA user_version = 1"); raw.commit(); raw.close()
    upgraded = Database(path)
    upgraded.add_log(time.time(), "INFO", "x", "works")
    assert upgraded.query_logs()["total"] == 1


def test_the_log_handler_writes_program_messages_to_the_database(db):
    handler = DBLogHandler(db)
    logger = logging.getLogger("tm1814.handlertest"); logger.setLevel(logging.INFO); logger.addHandler(handler)
    try:
        logger.info("hello %s", "world"); logger.warning("careful"); logger.debug("too quiet to keep")
    finally:
        logger.removeHandler(handler)
    got = [(e["level"], e["msg"]) for e in db.query_logs()["entries"]]
    assert got == [("INFO", "hello world"), ("WARNING", "careful")]


def test_a_broken_database_never_breaks_logging(db):
    handler = DBLogHandler(db)
    db.close()
    handler.emit(logging.LogRecord("tm1814.x", logging.INFO, "f", 1, "message", None, None))   # must not raise


# ---- API
@pytest.fixture
def client():
    app = create_app(data_dir=":memory:", simulate=True, start_background=False)
    now = time.time(); fill(app.state.db, now)
    with TestClient(app) as c:
        c.now = now
        yield c


def test_api_logs_filters(client):
    assert len(client.get("/api/logs?count=5").json()) == 5
    week = client.get(f"/api/logs?since={client.now - 7 * DAY}&count=1000").json()
    assert 7 <= len(week) <= 9                                        # the seeded week, plus startup messages
    assert [e["msg"] for e in client.get("/api/logs?level=ERROR&q=day").json()] == ["day -10"]


def test_api_query_has_totals_and_retention(client):
    r = client.get("/api/logs/query?limit=3").json()
    assert set(r) == {"entries", "total", "limit", "truncated", "oldest", "newest", "stored", "retention_days"}
    assert len(r["entries"]) == 3 and r["truncated"] and r["total"] >= 45 and r["retention_days"] == 60
    assert client.get("/api/logs/query?level=LOUD").status_code == 422
    client.put("/api/settings", json={"log_retention_days": 100})
    assert client.get("/api/logs/query").json()["retention_days"] == 100


def test_startup_removes_logs_past_retention(tmp_path):
    path = tmp_path
    app = create_app(data_dir=str(path), simulate=True, start_background=False)
    app.state.db.add_log(time.time() - 100 * DAY, "INFO", "x", "ancient")
    app.state.db.close()
    again = create_app(data_dir=str(path), simulate=True, start_background=False)
    assert not any(e["msg"] == "ancient" for e in again.state.db.query_logs()["entries"])


# ---- CLI time parsing
def test_cli_time_forms():
    now = dt.datetime(2026, 10, 14, 15, 30, 0)
    ts = lambda text: dt.datetime.fromtimestamp(cli.parse_when(text, now))        # noqa: E731
    assert ts("30m") == now - dt.timedelta(minutes=30) and ts("6h") == now - dt.timedelta(hours=6)
    assert ts("2d") == now - dt.timedelta(days=2) and ts("1w") == now - dt.timedelta(weeks=1)
    assert ts("today") == dt.datetime(2026, 10, 14) and ts("yesterday") == dt.datetime(2026, 10, 13)
    assert ts("2026-10-01") == dt.datetime(2026, 10, 1) and ts("2026-10-01 18:45") == dt.datetime(2026, 10, 1, 18, 45)
    assert ts("1.5h") == now - dt.timedelta(minutes=90)
    for bad in ("soon", "", "10x", "2026-13-01"):
        with pytest.raises(ValueError):
            cli.parse_when(bad, now)
