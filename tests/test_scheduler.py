import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from tm1814_led import scheduler as S

TZ = ZoneInfo("America/Chicago")
PLACE = S.Place("America/Chicago", 30.2672, -97.7431)


def spec(**kw):
    base = {"start_time": "18:00", "end_time": "23:00", "repeat": {"type": "daily"}}
    base.update(kw)
    return S.validate_spec(base)


def at(y, m, d, hh=0, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=TZ)


def dates(sp, start, days=40):
    return [start + dt.timedelta(days=i) for i in range(days) if S.occurs_on(sp, start + dt.timedelta(days=i))]


def test_daily_interval_and_date_range():
    sp = spec(start_date="2026-10-01", end_date="2026-10-10", repeat={"type": "daily", "interval": 3})
    assert dates(sp, dt.date(2026, 9, 28)) == [dt.date(2026, 10, d) for d in (1, 4, 7, 10)]


def test_weekly_weekdays_and_interval():
    sp = spec(start_date="2026-10-05", repeat={"type": "weekly", "weekdays": [0, 4], "interval": 2})
    got = dates(sp, dt.date(2026, 10, 1), 30)
    assert got == [dt.date(2026, 10, 5), dt.date(2026, 10, 9), dt.date(2026, 10, 19), dt.date(2026, 10, 23)]


def test_monthly_day_first_last_and_clamped():
    first = spec(repeat={"type": "monthly_day", "day": 1})
    assert dates(first, dt.date(2026, 10, 1), 65) == [dt.date(2026, 10, 1), dt.date(2026, 11, 1), dt.date(2026, 12, 1)]
    last = spec(repeat={"type": "monthly_day", "day": -1})
    assert dt.date(2026, 2, 28) in dates(last, dt.date(2026, 2, 1), 28)
    d31 = spec(repeat={"type": "monthly_day", "day": 31})
    assert dt.date(2026, 4, 30) in dates(d31, dt.date(2026, 4, 1), 30)      # clamped to the last day


def test_monthly_nth_weekday():
    first_sat = spec(repeat={"type": "monthly_nth", "nth": 1, "weekday": 5})
    assert dates(first_sat, dt.date(2026, 11, 1), 40)[:2] == [dt.date(2026, 11, 7), dt.date(2026, 12, 5)]
    last_fri = spec(repeat={"type": "monthly_nth", "nth": -1, "weekday": 4})
    assert dates(last_fri, dt.date(2026, 10, 1), 31) == [dt.date(2026, 10, 30)]


def test_yearly_and_once():
    y = spec(repeat={"type": "yearly", "month": 10, "day": 31})
    assert dates(y, dt.date(2026, 10, 1), 60) == [dt.date(2026, 10, 31)]
    leap = spec(repeat={"type": "yearly", "month": 2, "day": 29})
    assert S.occurs_on(leap, dt.date(2027, 2, 28))
    once = spec(start_date="2026-12-25", repeat={"type": "once"})
    assert dates(once, dt.date(2026, 12, 1), 40) == [dt.date(2026, 12, 25)]


def test_window_and_active_boundaries():
    sp = spec()
    assert S.window_at(sp, at(2026, 10, 13, 17, 59), PLACE) is None
    assert S.window_at(sp, at(2026, 10, 13, 18, 0), PLACE)
    assert S.window_at(sp, at(2026, 10, 13, 22, 59), PLACE)
    assert S.window_at(sp, at(2026, 10, 13, 23, 0), PLACE) is None


def test_overnight_window_crosses_midnight_and_uses_yesterdays_date():
    sp = spec(start_time="22:00", end_time="02:00", start_date="2026-10-13", end_date="2026-10-13")
    assert S.window_at(sp, at(2026, 10, 14, 1, 0), PLACE)          # next morning still inside the window
    assert S.window_at(sp, at(2026, 10, 14, 2, 0), PLACE) is None


def test_sunset_window_for_austin():
    sp = spec(start_time="sunset", end_time="23:00")
    start, end = S.window_on(sp, dt.date(2026, 10, 24), PLACE)
    assert 18 <= start.hour <= 19 and start.minute in range(60) and end.hour == 23
    assert abs((start - at(2026, 10, 24, 18, 50)).total_seconds()) < 15 * 60


