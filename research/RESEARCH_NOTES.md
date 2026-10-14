# Research notes: Zengge / Surplife 0xB6 (AK001-ZJ21413) local Wi-Fi protocol

> **Public research.** What the open-source community has documented about this controller family (mainly the `flux_led` project), with confidence labels and links. Written before the program; the program now implements the parts marked as used in [../TECHNICAL_README.md](../TECHNICAL_README.md). Addresses are replaced by examples.

Researched 2026-10-12. No traffic was sent to 192.168.1.50. Downloaded material (untrusted, read only, never executed) is in
`research_downloads/`: `flux_led/` (lightinglibs/flux_led main, HEAD cea6348, 2026-07-02), `flux_led_prs/` (diffs of PRs 540/541/542/544/555),
`zengge_lednetwf/` (8none1, BLE), `open-zengee/`, plus two unrelated BLE-bulb repos (`coffeekanzler_zengge/`, `kirillsimin_zengge/`).

Confidence labels: HIGH = in merged upstream code or tests and matches what we observed. MED = hardware-verified claim by one PR author, not merged.
LOW = inferred, from a different model, or a search-snippet only.

## 1. Headline finding

Our exact device class is already supported upstream in python-flux_led (`pip install flux_led`, but check the version, see below).

- Model 0xB6, `AK001-ZJ21413`, "Surplife Outdoor Permanent Lighting". `models_db.py` also has `"ZG-BL-UFO": "# Seen on Surplife outdoor permanent lighting 0xB6"`. [HIGH]
- Merged PRs: [#538](https://github.com/lightinglibs/flux_led/pull/538) (model entry) and [#539](https://github.com/lightinglibs/flux_led/pull/539) (`ProtocolLEDENETExtendedCustom` plus LED count). Both by asxzy, July 2026.
- Open PRs, not merged as of the HEAD I cloned: [#541](https://github.com/lightinglibs/flux_led/pull/541) (timers), [#542](https://github.com/lightinglibs/flux_led/pull/542) (CLI for effects, segments, scribble),
  [#544](https://github.com/lightinglibs/flux_led/pull/544) (E1 22 colour and segment set). [#540](https://github.com/lightinglibs/flux_led/pull/540) (closed and split) is the best write-up of the per-LED commands.
  [#555](https://github.com/lightinglibs/flux_led/pull/555) fixes unknown model bytes that answer `ea 81`. [#498](https://github.com/lightinglibs/flux_led/pull/498) was the original large PR, closed.
- The PyPI release that Home Assistant pins was 1.2.0 per [issue #402](https://github.com/lightinglibs/flux_led/issues/402), so it predates this. Use git main or the diffs, and check `pip index versions flux_led` before relying on PyPI.
- The PR diffs are the only place the per-LED ("scribble") and effect formats are documented. They are the best lead for (b).

Caveat: PR #540's author tested on their own 0xB6 and says "hardware-verified", but the code is not merged or independently confirmed. Their frames show
model_num 0xB6 (ours matches) and, in #555, an unknown model byte 0x77 on a sibling ZJ21413, so the model byte varies across units.

## 2. Framing (matches what we already see) [HIGH]

Wrapped message, built by `construct_wrapped_message` in `flux_led/protocol.py`:

```
b0 b1 b2 b3 00 01 <ver> <counter> <len_hi> <len_lo> <inner...> <checksum>
```
- `ver` is 0x01 for ordinary queries such as `2b 2c 2d`. The 0xB6 colour and E1 commands use **0x02**.
- `len` is the inner length, without the checksum.
- `checksum` is the sum of all preceding bytes & 0xFF. It is added by `construct_message`.
- `inner_pre_constructed=True` means the inner bytes carry no checksum of their own.

Example from the flux_led source comment (the E0 01 colour command, which we reproduced):
```
b0 b1 b2 b3 00 01 02 79 00 0e | e0 01 00 a1 78 64 64 00 00 00 00 14 00 00 | cs
                                         ^^ write mode a1=colour, b1=white;  78 = hue/2, 64 = sat, 64 = val
```
Unwrapped power commands are `71 23 0f a3` (on) and `71 24 0f a4` (off).
Probe: `81 8a 8b 96`. The 0xB6 answers only with the 27-byte extended state `ea 81 ...`
(also [issue #567](https://github.com/lightinglibs/flux_led/issues/567) for the sibling 0xAA/ZG-BK).

## 3. Extended state (27 bytes) layout [byte 18 = HIGH, others MED/LOW]

From `LEDENET_EXTENDED_STATE_LED_COUNT_POS = 18`, `extended_state_to_state()` and the test frames in `tests/test_sync.py` and `tests/test_aio.py`:

| idx | meaning | confidence |
|---|---|---|
| 0-1 | `ea 81` | HIGH |
| 4 | model_num (0xB6, or 0x77 on one sibling) | HIGH |
| 5 | firmware/version byte (0x09 in the test frames) | HIGH |
| 6 | power, 0x23 on / 0x24 off | HIGH |
| 7 | preset pattern. 0x24 = plain colour (E1 22), 0x25 = effect/scene (`mode` = byte 8 = effect id), 0x66 reported after scribble init | HIGH/MED |
| 9 | speed | HIGH |
| 11, 12, 13 | hue/2, saturation (0-100), value (0-100) | HIGH |
| 14, 15 | white temp (0-100), white brightness (0-100) | HIGH |
| **18** | **configured LED count** (0x64=100, 0x50=80, "verified against two real frames") | HIGH for count |
| 16, 17, 19-26 | unknown. Observed: [16]=0x05 or 0x01, [22]=0x20 or 0x80, [23]=0x02 or 0x03. Possibly IC type, colour order, segment count, but **nothing documents these** | LOW |

Test frame (hex): `ea 81 01 00 b6 09 24 66 01 64 f0 00 00 00 00 64 05 00 64 00 00 00 20 02 01 00 03`
Frame in #555: `ea 81 01 00 77 09 23 25 03 50 f0 0b e4 64 00 00 01 00 64 00 00 00 80 03 00 00 00`

So **pixel count is already in the `81 8a 8b 96` reply, byte 18**. Compare our 27-byte frame against these two for bytes 16, 17, 22, 23 to guess IC type / wiring.
flux_led exposes it as `light.led_count`.

## 4. Config queries that other models use (ignored by ours, consistent with your observation)

- A1 addressable: `63 12 21 36` -> `63 <cnt_hi> <cnt_lo> <ic> 00 00 00 00 00 00 <wiring> cs`. IC 01 UCS1903, 02 SM16703, 03 WS2811, 04 WS2812B, 05 SK6812, 06 INK1003, 07 WS2801, 08 LB1914. [HIGH, for 0xA1 only]
- A2/A3: `63 12 21 0f` -> `00 63 <cnt_hi> <cnt_lo> 00 <segs> <ic> <wiring> <music_cnt> <music_segs> cs`. [HIGH, for 0xA2/0xA3 only]
  A3 IC list: 01 WS2812B, 02 SM16703, 03 SM16704, 04 WS2811, 05 UCS1903, 06 SK6812, 07 SK6812RGBW, 08 INK1003, 09 UCS2904B. TM1814 is **not** in any list.
- Set config (A-series) is `62 ...` (see `construct_device_config`). Do **not** try it on the 0xB6 untested: a wrong write could change wiring or IC.
- BLE-only analogue (0x56 firmware, different transport): `81 8a 8b 96` returns a JSON settings payload; settings write is `62 00 64 00 03 01 00 64 03 f0 cs`
  per [8none1/zengge_lednetwf readme](https://github.com/8none1/zengge_lednetwf) "LED settings". Chip type 0x01-0x0b, colour order 0x00-0x05. [LOW for our device]

Conclusion: the Wi-Fi 0xB6 has no known IC-type query. The IC choice is presumably set in the app and may be a firmware-level "ZG-BL-UFO" fixed config (TM1814 RGBW would be a normal pairing for this outdoor product line).

## 5. Per-LED / effect commands for 0xB6 (all wrapped, `ver=0x02`) [MED, from PR #540 / #544 diffs]

Source: [PR #540](https://github.com/lightinglibs/flux_led/pull/540) (diff saved as `research_downloads/flux_led_prs/pr540.diff`), [PR #544](https://github.com/lightinglibs/flux_led/pull/544).
HSVW unit is 5 bytes: `[H/2, S, V, 00, W]` with H/2 0-180, S, V and W 0-100 (colours: W=0; white: H=0, S=0x64, V=0, W=level).
Note: with S=0 the device ignores the white frame; S must be 0x64.

1. **Solid colour, E1 22 uniform fill** (preferred over E0 01, because E0 01 may leave the device in a different mode):
   `e1 22 00 00 00 00 14 | 5 bytes x 20 segments` (20 identical segments for a solid fill). Lands in preset 0x24 "Colorful".
   Also **segment colours**: 20 fixed segments, each about N/20 LEDs long. Unsent segments are off, no auto-repeat.
2. **Effects, E1 21**: `e1 21 01 64 PP OO DD DENS SPD 00 00 00 00 00 00 CC | [H/2 S V 00 00] x CC`
   (PP pattern 1-22, 101 static gradient, 102 static fill; OO option 00/01 colour-change/02 breathe; DD direction 01 L-R / 02 R-L; DENS and SPD 0-100; CC up to 8 colours).
   Names: 01 Wave, 02 Meteor, 03 Streamer, 04 Building Blocks, 05 Flowing Water, 06 Chase, 07 Horse Racing, 08 Cycle, 09 Breathe, 0A Jump, 0B Strobe, 0C Twinkling Stars, 0D Stars Wink, 0E Warning,
   0F Collision, 10 Fireworks, 11 Comet, 12 Gradient Meteor, 13 Volcano, 14 Superluminal, 15 Rainbow Bridge, 16 Gradient Overlay, 65 Static Gradient, 66 Static Fill.
   (PR #540's docstring shows byte 2 as 0x01 while its summary text shows `00`. Use 0x01 as in the code.)
3. **Per-LED "scribble"**, the answer to (b):
   - E1 26 paint (the render path): `e1 26 EFFECT DIR DENS SPEED BLINK H/2 S V 00 WLVL BLINKSPD <bitmap>`
     (13-byte header, then `ceil(N/8)` bytes of bitmap, MSB-first: LED i = byte i//8, bit 0x80>>(i%8); bit set = paint this LED with this colour.
     N=100 -> 13 bytes with last byte 0xf0). Effect 0x00 static, 0x01 flowing, 0x02 twinkling, 0x03 stars-wink, 0x04 accumulate, up to 0x08.
     Blink 0x00 none / 0x08 slow / 0x10 fast. Defaults used: DIR 01, DENS 0x50, SPEED 0x64, BLINKSPD 0x64.
   - One E1 26 paints **one colour onto a set of LEDs**. To draw an arbitrary frame you send one E1 26 per distinct colour (the PR groups by colour/blink).
   - E1 23 init (non-rendering, enters scribble mode): `e1 23 01 00 01 50 64 00 <N> | (00 00 00 00 00 00 64) x N` (inner length 9 + 7N). Needs N <= 255 and a bitmap that fits in one byte count.
   - The PR notes E1 26 alone also works once already in scribble mode.
   - Because each frame is a handful of TCP writes (one per colour), full-motion streaming is probably limited by firmware/app behaviour; **nothing documents rate limits or flash wear**. [LOW]

Verification state: PR #540 was closed only to be split up, and the review rounds included hardware testing by the author. No second person has confirmed it.

## 6. The E0 family (`e0 01 ... e0 0d`), `multiple_screen_data`, `custom_data_hc3`, `smear`, `symphony_line`

- `e0 01 00 a1/b1 ...` is documented in flux_led as the wrapped HSV colour/white command for 0x35-like (25-byte) devices. [HIGH]
  No public source documents `e0 02` to `e0 0d`. The user-supplied names `multiple_screen_data`, `custom_data_hc3`, `symphony_line` returned **no public hits** (searches found only unrelated Fibaro HC3 results). [not found]
- "Smear" is the Zengge **BLE** app name for per-pixel patterns ([8none1](https://github.com/8none1/zengge_lednetwf) "Smear"):
  firmware 0x53 BLE packet `00 10 80 00 00 96 97 0b 59 <RR GG BB x N> <mode 1 static/2 stream/3 strobe/4 jump> <speed> <brightness> <dir> cs`; 0x56 "TBD". Opcode 0x59 with flux_led style `0x59`... the same
  `59` opcode appears in [zff.dev surplife-curtain](https://zff.dev/ge0rg/surplife-curtain) as the init query (`ea 81 8a 8b 59`). That page documents a **different, BLE-only** 'IOTBT' curtain (fw `BT.80.00C2.0B.05,V3`): `e2 0c 90` per-pixel bitmap,
  `ea 01/02/03/04` animation upload with an unknown compression, `e2 0b` HSV colour. [LOW for our model, it is a different transport and model]
- The `ea 01..04` upload sequence on the curtain, and E1 23/E1 26 on the 0xB6, suggest the same vendor SDK with several "screen" data paths; the compressed animation upload is unreverse-engineered anywhere I found.

## 7. Hardware / alternative firmware

- Search snippet only (elektroda forum, fetch timed out so [LOW]): "SurpLife (Zengge) Symphony Controller USB SPI" uses a **BK7238**, early OpenBK7238 flashed with Easy Flasher; a Magic Home Pro RGBW variant uses a **BL602** with a 74HC245 level shifter.
  Threads: https://www.elektroda.com/rtvforum/topic4058754.html and https://www.elektroda.com/rtvforum/topic4090758.html. Read these before any teardown, and note that the 24V outdoor 0xB6 unit may use a different chip.
- OpenBeken has SPI LED support for some BK chips, but nothing found documents running it on this exact controller. Flashing replaces the vendor firmware and the Surplife app, and is the fallback if the vendor protocol cannot do per-pixel frames. WLED is ESP-only (ESP8266/ESP32), so it applies only if the board turns out to be an ESP part. [LOW]
- No GitHub topic hits for a purpose-built 0xB6 or Symphony Wi-Fi project other than flux_led and the PRs above. `open-zengee` (mfalme0) only wraps the old 8-byte protocol and E1.31/WLED bridging, nothing for 0xB6.

## 8. What applies to our model vs others

| source | model | applies to our 0xB6? |
|---|---|---|
| flux_led main, `ProtocolLEDENETExtendedCustom`, `led_count` | 0xB6 | yes, merged |
| flux_led PR #540/#544/#542/#541 | 0xB6 | yes (MED), unmerged |
| flux_led #555 frame | ZJ21413 with model byte 0x77 | likely same family |
| flux_led #567 | 0xAA ZG-BK, ZJ21413, firmware 0x0B | related, 25-byte protocol, same E0 colour wrapper |
| `63 12 21 36`/`0f` configs | 0xA1/A2/A3 | no (ignored by ours as observed) |
| 8none1 BLE | LEDnetWF 0x53/0x56 | different transport and framing, only conceptual hints |
| zff.dev curtain | BT-only curtain | no |

## 9. Ranked options

### (a) Read pixel count / IC type
1. **Parse byte 18 of the 27-byte `ea 81` state reply** to get the pixel count (flux_led `led_count`). [HIGH]  Verified by two real frames upstream.
2. **Diff the full 27-byte frame across settings you change in the Surplife app** (IC type, colour order, LED count). Bytes 16, 17, 22, 23 vary in public frames and are the best IC/order candidates. Do this by reading `81 8a 8b 96` only; no config write needed. [MED]
3. Use `flux_led` itself against the device (read-only: `python -m flux_led 192.168.1.50 -i`), which would also confirm `led_count`. (Not run by me per instructions.) [HIGH that it reads state]
4. Packet-capture the Surplife app (phone via a Mac proxy, or tcpdump on the Mac with the phone on the same Wi-Fi/hotspot) while changing the IC type, to learn the config write opcode. Nobody has published this. [LOW, effort high]
5. Do not try `63 12 21 xx` or `62 ...` writes on the 0xB6. Queries are ignored; writes are risky.

### (b) Individual pixels, frames, custom animations
1. **E1 23 init then E1 26 paints** (section 5.3): bitmap per colour, one E1 26 per distinct colour per frame. This is the only documented per-LED path for 0xB6. [MED] Prototype on a short test string, one colour first.
2. **E1 22 segment colours**: 20 coarse segments in one packet, the cheapest "frame" (20 colours). [MED]
3. **E1 21 effects**: 24 built-in animations with up to 8 colours, for custom palettes without streaming. [MED]
4. Capture the app's "DIY / smear / scene" screens to find the compressed multi-frame upload (`ea 01..04`-style, as on the curtain). Not documented anywhere; effort high. [LOW]
5. Replace firmware with OpenBeken (BK7238) or WLED (only if ESP) for unrestricted E1.31/DDP streaming. Destructive; check the chip first. [LOW]

## 10. Sources

- https://github.com/lightinglibs/flux_led (main, `flux_led/protocol.py`, `models_db.py`, `tests/`)
- flux_led PRs: [#498](https://github.com/lightinglibs/flux_led/pull/498), [#538](https://github.com/lightinglibs/flux_led/pull/538), [#539](https://github.com/lightinglibs/flux_led/pull/539), [#540](https://github.com/lightinglibs/flux_led/pull/540), [#541](https://github.com/lightinglibs/flux_led/pull/541), [#542](https://github.com/lightinglibs/flux_led/pull/542), [#544](https://github.com/lightinglibs/flux_led/pull/544), [#555](https://github.com/lightinglibs/flux_led/pull/555)
- flux_led issues [#402](https://github.com/lightinglibs/flux_led/issues/402) (extended probe wrapper `b0b1b2b3000102000004ea818a8b4d`), [#567](https://github.com/lightinglibs/flux_led/issues/567)
- https://github.com/8none1/zengge_lednetwf (BLE protocol, smear, LED settings)
- https://zff.dev/ge0rg/surplife-curtain
- https://github.com/CoffeeKanzler/zengge-lightbulbs/issues/1 (ZJ21411 Spider 8D, `E1 01` effect frame: `B0 B1 B2 B3 00 01 02 51 00 23 E1 01 00 64 03 00 01 64 50 00 A1 ...`; related E1 family on a sibling model) [LOW for ours]
- https://www.elektroda.com/rtvforum/topic4058754.html, https://www.elektroda.com/rtvforum/topic4090758.html (chip info, snippet-only)
