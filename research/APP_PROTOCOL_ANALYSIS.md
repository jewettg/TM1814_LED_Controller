# Surplife v1.6.4 - protocol analysis for productId 182 (symphony_line / hc3, TM1814 RGBW)

> **Vendor app analysis.** Static analysis of the data files inside the Surplife Android app (read for interoperability research only; no vendor code or assets are in this repository). It records what the app's own files say about this controller, and which commands could **not** be found, so nobody repeats the search. Addresses and local paths are replaced by examples.

Static analysis only. The controller (192.168.1.50) was never contacted. No app code was executed.

Confidence labels:
- **verified-in-binary**: read directly from an app asset or binary (file and offset given).
- **inferred**: derived from templates plus a third-party reference (flux_led `protocol.py`, an open-source project and not app code).
- **MISSING**: the bytes live in compiled Dart code that strings/jadx cannot read. See "What is missing".

## 0. Bottom line

1. The `63 12 21 f0` config query is the wrong family for product 182. Product 182 is `categoryCode: "hc3"` and has an **empty `hexCmdForms`** in `wifi_devices.json`, so it has no `get_symp_config_v2` or `symp_config_v2` entry. The hc2 symphony products (162, 164, 166) do carry `get_symp_config_v2`. The hc3 products (163, 170-188, 57, 61, 69, 76, 83, 86...) never do. This matches the controller ignoring that query (verified-in-binary, section 2).
2. The hc3 commands (`custom_data_hc3`, `static_colorful_hc3`, `smear_hc3`, `smear_hc3_v2`, `symphony_scene_hc3`, `set_wiring_preview`, `multiple_screen_data`, `ic_RGBW_TM1814`, `function_setting`) have **no hex templates in any asset**. The templates exist only as function-code names. The byte builders are compiled Dart AOT code in `libapp.so`, in methods such as `buildSetLedMappingHEXCommand`, `buildWiringTypeHexCommand` and `buildColorfulV2HEXCommand`. Recovering them needs a Dart AOT decompiler. I tried to run one (blutter) and the permission classifier denied it (section 6).
3. These e0-family templates are verified-in-binary and usable now (section 3): `switch_led_v3`, `scene_data_v3`, `state_upload_v2` (`ea 81 8a 8b`), `function_inquiry` (`2b 2c 2d`), `fall_asleep_inquire` (`e0 0a`), `wake_up_inquire` (`e0 08`), `get_timer_list_v2` (`e0 06`), `countdown` (`e0 0d`).

## 1. Files and tools

| Item | Location |
|---|---|
| libapp.so (Dart 3.11.0 stable, android_arm64, engine 3452d735...) | `apk_re/lib/arm64-v8a/libapp.so` |
| classes.dex..classes5.dex | `apk_re/` |
| jadx 1.x decompile (scratch, not in the project) | `<scratch folder>` |
| libapp string dump with file offsets | `.../scratchpad/app_strings.txt` |
| Third-party reference | `.../scratchpad/flux_protocol.py` (flux_led master, downloaded as text only) |

jadx was installed via brew, as you allowed. The Java side (`com.zengge.wifi.MagicHome2SendCommand`, `ControlMessages.pushCommand`) only receives a ready-made hex string from Dart and sends it. It contains **no** command building and **no** envelope code. All protocol logic is in Dart. (verified-in-binary)

## 2. How the app picks a command family for product 182 (question 3)

Source: `60_surplife.js` (product records start at offset 226463 for productId 182) and `wifi_devices.json`. Both agree. (verified-in-binary)

```
productId 182, categoryCode "hc3", category "zm", icon enum://dengdai
protocols:      common(0) common1_0(0) common2_0(0) symphonyE1(0)   [deviceMinVer in parentheses]
stateProtocol:  wifibleSymphony(0)
hexCmdForms:    {}        <- empty
runtimeScripts: []   responseHexDescribes: []
```

Function list with `deviceMinVer` (anything without a number means 0):
- `use_ble_music_mode` 20 (BLE only)
- `symphony_scene_v2`, `smart_ai`, `sbl_dt_symphony`, `rgb_w_mix`
- `multiple_screen_data` 2
- `firmware_data`, `timer_data`, `state_upload`, `state_upload_v2`
- `function_setting` **9**, `function_inquiry`
- `set_time`, `control_data`
- `switch_led_v2`, `switch_led_v3`, `bright_value_v2`
- `timer_data_v2`, `colour_data_v2`, `colour_data_v3`, `switch_led`
- `get_time`, `get_timer_list`, `get_timer_list_v2`
- `wake_up`, `wake_up_inquire`, `fall_asleep`, `fall_asleep_inquire`, `countdown`
- `music_data_hc3`, `custom_data_hc3`, `linkage_hc3`, `static_colorful_hc3`
- `smear_hc3`, `set_wiring_preview`
- `smear_hc3_v2` **3**
- `microphone_V2`
- `ic_RGBW_TM1814` **9**
- `symphony_scene_hc3`, `symphony_mode_white`
- `symphony_music_mode_v2` 10

