# TM1814 LED Controller — Findings

> **Development log.** What was learned about the controller while building this project, in the order it was learned. Script names such as `zg.py`, `zg_pixels.py`, `tonight_tests.py` and `halloween.py` are early prototypes; everything useful in them is now part of the program (`tm1814_led/`). For the current, organized specification see [../TECHNICAL_README.md](../TECHNICAL_README.md). Addresses and IDs are replaced by examples.

Working log of what we have learned about the Eave lighting controller. Facts are marked **[verified]** (observed directly) or **[hypothesis]** (inferred, not yet tested).

Last updated: 2026-10-12

## 1. Hardware

| Item | Detail | Source |
| --- | --- | --- |
| Controller | White round Wi-Fi pixel controller, "RGBW", max output 15A, up to 2048 IC lamps, DC 12–48V input | photo of unit **[verified]** |
| Outputs | Two output groups, each with V+ / DAT / GND (two data lines = the two segments) | photo of terminals **[verified]** |
| Power supply | Mean Well LRS-350-24: 24V DC, 14.6A, 350W; input 100–120V (switch-selected) | photo **[verified]** |
| LED driver family | TM1814 (RGBW, 24V, single-wire data) | per owner |
| Controller app | Surplife 1.7.0 (Build 394), JS 60.2 | screenshot **[verified]** |
| App device name | "My House", room "Outside House" | screenshot **[verified]** |
| Firmware string | `WF.52.B6.26.0,V9_ZG-BL-UFO` (ZG = Zengge) | app Device Information **[verified]** |
| Chip/radio | Bluetooth + Wi-Fi (both icons show on the device card) | app **[verified]** |

Notes:
- Power budget: 350W at 24V. Two runs of ~250 pixels each is well within the supply, but real draw depends on pixel wattage (unknown).
- There is a red status LED on the controller's top face (lit in photo).

## 2. Network identity [verified]

| Item | Value |
| --- | --- |
| IP | 192.168.1.50 (same /24 as this Mac, 192.168.1.20) |
| MAC | AA:BB:CC:DD:EE:FF (matches app) |
| Virtual ID (app) | <virtual id> |
| Discovery reply model | `AK001-ZJ21413` |
| Discovery reply token | `<32-hex-token>` (32 hex chars; purpose not yet known) |
| Ping | ~6 ms typical, first reply slow (Wi-Fi power save) |

### Open / reachable services
| Port | Result |
| --- | --- |
| TCP 5577 | **open** — the control port |
| UDP 48899 | **answers** broadcast-style discovery (`HF-A11ASSISTHREAD`), reply: `192.168.1.50,AABBCCDDEEFF,AK001-ZJ21413,<token>` |
| TCP 22, 23, 80, 443, 1883, 5588, 6668, 8080, 8883, 8899, 9000, 48899 | filtered (no response) |

A full 65535-port TCP scan took 64 minutes and is **unreliable**: it did not list 5577 as open (which works fine for control), showed 53/tcp "open" and 9524/54122 "closed", and reported 0.7 ms latency (too fast for this Wi-Fi device). The controller drops/rate-limits probes, so treat the full scan as inconclusive. Confirmed working ports are only TCP 5577 and UDP 48899.

## 3. Protocol observations

Local control is on **TCP 5577**, no authentication was needed for a read-only query **[verified]**.

### Status query
Sent `81 8a 8b 96` (the classic Magic Home "query state" command). Reply, 27 bytes:

```
ea 81 01 00 b6 15 23 25 65 50 f0 b4 64 64 3c 1d 05 00 fa 00 00 00 20 02 00 00 03
```

- This is **not** the classic 14-byte `81 ...` status reply. It starts with `ea 81`, the newer Zengge framing. **[verified]**
- `flux_led` (the open-source Magic Home library) fails with "Cannot determine protocol", so this firmware is newer than what that library supports. **[verified]**
- `0xb6` at byte 4 is probably a device-type/model code. **[hypothesis]**
- Other probes (`63 12 21 36`, `24 2a 2b 0f`) got no reply. **[verified]**

Field-by-field decoding is not done yet.

