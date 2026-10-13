"""Default catalog installed on first run (and any entries added by newer versions).

Seeded entries are ordinary rows: edit, rename, duplicate or delete them freely. Deleted ones are not recreated.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import protocol as P
from .db import Database

HALLOWEEN_FLASH = ["#ffffff", "#78a0ff"]


def _effects() -> List[Dict[str, Any]]:
    e: List[Dict[str, Any]] = []

    def add(key, name, kind, params, desc=""):
        e.append({"key": key, "name": name, "kind": kind, "params": params, "description": desc})

    for key, name, color in (("red", "Red", "#ff0000"), ("orange", "Orange", "#ff5a00"), ("green", "Green", "#00ff00"),
                             ("blue", "Blue", "#0000ff"), ("purple", "Purple", "#8c00ff"),
                             ("dark-orange", "Dark orange (red-orange)", "#5a0a00")):
        add(f"solid-{key}", f"Solid - {name}", "solid", {"color": color})
    add("solid-white", "Solid - White (white LED)", "solid", {"use_white": True, "white_level": 100})

    for pid, pname in P.BUILTIN_PATTERNS.items():
        add(f"builtin-{pid}", f"Built-in - {pname}", "builtin",
            {"pattern": pid, "colors": ["#ff0000", "#00ff00", "#0000ff"], "speed": 50, "density": 50},
            "Runs on the controller itself.")

    add("halloween-embers", "Halloween - Embers", "embers", {}, "Flickering orange and red coals.")
    add("halloween-wave", "Halloween - Creeping wave", "wave", {}, "Purple and orange rolling slowly.")
    add("halloween-ghost", "Halloween - Ghost", "comet", {}, "A green wisp with a fading tail.")
    add("halloween-lightning", "Halloween - Lightning (storm)", "lightning", {},
        "Small white and blue-white bolts over dim purple.")
    add("halloween-lightning-black", "Halloween - Lightning (black sky)", "lightning",
        {"base": "#000000"}, "The same bolts on a black strip.")
    add("halloween-lightning-orange", "Halloween - Lightning (dark orange)", "lightning",
        {"base": "#5a0a00", "flash_colors": ["#8c00ff", "#8c00ff", "#000000"], "big_colors": ["#8c00ff", "#000000"]},
        "Dark red-orange glow with purple and black flashes.")

    add("rainbow", "Rainbow", "rainbow", {})
    add("breathe-red", "Breathing red", "breathe", {"colors": ["#ff0000"]})
    add("breathe-warm", "Breathing warm white", "breathe", {"colors": ["#ffb347"], "period": 6})
    add("twinkle-white", "Twinkling stars", "twinkle", {})
    add("candy-cane", "Candy cane", "chase", {"colors": ["#ff0000", "#ffffff"], "block": 6, "speed": 10})
    add("christmas-chase", "Christmas chase", "chase",
        {"colors": ["#ff0000", "#00ff00"], "block": 8, "gap": 2, "speed": 14})
    add("color-wipe", "Color wipe", "wipe", {})
    add("sunset", "Sunset gradient", "gradient", {})
    add("ocean", "Ocean gradient", "gradient",
        {"colors": ["#003cff", "#00c8ff", "#00ffb4"], "period": 14})
    add("team-colors", "Team colors (burnt orange and white)", "chase",
        {"colors": ["#bf5700", "#ffffff"], "block": 12, "speed": 8})
    for key, name, every, desc in (("bright", "Bright", 3, "every 3rd pixel"), ("medium", "Medium", 4, "every 4th pixel"),
                                   ("low", "Low", 5, "every 5th pixel"), ("dim", "Dim", 6, "every 6th pixel")):
        add(f"night-{key}", f"Night ({name})", "nth", {"every": every},
            f"Fixed night light: the white LED on {desc}, nothing animated.")
    add("night-hourly", "Night (Hourly 4-6)", "nth_step", {},
        "Every 4th pixel for an hour, then every 5th, then every 6th, then back to 4th (changes on the hour).")
    add("custom-alternate", "Custom example - Red/blue alternate", "frames", {},
        "Two-frame animation made in the frame editor; open it to see how it works.")
    return e


def _runlists() -> List[Dict[str, Any]]:
    return [
        {"key": "halloween-show", "name": "Halloween show", "loop": True,
         "notes": "Cycles through the Halloween scenes.",
         "items": [("halloween-embers", 60), ("halloween-wave", 60), ("halloween-ghost", 60),
                   ("halloween-lightning", 90), ("halloween-lightning-black", 60),
                   ("halloween-lightning-orange", 90)]},
        {"key": "controller-favorites", "name": "Built-in favorites", "loop": True,
         "notes": "Smooth controller-native animations.",
         "items": [("builtin-7", 120), ("builtin-8", 120), ("builtin-12", 120)]},
    ]


def seed_catalog(db: Database) -> None:
    done = set(db.settings()["seeded_keys"])
    new_done = set(done)
    ids: Dict[str, int] = {}
    for row in db.list_effects():
        if row["seed_key"]:
            ids[row["seed_key"]] = row["id"]
    for e in _effects():
        if e["key"] in done:
            continue
        created = db.create_effect(e["name"], e["kind"], e["params"], e["description"], seed_key=e["key"])
        ids[e["key"]] = created["id"]
        new_done.add(e["key"])
    for r in _runlists():
        key = "runlist:" + r["key"]
        if key in done:
            continue
        items = [{"effect_id": ids[k], "duration_sec": d} for k, d in r["items"] if k in ids]
        if items:
            db.create_runlist(r["name"], r["loop"], False, r["notes"], items)
        new_done.add(key)
    if new_done != done:
        db.update_settings({"seeded_keys": sorted(new_done)})
