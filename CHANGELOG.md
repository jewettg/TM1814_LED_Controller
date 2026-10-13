# Changelog

## Unreleased

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
