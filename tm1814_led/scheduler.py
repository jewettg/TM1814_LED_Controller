"""Schedule rules: which dates a schedule occurs on, the time window each day, and which one is active now.

A schedule *spec* is a plain dict (stored as JSON):

    {"start_date": "2026-10-01" | None, "end_date": "2026-10-31" | None,
     "start_time": "18:00" | "sunset" | "sunrise", "start_offset": 0,     # offset minutes, used with sun times
     "end_time":   "23:00" | "sunset" | "sunrise", "end_offset": 0,
     "repeat": {"type": "once"}
             | {"type": "daily",        "interval": 1}
             | {"type": "weekly",       "weekdays": [4, 5], "interval": 1}     # 0 = Monday ... 6 = Sunday
             | {"type": "monthly_day",  "day": 1}                              # 1-31, or -1 = last day
             | {"type": "monthly_nth",  "nth": 1, "weekday": 5}                # nth 1-4, or -1 = last
             | {"type": "yearly",       "month": 10, "day": 31}}

The date range limits which dates are eligible. The window on an eligible date runs from start_time to end_time;
if end_time is not later than start_time the window ends the next day (overnight, e.g. sunset to sunrise).
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

try:  # zoneinfo is standard from Python 3.9
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

REPEAT_TYPES = ("once", "daily", "weekly", "monthly_day", "monthly_nth", "yearly")
SUN_WORDS = ("sunrise", "sunset")
DEFAULT_SUNRISE = dt.time(7, 0)
DEFAULT_SUNSET = dt.time(19, 0)
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class SpecError(ValueError):
    """A schedule spec is invalid."""


@dataclass
class Place:
    """Where the lights are: needed for sunrise/sunset and for the time zone."""
    timezone: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def tz(self) -> dt.tzinfo:
        if self.timezone and ZoneInfo is not None:
            try:
                return ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise SpecError(f"unknown time zone {self.timezone!r}") from exc
        return system_tz()

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def system_tz() -> dt.tzinfo:
    """Best effort at the machine's named time zone (so DST is handled), else the current UTC offset."""
    import os
    name = os.environ.get("TZ", "")
    if not name:
        try:
            link = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in link:
                name = link.split("zoneinfo/", 1)[1]
        except OSError:
            name = ""
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


# ---------------------------------------------------------------- validation

def _date(value: Any, label: str) -> Optional[dt.date]:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise SpecError(f"{label}: expected YYYY-MM-DD") from exc


def _time_field(value: Any, label: str) -> str:
    v = str(value or "").strip().lower()
    if v in SUN_WORDS:
        return v
    try:
        h, m = v.split(":")
        dt.time(int(h), int(m))
        return f"{int(h):02d}:{int(m):02d}"
    except Exception as exc:
        raise SpecError(f"{label}: use HH:MM, sunrise or sunset") from exc


def validate_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return a normalized copy of the spec or raise SpecError."""
    out: Dict[str, Any] = {}
    out["start_date"] = _date(spec.get("start_date"), "start date")
    out["end_date"] = _date(spec.get("end_date"), "end date")
    if out["start_date"] and out["end_date"] and out["end_date"] < out["start_date"]:
        raise SpecError("end date is before the start date")
    out["start_time"] = _time_field(spec.get("start_time", "18:00"), "start time")
    out["end_time"] = _time_field(spec.get("end_time", "23:00"), "end time")
    for key in ("start_offset", "end_offset"):
        try:
            out[key] = max(-720, min(720, int(spec.get(key, 0) or 0)))
        except (TypeError, ValueError) as exc:
            raise SpecError(f"{key} must be a whole number of minutes") from exc
    rep = dict(spec.get("repeat") or {"type": "daily"})
    rtype = rep.get("type")
    if rtype not in REPEAT_TYPES:
        raise SpecError(f"repeat type must be one of {', '.join(REPEAT_TYPES)}")
    clean: Dict[str, Any] = {"type": rtype}
    try:
        if rtype in ("daily", "weekly"):
            clean["interval"] = max(1, min(52, int(rep.get("interval", 1) or 1)))
        if rtype == "weekly":
            days = sorted({int(d) for d in rep.get("weekdays", [])})
            if not days or any(d < 0 or d > 6 for d in days):
                raise SpecError("weekly repeat needs at least one weekday (0 = Monday ... 6 = Sunday)")
            clean["weekdays"] = days
        if rtype == "monthly_day":
            day = int(rep.get("day", 1))
            if day != -1 and not 1 <= day <= 31:
                raise SpecError("day of month must be 1-31 or -1 for the last day")
            clean["day"] = day
        if rtype == "monthly_nth":
            nth, wd = int(rep.get("nth", 1)), int(rep.get("weekday", 0))
            if nth not in (1, 2, 3, 4, -1) or not 0 <= wd <= 6:
                raise SpecError("nth must be 1-4 or -1 (last) and weekday 0-6")
            clean["nth"], clean["weekday"] = nth, wd
        if rtype == "yearly":
            month, day = int(rep.get("month", 1)), int(rep.get("day", 1))
            if not 1 <= month <= 12 or not 1 <= day <= 31:
                raise SpecError("yearly repeat needs a valid month and day")
            clean["month"], clean["day"] = month, day
    except (TypeError, ValueError) as exc:
        raise SpecError(f"bad repeat settings: {exc}") from exc
    out["repeat"] = clean
    if rtype == "once":
        if not out["start_date"]:
            raise SpecError("a one-time schedule needs a start date")
        out["end_date"] = None  # a one-time schedule is just its start date
    return out


def spec_to_json(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Make a validated spec JSON-serializable (dates as strings)."""
    out = dict(spec)
    for k in ("start_date", "end_date"):
        v = out.get(k)
        out[k] = (v.isoformat() if hasattr(v, "isoformat") else v) or None
    return out


