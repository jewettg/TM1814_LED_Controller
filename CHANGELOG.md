# Changelog

## Unreleased

* **Location by address.** Settings asks for an address, town or ZIP code instead of latitude and longitude; the program
  looks it up (OpenStreetMap Nominatim, politely: cached, one request a second) and shows today's sunrise and sunset to
  confirm. Sunrise/sunset schedules in either order (sunset to sunrise, sunrise to sunset, mixed with clock times) with
  presets for both.
* **Alerts limited to what is critical** (safe for ntfy.sh's free 250 a day): only a sustained outage, its recovery and the
  watchdog's findings; "moved" is opt-in; a daily cap (default 20), flap damping (60 min between "down" alerts) and
  back-off after failed deliveries, shared by the program and the watchdog. ntfy priority/tags per kind.
* **Quieter log, with alerts in it.** Routine health checks no longer write a line each (the once-a-minute
  "connected to ..." is gone); only changes are logged. Every alert event is logged with the word ALERT and there is an
  "Alerts only" view on the Logs page.

* **Self-healing connection to the controller.** A connection monitor checks the controller, learns its MAC address,
  and **follows it when its IP address changes** (found by a network search that skips VPNs); it records each outage
  as an incident with a plain-English diagnosis, re-sends the current effect after recovery, and sends de-duplicated
  alerts (email and/or webhook such as ntfy): one after N minutes, reminders, "recovered", and "moved".
* **Independent watchdog** run by the system scheduler (launchd / systemd timer / Windows task / cron): restarts the
  service if it stops answering or a worker is stuck, and alerts. The program also replaces stalled worker threads.
* Dashboard "Connection health" card, status-bar outage message, Settings "Self-healing and alerts", Logs calmer
  during outages (no more hundreds of identical warnings), `tm1814-led health` / `find` / `watchdog`,
  `GET /api/health`, `/api/health/live`, `POST /api/health/recover`.

* **Change password** in Settings and with `tm1814-led password`: needs the current password, stores a salted
  PBKDF2 hash in the database, signs every other browser and terminal out, and works the same on a Mac or a
  Raspberry Pi. Re-running the installer with a new password still takes precedence.
* "Use my location" now explains why browsers refuse it on a plain-http network address.

* The controller closes idle connections; the program now notices and reconnects quietly instead of failing the first
  send after a quiet period (which cost about 3 seconds at the start of a scheduled run).

* **Logs are kept in the database** and survive restarts. Retention is 60 days by default and never less than 31
  (Settings); older lines are deleted automatically (at startup and hourly), oldest first.
* **Logs page**: choose the range (hour / day / week / month / everything / custom from-to), the level, a text search and
  the line limit, with totals, a truncation notice and CSV download. `tm1814-led logs` gains `--since`, `--until`,
  `--level` and `--grep`. New API: `GET /api/logs/query`; `/api/logs` accepts `since`, `until`, `level`, `q`.

* **Fixed effects** (a third family next to controller-native and animated): a picture painted once that simply
  stays lit. New kind *Every Nth pixel* with the white-LED option; the catalog gains **Night (Bright / Medium / Low /
  Dim)** (every 3rd / 4th / 5th / 6th pixel).
* "Streamed" is now called **Animated**. New animated kind *Every Nth pixel, stepping* and the effect
  **Night (Hourly 4-6)**: the spacing steps 4, 5, 6 and loops, changing on the hour by the clock.
* White-LED pixels in the frame painter (follow master brightness, never quantized).

* **Login page** with a session cookie replaces HTTP Basic (browsers handled it badly). A password is now **always**
  required: the installer sets one and `serve` refuses to start without one. Log out link, login throttling,
  no-cache headers.
* **Command line rewritten**: status, effects, runlists, schedules, play, stop, next, pause, resume, brightness,
  logs, plus discover, state, on, off, color and `test` walk-around patterns. Every command asks for the password
  once per terminal session (only a signed token is cached, `0600`, 12 hours); `login` / `logout`; `--json`,
  colored output, named colors.
* Installer lists every local address (a VPN address is never presented as the home-network address), keeps the
  existing password on re-install, and retries `launchctl bootstrap`.

## 0.1.0 — first release

* Control library for the Zengge/Surplife `0xB6` (TM1814 RGBW) controller: state, power, solid color,
  20-segment color, the 24 built-in animations, and per-pixel painting with diffed, paced frame streaming.
* Effect catalog with 14 effect kinds (controller-native and streamed), schema-driven editing, duplicate/rename,
  server-side preview, and a frame-by-frame animation editor.
* Run lists (loop, shuffle, per-item durations) and schedules (date ranges, clock or sunrise/sunset times with
  offsets, once/daily/weekly/monthly-day/monthly-nth-weekday/yearly repeats, priorities).
* Background engine with health checks, reconnect, idle action and schedule suppression after Stop.
* Web UI (no build step, no external resources) and a REST API.
* `--install` / `--uninstall` for systemd (system or user), launchd, Windows Task Scheduler and cron;
  `--dry-run` to preview.
* Optional password protection; `--simulate` mode for development without hardware.
