# Changelog

## Unreleased

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