Notes on how this works:
- `deviceMinVer` is compared with the firmware version byte the controller reports. In `state_upload` the `{ver}` field is byte 10 (response template below). Firmware v9 or higher is needed for `function_setting` and the TM1814 IC option. v3 or higher is needed for `smear_hc3_v2`, and v2 or higher for `multiple_screen_data`. (verified that the thresholds exist; inferred that the `ver` byte is what is compared)
- **Tell the user's firmware version.** The `ver` byte of an `81 8a 8b 96` reply is state-response byte 10. If it is below 9, the app will not offer the TM1814 IC setting or `function_setting` at all.
- The `symphonyE1` protocol name is a Dart-implemented protocol with no asset template. The string is at libapp offset 11327872. `curtainLightE2` (offset 5176063) is the sibling used by curtain products 172-179. (verified-in-binary)
- Other hc3 products 170, 171, 175, 181, 183 and 184 use the same `symphonyE1` protocol. Products 163/178/185 omit it. Product 182 is therefore the same family as 170-188.
- The JS bundle's `hexCmdForms` for hc2 products 162/164/166 do contain `get_symp_config_v2: 631221f0`, `symp_config_v2`, `symp_scene_v2` and `symp_fixed_data`. The hc3 records have none of these.
- The name `get_symp_config_v2` does exist in `wifi_dp_cmd.json` (`631221f0`, checksum, 14-byte response). That is a global default, not a per-product opt-in. The firmware never answers it because it is the hc2 family.

## 3. Command templates that ARE in the assets (verified-in-binary)

Source: `apk_extracted/.../magichome2_home_data_provide/assets/wifi_dp_cmd.json`.

`needChecksum: true` means one trailing byte equal to the sum of all preceding bytes mod 256. This is inferred from flux_led (`construct_message`) and consistent with the `81 8a 8b 96` / `71 23 0f` + cs pattern you already use. `needChecksum: false` means no inner checksum. Those commands are sent inside the envelope, which has its own checksum.

| dpCode | cmdForm | cs | Meaning / notes |
|---|---|---|---|
| state_upload | `81 8a 8b 96` | no (96 is the literal cs) | state query, plain |
| state_upload_v2 | `ea 81 8a 8b` | no | state query, e0 family. You confirmed the controller answers it. |
| function_inquiry | `2b 2c 2d` | yes | remote config query. Confirmed answered, wrapped. |
| switch_led | `71 {open} 0f` | yes | open = 23 on, 24 off |
| switch_led_v2 | `3b {open} 00 00 00 00 00 {gradient}x3 {delay}x2` | yes | gradient = 3 bytes (V24), delay = 2 bytes |
| **switch_led_v3** | `e0 01 {preview} 3b {open} 00 00 00 00 00 {gradient}x3 {delay}x2` | no | e0 wrapper around the 3b unified command |
| bright_value_v2 | `3b 01 00 00 {value} 00 {value} {delay}x3 {gradient}x2` | yes | field widths as written in the template |
| temp_value_v2 | `3b b1 00 00 00 {cct} {bright} 00 00 1e 00 00` | yes | CCT, not relevant to RGBW strips |
| scene_data_v2 | `38 {model} {speed} {bright}` | yes | legacy scene |
| symp_scene_v2 | `42 {model} {speed} {bright}` | yes | symphony scene (hc2-style, 182 lists `symphony_scene_v2`) |
| **scene_data_v3** | `e0 02 {preview} {model} {speed} {bright}` | no | e0 family scene command |
| get_timer_list | `22 2a 2b` | yes | |
| get_timer_list_v2 | `e0 06` | no | |
| fall_asleep_inquire | `e0 0a` | no | |
| wake_up_inquire | `e0 08` | no | |
| countdown | `e0 0d` | no | |
| get_time / set_time | `11 1a 1b` / `10 14 {yr} {mo} {d} {h} {min} {s} {wk} 00` | yes | year is offset from 2000 |
| get_custom | `52 5a 5b` | yes | |
| get_mic_info(_v2) | `72 00` / `75 00` | yes | |
| symp_get_mic_info | `72 00 72` | no | |
| symp_app_volume | `74 {volume}` | yes | |
| get_symp_fixed_data | `44 4a 4b` | yes | hc2 family |
| get_symp_config_v2 | `63 12 21 f0` | yes | hc2 family. Ignored by hc3. |
| sdd_get_colour_data | `b0 ba bb` | yes | |
| reset_v2 | `e0 43 {mac1..mac6}` | no | factory reset by MAC (dataType 2). Do not send. |
| twinkle_data | `3a ...` | yes | |
| music_data_v2 | `36 ...` | yes | |