### Update: protocol family (2026-10-12)
- `flux_led` 1.2.0 has a `ProtocolLEDENET25Byte` class whose reply format starts `ea 81` with 20+ bytes, which matches our reply. Its query is the same `81 8a 8b 96` we sent. **[verified]**
- Byte 2 of our reply changed between two queries (`01` then `02`), so it is probably a sequence counter. **[hypothesis]**
- Model code `0xB6` and discovery model `AK001-ZJ21413` are **not** in `flux_led`'s model database (it only goes to `ZJ21412`), so this is a newer unsupported variant. **[verified]**
- Public reverse-engineering of a related Surplife device ([zff.dev write-up](https://zff.dev/ge0rg/surplife-curtain)) shows the same family: frames of `seq(2) 80 00 len(2) len+1 0a payload`, power `71 23`/`71 24`, HSV color `e2 0b ...`, per-pixel `e2 0c 90 ...`, init `ea 81 8a 8b 59`. That device was BLE-only and a different product, so none of this is confirmed for ours yet. **[hypothesis]**
- The Surplife app ships JSON protocol definitions (`ble_dp_cmd.json` inside the APK, downloadable from the vendor). This is a possible way to get the full command set without capturing traffic.
- Passive capture on the Mac cannot see phone-to-controller Wi-Fi traffic (per-client encryption). A phone-side capture (`rvictl` over USB, needs Xcode setup) is possible but not required yet.

### Update: status frame decoded, app command files obtained (2026-10-12)

**Status frame decode [verified against the live lights].** The lights were on and red when queried. Using `flux_led`'s 25-byte layout:

| Byte(s) | Value seen | Meaning |
| --- | --- | --- |
| 0-1 | `ea 81` | extended status header |
| 2 | `01` / `02` | changes per query (likely a counter) |
| 4 | `b6` | model number |
| 5 | `15` | firmware/version number |
| 6 | `23` | power (0x23 = ON, 0x24 = OFF) |
| 7 | `25` | preset pattern number (37) |
| 9 | `65` | speed |
| 10 | `f0` | `f0` = RGB mode, `0f` = white mode |
| 11 | `b4` | hue/2 = 180, so hue 360 = **red** (matches the lights) |
| 12 | `64` | saturation 100 |
| 13 | `64` | value/brightness 100 |

**App package.** Downloaded Surplife 1.6.4 (Build 727) from the vendor server into `apk_download/surplife.apk` (349 MB, SHA-256 `f4520dbac15f13e47f667c50f5d67c89e2122703b4f481d7b7f76dfdc8a06dc0`). Assets extracted (not executed) into `apk_extracted/`. The command definitions are in `apk_extracted/assets/flutter_assets/packages/magichome2_home_data_provide/assets/wifi_dp_cmd.json` and `wifi_dp_response.json`.

**Wi-Fi commands from the app (payload before checksum; "cs" = sum of bytes & 0xFF appended):**

| Name | Bytes | Notes |
| --- | --- | --- |
| state query | `81 8a 8b 96` | works on this controller **[verified]** |
| state query v2 | `ea 81 8a 8b` | |
| power on/off | `71 23 0f` / `71 24 0f` + cs | not yet sent |
| built-in scene | `38 <model 37-56> <speed> <bright>` + cs; v3: `e0 02 <preview> <model> <speed> <bright>` | scene numbers 37-56 |
| brightness | `3b 01 00 00 <value> 00 <value> <delay> ...` + cs | |
| set time | `10 14 <yy mm dd hh mm ss week> 00` + cs | |
| **pixel/IC config query (A3)** | `63 12 21 f0` + cs | reply layout: `{type} 63 {pointCount x2} {part x2} {icType} {sort} {musicPart x2} {cs}` (pixel count, segment count, IC type, color order) |
| older config query | `63 12 21 36`, `63 12 21 0f` | |
| other queries | timer `22 2a 2b`, custom `52 5a 5b`, symphony fixed data `44 4a 4b`, function `2b 2c 2d`, colour data `b0 ba bb` | |

Newer devices may also need an outer envelope `b0 b1 b2 b3 00 01 01 <seq> <len16> <payload> <cs>` (as built by `flux_led`).

**Config queries tried, all read-only, all got NO reply [verified]:** `63 12 21 f0` (plain with/without checksum and wrapped), `63 12 21 0f`, `63 12 21 36`, `44 4a 4b`, `2b 2c 2d`, `b0 ba bb`. Pixel count and IC type are therefore still unknown. Possible reasons: the controller needs a different wrapper or sequence, requires a handshake first, or the app reads this from the cloud/BLE instead. The app's `60_surplife.js` bundle may show the real exchange (not yet read).

### Update: this controller is Surplife product 182 "symphony_line" (2026-10-12)

From the app's product table (`60_surplife.js`, a data bundle, not runnable logic; it was read, not executed):
- Model byte `0xB6` = decimal **productId 182**, `type: "symphony_line"`, category `hc3`, protocol `symphonyE1`, state protocol `wifibleSymphony`.
- App tabs for this product: **colorful, scene, carousel, smear, musicMic, power, timing, settings, linkage, white, layout**. The pixel count / IC type / wiring controls are therefore under the in-app **Settings** and **Layout** tabs of the light's control screen, not under "Remote Control Settings" (that one is for BLE remotes/panels).
- Functions: `custom_data_hc3`, `smear_hc3`, `smear_hc3_v2`, `static_colorful_hc3`, `symphony_scene_hc3`, `symphony_scene_v2`, `set_wiring_preview`, `multiple_screen_data`, `function_setting`, `function_inquiry`, `ic_RGBW_TM1814` (needs device version >= 9), `rgb_w_mix`, `colour_data_v2/v3`, `music_data_hc3`, `symphony_mode_white`, `linkage_hc3`. Their byte formats are NOT in the JSON files; they are compiled into `libapp.so` (being analyzed, see `APP_PROTOCOL_ANALYSIS.md` when present).

**Envelope and replies [verified].** The controller only answers wrapped commands with envelope version `02`:
`b0 b1 b2 b3 00 01 02 <counter> <len16> <inner> <checksum>`.
- `ea 81 8a 8b` plain -> reply is the state frame wrapped in an envelope.
- Wrapped `2b 2c 2d` -> `2b 02 00 00 00 00 3a 00 00 00 00 00 00` (remote config; 0x3a is probably a setting).
- Wrapped `e0 06` (timer list v2) -> `e0 06 01 00.. 02 00.. 03 .. 06 ..` = **six timer slots, all empty**.
- Wrapped `e0 0a`, `e0 08`, `e0 0d` (sleep/wake/countdown inquiries) -> no reply (probably unsupported or no data).
- Still no reply to any `63 12 ..` pixel/IC config query, `44 4a 4b`, `52 5a 5b`, `b0 ba bb`.
- The controller stops accepting new TCP connections for a few seconds if hit with many rapid connect/disconnects; `zg.py` uses one persistent connection with a small gap between commands.

**Idea: "write a pixel color then read it back" [not possible].** TM1814 pixels have a one-way data line (controller -> pixel, daisy-chained); nothing is read back from the LEDs. The controller can only echo what it was told, so a read-back would "succeed" for pixel numbers that don't physically exist. Real pixel-count detection must come from the controller's stored config, or from a human seeing where the lit pixels stop.

### Update: pixel count and per-pixel commands (2026-10-12)

- **LED count = state-frame byte 18.** Our frame has `0xFA` there = **250**. Source: flux_led PR #539 (`extended_state_led_count`); matches the owner's guess. Unknown whether 250 is the total or per output (two outputs); to be settled by the walk-around test. **[verified read; interpretation medium confidence]**
- **Firmware version** is byte 5 of the `ea 81` frame = `0x15` = 21, which is above the app's feature thresholds (9 for `ic_RGBW_TM1814`/`function_setting`). **[verified]**
- Why `63 12 21 f0` is ignored: that query belongs to the older hc2 products (162/164/166); all hc3 products, including 182, have no such template. **[from app data, see APP_PROTOCOL_ANALYSIS.md]**
- Per-pixel commands (`zg_pixels.py`, from flux_led PRs #540/#544, one author tested on a 0xB6; medium confidence): `E1 22` solid/20-segment fill, `E1 23` scribble init (N<=255), `E1 26` paint a color onto a pixel bitmap, `E1 21` built-in animations. First live test: after the sequence the pattern byte changed `0x61` -> `0x66` (scribble mode) as upstream predicts; visual result awaiting owner report.
- **Owner confirmed [verified]:** the alternating red/blue per-pixel pattern (E1 23 init + E1 26 paints) displayed correctly on the visible portion of the strip.
- Walk-around tests are in `tonight_tests.py` (ruler, halves, chase, fillto, blocks, colororder, gradient, effects, restore, on/off); each returns the lights to solid red on exit. Run with `.venv/bin/python tonight_tests.py <test>`.
- **Walk-around results, round 1 (owner, 2026-10-12) [verified by observation]:**
  - `chase`: pixel #1 appeared **twice** on the house (and #2 twice, etc.), and `halves` also showed the same thing. So the two outputs are **mirrored**: both receive the same data, and a pixel index lights one spot per output.
  - `fillto 100`: green covered the entire back porch, i.e. **more than 100 physical lights**. Consistent with each controller "pixel" driving a group of several LEDs (as the TM1814 24V description says), but not yet measured.
  - `ruler`: owner counted about 14 red ticks (14 x 50 = 700 "pixels"), which is **more than expected** (5 red ticks per output = 10 maximum if each index lit once per output). Unexplained; possible causes: pixel groups/segments repeating along the run, a mapping/layout setting in the app, or miscounting ticks across both mirrored runs. New tests `single N` and `zones` are meant to separate these.
  - Built-in effects the owner liked: **7 Horse Racing, 8 Cycle, 12 Twinkling Stars** (names from flux_led PR #540 pattern enum: 1 Wave, 2 Meteor, 3 Streamer, 4 Building Blocks, 5 Flowing Water, 6 Chase, 7 Horse Racing, 8 Cycle, 9 Breathe, 10 Jump, 11 Strobe, 12 Twinkling Stars, 13 Stars Wink, 14 Warning, 15 Collision, 16 Fireworks, 17 Comet, 18 Gradient Meteor, 19 Volcano, 20 Superluminal, 21 Rainbow Bridge, 22 Gradient Overlay, 101 Static Gradient, 102 Static Fill).
- **Custom animation (2026-10-12), `halloween.py` [verified, owner approved the look]:** scenes embers, creep, ghost, lightning, blklightning, orglightning (dark red-orange fill `(90,10,0)`, purple/black flashes). Frames are painted with E1 26, one command per distinct color per frame.
- **Controller rate limit [verified]:** flooding it at ~75 paint commands/s made it stop accepting data (it recovered by itself and once briefly reported power off). Safe pacing: ~0.03 s between paint commands and >= 0.2 s per frame, i.e. about 4-5 frames/s. Built-in effects run on the controller and are smooth.
- Still unknown: how to READ IC type/color order, or WRITE the pixel count (hc3 config layouts exist only in the app's compiled Dart code; builder names/offsets are in `APP_PROTOCOL_ANALYSIS.md` section 5). Do not send `62 ...` config writes or `e0 43` (reset) on this model.
- Other notes: `RESEARCH_NOTES.md` has the full upstream research with links.

## 4. Pixel count / segments

Not yet determined. The owner's estimate is about 250 pixels per segment, two segments. Two ways to find out:
1. Read the controller's configuration (IC type, pixels per segment, color order) over port 5577 once the protocol is decoded.
2. Physical test: light pixels one at a time (or in a sweep) and count/observe where the run ends.

## 5. Open questions / next steps

1. Decode the `ea 81` frame, find the "get device settings" command (IC type, pixel count, segments, color order).
2. Find the command framing for setting color and effects, and a custom per-pixel mode.
3. Look for packet captures of the Surplife app (Wireshark/tcpdump on the Mac with the phone on the same Wi-Fi) to learn the exact command set.
4. Bluetooth LE: scan for the controller and document its GATT services.
5. Confirm which commands are safe. **Do not send write/config commands until we agree on a test and a way to restore the settings.** Pixel count, IC type and color order are persistent settings and a wrong value changes how the whole run displays.

## 6. Environment

- Project folder: `TM1814_LED_Controller/`
- Python venv: `.venv/` (contains `flux_led`, used only for read-only probing)
