# tm1814-led — Technical README

Everything an engineer needs to understand, extend, debug, or re-implement this project: the controller's wire protocol, the streaming engine, the data model, the scheduler, the REST API, the installer, and the limits we know about.

For installation and everyday use, see [README.md](README.md).

**Confidence labels used throughout**

| Label | Meaning |
| --- | --- |
| **Verified** | Observed on real hardware (a Zengge/Surplife controller, model byte `0xB6`, firmware string `WF.52.B6.26.0,V9_ZG-BL-UFO`, TM1814 RGBW 24 V pixels) during development, with the lights watched by a person. |
| **Upstream** | Taken from the open-source `flux_led` project (pull requests #538–#544, whose author tested them on a `0xB6`) and cross-checked with our own bytes where possible. |
| **Inferred** | A reasoned guess that fits the evidence but was not directly confirmed. |

---

## Contents

1. [Architecture](#1-architecture)
2. [The controller](#2-the-controller)
3. [Network interfaces](#3-network-interfaces)
4. [Message framing](#4-message-framing)
5. [Command reference](#5-command-reference)
6. [Commands that are deliberately not used](#6-commands-that-are-deliberately-not-used)
7. [The streaming engine](#7-the-streaming-engine)
8. [Effects](#8-effects)
9. [Data model](#9-data-model)
10. [Scheduling](#10-scheduling)
11. [The runner](#11-the-runner)
12. [REST API](#12-rest-api)
13. [Security model](#13-security-model)
14. [Installer internals](#14-installer-internals)
15. [Configuration reference](#15-configuration-reference)
16. [Testing and hardware validation](#16-testing-and-hardware-validation)
17. [Known limits and open questions](#17-known-limits-and-open-questions)
18. [Provenance and credits](#18-provenance-and-credits)

---

## 1. Architecture

```
 Browser (single-page app, no build step)
        │  HTTP: /api/*  +  static files
        ▼
 ┌─────────────────────────── one Python process ───────────────────────────┐
 │  api.py  FastAPI app ── db.py (SQLite)                                   │
 │     │                      effects, run lists, schedules, settings       │
 │     ▼                                                                    │
 │  runner.py                                                               │
 │   ├─ Runner thread ────── plays one "plan" (a run list or one effect)    │
 │   │      │  builds Effect objects (effects/*.py), renders frames         │
 │   │      ▼                                                               │
 │   │   controller.py ──── pacing, diffing, reconnect ── protocol.py       │
 │   └─ ScheduleManager thread ── scheduler.py picks the active schedule    │
 └──────────────────────────────────────────────┬───────────────────────────┘
                                                │ TCP 5577 (one persistent connection)
                                                ▼
                                  LED controller ──► 2 outputs (mirrored) ──► TM1814 pixels
```

| Module | Responsibility |
| --- | --- |
| `protocol.py` | Pure functions: build and parse bytes. No sockets. Fully unit-tested against hardware-verified byte strings. |
| `controller.py` | `Controller` (real TCP client), `SimulatedController` (same interface, no network), `discover()` (UDP). Pacing, reconnect, diff painting. |
| `effects/` | Effect kinds. `native.py` (run on the controller), `fixed.py` (a still picture painted once), `animated.py` (rendered frame by frame), `frames.py` (custom animations). |
| `db.py` | SQLite storage and validation (effects, run lists, schedules, settings). |
| `seed.py` | The default catalog and run lists. |
| `scheduler.py` | Pure functions: schedule validation, date rules, windows, sun times, choosing the active schedule. |
| `runner.py` | `Runner` (plays plans) and `ScheduleManager` (starts/stops them per schedule). |
| `api.py` | FastAPI routes, error mapping, optional password, static file hosting. |
| `installer.py` | `--install` / `--uninstall` for systemd, launchd, Windows Task Scheduler, cron. |
| `cli.py` | Entry point and small utility commands. |
| `web/` | The single-page web UI (HTML/CSS/JS, no external resources). |

Threads: the FastAPI worker pool serves requests; one `Runner` worker thread talks to the controller; one `ScheduleManager` thread evaluates schedules every 5 seconds. All shared state is behind locks (`Database.lock`, `Runner.lock`, `Controller._lock`).

---

## 2. The controller

| Property | Value | Label |
| --- | --- | --- |
| Product family | Zengge / Surplife / Magic Home "symphony_line" (app product id 182) | Verified (app data) |
| Model byte | `0xB6` (= 182) | Verified |
| Discovery model string | `AK001-ZJ21413` | Verified |
| Firmware string (from the app) | `WF.52.B6.26.0,V9_ZG-BL-UFO`; state-frame version byte `0x15` (21) | Verified |
| LED driver | TM1814, RGBW, 24 V, single-wire data | Owner-supplied |
| Outputs | Two data outputs. **Both receive identical data** (pixel *n* lights one spot per output). | Verified |
| Pixel count | `250` reported in state byte 18 | Verified |
| Pixel = group of LEDs | At 24 V one addressable "pixel" drives a small group of LEDs, so 100 pixels covered more than 100 physical lights. | Verified (observed) |
| Authentication | None on the local network | Verified |
| Radios | Wi-Fi + Bluetooth LE (the vendor app also uses a cloud service for remote access; this project never uses either) | Verified (app UI) |

**Pixel data flows one way.** The TM1814 data line goes from the controller to the pixels; nothing is read back from the LEDs. The controller can only report what it was *told*, so there is no way to detect how many pixels physically exist by "painting and reading back". The configured count (state byte 18) is the controller's stored setting, and a human has to confirm physical reality.

**"Off" is not electrically off.** The power command makes the controller send black. The 24 V supply and every pixel's driver stay powered and listening, so a small standby current remains. Only a relay or smart plug on the supply's AC side removes it.

---

## 3. Network interfaces

| Interface | Use | Label |
| --- | --- | --- |
| **TCP 5577** | Control channel. One persistent connection. | Verified |
| **UDP 48899** | Discovery. Send `HF-A11ASSISTHREAD` (broadcast or unicast); the controller replies `ip,MAC,model,token` as ASCII. | Verified |
| Other TCP ports | A full port scan was inconclusive (the device drops probes); only 5577 and 48899 are confirmed. | Verified |

Discovery reply example (documentation address): `192.168.1.50,AABBCCDDEEFF,AK001-ZJ21413,<32 hex chars>`. The trailing token's purpose is unknown.

### Connection behavior and rate limits (Verified)

* Use **one persistent TCP connection**. Rapidly opening and closing connections makes the controller stop accepting new ones for a few seconds.
* The controller **pushes unsolicited state frames** back on the socket (for example after remote-control or app changes). The client must read and discard these periodically, or buffers fill. `Controller._drain()` does so every 20 sends and before every query.
* **Flooding stalls it.** Sending about 75 paint commands per second made the controller stop reading from the socket (send timeouts) and it briefly reported itself off before recovering by itself. A pause of ~30 ms between commands (about 30 commands/s) is stable and was used for all live testing. This project enforces the gap globally in `Controller._pace()`; it is the `min_gap_ms` setting (minimum 5).
* The controller **closes idle connections** (seen in real logs as `Broken pipe` on the first send after a quiet period). Before every send the client checks whether the peer closed the socket and, if so, reconnects first; a send that still fails is retried once on a fresh connection, so an idle gap never shows up as an error or a delay.
* After `E1 23` (scribble init) wait about 0.4 s before painting.
* Only one controller client at a time is advisable; the vendor app and this program should not stream simultaneously.

---

## 4. Message framing

Most commands are wrapped in an **envelope**:

```
 offset  size  field
   0      4    b0 b1 b2 b3          magic
   4      2    00 01                (constant)
   6      1    version              0x02 for this firmware (version 0x01 is ignored by it)
   7      1    counter              increments per message; any value is accepted (the controller echoes its own)
   8      2    length               big-endian length of the inner message
  10      n    inner message
 10+n     1    checksum             (sum of all preceding bytes) & 0xFF
```

Replies use the same envelope. `protocol.unwrap()` splits a receive buffer into envelopes, skips ones with a bad checksum, and returns any trailing partial frame so it can be completed by the next read.

Two commands are sent **plain** (not wrapped): the state query and power. Plain commands carry a one-byte checksum of their own bytes.

**Verified example** — the wrapped "solid green" color message (counter 0):

```
b0 b1 b2 b3 00 01 02 00 00 0e   e0 01 00 a1 3c 64 64 00 00 00 00 14 00 00   71
 └ magic ┘ └─┘ ver ctr └len=14┘ └──────────── inner (14 bytes) ───────────┘  └ checksum
```

(`e0 01 00 a1 <hue/2> <sat> <val> …` is the older solid-color message; this project uses `E1 22` instead, see 5.3.)

### Color encoding

The `E1` commands describe a color as a **5-byte pixel record**:

```
[ hue/2 (0–180) , saturation (0–100) , value (0–100) , 0x00 , white (0–100) ]
```

RGB → HSV conversion happens in `protocol.hsv_bytes()`. Hue is stored halved so it fits in a byte. A pure white *LED* level uses `[0, 100, 0, 0, W]` in `E1 22` (saturation **must** be 100 or the controller silently ignores the frame — Upstream, hardware-tested) and `[0, 0, 0, 0, W]` semantics in `E1 26`.

---

## 5. Command reference

All hex below is the **inner message** (before the envelope) unless stated.

### 5.1 State query and reply — Verified

Send (plain): `81 8a 8b 96`

Reply (27 bytes, found anywhere in the receive buffer; may also arrive wrapped in an envelope):

```
ea 81 01 00 b6 15 23 25 65 50 f0 b4 64 64 3c 1d 05 00 fa 00 00 00 20 02 00 00 03
```

| Byte | Example | Meaning |
| --- | --- | --- |
| 0–1 | `ea 81` | extended state header |
| 2 | `01` | changes between replies (likely a counter) — Inferred |
| 4 | `b6` | model number (182) |
| 5 | `15` | firmware version (21) |
| 6 | `23` | power: `23` on, `24` off |
| 7 | `25` | current "pattern": `0x25` a built-in scene was running; `0x61` static color; `0x66` per-pixel (scribble) mode |
| 9 | `50` | speed |
| 10 | `f0` | `f0` = RGB mode, `0f` = white mode |
| 11 | `b4` | hue/2 (180 → 360° = red) |
| 12 | `64` | saturation (100) |
| 13 | `64` | value (100) |
| 14–15 | `3c 1d` | white temperature / white brightness — Inferred |
| 18 | `fa` | **configured LED count (250)** |
| others | | unknown (some vary when settings change; candidates for IC type / wiring order) |

Parsed by `protocol.parse_state()` into a `State` dataclass. The runner uses byte 6 (power) and byte 7 (`0x66`) for its health check.

### 5.2 Power — Verified

Send (plain): `71 23 0f a3` (on) and `71 24 0f a4` (off). Takes effect immediately; "off" = black.

### 5.3 Solid color / 20 segments — `E1 22` — Verified (color), Upstream (format)

```
e1 22 00 00 00 00 14   [ 5-byte pixel record ] × 20
```

The strip is split into 20 equal segments (`0x14`); send the same record 20 times for a solid color, or different records for a coarse gradient. The controller holds the result by itself (it keeps showing it if this program stops).

### 5.4 Built-in animation — `E1 21` — Verified (patterns 7, 8, 12 watched), Upstream (format)

```
e1 21 01 64 PP OO DD NS SP 00 00 00 00 00 00 CC   [ 5-byte color record ] × CC
        │  │  │  │  │  │  │                  └ color count (1–8)
        │  │  │  │  │  └ speed 0–100
        │  │  │  │  └ density 0–100
        │  │  │  └ direction: 01 left→right, 02 right→left
        │  │  └ option / variant: 00 default, 01 variant 1, 02 variant 2
        │  └ pattern id (table below)
        └ constant 0x64
```

The controller renders these itself, smoothly, and keeps running them with no host attached.

| id | name | id | name | id | name |
| --- | --- | --- | --- | --- | --- |
| 1 | Wave | 9 | Breathe | 17 | Comet |
| 2 | Meteor | 10 | Jump | 18 | Gradient Meteor |
| 3 | Streamer | 11 | Strobe | 19 | Volcano |
| 4 | Building Blocks | 12 | Twinkling Stars | 20 | Superluminal |
| 5 | Flowing Water | 13 | Stars Wink | 21 | Rainbow Bridge |
| 6 | Chase | 14 | Warning | 22 | Gradient Overlay |
| 7 | Horse Racing | 15 | Collision | 101 | Static Gradient |
| 8 | Cycle | 16 | Fireworks | 102 | Static Fill |

### 5.5 Per-pixel mode — `E1 23` (init) and `E1 26` (paint)

This is how fixed and animated effects work.

**Init** — Verified:

```
e1 23 01 00 01 50 64 00 N   [ 00 00 00 00 00 00 64 ] × N        (N = LED count, 1–255)
```

Switches to per-LED mode (state byte 7 becomes `0x66`). It does not render by itself. `N` is a single byte, so **per-pixel mode supports at most 255 pixels**.

**Paint** — Verified:

```
e1 26 EE DD DS SP BL HH SS VV 00 WW BS   <bitmap: ceil(N/8) bytes, MSB first>
      │  │  │  │  │  └─ color record (hue/2, sat, val, 0, white)
      │  │  │  │  └ blink mode (00 none, 08 slow, 10 fast)
      │  │  │  └ speed (0x64 used)
      │  │  └ density (0x50 used)
      │  └ direction (01 used)
      └ effect: 00 static, 01 flowing, 02 twinkling stars, 03 variant, 05 stars wink, 08 accumulate
```

Paints one color onto every pixel whose bit is set. **Pixels not selected keep their current color** (Verified: red everywhere, then blue on every other pixel shows alternating red/blue). Bit *i* of the bitmap (byte `i // 8`, bit `0x80 >> (i % 8)`) selects pixel *i*. This project always uses effect `00` (static) and no blink; the other values come from upstream and are not exposed.

A frame is therefore sent as **one `E1 26` per distinct color**. This is the central cost model of the streaming engine (section 7).

### 5.6 Other messages seen — informational

| Message | Reply | Notes |
| --- | --- | --- |
| wrapped `2b 2c 2d` | `2b 02 00 00 00 00 3a 00 00 00 00 00 00` | remote-control configuration; meaning of `3a` unknown |
| wrapped `e0 06` | `e0 06 01 00… 02 00… 03 00… … 06 00…` | timer list: **six slots, all empty** here. The controller has an on-board clock/timer feature this project does not use. |
| `e0 01 00 a1 H S V …` | none | older solid-color message (works; superseded by `E1 22`) |

---

## 6. Commands that are deliberately not used

`protocol.FORBIDDEN_PREFIXES` and `Controller.send_inner()` refuse (raising `ControllerError`) any inner message beginning with:

| Prefix | Why |
| --- | --- |
| `62 …` | Configuration **write** on related models (pixel count, IC type, wiring order, segments). Persistent; a wrong value changes how the whole run displays. |
| `e0 43 …` | Factory **reset**. |

**Not supported by this firmware (Verified: no reply):** the older configuration *read* queries `63 12 21 f0`, `63 12 21 0f`, `63 12 21 36`, `63 12 23 36`, plus `44 4a 4b`, `52 5a 5b`, `b0 ba bb`, in plain and wrapped (v1 and v2) forms. This is consistent with the vendor app's product table: product 182 has no such template; they belong to older product generations.

Reading the pixel count therefore uses state byte 18 (5.1). There is **no known way to read or change IC type, color order or the pixel count** on this model; those layouts exist only in the vendor app's compiled code. Use the vendor app for that.

---

## 7. The streaming engine

Implemented in `controller.py` (`show_frame`) and `runner.py`.

**Goal:** show an arbitrary 250-pixel frame as fast as the controller allows, using only the `E1 26` paint command.

1. **Master brightness** is applied to every color before sending (`Controller.brightness`, 0–100).
2. **First frame, or after any reconnect/mode change:** enter scribble mode (`E1 23`), wait 0.4 s, then paint *every* pixel.
3. **Diffing:** later frames compare against the previous frame and send only pixels whose color changed. An unchanged frame sends **zero** commands (Verified in tests).
4. **Grouping:** changed pixels are grouped by color; each group is one `E1 26`.
5. **Adaptive quantization** (`group_by_color`): if a frame needs more than `max_groups` distinct colors (default 10), colors are rounded to progressively coarser steps (1, 4, 8, 16, 32, 48, 64, 96) until it fits. Small steps preserve exact colors for simple scenes; busy gradients (a rainbow has >100 colors) get banded. Zero channels stay zero and non-zero channels never round to zero, so a dark red-orange never collapses to black or drifts toward plain orange.
6. **Pacing:** `min_gap` seconds (default 30 ms) between *any* two commands. With 10 groups per frame that is ≈0.3 s per frame, so busy frames run at ~3 fps, simple ones at the configured `fps` (default 5, max 15).
7. **Periodic full repaint** every 20 s heals quantization drift and any dropped packet.
8. **Health check** every 30 s while an effect plays: query state; if the controller reports power off, power it back on; if it left per-pixel mode (`pattern != 0x66`, for example after a power cut and reboot), re-enter it. Native effects are re-applied.
9. **Errors:** any socket error closes the connection, records `last_error`, and the runner retries every ~3 s until the controller returns, then repaints in full.

Native effects (`solid`, `segments`, `builtin`) send one command; the runner then only waits and health-checks.

**Fixed effects** (`static = True`, e.g. `nth`) are painted **once** (`E1 23` + the paint commands for `frame(0)`), after which nothing is sent: the controller keeps showing the picture, even if this program stops. The runner only health-checks every 30 s and repaints if the controller reports power off or has left per-pixel mode (for example after a power cut). A fixed effect needs two paint commands at most (one color for the lit pixels, one for the rest).

**Pixel types.** A frame is a list of pixels; a pixel is `(r, g, b)` or `(0, 0, 0, level)`, which lights only the **white LED** of the RGBW pixel at `level` 0–100 (sent as an `E1 26` paint in white mode). White pixels follow master brightness, are never quantized, and are drawn as a warm white in previews.

---

## 8. Effects

An effect = **kind** (code, `effects/`) + **parameters** (JSON in the database). Every kind belongs to one of three **families** (`family` in `/api/effect-kinds`): `native` (runs on the controller), `fixed` (a still picture, painted once) or `animated` (rendered frame by frame by this program).

### 8.1 Kinds

| Kind | Family | Previewable | Parameters (summary) |
| --- | --- | --- | --- |
| `solid` | native | yes | color; or use the white LED + level |
| `segments` | native | yes | 1–20 colors repeated to fill 20 segments |
| `builtin` | native | no | pattern, 1–8 colors, speed, density, direction, variant |
| `embers` | animated | yes | color, hot color, block size, dimmest level, flicker |
| `wave` | animated | yes | two colors, wavelength, period, floor/strength, direction |
| `comet` | animated | yes | color, background, tail length, speed, gap, bounce, direction |
| `lightning` | animated | yes | base, bolt colors, big-flash colors, bolts/s, bolt width range, life range, flicker, big flashes/min |
| `twinkle` | animated | yes | base, sparkle colors, sparkles/s, life |
| `rainbow` | animated | yes | length, period, saturation, brightness, direction |
| `breathe` | animated | yes | colors, period, dimmest level |
| `chase` | animated | yes | block colors, gap color, block size, gap, speed, direction |
| `wipe` | animated | yes | color, background, period, direction |
| `gradient` | animated | yes | 2–12 colors, length, period, direction |
| `frames` | animated | yes | custom frame animation (8.3) |
| `nth` | **fixed** | yes (1 frame) | light every Nth pixel: N, first lit pixel, white LED on/off + level, color, background |
| `nth_step` | animated | yes | like `nth`, but N steps through `first`..`last` (default 4, 5, 6) every `change_hours` (1–24, default 1), by the clock or by elapsed time |

`GET /api/effect-kinds` returns the full schema. The web UI builds its editor forms from it.

### 8.2 Adding a new kind

Create a class in `effects/animated.py` (or a new module imported at the bottom of `effects/__init__.py`):

```python
from . import Effect, Param, register
from .util import scale

@register
class Pulse(Effect):
    kind = "pulse"                      # stored in the database
    label = "Pulse"                     # shown in the UI
    category = "Animated"               # "Controller-native" (runs on the controller) or "Fixed" (a still picture: set static = True)
    description = "Every LED pulses together."
    params = (
        Param("color", "Color", "color", "#ff0000"),
        Param("period", "Seconds per pulse", "float", 2.0, min=0.2, max=60, step=0.1),
    )

    def frame(self, t):                 # t = seconds since this effect started; return one (r, g, b) per LED
        import math
        k = 0.5 - 0.5 * math.cos(2 * math.pi * t / self.p["period"])
        return [scale(self.color("color"), k)] * self.n
```

`self.p` holds validated parameters, `self.n` the LED count, `self.rng` a (seedable) `random.Random`. Use `self.dt(t)` for time since the previous frame, and override `start()` to initialize state. Parameter types: `int`, `float`, `bool`, `choice` (give `choices=[(value, label), ...]`), `color`, `colors` (list, use `min`/`max` for the count), `frames`. A native kind implements `apply(controller)` instead of `frame()`, sets `native = True`, and `previewable = False` unless it can also render frames.

Validation happens in `Effect.clean()`: unknown keys are dropped, missing keys take defaults, numbers are clamped, and invalid colors/choices raise `ParamError` (HTTP 422).

### 8.3 Custom frame animations (`frames`)

Data model (the `data` parameter):

```json
{
  "background": "#000000",
  "loop": "loop",
  "frames": [
    {"duration": 0.6, "transition": "cut",
     "segments": [{"start": 0, "end": 124, "color": "#ff0000"},
                  {"start": 125, "end": 249, "color": "#0000ff", "color_end": "#00ff00"}]}
  ]
}
```

* `loop`: `loop` (0→n−1, repeat), `pingpong` (0→n−1→1, ends not repeated), `once` (hold the last frame).
* Each frame starts as the `background`, then paints its `segments` in order (later wins). LED indices are 0-based and inclusive; segments past the end of the strip are clipped. A `color_end` makes a linear gradient.
* `transition: "fade"` blends the frame into the **next one in playback order** over its own duration; `"cut"` holds it then switches.
* Limits: 500 frames, 500 segments per frame, duration 0.05–3600 s.

### 8.4 Preview

`POST /api/preview` renders an effect server-side (single source of truth) and returns frames as hex strings (6 hex digits per LED, 1500 characters for 250 LEDs), capped at 600 frames. Preview uses a fixed seed so it is repeatable; real playback is not seeded.

---

## 9. Data model

SQLite, schema version 3 (`PRAGMA user_version`; older files are upgraded automatically on start), WAL mode, foreign keys on. File: `<data dir>/tm1814.db`.

```
settings(key PK, value JSON)
effects(id, name UNIQUE NOCASE, kind, params JSON, description, seed_key, created, updated)
runlists(id, name UNIQUE NOCASE, loop, shuffle, notes, created, updated)
runlist_items(id, runlist_id → runlists CASCADE, position, effect_id → effects RESTRICT, duration_sec)
schedules(id, name UNIQUE NOCASE, runlist_id → runlists RESTRICT, enabled, priority, spec JSON, created, updated)
logs(id, ts REAL, lvl INTEGER, level TEXT, logger TEXT, msg TEXT)      -- index on ts; see "Logs" below
incidents(id, kind, started REAL, ended REAL NULL, summary, detail JSON, resolution)   -- connection outages; pruned with the logs
```

* Names are unique case-insensitively within a table (HTTP 409 on conflict).
* An effect used by any run list cannot be deleted; a run list used by any schedule cannot be deleted (409 with the names listed).
* **Run list items store an effect id, not a copy.** Editing an effect changes every run list that uses it. When a run list *starts playing* the effect parameters are snapshotted into the plan, so edits take effect the next time the item starts (or when the run list is saved, for scheduled runs).
* Item durations are clamped to 1 s – 24 h.

### Settings keys

| Key | Default | Meaning |
| --- | --- | --- |
| `controller_host` | `""` | controller IP or host name; empty = not configured |
| `controller_port` | 5577 | |
| `led_count` | 0 | 0 = use the controller's reported count; otherwise override (≤255) |
| `fps` | 5 | target frames/s for animated effects (1–15) |
| `min_gap_ms` | 30 | pause between commands (5–500) |
| `max_groups` | 10 | max distinct colors per frame (2–30) |
| `brightness` | 100 | master brightness, 0–100 |
| `idle_action` | `off` | when nothing is scheduled/playing: `off`, `leave`, `solid` |
| `idle_color` | `#ff5a00` | color for `solid` |
| `timezone` | `""` | IANA name; empty = this machine's zone |
| `location_name` | `""` | the place found from the address the user typed (display only) |
| `latitude`, `longitude` | `null` | worked out from that address (not entered by hand in the UI; the API still accepts them); used only for sunrise/sunset |
| `log_retention_days` | 60 | log lines older than this are deleted (minimum 31) |
| `seeded_keys` | `[]` | internal: which default catalog entries were already created |

### Seeding

On startup `seed_catalog()` creates default effects/run lists whose keys are not in `seeded_keys`, then records them. Deleting a seeded item is respected (it is not recreated); a newer release can add new keys. Seeded items are ordinary rows with `seed_key` set.

---

## 10. Scheduling

Pure functions in `scheduler.py`; the stored `spec` (JSON):

```json
{
  "start_date": "2026-10-24",            // optional; first eligible date
  "end_date": "2026-10-31",              // optional; last eligible date (inclusive)
  "start_time": "sunset",                // "HH:MM" | "sunrise" | "sunset"
  "start_offset": -15,                   // minutes; added to sunrise/sunset
  "end_time": "23:00",
  "end_offset": 0,
  "repeat": {"type": "daily", "interval": 1}
}
```

### Repeat types

| `repeat` | Fields | Occurs on |
| --- | --- | --- |
| `once` | — | exactly `start_date` (required) |
| `daily` | `interval` (1–52) | every N days counted from `start_date` (or a fixed anchor if none) |
| `weekly` | `weekdays` (0 = Mon … 6 = Sun), `interval` | listed weekdays, every N weeks (weeks counted from the Monday of the anchor week) |
| `monthly_day` | `day` 1–31, or −1 = last | that day each month; day > month length is **clamped to the last day** (31st → Apr 30) |
| `monthly_nth` | `nth` 1–4 or −1, `weekday` | e.g. first Saturday, last Friday |
| `yearly` | `month`, `day` | that date each year (Feb 29 clamps to Feb 28 in other years) |

"First of every month" is `monthly_day` with `day: 1`. The date range always filters eligible dates.

### Windows

On each eligible date *D* the window runs from `start_time` to `end_time`. If the end is not later than the start the window ends the **next day** (overnight). For sun-based times the end is recomputed for D+1 (so "sunset → sunrise" ends at tomorrow's sunrise). To test whether `now` is active, dates *today* and *yesterday* are checked (yesterday's window may still be running).

### Sun times

`astral` computes sunrise/sunset from the saved coordinates in the configured time zone. The coordinates come from an **address lookup** (`geocode.py`): `POST /api/location/search {query}` asks OpenStreetMap's Nominatim (`https://nominatim.openstreetmap.org/search`, `format=jsonv2`, up to 5 matches) from the server with an identifying User-Agent, at most one request per 1.1 s, results cached for 24 h, and friendly errors (too short, nothing found, busy/429, offline) mapped to HTTP 422; the user picks a match and Settings saves `latitude`, `longitude` and `location_name`. `GET /api/location/sun?latitude=&longitude=&timezone=` returns today's sunrise and sunset (for the saved place, or a place being previewed), so the UI can confirm the choice. Windows of any combination work: `sunset → sunrise` and `sunrise → sunset` (and clock times mixed with either) with optional minute offsets on each end; a window whose end is not later than its start continues into the next day, and for sun times the end is recomputed for that next day. If no location is set, or the sun does not rise/set that day, defaults are used (**sunrise 07:00, sunset 19:00**) and the API flags `sun_estimated: true` so the UI can warn. The time zone must be set for DST to be handled; with it unset the machine's zone is detected from `$TZ` or `/etc/localtime`.

### Choosing the active schedule

`pick_active()` considers enabled schedules whose window contains *now*, and picks the highest `priority`; ties go to the one that started most recently, then the lowest id.

### Manager behavior (`ScheduleManager.evaluate`, every 5 s)

* A **manual** play is never preempted. When it ends, the schedule (if still in its window) takes over.
* If a schedule becomes active → `Runner.play_runlist(source="schedule:<id>", window=<start ISO>)`.
* If no schedule is active and a scheduled plan is running → stop it (applies the idle action).
* A scheduled run that **finished** (non-looping run list) or was **stopped by the user** is remembered as `(schedule id, window start)` and **not restarted within the same window**. The user's Stop button also suppresses the current window, so lights stay off until the next window.
* Editing the run list of an active schedule restarts the scheduled run (detected by the run list's `updated` timestamp).

---

## 11. The runner

`Runner` plays one **plan** at a time:

```
plan = { source: "manual" | "schedule:<id>", name, runlist_id, loop, shuffle, window,
         items: [ {effect_id, name, kind, params, duration|None} ] }
```

* `duration: None` plays until stopped (used when playing a single effect).
* Items play in order; `shuffle` randomizes each cycle; `loop` repeats, otherwise the plan ends (`finished`) and the idle action runs.
* `next()` skips the current item; `pause()` freezes the item clock (the strip holds its last frame).
* Replacing a plan bumps a generation counter; the worker notices on its next iteration (≤ ~0.25 s).
* The **idle action** runs when a plan finishes, a schedule window ends, or Stop is pressed: `off` (power off), `leave` (do nothing), `solid` (power on, show `idle_color`). The *power* endpoint stops the plan without an idle action.
* Changing settings (`apply_settings`) updates pacing/brightness live; a brightness change repaints animated frames and re-applies native effects.

Status payload (`GET /api/status`) includes `state` (`idle`/`running`/`paused`), `source`, `plan`, `item` (`index`, `count`, `name`, `kind`, `native`, `duration`, `elapsed`), `fps_target`, `fps_actual`, a `controller` block (host, port, connected, `led_count`, `last_error`, `commands_per_second`, `commands_sent`, `brightness`, `power`, `model`, `firmware`, `simulated`), `uptime_sec`, `version`, `active_schedule`, `upcoming` (next windows), `needs_setup`, and `has_location`.

**Logs.** Every record of the `tm1814.*` loggers at INFO or above is written to the `logs` table by `DBLogHandler` (so history survives restarts; the same lines also go to the console/journal). Retention: lines older than `log_retention_days` (default 60, never below 31) are deleted **at startup and then hourly** by the schedule thread, and a safety net of 500,000 rows drops the oldest beyond that. A logging failure (for example while the database is closing) is swallowed and never affects the program. Queries (`Database.query_logs`) filter by time range, minimum level, and a literal text search (`%` and `_` are escaped), and when more lines match than `limit` they return the **newest** `limit` lines (oldest first) with `total`, `truncated`, the oldest and newest stored times, and the retention.

---

### Connection health and self-healing

Implemented in `health.py` (monitor), `netscan.py` (finding the controller), `alerts.py` (email and webhook) and `watchdog.py` (independent layer 2). It mirrors the two-layer pattern of the scriptbox platform: an in-process sweep plus a separate watchdog started by the operating system, both with de-duplicated alerts.

**Layer 1: `ConnectionMonitor`** (one thread, started with the service; `tick()` is one check and is what the tests drive).

| Step | Behavior |
| --- | --- |
| Probe | `query_state()` every `health_check_sec` (default 30; every 10 s while something is wrong; `nudge()` makes it check immediately). If an effect is playing and the controller client has had a successful send within the interval and no error, that counts as proof of life and nothing is sent. When idle, the probe connection is closed afterwards (the controller drops idle connections anyway). |
| States | `unknown` → `online` / `unconfigured`; a failed check → `degraded`; **3 consecutive failures → `offline`**. |
| Learn the ID | The first time it is online with no `controller_mac` saved, a unicast discovery probe to the controller's address returns its MAC (fallback: the ARP table). Retried every 10 minutes while unknown. Never done for a simulated controller. Editing `controller_host` in Settings clears the saved MAC (another device) unless one is supplied (the "Find controllers" list supplies it). |
| Search | On a failed check, at most **once a minute**: `netscan.find_by_mac(mac, near=host)`. If it is found at a **different IP** the monitor `_adopt`s it: saves the new `controller_host`, calls `Runner.apply_settings()` and `Controller.set_address()`, opens/updates the incident with `moved_from`/`moved_to`, sends a "Controller moved" alert at once, and re-probes. Without a saved MAC it logs that it cannot follow the controller. |
| Incident | When it reaches `offline` an `incidents` row (`kind = controller_offline`) is opened with `started` = the time of the **first** failed check, then a diagnosis (`netscan.diagnose`) is stored (refreshed every 5 minutes). One open incident at a time; it survives restarts of the program. |
| Recovery | The first successful check after failures closes the incident (`resolution`: "reconnected on its own" or "found at its new address X (was Y)"), logs the duration, and calls `Runner.request_reapply()` so the current effect is sent again (the controller may have rebooted). |
| Alerts | See below. |
| Worker supervision | Each check also inspects the program's own threads. A runner thread that has died, or has had work (a plan) but has not stamped its heartbeat for 120 s, is replaced with `Runner.restart_worker()`; a dead schedule thread is restarted. **Three replacements within 15 minutes → `os._exit(70)`**, so launchd/systemd restart the whole process. |

**Finding the controller (`netscan.py`).** The controller answers a UDP datagram `HF-A11ASSISTHREAD` on port 48899 with `ip,MAC,model,token`. `discover()` sends it by broadcast on every interface **and** directly to every host of the interface networks (some routers and access points drop broadcasts), batched, and collects replies for up to the timeout (about 1.5 s for a /24 on this hardware). The MAC is compared after normalization (`7C:3E:..`, `7C-3E-..` and `7C3E...` are the same). Safety rules: **VPN tunnels and virtual adapters are skipped** by interface name (`utun*`, `tun*`, `wg*`, `ppp*`, `docker*`, `vboxnet*`, names containing "vpn", "tap-", "virtual", ...), only private networks between /22 and /30 are swept (at most 1024 hosts), and with `near=<last known IP>` only the network containing that address is swept. Interface and routing details are parsed from `ifconfig` (macOS/BSD), `ip -o -4 addr` / `ip route` (Linux) and `ipconfig` / `route print` (Windows); every helper is best effort and never raises.

**Diagnosis (`netscan.diagnose`).** Pings the default gateway, pings the controller's address and reads the ARP table, then reports one of: "this computer cannot reach its own router" (the problem is this computer's network); "another device now has the address X" (the ARP MAC differs: the controller has moved); "the address answers a ping but refuses the control connection" (it may be restarting); or "the router is reachable but nothing answers at the controller's address: off, lost Wi-Fi, or moved".

**Alerts (`alerts.py`).** Channels: email (`alert_email` + `smtp_host`, optional `smtp_user`/`smtp_password`, STARTTLS or SSL on 465) and a webhook (`alert_webhook`: a plain-text POST with the subject in a `Title` header, which ntfy.sh and similar services accept). Nothing is sent until a channel is configured, and a failing channel is logged, never raised. Policy (state in the `_state_alert` row, so it survives restarts): **one alert when the incident has lasted `alert_after_minutes` (default 10), a `[reminder]` every `alert_reminder_hours` (default 6) while it lasts, and a "Recovered" message when it ends (only if a down alert was sent)**. A "Controller moved to X" notice exists but is **opt-in** (`alert_on_move`), because a followed address change is not critical.

**Keeping alerts few (`alerts.Budget`).** Designed for ntfy.sh's free tier (250 messages/day) and shared by the program (state in the database, row `_state_alert_budget`) and the watchdog (state file). Kinds: *critical* = `down`, `recovered`, `watchdog`, `watchdog_recovered`; non-critical = `reminder`, `moved`. Rules, in order: a hand-sent `test` always goes (and counts); a recent failed delivery blocks sending until `next_try` (back-off 5, 10, 20 ... 60 minutes, reset on success); **at most `alert_daily_limit` (default 20, 1-200) alerts in any rolling 24 hours**; non-critical kinds only while under **70%** of that limit; a new `down`/`watchdog` alert waits `alert_min_gap_minutes` (default 60) after the previous one, so a flapping connection cannot alert in a loop (and a down alert that never went out also suppresses its "recovered"). ntfy gets `Priority` and `Tags` headers per kind (down/watchdog: `high`; recovered/test/reminder: `default`; moved: `low`), bodies are cut at 3500 characters, and an HTTP 429 from the push service is logged and counted as a failure. Everything that happens to an alert is logged with the word `ALERT` (sent, held back with the reason at most once an hour, no channel configured, delivery failed with the retry time), and `GET /api/health` returns `alerts: {sent_24h, limit, min_gap_minutes, retry_at, last_down_alert, channels, on_move, ...}`. The SMTP password is stored in the database and is write-only through the API (`GET /api/settings` returns `smtp_password_set` instead). `POST /api/health/test-alert` sends a test message.

**Calm logging.** Routine health checks write **nothing** to the log: opening a connection is logged at DEBUG (not stored), and the monitor logs only state *changes* (`unknown -> online`, `online -> degraded`, `degraded -> offline`, `offline -> online`), incidents, searches and alerts. The runner logs the first failed command of a streak, then at most one "still cannot reach the controller (N attempts over T)" line every 5 minutes, and one line when it works again (previously a three-hour outage wrote about 800 identical warnings). Backoff between failed attempts grows from 3 s to 15 s.

**Layer 2: the independent watchdog (`watchdog.py`, `tm1814-led watchdog`).** A separate short-lived process run every 3 minutes by the **operating system's own scheduler**, so it works when the program has died or hung: a launchd `StartInterval` job (macOS), a systemd timer + oneshot service (Linux; runs as root in a system install so it may restart the service), a Windows scheduled task (`/SC MINUTE /MO 3`) or a cron line. It requests the program's public `GET /api/health/live` (secret-free: `{ok, version, heartbeat, uptime_sec}`; `ok` is false if the worker, schedule or monitor thread has been silent for 120 s). On a bad check it counts; at **2 consecutive bad checks** it restarts the service (`launchctl kickstart -k`, `systemctl [--user] restart`, or `schtasks /End` + `/Run`), at most **3 restarts per hour**, and alerts (one alert, reminders every 6 hours, a recovery message). Its state is a small JSON file in the data directory and it reads the alert settings straight from the database **read-only**, so it can alert while the program is down. Not covered: the whole computer being off (the watchdog dies with it); for that use an external dead-man's-switch service.

| Setting | Default | Meaning |
| --- | --- | --- |
| `controller_mac` | learned | the controller's MAC address (12 hex digits) |
| `health_check_sec` | 30 (10–600) | check interval while healthy |
| `alert_after_minutes` | 10 (1–1440) | down this long before the first alert |
| `alert_reminder_hours` | 6 (1–168) | reminder interval |
| `alert_email`, `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `smtp_tls`, `smtp_from` | empty / 25 | email channel |
| `alert_webhook` | empty | webhook channel (http or https) |
| `alert_daily_limit` | 20 (1-200) | most alerts in any 24 hours |
| `alert_min_gap_minutes` | 60 (1-1440) | minimum time between two `down` alerts |
| `alert_on_move` | false | also alert when an address change is followed |

---

## 12. REST API

JSON over HTTP, prefix `/api`. Errors are `{"detail": "message"}`:

| Status | Meaning |
| --- | --- |
| 404 | not found |
| 409 | conflict (duplicate name, item in use) |
| 422 | invalid input (bad parameter, bad schedule spec, unknown kind) |
| 502 | the controller could not be reached for this request |
| 401 | not signed in (`/api/login`: wrong password) |
| 429 | too many wrong passwords |

Interactive documentation is served by FastAPI at `/docs` (and `/openapi.json`) — note this is a separate path from the web UI.

### Status and metadata

| Route | Description |
| --- | --- |
| `GET /api/status` | runtime status (above) |
| `POST /api/login`, `POST /api/logout`, `GET /api/auth` | sign in `{password}` / sign out / `{required, authenticated}` (public; see section 13) |
| `POST /api/password` | change the password `{current, new}`; requires a session (see section 13) |
| `GET /api/meta` | `{version, simulated, timezones[], weekdays[], repeat_types[], led_count, builtin_patterns[]}` |
| `GET /api/effect-kinds` | schema for every effect kind (drives the editor forms) |
| `GET /api/logs?count=200&since=&until=&level=&q=` | the newest `count` matching lines, oldest first: `[{ts, level, logger, msg}]`. `since`/`until` are Unix times in seconds, `level` is the minimum (`INFO`, `WARNING`, `ERROR`), `q` is a text search |
| `GET /api/logs/query?limit=1000&since=&until=&level=&q=` | the same with totals: `{entries, total, limit, truncated, oldest, newest, stored, retention_days}` (used by the Logs page) |

### Settings and controller

| Route | Description |
| --- | --- |
| `GET /api/health` | `{state, since, last_ok, failures, unreachable_for_sec, error, diagnosis, host, mac, incident, alert_channels, heartbeat, incidents[]}` |
| `GET /api/health/live` | **public**, secret-free liveness for the watchdog: `{ok, version, heartbeat, uptime_sec}` |
| `POST /api/health/recover` | search for the controller now and follow it: `{moved, host, was, state, mac_known}` |
| `POST /api/health/test-alert` | send a test alert; body `{channel: "email"\|"webhook"}` limits it to one channel, response `{sent, channels, failed}` |
| `GET /api/sections`, `POST /api/sections`, `/sections/rename`, `/sections/delete` | effect sections (names stored in settings row `_state_sections`; `effects.section` is `''` for the built-in group by type; deleting a section resets its effects, schema v4) |
| `GET /api/incidents?limit=50` | recorded connection problems, newest first |
| `GET /api/settings`, `PUT /api/settings` | read / change settings (partial body; validated; applied live) |
| `GET /api/controller/state` | query the controller: `{reachable, state{…}, error}` |
| `POST /api/controller/discover` | UDP discovery: `[{ip, mac, model, token}]` (takes ~2 s) |

### Effects

| Route | Description |
| --- | --- |
| `GET /api/effects` | list |
| `POST /api/effects` | create `{name, kind, params, description}` → 201 |
| `GET/PUT/DELETE /api/effects/{id}` | read; update (`name`, `params`, `description`, all optional); delete |
| `POST /api/effects/{id}/duplicate` | copy, optional `{name}` (default "Copy of …") → 201 |
| `POST /api/preview` | `{kind, params, seconds≤60, fps≤15}` → `{fps, led_count, frames[]}` (unsaved params allowed) |

### Run lists

| Route | Description |
| --- | --- |
| `GET/POST /api/runlists` | list / create `{name, loop, shuffle, notes, items:[{effect_id, duration_sec}]}` |
| `GET/PUT/DELETE /api/runlists/{id}` | read / update (any field; `items` replaces the whole list) / delete |
| `POST /api/runlists/{id}/duplicate` | copy |

### Schedules

| Route | Description |
| --- | --- |
| `GET/POST /api/schedules` | list / create `{name, runlist_id, spec, enabled, priority}` |
| `GET/PUT/DELETE /api/schedules/{id}` | read / update / delete |
| `POST /api/schedules/{id}/duplicate` | copy (created **disabled**) |
| `POST /api/schedules/preview` | `{spec, count}` → `{summary, sun_estimated, windows:[{start,end}]}` for an unsaved spec |
| `GET /api/schedules/{id}/occurrences?count=6` | the same for a saved schedule |

### Control

| Route | Body | Description |
| --- | --- | --- |
| `POST /api/control/play` | `{runlist_id}` or `{effect_id, duration_sec?}` or `{kind, params, name?, duration_sec?}` | start playing (manual); the third form plays unsaved editor content |
| `POST /api/control/stop` | | stop, apply the idle action, suppress the active schedule window |
| `POST /api/control/next`, `pause`, `resume` | | |
| `POST /api/control/power` | `{on: bool}` | stops the plan, powers on/off |
| `POST /api/control/color` | `{color: "#rrggbb"}` | plays a solid color |
| `POST /api/control/brightness` | `{value: 0–100}` | master brightness |

Example:

```bash
curl -s -X POST localhost:8080/api/control/play -H 'Content-Type: application/json' \
     -d '{"effect_id": 12}'
```

---

## 13. Security model

* The controller itself has **no authentication**; anyone on the same network can drive it. Keep it on a trusted LAN/VLAN.
* **The web page is the only way into the program, and it always requires a password.** `serve` refuses to start without one (except `--simulate`); the installer always sets one. A single shared password; no accounts.
* **Sign-in:** `POST /api/login {"password"}` returns an `HttpOnly`, `SameSite=Lax` cookie `tm1814_session` holding `v1.<expiry>.<HMAC-SHA256>`. The key is derived from a random 256-bit secret kept in the database plus a hash of the password, so sessions survive restarts and **changing the password signs everyone out**. Sessions last 30 days. Nothing about sessions is stored server-side.
* **Changing the password** (`POST /api/password {current, new}`, Settings page, or `tm1814-led password`): requires a signed-in session **and** the current password (wrong attempts count toward the same throttle), a new password of 8–200 characters different from the current one. The new password is stored in the database (settings key `_password`) as a **salted PBKDF2-HMAC-SHA256 hash** (200,000 iterations, random 16-byte salt), never in clear text, and it overrides the settings-file password while present. The signing key is derived from the active password, so every other session and terminal is signed out at once; the caller gets a fresh cookie and stays signed in. The record also stores a fingerprint of the settings-file password: if the installer later writes a *different* password to the settings file, the stale override is discarded at startup and the installer's password wins again (re-running the installer without a password option keeps the changed one). The override is not an editable setting and is not returned by `/api/settings`.
* **Gatekeeping:** a middleware lets only `/login`, `/api/login`, `/api/auth`, `/api/health/live` and `/style.css` through without a valid cookie. Unauthenticated `/api/*` requests get `401 {"detail": "login required"}` **without** a `WWW-Authenticate` header (so browsers never show a native prompt); other paths get `302 /login`. **HTTP Basic is not accepted.** `/docs` and `/openapi.json` are protected too.
* **Caching:** every response carries `Cache-Control: no-store` (API) or `no-cache` (pages), so after Log out a browser can never show a cached page before being redirected.
* **Brute force:** 8 wrong passwords per client address in 5 minutes returns `429` for the rest of the window; every wrong guess also costs 0.6 s. Password comparison is constant-time.
* There is **no TLS**: the password and cookie cross the LAN in clear text. Put the app behind a reverse proxy that terminates HTTPS for anything beyond a trusted home network, or bind it to `127.0.0.1` (`--host 127.0.0.1`).
* The command line is a client of the same sign-in (section 15): it prompts for the password (no echo) and caches only the signed session token, in a `0600` file keyed to the terminal session, expiring after 12 hours. It does not read the password from any file.
* The system service runs as an unprivileged `tm1814` user with `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome` and write access only to its data directory. The settings file (`tm1814-led.env`, which holds the password) is `0600` (user installs) or `0640 root:tm1814` (system install).
* The UI builds DOM with `textContent`, never `innerHTML`, for user-entered strings. User data reaches SQLite only as bound parameters.
* The program never sends the configuration-write and reset commands (section 6).

---

## 14. Installer internals

`tm1814-led --install [--system|--user] [--port N] [--host ADDR] [--controller-host IP] [--password PW | --generate-password] [--dry-run]` (a password is always set: given, kept from a previous install, typed at a prompt, or generated)

Always run `--dry-run` first to see every command and file.

| OS | Mode | Program | Data | Settings file | Service |
| --- | --- | --- | --- | --- | --- |
| Linux + systemd | **system** (run as root; default when root) | `/opt/tm1814-led/venv` | `/var/lib/tm1814-led` | `/etc/tm1814-led/tm1814-led.env` (0640 root:tm1814) | `/etc/systemd/system/tm1814-led.service`, user `tm1814` |
| Linux + systemd | **user** (default when not root) | `~/.local/share/tm1814-led/venv` | `~/.local/share/tm1814-led/data` | `~/.config/tm1814-led/tm1814-led.env` (0600) | `~/.config/systemd/user/tm1814-led.service`; `loginctl enable-linger` is attempted |
| Linux, no systemd | user | as user mode | | | `@reboot` crontab line, and it is started immediately |
| macOS | user (LaunchAgent; runs while you are logged in) | `~/Library/Application Support/tm1814-led/venv` | `…/data` | `…/tm1814-led.env` (0600) | `~/Library/LaunchAgents/com.jewettg.tm1814-led.plist`; logs in `~/Library/Logs/tm1814-led/` |
| Windows | user (task at logon) or administrator (task at startup as SYSTEM) | `%LOCALAPPDATA%\tm1814-led\venv` | `…\data` | `…\tm1814-led.env` | Scheduled Task "TM1814 LED Controller" running `pythonw -m tm1814_led serve --env-file …` |

The install also schedules the **watchdog** (section 11: macOS second launchd job `com.jewettg.tm1814-led-watchdog` with `StartInterval 180`; Linux `tm1814-led-watchdog.timer`/`.service` units; Windows task `TM1814 LED Watchdog`; cron `*/3`), and `--uninstall` removes it. Steps: (1) create the system user (Linux system mode); (2) create a virtual environment with the current Python (≥3.9); (3) `pip install` **this checkout** into it (so the checkout can later be moved or deleted; installing requires running from a repository checkout); (4) put a `tm1814-led` link on your PATH (`/usr/local/bin` for a system install, `~/.local/bin` for a user install; skipped on Windows), (5) write the settings file (data dir, host, port, optional password and controller address); (6) write and enable the service; (7) print the URLs.

`--uninstall` stops and removes the service and settings file but **keeps your data**; add `--purge` to delete the program folder, data, and (Linux system mode) the `tm1814` user. It asks for confirmation unless `--yes`. `--service-status` shows the service state.

Service environment variables (the settings file; `cli.load_env_file` loads it without overriding variables already set): `TM1814_DATA_DIR`, `TM1814_HOST`, `TM1814_PORT`, `TM1814_PASSWORD`, `TM1814_CONTROLLER_HOST`.

---

## 15. Configuration reference

### Command line

```
tm1814-led                     list the commands
tm1814-led serve [--host ADDR] [--port N] [--data-dir DIR] [--controller-host IP] [--simulate]
                 [--password PW] [--env-file FILE] [--log-level LEVEL]
tm1814-led install|uninstall|service      same as --install / --uninstall / --service-status (section 14)
tm1814-led watchdog [--env-file FILE]     one independent health check (run by the system scheduler)

 service commands (via the running web service):
tm1814-led status | health | find | effects [text] [--native|--fixed|--animated] | runlists | schedules
tm1814-led play NAME [--effect|--runlist] [--for SECONDS] | stop | next | pause | resume
tm1814-led brightness 0-100
tm1814-led logs [-n N] [--since WHEN] [--until WHEN] [--level info|warning|error] [--grep TEXT] [-f]
 direct commands (straight to the controller; sign-in still required):
tm1814-led discover | state | on | off | color COLOR [--white LEVEL] | test [PATTERN [N]] [--delay S]
 session:
tm1814-led login | logout | password
 common options:  --url URL   --controller-host HOST[:PORT]   --json   --no-color
```

`python3 -m tm1814_led …` is equivalent. `--simulate` runs everything against a fake controller (no hardware), which is how the UI and tests are developed.

**Sign-in for the CLI** (`client.py`): the first command in a terminal session prompts `Password for the LED controller at <url>:` (up to 3 attempts), calls `POST /api/login`, and stores `{url, token, saved}` in `~/Library/Caches/tm1814-led/session-<sid>.json` (macOS), `$XDG_CACHE_HOME|~/.cache/tm1814-led/…` (Linux) or `%LOCALAPPDATA%\tm1814-led\cache\…` (Windows), mode `0600`, where `<sid>` is the POSIX session id (one per terminal window; the parent PID elsewhere). Entries older than 12 hours, or for a different `--url`, are ignored, and a token the service rejects (expired, or the password changed) is discarded and the prompt appears again. `TM1814_PASSWORD` supplies the password without prompting, for scripts. Direct commands sign in first too, then read the saved controller address from the service's settings (`GET /api/settings`) unless `--controller-host` is given.

The service URL defaults to `http://127.0.0.1:<port>`, with the port read from the installed settings file (or 8080). `--json` prints the service's JSON; `test` patterns live in `diagnostics.py`.

### Environment

| Variable | Equivalent |
| --- | --- |
| `TM1814_DATA_DIR` | `--data-dir` (default: per-user data folder for the OS) |
| `TM1814_HOST` / `TM1814_PORT` | `--host` / `--port` (defaults `0.0.0.0` / `8080`) |
| `TM1814_PASSWORD` | `serve --password` (and, for the CLI, the password to sign in with non-interactively) |
| `TM1814_URL` | CLI `--url` |
| `TM1814_CONTROLLER_HOST` | `--controller-host` (only used when the database has none yet) |

---

## 16. Testing and hardware validation

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

| Test file | Covers |
| --- | --- |
| `test_protocol.py` | envelope, checksums, every message layout, state parsing, hardware-verified byte strings |
| `test_controller.py` | quantization/grouping, diff painting, brightness, forbidden commands, a real TCP socket against a fake controller, connection errors |
| `test_effects.py` | every kind renders valid frames, determinism, parameter validation, frame-animation timing (cut/fade/loop/pingpong/once) |
| `test_scheduler.py` | every repeat rule, month-end clamping, nth weekdays, overnight windows, sun times (Austin), priority |
| `test_db.py` | CRUD, uniqueness, in-use protection, settings validation, seeding, upgrading old saved effects |
| `test_logs.py` | log ranges, level and text filters, truncation, retention and pruning, the row cap, surviving a restart, upgrading an old database, the handler, the API and CLI time parsing |
| `test_runner.py` | playing/looping/next/pause/stop, idle actions, schedule start/stop, user-stop suppression, priority, restart on edit |
| `test_api.py` | every route group end to end with the simulated controller, auth, error codes |
| `test_installer.py` | file contents per OS, mode selection, address detection (VPN-safe), a password is always set, a full install against a fake command runner, dry runs |
| `test_password.py` | hashing and salting, an override beating the settings-file password, signing everyone else out, restarts, the installer's password winning, the CLI `password` command |
| `test_health.py` | the monitor: failure thresholds, incidents, recovery and re-sending the effect, following an IP change by MAC, search rate limit, learning the MAC, proof-of-life shortcut, alert de-duplication and reminders and recovery, worker supervision and the exit after repeated stalls, nudging |
| `test_netscan.py` | parsing `ifconfig`/`ip`/`ipconfig`, VPN and virtual interfaces excluded, sweep limits, discovery against a local UDP responder, MAC matching, diagnosis |
| `test_alerts.py` | email (STARTTLS, login, recipients), webhook headers, failures never raise |
| `test_watchdog.py` | one check at a time: restart after two bad checks, the hourly restart limit, alert once, reminders, recovery, unreadable database, restart commands per OS |
| `test_health_api.py` | the health endpoints, the public liveness route, write-only SMTP password, MAC cleared on address change, new settings validated |
| `test_location.py` | the address lookup (parsing, caching, one request per second, every failure message), the search and sun routes, saving or clearing the place, and sunset-to-sunrise, sunrise-to-sunset, mixed and offset windows |
| `test_auth.py` | login page and cookie sessions, redirects, forged/expired tokens, throttling, no cache, Basic rejected |
| `test_cli.py` | the real CLI against a live local service: password prompt once per terminal session, session cache, name matching, every command, direct commands against a fake controller |

The streaming engine was also exercised against real hardware through the running service: solid color, a built-in animation, embers, comet, wave, lightning and rainbow ran for 12 s each with zero errors at ~30 commands/s, and Stop returned the controller to off. The lights themselves were evaluated visually by the owner during development (the alternating-pixel test, the effect scenes and their color tweaks).

---

## 17. Known limits and open questions

* **Frame rate.** About 3–5 frames/s for ordinary animated effects, lower for busy gradients, because every distinct color costs a command and commands are paced at ~30/s. Use native effects for smooth fast motion.
* **Banding.** Frames needing more than `max_groups` colors are quantized. Raising `max_groups` improves fidelity at the cost of frame rate.
* **Animated effects need this program running**; native and fixed ones do not (a fixed effect is painted once and the controller holds it). If the program stops, an animated effect freezes on its last frame (the controller does not time it out — Inferred, not tested).
* **Native effects cannot be previewed** (the controller's renderer is a black box), only played.
* **Both outputs always show the same thing.** Independent control of the two outputs is not known to be possible.
* **IC type, color order, pixel count (write) and segment layout cannot be read or changed** with the commands this project knows. The count is read from state byte 18.
* **On-controller storage.** The vendor app appears to upload custom animations/timers to the controller (the controller has timer slots and the app lists commands such as `custom_data_hc3`, `multiple_screen_data`), but the formats are unknown, so custom animations here are always streamed. Recovering them would need a capture of the app or analysis of its compiled code.
* **Per-pixel mode is limited to 255 pixels** (single-byte count).
* **State byte meanings** beyond those in 5.1 (bytes 2, 14–15, 16–17, 19–26) are unknown.
* **Other firmware.** Only one controller/firmware was available. Other `0xB6` firmware, or sibling models (e.g. `0xAA`), may behave differently.
* **Single user.** No accounts; one shared optional password.

---

## 18. Provenance and credits

The research behind this document is kept in [research/](research/): the development log, the community research, and the analysis of the vendor app's data files.

* The behavior documented here was established by **observation of a real controller** during development (queries, controlled commands, and a person watching the lights), recorded in the project history.
* The `E1 21`, `E1 22`, `E1 23` and `E1 26` layouts and the pattern names come from the pull requests of the open-source [`flux_led`](https://github.com/lightinglibs/flux_led) project by their contributors (Apache-2.0); this project reimplements the byte layouts from scratch and verified them on hardware. The envelope format and state-frame field meanings are likewise from `flux_led` and were cross-checked against real frames.
* The app's data files (product definitions and command templates shipped inside the vendor's Android app) were **read for interoperability research only**; no vendor code or assets are included in this repository.
* Third-party libraries: FastAPI, Uvicorn, Pydantic, Astral, tzdata.
* This project is not affiliated with Zengge, Surplife, Magic Home, or the TM1814 manufacturer.
* License: GPL-3.0 (see `LICENSE`).
