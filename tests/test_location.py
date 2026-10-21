import datetime as dt
import urllib.error
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from tm1814_led import api as api_module
from tm1814_led import geocode, scheduler as S
from tm1814_led.api import create_app

TZ = ZoneInfo("America/Chicago")
AUSTIN = S.Place("America/Chicago", 30.2672, -97.7431)

NOMINATIM = [{"display_name": "Austin, Travis County, Texas, United States", "lat": "30.2711286", "lon": "-97.7436995"},
             {"display_name": "Austin County, Texas, United States", "lat": "29.8916", "lon": "-96.2443"},
             {"display_name": "broken entry"}]


@pytest.fixture(autouse=True)
def fresh_cache():
    geocode._cache.clear()
    geocode._last_request = 0.0
    yield


def lookup(query, fetch, **kw):
    return geocode.search(query, fetch=fetch, **kw)


def test_results_are_cleaned_rounded_and_broken_entries_skipped():
    got = lookup("Austin, Texas", lambda url: NOMINATIM)
    assert got == [{"name": "Austin, Travis County, Texas, United States", "latitude": 30.2711, "longitude": -97.7437},
                   {"name": "Austin County, Texas, United States", "latitude": 29.8916, "longitude": -96.2443}]


def test_the_request_identifies_this_program_and_carries_the_text_safely():
    seen = []
    lookup("12 Main St & Elm #3", lambda url: (seen.append(url), NOMINATIM)[1])
    assert seen[0].startswith("https://nominatim.openstreetmap.org/search?") and "q=12+Main+St+%26+Elm+%233" in seen[0]
    assert "format=jsonv2" in seen[0] and "limit=5" in seen[0]
    assert geocode.USER_AGENT.startswith("tm1814-led/") and "github.com" in geocode.USER_AGENT


def test_results_are_cached_so_the_service_is_not_asked_twice():
    calls = []
    fetch = lambda url: (calls.append(url), NOMINATIM)[1]                      # noqa: E731
    lookup("Austin, Texas", fetch); lookup("  austin,   TEXAS ", fetch)
    assert len(calls) == 1                                                      # same place, different spelling of spaces/case


def test_requests_are_spaced_at_least_a_second_apart():
    clock, naps = [100.0], []
    now = lambda: clock[0]                                                      # noqa: E731
    sleep = lambda s: (naps.append(s), clock.__setitem__(0, clock[0] + s))      # noqa: E731
    lookup("first place", lambda u: NOMINATIM, now=now, sleep=sleep)
    clock[0] += 0.2
    lookup("second place", lambda u: NOMINATIM, now=now, sleep=sleep)
    assert len(naps) == 1 and naps[0] == pytest.approx(geocode.MIN_INTERVAL - 0.2)


@pytest.mark.parametrize("query", ["", "ab", "   ", None])
def test_too_short_input_is_refused_without_calling_the_service(query):
    with pytest.raises(geocode.GeocodeError, match="at least 3"):
        lookup(query, lambda u: pytest.fail("must not be called"))


def test_friendly_messages_for_every_way_it_can_fail():
    def raiser(exc):
        def f(url):
            raise exc
        return f
    with pytest.raises(geocode.GeocodeError, match="No place was found"):
        lookup("nowhere at all", lambda u: [])
    with pytest.raises(geocode.GeocodeError, match="busy"):
        lookup("one", raiser(urllib.error.HTTPError("u", 429, "slow down", {}, None)))
    with pytest.raises(geocode.GeocodeError, match="error \\(500\\)"):
        lookup("two", raiser(urllib.error.HTTPError("u", 500, "oops", {}, None)))
    with pytest.raises(geocode.GeocodeError, match="Could not reach"):
        lookup("three", raiser(urllib.error.URLError("offline")))
    with pytest.raises(geocode.GeocodeError, match="Could not reach"):
        lookup("four", raiser(ValueError("not json")))
    assert issubclass(geocode.GeocodeError, ValueError)                         # so the API answers 422


# ---- the API
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_module.time, "sleep", lambda s: None)
    app = create_app(data_dir=":memory:", simulate=True, password="s3cretpw", start_background=False)
    with TestClient(app, follow_redirects=False) as c:
        c.post("/api/login", json={"password": "s3cretpw"})
        c.app = app
        yield c


def test_search_route_returns_candidates_and_needs_a_session(client, monkeypatch):
    monkeypatch.setattr(api_module.geocode, "search", lambda q: [{"name": f"Found {q}", "latitude": 1.5, "longitude": 2.5}])
    assert client.post("/api/location/search", json={"query": "Springfield"}).json() == {
        "results": [{"name": "Found Springfield", "latitude": 1.5, "longitude": 2.5}]}
    assert TestClient(client.app).post("/api/location/search", json={"query": "x"}).status_code == 401
    monkeypatch.undo()
    assert client.post("/api/location/search", json={"query": "a"}).status_code == 422       # too short, with a friendly message