**Observation on your working color command.** The template `e0 01 {preview} 3b {open}...` shows the full layout `e0 01 <preview> 3b <mode> ...`. Your working color frame is `e0 01 00 a1 <hue/2> <sat> <val> 00 00 00 00 14 00 00`, which has **no `3b` byte** after the preview byte. Either the colour path is a different Dart function (`colour_data_v3` or `buildColorfulV2HEXCommand`) with the shape `e0 01 <preview> <mode a1> ...`, or the `3b` is implied. I cannot resolve this statically. It is a reminder to try both shapes when testing, using power first: `e0 01 00 3b 23 00 00 00 00 00 00 00 00 00 00` against the controller's reply. (inferred)

Power via the old family also works for you: `71 23 0f` + cs.

### Envelope (inferred from flux_led, matches what you found)

```
b0 b1 b2 b3 | 00 01 | ver | counter | len_hi len_lo | inner... | cs
```

- `cs` is the sum of all preceding bytes mod 256 (outer message).
- `len` is the length of `inner`, including its own checksum when the inner command has one.
- flux_led always uses `ver = 01`. You observed `02` working for e0 frames. That suggests `ver` is tied to the `e0` family and probably the "isLocalDataType / dataType 2" flag in `reset_v2` and `switch_led_v3`. (inferred)
- Example from flux_led, an A3 state query: `b0 b1 b2 b3 00 01 01 0c 00 04 81 8a 8b 96 04`.

## 4. Response formats (question 2)

Source: `wifi_dp_response.json` (verified-in-binary):

```
state_upload (flag byte0 = 0x81, length 14):
  81 {devType} {open} {mode} {zg://wiring_sort} {speed} {color}{color}{color} {warm} {ver} {cool} {flag} {cs}

  byte: 0=81, 1=devType, 2=open (23/24), 3=mode, 4=wiring_sort, 5=speed, 6-8=R G B,
        9=warm white, 10=ver (firmware version, compared with deviceMinVer),
        11=cool white, 12=flag, 13=checksum.
  Your controller's 81 reply already gives `wiring_sort` at byte 4 and `ver` at byte 10.

get_symp_config (old, flag 0x63 at index 0, length 12):
  63 {pointCount}{pointCount} {icType} {code_H_1}{code_L_1} {code_H_0}{code_L_0} {resetCode}{resetCode} {sort} {cs}

get_symp_config_v2 (flag 0x63 at index 1, length 11):
  {supportType} 63 {pointCount}{pointCount} {part}{part} {icType} {sort} {musicPart}{musicPart} {cs}
  = supportType | 63 | pixels_hi pixels_lo | part_hi part_lo | icType | sort | musicPix musicSeg | cs
  flux_led's A3 example agrees: 00 63 00 32 00 01 04 03 32 01 d0
      -> supportType 00, 50 pixels, 1 segment, icType 4, wiring 3, music 50x1, cs d0.

get_time (flags 0x11 at index 1, 0x14 at index 2, length 12)
get_mic_info (flag 0x72 at index 1, length 5): {from} 72 {open} {sensitivity} {cs}
AT_Rsp: "+ok" frames (flags 0x2b 0x6f 0x6b)
```

There is no response template for any hc3 command (`responseHexDescribes` is `[]` for product 182). Responses to e0-family commands are parsed in Dart (`parserHC3UIData`, offset 9291605; `_getHC3State@13585007994`, offset 9457972; `parserWifiBleV2WiringState`, offset 3041124). The inner bytes are MISSING.

## 5. Configuration read/write (question 1, inferred, not verified for hc3)

I cannot give a verified byte layout for any hc3 config read or write. What I can give is the nearest verified relative (the A3 family, from flux_led):

- Config read, A3, wrapped: `63 12 21 f0 <cs>` in the envelope. Your controller ignores it. That is consistent with hc3 using a different query.
- Config write, A3: `62 <pixels_hi> <pixels_lo> 00 <segments> <icType> <wiring> <musicPix> <musicSeg> f0 <cs>`.
  - Use only if you decide to risk it. It was never tried in your tests, and I have no evidence hc3 accepts it.
