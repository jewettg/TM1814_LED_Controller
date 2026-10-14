# Research notes

How the controller's protocol was worked out, and what is still unknown. The current specification lives in
[../TECHNICAL_README.md](../TECHNICAL_README.md); these notes are the evidence and the history behind it.

| File | What it is |
| --- | --- |
| [FINDINGS.md](FINDINGS.md) | The development log: every probe sent to a real controller, what it answered, what was verified by watching the lights, and the walk-around results that showed both outputs are mirrored. |
| [RESEARCH_NOTES.md](RESEARCH_NOTES.md) | What the open-source community (mainly the `flux_led` project) has published about this controller family, with links and confidence labels. |
| [APP_PROTOCOL_ANALYSIS.md](APP_PROTOCOL_ANALYSIS.md) | What the vendor app's own data files say about this controller, and which commands could **not** be found (so nobody repeats that search). |

**Open questions** (the most useful things left to learn): how to read or change the IC type, color order and pixel
count (the layouts exist only in the app's compiled code); whether the controller can store animations or timers
itself (it has six empty timer slots); how a controller "pixel" maps to physical LEDs on a 24 V strip.
To help work out the layout of *your* lights, see "Working out how your lights are laid out" in the main
[README](../README.md).

Confidence labels: **verified** (seen on real hardware), **upstream** (published by others, cross-checked),
**inferred** (a reasoned guess). Addresses, IDs and local paths in these notes are examples. The vendor app and
third-party code that were studied are not part of this repository.