# ---------------------------------------------------------------- date rules

def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def occurs_on(spec: Dict[str, Any], day: dt.date) -> bool:
    """Is `day` one of the dates this (validated) spec occurs on?"""
    if spec["start_date"] and day < spec["start_date"]:
        return False
    if spec["end_date"] and day > spec["end_date"]:
        return False
    rep = spec["repeat"]
    t = rep["type"]
    anchor = spec["start_date"] or dt.date(2000, 1, 3)  # a Monday
    if t == "once":
        return day == spec["start_date"]
    if t == "daily":
        return (day - anchor).days % rep["interval"] == 0
    if t == "weekly":
        if day.weekday() not in rep["weekdays"]:
            return False
        monday = lambda d: d - dt.timedelta(days=d.weekday())  # noqa: E731
        return ((monday(day) - monday(anchor)).days // 7) % rep["interval"] == 0
    if t == "monthly_day":
        want = _last_day(day.year, day.month) if rep["day"] == -1 else min(rep["day"], _last_day(day.year, day.month))
        return day.day == want
    if t == "monthly_nth":
        if day.weekday() != rep["weekday"]:
            return False
        if rep["nth"] == -1:
            return day.day + 7 > _last_day(day.year, day.month)
        return (day.day - 1) // 7 + 1 == rep["nth"]
    if t == "yearly":
        want = min(rep["day"], _last_day(day.year, rep["month"]))
        return day.month == rep["month"] and day.day == want
    return False


# ---------------------------------------------------------------- time resolution

def sun_time(day: dt.date, which: str, place: Place) -> Tuple[dt.datetime, bool]:
    """Sunrise or sunset on `day` in the place's time zone. Second value is True if it is only an estimate."""
    tz = place.tz()
    if place.has_location:
        try:
            from astral import LocationInfo
            from astral.sun import sun
            obs = LocationInfo("", "", place.timezone or "UTC", place.latitude, place.longitude).observer
            return sun(obs, date=day, tzinfo=tz)[which], False
        except Exception:
            pass  # polar day/night or astral problem: fall through to the estimate
    default = DEFAULT_SUNRISE if which == "sunrise" else DEFAULT_SUNSET
    return dt.datetime.combine(day, default, tzinfo=tz), True


def resolve(day: dt.date, value: str, offset: int, place: Place) -> dt.datetime:
    tz = place.tz()
    if value in SUN_WORDS:
        base, _ = sun_time(day, value, place)
        return base + dt.timedelta(minutes=offset)
    h, m = value.split(":")
    return dt.datetime.combine(day, dt.time(int(h), int(m)), tzinfo=tz)


def window_on(spec: Dict[str, Any], day: dt.date, place: Place) -> Tuple[dt.datetime, dt.datetime]:
    start = resolve(day, spec["start_time"], spec["start_offset"], place)
    end = resolve(day, spec["end_time"], spec["end_offset"], place)
    if end <= start:
        end = resolve(day + dt.timedelta(days=1), spec["end_time"], spec["end_offset"], place)
    return start, end


def window_at(spec: Dict[str, Any], now: dt.datetime, place: Place) -> Optional[Tuple[dt.datetime, dt.datetime]]:
    """The window containing `now`, if the schedule is active at that moment."""
    today = now.astimezone(place.tz()).date()
    for day in (today, today - dt.timedelta(days=1)):
        if occurs_on(spec, day):
            start, end = window_on(spec, day, place)
            if start <= now < end:
                return start, end
    return None


def next_windows(spec: Dict[str, Any], now: dt.datetime, place: Place, count: int = 5,
                 horizon_days: int = 800) -> List[Tuple[dt.datetime, dt.datetime]]:
    """Upcoming (or in-progress) windows, soonest first."""
    out: List[Tuple[dt.datetime, dt.datetime]] = []
    today = now.astimezone(place.tz()).date()
    for offset in range(-1, horizon_days):
        day = today + dt.timedelta(days=offset)
        if occurs_on(spec, day):
            start, end = window_on(spec, day, place)
            if end > now:
                out.append((start, end))
                if len(out) >= count:
                    break
    return out


@dataclass
class Candidate:
    id: int
    priority: int
    start: dt.datetime
    end: dt.datetime


def pick_active(schedules: List[Dict[str, Any]], now: dt.datetime, place: Place) -> Optional[Candidate]:
    """Choose the schedule that should be running now.

    `schedules` are rows with keys id, enabled, priority and spec (validated). Highest priority wins; ties go to
    the one that started most recently, then to the lowest id.
    """
    found: List[Candidate] = []
    for row in schedules:
        if not row.get("enabled", True):
            continue
        win = window_at(row["spec"], now, place)
        if win:
            found.append(Candidate(row["id"], int(row.get("priority", 0)), win[0], win[1]))
    if not found:
        return None
    found.sort(key=lambda c: (-c.priority, -c.start.timestamp(), c.id))
    return found[0]


def beats(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """True when window `a` takes precedence over window `b` (the same order pick_active uses)."""
    ka = (-a["priority"], -a["start"].timestamp(), a["schedule_id"])
    kb = (-b["priority"], -b["start"].timestamp(), b["schedule_id"])
    return ka < kb


def annotate_overrides(windows: List[Dict[str, Any]]) -> None:
    """Add `status` ('runs', 'partial' or 'overridden') and `overridden_by` to each window, in place.

    Each window needs schedule_id, name, priority, start and end (datetimes). A window is overridden for the stretches
    where an overlapping window of a higher-priority schedule is running; `overridden_by` lists those stretches.
    """
    for w in windows:
        cuts = []
        for o in windows:
            if o is w or o["schedule_id"] == w["schedule_id"] or not beats(o, w):
                continue
            a, b = max(w["start"], o["start"]), min(w["end"], o["end"])
            if a < b:
                cuts.append((a, b, o))
        covered, last = 0.0, w["start"]
        for a, b, _ in sorted(cuts, key=lambda c: c[0]):
            a = max(a, last)
            if b > a:
                covered += (b - a).total_seconds(); last = b
        total = (w["end"] - w["start"]).total_seconds()
        w["status"] = "overridden" if cuts and covered >= total - 1 else ("partial" if cuts else "runs")
        w["overridden_by"] = [{"schedule_id": o["schedule_id"], "name": o["name"], "priority": o["priority"],
                               "start": a, "end": b} for a, b, o in sorted(cuts, key=lambda c: c[0])]


def describe(spec: Dict[str, Any]) -> str:
    """Short human-readable summary of a validated spec."""
    rep = spec["repeat"]
    t = rep["type"]
    if t == "once":
        when = f"once on {spec['start_date']}"
    elif t == "daily":
        when = "every day" if rep["interval"] == 1 else f"every {rep['interval']} days"
    elif t == "weekly":
        days = ", ".join(WEEKDAYS[d][:3] for d in rep["weekdays"])
        when = f"every {days}" if rep["interval"] == 1 else f"every {rep['interval']} weeks on {days}"
    elif t == "monthly_day":
        when = "on the last day of each month" if rep["day"] == -1 else f"on day {rep['day']} of each month"
    elif t == "monthly_nth":
        nth = "last" if rep["nth"] == -1 else ["", "first", "second", "third", "fourth"][rep["nth"]]
        when = f"on the {nth} {WEEKDAYS[rep['weekday']]} of each month"
    else:
        when = f"every year on {calendar.month_name[rep['month']]} {rep['day']}"

    def fmt(v: str, off: int) -> str:
        if v in SUN_WORDS:
            return v + (f" {off:+d} min" if off else "")
        return v
    out = f"{when}, {fmt(spec['start_time'], spec['start_offset'])} to {fmt(spec['end_time'], spec['end_offset'])}"
    if spec["start_date"] or spec["end_date"]:
        out += f" ({spec['start_date'] or 'any time'} through {spec['end_date'] or 'no end'})"
    return out