def test_sun_offset_and_overnight_sun_window():
    plain = S.window_on(spec(start_time="sunset"), dt.date(2026, 10, 24), PLACE)[0]
    shifted = S.window_on(spec(start_time="sunset", start_offset=-30), dt.date(2026, 10, 24), PLACE)[0]
    assert (plain - shifted).total_seconds() == 1800
    night = spec(start_time="sunset", end_time="sunrise")
    s, e = S.window_on(night, dt.date(2026, 10, 24), PLACE)
    assert e.date() == dt.date(2026, 10, 25) and e > s


def test_sun_estimate_without_location():
    nowhere = S.Place("America/Chicago")
    dtm, est = S.sun_time(dt.date(2026, 10, 24), "sunset", nowhere)
    assert est and dtm.hour == 19


def test_next_windows_lists_future_in_order():
    sp = spec(start_date="2026-10-24", end_date="2026-10-31")
    wins = S.next_windows(sp, at(2026, 10, 13, 12), PLACE, 3)
    assert [w[0].date() for w in wins] == [dt.date(2026, 10, d) for d in (24, 25, 26)]


def row(i, priority, **kw):
    return {"id": i, "enabled": kw.pop("enabled", True), "priority": priority, "spec": spec(**kw)}


def test_pick_active_priority_then_latest_start():
    now = at(2026, 10, 13, 20)
    rows = [row(1, 0), row(2, 5, start_time="19:00"), row(3, 5, start_time="19:30"), row(4, 9, enabled=False)]
    assert S.pick_active(rows, now, PLACE).id == 3            # same priority: the one that started last
    assert S.pick_active([row(1, 0)], at(2026, 10, 13, 8), PLACE) is None


@pytest.mark.parametrize("bad", [
    {"start_time": "25:00"}, {"end_time": "later"}, {"start_date": "10/31/2026"},
    {"start_date": "2026-10-31", "end_date": "2026-10-01"}, {"repeat": {"type": "hourly"}},
    {"repeat": {"type": "weekly", "weekdays": []}}, {"repeat": {"type": "weekly", "weekdays": [7]}},
    {"repeat": {"type": "monthly_day", "day": 32}}, {"repeat": {"type": "monthly_nth", "nth": 5, "weekday": 1}},
    {"repeat": {"type": "once"}}, {"start_offset": "soon"},
])
def test_validation_errors(bad):
    base = {"start_time": "18:00", "end_time": "23:00", "repeat": {"type": "daily"}}
    base.update(bad)
    with pytest.raises(S.SpecError):
        S.validate_spec(base)


def test_unknown_timezone_rejected():
    with pytest.raises(S.SpecError):
        S.Place("Mars/Olympus").tz()


def test_describe_is_readable():
    assert "first Saturday" in S.describe(spec(repeat={"type": "monthly_nth", "nth": 1, "weekday": 5}))
    assert "sunset -30 min" in S.describe(spec(start_time="sunset", start_offset=-30))
    assert "Oct" in S.describe(spec(repeat={"type": "yearly", "month": 10, "day": 31})) or \
        "October" in S.describe(spec(repeat={"type": "yearly", "month": 10, "day": 31}))


def test_overridden_windows_are_marked():
    import datetime as dt
    from tm1814_led import scheduler as sch
    t = lambda h, m=0: dt.datetime(2026, 10, 21, h, m, tzinfo=dt.timezone.utc)
    w = [{"schedule_id": 1, "name": "Halloween", "priority": 100, "start": t(18), "end": t(23)},
         {"schedule_id": 2, "name": "Default", "priority": 0, "start": t(18), "end": t(23)},
         {"schedule_id": 3, "name": "Night", "priority": 500, "start": t(22), "end": t(23) + dt.timedelta(hours=8)},
         {"schedule_id": 4, "name": "Other", "priority": 0, "start": t(7), "end": t(8)}]
    sch.annotate_overrides(w)
    by = {x["name"]: x for x in w}
    assert by["Default"]["status"] == "overridden" and by["Default"]["overridden_by"][0]["name"] == "Halloween"
    assert by["Halloween"]["status"] == "partial" and by["Halloween"]["overridden_by"][0]["name"] == "Night"
    assert by["Night"]["status"] == "runs" and by["Other"]["status"] == "runs"