def test_nothing_is_saved_by_searching_and_the_place_is_saved_with_settings(client):
    assert client.get("/api/location/sun").json() == {"configured": False}
    client.put("/api/settings", json={"timezone": "America/Chicago", "latitude": 30.2672, "longitude": -97.7431,
                                      "location_name": "Austin, Texas, United States"})
    s = client.get("/api/settings").json()
    assert s["location_name"] == "Austin, Texas, United States" and s["latitude"] == 30.2672
    cleared = client.put("/api/settings", json={"latitude": None, "longitude": None, "location_name": ""}).json()
    assert cleared["latitude"] is None and cleared["location_name"] == ""
    assert client.put("/api/settings", json={"latitude": 123}).json()["latitude"] == 90          # clamped, never rejected


def test_sun_route_gives_todays_sunrise_and_sunset_for_the_saved_or_previewed_place(client):
    saved = client.put("/api/settings", json={"timezone": "America/Chicago", "latitude": 30.2672, "longitude": -97.7431}) and \
        client.get("/api/location/sun").json()
    assert saved["configured"] and saved["estimated"] is False and saved["timezone"] == "America/Chicago"
    rise, sset = dt.datetime.fromisoformat(saved["sunrise"]), dt.datetime.fromisoformat(saved["sunset"])
    assert rise < sset and (sset - rise).total_seconds() > 8 * 3600                              # a plausible day length
    other = client.get("/api/location/sun?latitude=64.8&longitude=-147.7&timezone=America/Anchorage").json()
    assert other["timezone"] == "America/Anchorage" and other["sunrise"] != saved["sunrise"]     # a preview of a different place


# ---- "start at sunset and stop at sunrise, and vice versa"
def spec(**kw):
    base = {"repeat": {"type": "daily"}}
    base.update(kw)
    return S.validate_spec(base)


def at(m, d, h, mi=0):
    return dt.datetime(2026, m, d, h, mi, tzinfo=TZ)


def test_sunset_to_sunrise_runs_all_night_across_midnight():
    night = spec(start_time="sunset", end_time="sunrise")
    assert S.window_at(night, at(10, 20, 19, 30), AUSTIN)                       # evening after sunset
    assert S.window_at(night, at(10, 21, 2, 0), AUSTIN)                         # after midnight, still last evening's window
    assert S.window_at(night, at(10, 21, 7, 0), AUSTIN)                         # just before sunrise (about 7:30)
    assert S.window_at(night, at(10, 21, 12, 0), AUSTIN) is None                # daytime: off
    assert S.window_at(night, at(10, 20, 17, 0), AUSTIN) is None                # before sunset: off
    start, end = S.window_on(night, dt.date(2026, 10, 20), AUSTIN)
    assert start.date() == dt.date(2026, 10, 20) and end.date() == dt.date(2026, 10, 21) and end > start


def test_sunrise_to_sunset_is_the_daytime_and_the_opposite_of_the_night():
    day = spec(start_time="sunrise", end_time="sunset")
    night = spec(start_time="sunset", end_time="sunrise")
    for hour in range(24):
        t = at(10, 21, hour, 15)
        assert bool(S.window_at(day, t, AUSTIN)) != bool(S.window_at(night, t, AUSTIN)), hour     # exactly one is active
    assert S.window_at(day, at(10, 21, 12), AUSTIN) and S.window_at(day, at(10, 21, 3), AUSTIN) is None
    start, end = S.window_on(day, dt.date(2026, 10, 21), AUSTIN)
    assert start.date() == end.date() == dt.date(2026, 10, 21)                  # a daytime window stays within the day


def test_offsets_apply_to_either_end_of_a_sun_window():
    plain = S.window_on(spec(start_time="sunset", end_time="sunrise"), dt.date(2026, 10, 20), AUSTIN)
    shifted = S.window_on(spec(start_time="sunset", end_time="sunrise", start_offset=-30, end_offset=45), dt.date(2026, 10, 20), AUSTIN)
    assert (plain[0] - shifted[0]).total_seconds() == 1800 and (shifted[1] - plain[1]).total_seconds() == 2700


def test_a_schedule_can_mix_a_clock_time_with_a_sun_time_both_ways():
    until_sunrise = spec(start_time="21:00", end_time="sunrise")
    assert S.window_at(until_sunrise, at(10, 21, 3), AUSTIN) and S.window_at(until_sunrise, at(10, 21, 12), AUSTIN) is None
    from_sunrise = spec(start_time="sunrise", end_time="17:00")
    assert S.window_at(from_sunrise, at(10, 21, 9), AUSTIN) and S.window_at(from_sunrise, at(10, 21, 18), AUSTIN) is None


def test_the_active_schedule_flips_over_at_sunset_and_at_sunrise():
    rows = [{"id": 1, "enabled": True, "priority": 0, "spec": spec(start_time="sunrise", end_time="sunset")},
            {"id": 2, "enabled": True, "priority": 0, "spec": spec(start_time="sunset", end_time="sunrise")}]
    sunset = S.sun_time(dt.date(2026, 10, 21), "sunset", AUSTIN)[0]
    before, after = sunset - dt.timedelta(minutes=2), sunset + dt.timedelta(minutes=2)
    assert S.pick_active(rows, before, AUSTIN).id == 1 and S.pick_active(rows, after, AUSTIN).id == 2


def test_a_schedule_summary_reads_naturally():
    assert S.describe(spec(start_time="sunset", end_time="sunrise")).startswith("every day, sunset to sunrise")
    assert S.describe(spec(start_time="sunrise", end_time="sunset")).startswith("every day, sunrise to sunset")