- IC type numbering (A3, flux_led): 1 WS2812B, 2 SM16703, 3 SM16704, 4 WS2811, 5 UCS1903, 6 SK6812, 7 SK6812RGBW, 8 INK1003, 9 UCS2904B. **TM1814 is not in this table.** The app added `ic_RGBW_TM1814` as its own function (firmware 9 or higher), so the ID is probably 10 or higher. MISSING.
- RGBW wiring order numbering (flux_led): 0 RGBW, 1 RBGW, 2 GRBW, 3 GBRW, 4 BRGW, 5 BGRW, 6 WRGB, 7 WRBG, 8 WGRB, 9 WGBR, 10 WBRG, 11 WBGR. TM1814 is normally WRGB. Whether the hc3 `wiring_sort` byte uses the same numbering is unverified.

Dart builder names that would contain the real hc3 bytes (string offsets in `libapp.so`):

| Function | Offset | Likely role |
|---|---|---|
| `buildSetLedMappingHEXCommand` | 7047086 | set pixel/segment mapping (config write) |
| `buildQueryLedMappingHEXCommand` | 9185031 | config read |
| `buildCustomSetLedMappingHEXCommand` | 8102990 | custom mapping |
| `buildWiringTypeHexCommand` | 4074312 | wiring/colour order write |
| `buildGetWiringTypeHexCommand` | 7424796 | wiring read |
| `buildSymphonyControllerHEXCommand` | 3085551 | symphony controller settings |
| `buildColorfulV2HEXCommand` | 9114891 | `static_colorful_hc3` / colour |
| `buildSmearHEXCommand` | 9844766 | `smear_hc3` |
| `buildPreviewEffectHEXCommand` | 7744349 | preview |
| `buildCompressEffectFrameBodyHEXCommand` | 8109740 | custom animation frames (compressed) |
| `buildSymphonyCustomHEXCommand` | 2350065 | `custom_data_hc3` |
| `buildVerticalPixelsHEXCommand` | 1921606 | layout |
| `buildLedCountCalibrationHEXCommand` | 1783731 | LED count |
| `set_wiring_preview` | 6184527 | wiring preview |
| `smear_hc3_v2` | 5476082 | |
| `symphony_scene_hc3` | 8875609 | |

Other hints:
- Custom animations (`custom_data_hc3`) appear to be frame data compressed with LZ4. The Java plugin `com.zengge.zg_lz4_compress.ZgLz4CompressPlugin` exists, and `buildCompressEffectFrameBodyHEXCommand` is named in Dart. (inferred)
- The `symphony_layout` / `multiple_screen_data` features are screen/matrix layout, not strip configuration.

## 6. What is missing, and the blocker

- All e0-family builder byte layouts for hc3: config read, config write, IC type IDs, `set_wiring_preview`, `custom_data_hc3`, `smear_hc3` and `smear_hc3_v2`, `static_colorful_hc3`, `symphony_scene_hc3` (scene numbers/speed), `multiple_screen_data`, `function_setting`, and their responses.
- I tried to run **blutter** (github.com/worawit/blutter, supports Dart 3.11). It builds a native Dart runtime and decodes `libapp.so` into annotated assembly and an object pool with named functions. The permission classifier denied the action (`Code from External`), because it executes code cloned from GitHub. I killed the background process and did not retry or work around it. The cloned repo is at `.../scratchpad/blutter` and a venv at `.../scratchpad/venv`. Nothing from it ran on the target.
- If you approve it, the next step is `blutter.py` on `libapp.so` and then reading `asm/` for the functions in the section 5 table. That would turn the "MISSING" rows into verified layouts.
- Without a decompiler, the remaining options are to capture the app's own traffic with a packet capture while using the app against a test controller (needs the phone and controller, which is outside this static task), or to try the inferred frames above one at a time.

## 7. Practical next tests (all inferred, send at your own risk)

1. Check firmware version from byte 10 of the `81` reply. If it is below 9, no TM1814 setting is exposed by the app.
2. Power via `switch_led_v3`: envelope(ver 02, inner `e0 01 00 3b 23 00 00 00 00 00 00 00 00 00 00`).
3. Scene via `scene_data_v3`: envelope(ver 02, inner `e0 02 00 <model> <speed> <bright>`).
4. Config read: try `e0` family queries `e0 0a`, `e0 08`, `e0 06` as probes of what the firmware answers. These are verified templates, but none returns pixel config.
5. Do NOT send `reset_v2` (`e0 43 <mac>`) or `firmware_data`.
