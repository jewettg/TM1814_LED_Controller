# Changelog

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
