"""SQLite storage: effects, run lists, schedules and settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import effects as fx
from . import scheduler as sch

SCHEMA_VERSION = 2

DEFAULT_SETTINGS: Dict[str, Any] = {
    "controller_host": "",        # IP or host name of the controller ("" = not configured yet)
    "controller_port": 5577,
    "led_count": 0,               # 0 = ask the controller (it reports 250 here)
    "fps": 5,                     # frames per second for streamed effects (1-15)
    "min_gap_ms": 30,             # minimum pause between two commands to the controller
    "max_groups": 10,             # most distinct colors painted per frame (each costs a command)
    "brightness": 100,            # master brightness 0-100
    "idle_action": "off",         # what to do when nothing is scheduled: off | leave | solid
    "idle_color": "#ff5a00",
    "timezone": "",               # IANA name, e.g. America/Chicago ("" = this machine's zone)
    "latitude": None,             # for sunrise/sunset times
    "longitude": None,
    "log_retention_days": 60,     # logs older than this are deleted automatically (31 or more)
    "seeded_keys": [],
}

MAX_LOG_ROWS = 500_000            # a safety net on top of the age limit
LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


class NotFound(LookupError):
    """The requested row does not exist."""


class Conflict(ValueError):
    """The change conflicts with existing data (duplicate name, item in use)."""


def _now() -> float:
    return time.time()


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def _migrate(self) -> None:
        with self.lock:
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self.conn.executescript("""
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE effects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    kind TEXT NOT NULL,
                    params TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    seed_key TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE runlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    loop INTEGER NOT NULL DEFAULT 1,
                    shuffle INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE runlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    runlist_id INTEGER NOT NULL REFERENCES runlists(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    effect_id INTEGER NOT NULL REFERENCES effects(id) ON DELETE RESTRICT,
                    duration_sec REAL NOT NULL);
                CREATE TABLE schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    runlist_id INTEGER NOT NULL REFERENCES runlists(id) ON DELETE RESTRICT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 0,
                    spec TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL);
                PRAGMA user_version = 1;
                """)
                self.conn.commit()
            if version < 2:
                self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL, lvl INTEGER NOT NULL, level TEXT NOT NULL, logger TEXT NOT NULL, msg TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS logs_ts ON logs(ts);
                PRAGMA user_version = 2;
                """)
                self.conn.commit()

    # ------------------------------------------------------------ helpers
    def _one(self, sql: str, args=()) -> Optional[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def _all(self, sql: str, args=()) -> List[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def _write(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.lock:
            try:
                cur = self.conn.execute(sql, args)
                self.conn.commit()
                return cur
            except sqlite3.IntegrityError as exc:
                self.conn.rollback()
                msg = str(exc)
                if "UNIQUE" in msg:
                    raise Conflict("that name is already in use") from exc
                raise Conflict(msg) from exc

    @staticmethod
    def _name(name: Any) -> str:
        name = str(name or "").strip()
        if not name:
            raise ValueError("a name is required")
        if len(name) > 100:
            raise ValueError("names are limited to 100 characters")
        return name

    def _unique_copy_name(self, table: str, base: str) -> str:
        candidate, n = f"Copy of {base}", 2
        while self._one(f"SELECT 1 FROM {table} WHERE name = ?", (candidate,)):
            candidate = f"Copy of {base} ({n})"
            n += 1
        return candidate

    # ------------------------------------------------------------ settings
    def settings(self) -> Dict[str, Any]:
        out = dict(DEFAULT_SETTINGS)
        for row in self._all("SELECT key, value FROM settings"):
            if row["key"] in out:
                out[row["key"]] = json.loads(row["value"])
        return out

    def update_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        unknown = set(changes) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        clean = self._clean_settings(changes)
        with self.lock:
            for k, v in clean.items():
                self.conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, json.dumps(v)))
            self.conn.commit()
        return self.settings()

    @staticmethod
    def _clean_settings(ch: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}

        def num(key, lo, hi, kind=int):
            if key in ch:
                try:
                    out[key] = max(lo, min(hi, kind(ch[key])))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be a number") from exc
        num("controller_port", 1, 65535)
        num("led_count", 0, 255)
        num("fps", 1, 15)
        num("min_gap_ms", 5, 500)
        num("max_groups", 2, 30)
        num("brightness", 0, 100)
        num("log_retention_days", 31, 3650)
        if "controller_host" in ch:
            out["controller_host"] = str(ch["controller_host"] or "").strip()
        if "idle_action" in ch:
            if ch["idle_action"] not in ("off", "leave", "solid"):
                raise ValueError("idle_action must be off, leave or solid")
            out["idle_action"] = ch["idle_action"]
        if "idle_color" in ch:
            from .protocol import parse_hex, to_hex
            out["idle_color"] = to_hex(parse_hex(str(ch["idle_color"])))
        if "timezone" in ch:
            tz = str(ch["timezone"] or "").strip()
            if tz:
                sch.Place(timezone=tz).tz()  # raises SpecError if unknown
            out["timezone"] = tz
        for key, lo, hi in (("latitude", -90, 90), ("longitude", -180, 180)):
            if key in ch:
                if ch[key] in (None, ""):
                    out[key] = None
                else:
                    try:
                        out[key] = max(lo, min(hi, float(ch[key])))
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"{key} must be a number") from exc
        if "seeded_keys" in ch:
            out["seeded_keys"] = list(ch["seeded_keys"])
        return out

    def auth_secret(self) -> str:
        """A random secret created on first use (signs login sessions). Not part of the editable settings."""
        row = self._one("SELECT value FROM settings WHERE key = '_auth_secret'")
        if row:
            return json.loads(row["value"])
        import secrets
        secret = secrets.token_hex(32)
        self._write("INSERT INTO settings(key, value) VALUES('_auth_secret', ?)", (json.dumps(secret),))
        return secret

    def place(self) -> sch.Place:
        s = self.settings()
        return sch.Place(s["timezone"], s["latitude"], s["longitude"])

    # ------------------------------------------------------------ logs
    def add_log(self, ts: float, level: str, logger: str, msg: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO logs(ts, lvl, level, logger, msg) VALUES(?,?,?,?,?)",
                              (ts, LOG_LEVELS.get(level, 20), level, logger, msg))
            self.conn.commit()

    @staticmethod
    def _log_filter(since: Optional[float], until: Optional[float], level: Optional[str], q: Optional[str]):
        where, args = ["1=1"], []
        if since is not None:
            where.append("ts >= ?"); args.append(since)
        if until is not None:
            where.append("ts <= ?"); args.append(until)
        if level:
            if level.upper() not in LOG_LEVELS:
                raise ValueError(f"level must be one of {', '.join(LOG_LEVELS)}")
            where.append("lvl >= ?"); args.append(LOG_LEVELS[level.upper()])
        if q:
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("msg LIKE ? ESCAPE '\\'"); args.append(f"%{esc}%")
        return " AND ".join(where), args

    def query_logs(self, since: Optional[float] = None, until: Optional[float] = None, level: Optional[str] = None,
                   q: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
        """Matching log lines, oldest first. If more than `limit` match, the NEWEST `limit` are returned."""
        where, args = self._log_filter(since, until, level, q)
        limit = max(1, min(10000, int(limit)))
        total = self._one(f"SELECT COUNT(*) AS n FROM logs WHERE {where}", args)["n"]
        rows = self._all(f"SELECT ts, level, logger, msg FROM logs WHERE {where} ORDER BY ts DESC, id DESC LIMIT ?",
                         args + [limit])
        bounds = self._one("SELECT MIN(ts) AS lo, MAX(ts) AS hi, COUNT(*) AS n FROM logs")
        return {"entries": [{"ts": r["ts"], "level": r["level"], "logger": r["logger"], "msg": r["msg"]} for r in reversed(rows)],
                "total": total, "limit": limit, "truncated": total > limit,
                "oldest": bounds["lo"], "newest": bounds["hi"], "stored": bounds["n"],
                "retention_days": self.settings()["log_retention_days"]}

    def prune_logs(self, now: Optional[float] = None) -> int:
        """Delete logs older than the retention period (and, as a safety net, anything beyond MAX_LOG_ROWS)."""
        cutoff = (now if now is not None else _now()) - self.settings()["log_retention_days"] * 86400
        with self.lock:
            removed = self.conn.execute("DELETE FROM logs WHERE ts < ?", (cutoff,)).rowcount
            over = self.conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] - MAX_LOG_ROWS
            if over > 0:
                removed += self.conn.execute("DELETE FROM logs WHERE id IN (SELECT id FROM logs ORDER BY ts, id LIMIT ?)",
                                             (over,)).rowcount
            self.conn.commit()
        return removed

    # ------------------------------------------------------------ effects
    @staticmethod
    def _effect_row(r: sqlite3.Row) -> Dict[str, Any]:
        params = json.loads(r["params"])
        try:   # bring effects saved by an older version up to date (renamed or converted settings)
            params = fx.get_kind(r["kind"]).clean(params)
        except (fx.ParamError, ValueError):
            pass
        return {"id": r["id"], "name": r["name"], "kind": r["kind"], "params": params,
                "description": r["description"], "seed_key": r["seed_key"],
                "created": r["created"], "updated": r["updated"]}

    def list_effects(self) -> List[Dict[str, Any]]:
        return [self._effect_row(r) for r in self._all("SELECT * FROM effects ORDER BY name COLLATE NOCASE")]

    def get_effect(self, effect_id: int) -> Dict[str, Any]:
        r = self._one("SELECT * FROM effects WHERE id = ?", (effect_id,))
        if not r:
            raise NotFound(f"effect {effect_id} not found")
        return self._effect_row(r)

    def create_effect(self, name: str, kind: str, params: Optional[dict] = None, description: str = "",
                      seed_key: Optional[str] = None) -> Dict[str, Any]:
        cleaned = fx.get_kind(kind).clean(params or {})
        now = _now()
        cur = self._write("INSERT INTO effects(name, kind, params, description, seed_key, created, updated) "
                          "VALUES(?,?,?,?,?,?,?)",
                          (self._name(name), kind, json.dumps(cleaned), description or "", seed_key, now, now))
        return self.get_effect(cur.lastrowid)

    def update_effect(self, effect_id: int, name: Optional[str] = None, params: Optional[dict] = None,
                      description: Optional[str] = None) -> Dict[str, Any]:
        cur = self.get_effect(effect_id)
        new_name = self._name(name) if name is not None else cur["name"]
        new_params = fx.get_kind(cur["kind"]).clean(params) if params is not None else cur["params"]
        new_desc = description if description is not None else cur["description"]
        self._write("UPDATE effects SET name=?, params=?, description=?, updated=? WHERE id=?",
                    (new_name, json.dumps(new_params), new_desc, _now(), effect_id))
        return self.get_effect(effect_id)

    def duplicate_effect(self, effect_id: int, name: Optional[str] = None) -> Dict[str, Any]:
        src = self.get_effect(effect_id)
        return self.create_effect(name or self._unique_copy_name("effects", src["name"]), src["kind"],
                                  src["params"], src["description"])

    def delete_effect(self, effect_id: int) -> None:
        self.get_effect(effect_id)
        used = self._all("SELECT DISTINCT r.name FROM runlist_items i JOIN runlists r ON r.id = i.runlist_id "
                         "WHERE i.effect_id = ?", (effect_id,))
        if used:
            raise Conflict("this effect is used by run list(s): " + ", ".join(r["name"] for r in used))
        self._write("DELETE FROM effects WHERE id = ?", (effect_id,))

    # ------------------------------------------------------------ run lists
    def _items(self, runlist_id: int) -> List[Dict[str, Any]]:
        rows = self._all("SELECT i.id, i.effect_id, i.duration_sec, e.name AS effect_name, e.kind AS effect_kind "
                         "FROM runlist_items i JOIN effects e ON e.id = i.effect_id "
                         "WHERE i.runlist_id = ? ORDER BY i.position", (runlist_id,))
        return [{"effect_id": r["effect_id"], "effect_name": r["effect_name"], "effect_kind": r["effect_kind"],
                 "duration_sec": r["duration_sec"]} for r in rows]

    def _runlist_row(self, r: sqlite3.Row) -> Dict[str, Any]:
        items = self._items(r["id"])
        return {"id": r["id"], "name": r["name"], "loop": bool(r["loop"]), "shuffle": bool(r["shuffle"]),
                "notes": r["notes"], "items": items, "total_sec": sum(i["duration_sec"] for i in items),
                "created": r["created"], "updated": r["updated"]}

    def list_runlists(self) -> List[Dict[str, Any]]:
        return [self._runlist_row(r) for r in self._all("SELECT * FROM runlists ORDER BY name COLLATE NOCASE")]

    def get_runlist(self, runlist_id: int) -> Dict[str, Any]:
        r = self._one("SELECT * FROM runlists WHERE id = ?", (runlist_id,))
        if not r:
            raise NotFound(f"run list {runlist_id} not found")
        return self._runlist_row(r)

    def _clean_items(self, items: Optional[List[dict]]) -> List[dict]:
        out = []
        for n, it in enumerate(items or [], 1):
            try:
                eid, dur = int(it["effect_id"]), float(it.get("duration_sec", 60))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"item {n}: needs effect_id and a numeric duration_sec") from exc
            if not self._one("SELECT 1 FROM effects WHERE id = ?", (eid,)):
                raise ValueError(f"item {n}: effect {eid} does not exist")
            out.append({"effect_id": eid, "duration_sec": max(1.0, min(86400.0, dur))})
        return out

    def _set_items(self, runlist_id: int, items: List[dict]) -> None:
        self.conn.execute("DELETE FROM runlist_items WHERE runlist_id = ?", (runlist_id,))
        for pos, it in enumerate(items):
            self.conn.execute("INSERT INTO runlist_items(runlist_id, position, effect_id, duration_sec) VALUES(?,?,?,?)",
                              (runlist_id, pos, it["effect_id"], it["duration_sec"]))

    def create_runlist(self, name: str, loop: bool = True, shuffle: bool = False, notes: str = "",
                       items: Optional[List[dict]] = None) -> Dict[str, Any]:
        items = self._clean_items(items)
        with self.lock:
            now = _now()
            cur = self._write("INSERT INTO runlists(name, loop, shuffle, notes, created, updated) VALUES(?,?,?,?,?,?)",
                              (self._name(name), int(bool(loop)), int(bool(shuffle)), notes or "", now, now))
            self._set_items(cur.lastrowid, items)
            self.conn.commit()
        return self.get_runlist(cur.lastrowid)

    def update_runlist(self, runlist_id: int, name: Optional[str] = None, loop: Optional[bool] = None,
                       shuffle: Optional[bool] = None, notes: Optional[str] = None,
                       items: Optional[List[dict]] = None) -> Dict[str, Any]:
        cur = self.get_runlist(runlist_id)
        new_items = self._clean_items(items) if items is not None else None
        with self.lock:
            self._write("UPDATE runlists SET name=?, loop=?, shuffle=?, notes=?, updated=? WHERE id=?",
                        (self._name(name) if name is not None else cur["name"],
                         int(bool(cur["loop"] if loop is None else loop)),
                         int(bool(cur["shuffle"] if shuffle is None else shuffle)),
                         cur["notes"] if notes is None else notes, _now(), runlist_id))
            if new_items is not None:
                self._set_items(runlist_id, new_items)
                self.conn.commit()
        return self.get_runlist(runlist_id)

    def duplicate_runlist(self, runlist_id: int, name: Optional[str] = None) -> Dict[str, Any]:
        src = self.get_runlist(runlist_id)
        return self.create_runlist(name or self._unique_copy_name("runlists", src["name"]), src["loop"],
                                   src["shuffle"], src["notes"], src["items"])

    def delete_runlist(self, runlist_id: int) -> None:
        self.get_runlist(runlist_id)
        used = self._all("SELECT name FROM schedules WHERE runlist_id = ?", (runlist_id,))
        if used:
            raise Conflict("this run list is used by schedule(s): " + ", ".join(r["name"] for r in used))
        self._write("DELETE FROM runlists WHERE id = ?", (runlist_id,))

    # ------------------------------------------------------------ schedules
    def _schedule_row(self, r: sqlite3.Row) -> Dict[str, Any]:
        spec = sch.validate_spec(json.loads(r["spec"]))
        rl = self._one("SELECT name FROM runlists WHERE id = ?", (r["runlist_id"],))
        return {"id": r["id"], "name": r["name"], "runlist_id": r["runlist_id"],
                "runlist_name": rl["name"] if rl else "", "enabled": bool(r["enabled"]),
                "priority": r["priority"], "spec": sch.spec_to_json(spec), "summary": sch.describe(spec),
                "created": r["created"], "updated": r["updated"]}

    def list_schedules(self) -> List[Dict[str, Any]]:
        return [self._schedule_row(r) for r in self._all("SELECT * FROM schedules ORDER BY name COLLATE NOCASE")]

    def get_schedule(self, schedule_id: int) -> Dict[str, Any]:
        r = self._one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        if not r:
            raise NotFound(f"schedule {schedule_id} not found")
        return self._schedule_row(r)

    def create_schedule(self, name: str, runlist_id: int, spec: dict, enabled: bool = True,
                        priority: int = 0) -> Dict[str, Any]:
        self.get_runlist(runlist_id)
        clean = sch.spec_to_json(sch.validate_spec(spec))
        now = _now()
        cur = self._write("INSERT INTO schedules(name, runlist_id, enabled, priority, spec, created, updated) "
                          "VALUES(?,?,?,?,?,?,?)",
                          (self._name(name), runlist_id, int(bool(enabled)), int(priority), json.dumps(clean), now, now))
        return self.get_schedule(cur.lastrowid)

    def update_schedule(self, schedule_id: int, name: Optional[str] = None, runlist_id: Optional[int] = None,
                        spec: Optional[dict] = None, enabled: Optional[bool] = None,
                        priority: Optional[int] = None) -> Dict[str, Any]:
        cur = self.get_schedule(schedule_id)
        if runlist_id is not None:
            self.get_runlist(runlist_id)
        new_spec = sch.spec_to_json(sch.validate_spec(spec)) if spec is not None else sch.spec_to_json(cur["spec"])
        self._write("UPDATE schedules SET name=?, runlist_id=?, enabled=?, priority=?, spec=?, updated=? WHERE id=?",
                    (self._name(name) if name is not None else cur["name"],
                     runlist_id if runlist_id is not None else cur["runlist_id"],
                     int(bool(cur["enabled"] if enabled is None else enabled)),
                     int(cur["priority"] if priority is None else priority), json.dumps(new_spec), _now(), schedule_id))
        return self.get_schedule(schedule_id)

    def duplicate_schedule(self, schedule_id: int, name: Optional[str] = None) -> Dict[str, Any]:
        src = self.get_schedule(schedule_id)
        return self.create_schedule(name or self._unique_copy_name("schedules", src["name"]), src["runlist_id"],
                                    src["spec"], False, src["priority"])

    def delete_schedule(self, schedule_id: int) -> None:
        self.get_schedule(schedule_id)
        self._write("DELETE FROM schedules WHERE id = ?", (schedule_id,))
