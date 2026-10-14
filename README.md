# tm1814-led

**Run your own light shows on a TM1814 / Zengge (Surplife, Magic Home) Wi-Fi LED controller — from a web page, with schedules, and without the phone app.**

Perfect for eave lights, roof lines and permanent holiday lighting that was installed with one of these controllers. Pick animations from a catalog, tweak them, string them into run lists, and tell the lights when to play — "every night from sunset to 11 pm", "Halloween week", "first Saturday of each month".

> **You will need:** the controller on your home Wi-Fi, and any computer that can stay on (a Raspberry Pi is ideal). Nothing in the cloud is used.

---

## What you get

* **A web page** you can open on your phone or computer.
* **A catalog of 53 ready-made effects** — solid colors, all 24 of the controller's built-in animations, and Halloween scenes (embers, ghost, rolling wave, three kinds of lightning) plus rainbow, candy cane, twinkle, sunset gradient and more.
* **A full editor for every effect** — change speed, colors, direction, size, anything. Duplicate, rename, delete. A live preview shows what it will look like before you send it to the lights.
* **A frame studio** to draw your own animations frame by frame, with gradients, fades, loops and undo.
* **Run lists** — an ordered playlist of effects with how long each plays. Loop or shuffle them.
* **Schedules** — attach a run list to dates and times, including **sunrise and sunset** (with an offset), and repeat daily, weekly, monthly, "first of the month", "first Saturday", or yearly. Overlapping schedules are settled by priority.
* **A background service** that keeps it all running, starts at boot, and survives power cuts and Wi-Fi hiccups.
* **One-command install** for Linux (Raspberry Pi), macOS and Windows.

---

## Quick start

### 1. Get the code

```bash
git clone https://github.com/jewettg/TM1814_LED_Controller.git
cd TM1814_LED_Controller
```

You need **Python 3.9 or newer** (check with `python3 --version`).

### 2. Install it as a service (recommended)

```bash
# See exactly what will happen first (changes nothing):
python3 -m tm1814_led --install --dry-run

# Then do it. On a Raspberry Pi, use sudo to get a proper system service:
sudo python3 -m tm1814_led --install
```

The installer asks you to choose a password for the web page (or, if you add `--generate-password`, makes one and shows it once). It then prints the address of the web page, for example `http://192.168.1.20:8080/`. Open that address in a browser and sign in.

On macOS or Windows leave out `sudo`; on Windows run it from a Command Prompt or PowerShell. Details for each system are [below](#installing-as-a-service).

### 3. Connect to your controller

1. Open the web page and go to **Settings**.
2. Click **Find controllers**. Your controller appears in the list — click it to fill in its address.
3. Click **Test connection**, then **Save**.

If it is not found, see [Troubleshooting](#troubleshooting).

### 4. Try it

Go to **Effects** (the catalog), find *Halloween - Embers* (or any effect) and press **Play on lights**. Press **Stop** in the top bar to end it.

### Just want to try it without installing?

```bash
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m tm1814_led serve --controller-host 192.168.1.50 --password choose-a-password
```

Open <http://localhost:8080/>. Add `--simulate` to explore the whole app **with no controller at all**.

---

## A tour of the web page

### Dashboard

What is playing right now, with a progress bar; **Play / Stop / Next / Pause**; **power** and **master brightness**; a **quick color** picker; start any run list; the active and upcoming schedules; the controller's status; and the latest log lines.

> **What does Stop do?** It ends what is playing, applies your "when idle" setting (default: lights off) and keeps any schedule that is currently in its time window from starting again until its **next** window. To bring a schedule back right away, play its run list manually.

### Effects (the catalog)

Every effect as a card. Search, or filter by type. On each card: **Play on lights**, **Edit**, **Duplicate**, **Rename**, **Delete**. **New effect** lets you start from any kind.

There are three kinds, and the page labels each one:

| | **Controller-native** | **Fixed** | **Animated** |
| --- | --- | --- | --- |
| What it is | One of the controller's own effects | A still picture: some lights on, the rest off | Motion drawn by this program, frame by frame |
| Where it runs | On the controller itself | Painted once by this program, then the lights just stay that way | In this program, which keeps sending frames |
| If this program stops | Keeps running | **Stays lit** | Freezes on its last frame |
| Motion | Perfectly smooth | None | About 3–5 frames per second |
| Preview in the browser | No (it is the controller's own animation) | Yes | Yes |
| Examples | Solid colors, Horse Racing, Cycle, Twinkling Stars and the other built-in animations | **Night (Bright / Medium / Low / Dim)**: every 3rd / 4th / 5th / 6th pixel lit | Embers, Ghost, Lightning, Rainbow, **Night (Hourly 4-6)**, your own frame animations |

Use native effects for fast, smooth motion, fixed effects for steady night lighting, and animated ones when you want something the controller can't do on its own.

**Night lights.** The catalog comes with four fixed night effects that light every 3rd, 4th, 5th or 6th pixel with the strip's white LED (a bigger number means fewer lights, so a dimmer glow), and **Night (Hourly 4-6)**, an animated one that steps through every 4th, 5th and 6th pixel, changing on the hour and looping (the editor's preview is fast-forwarded and says so). Open any of them to change the color, the white brightness, which pixel starts the pattern, or (for the hourly one) the range and how often it changes.

### The effect editor

Every effect has a form for its settings (sliders, color pickers, drop-downs) and a **live preview** that updates as you change things. Change values, and:

* **Play on lights** — try it right now, even before saving.
* **Save** — keep your changes. **Save as copy** — keep the original and store a new version.
* **Rename** and **Delete** at any time. (An effect that is part of a run list can't be deleted until you remove it from the list.)

**Make your own lightning.** Duplicate *Halloween - Lightning (storm)*, name it, then change the bolt colors, how many bolts per second, how big they are, the background color... The built-in *black sky* and *dark orange* versions are just this effect with different settings.

### The frame studio (your own animations)

Create a new effect of the kind **Custom animation (frames)** to draw one yourself:

* Click and drag on the strip to select a range of lights; fill it with a color, a gradient, or clear it.
* Add as many **frames** as you like. For each: how long it shows, and whether it **cuts** to the next or **fades**.
* Choose **loop**, **ping-pong** (forwards then back) or **play once**.
* Shift or mirror a whole frame, copy frames, reorder them; **undo/redo** as you go.
* **Play** previews it; **Play on lights** shows it for real.

### Run lists

A run list is a named playlist: effects in order, each with its own duration. Reorder by dragging or with the arrows. Tick **Loop** to repeat forever and **Shuffle** to randomize the order each round. Press **Play** to run one at once.

### Schedules

A schedule says *when* a run list plays:

| Setting | What it means |
| --- | --- |
| **Run list** | what to play |
| **Date range** (optional) | the first and last dates it may run, e.g. Oct 24 – Oct 31 |
| **Start / end time** | a clock time, or **sunrise / sunset** with minutes before or after (e.g. *30 minutes before sunset*). If the end is earlier than the start the schedule runs overnight. |
| **Repeat** | once · every N days · chosen weekdays (every N weeks) · a day of the month (or the last day) · the Nth weekday of the month (*first Saturday*, *last Friday*) · every year on a date |
| **Priority** | when two schedules overlap, the higher number wins |
| **Enabled** | switch off without deleting |

The editor shows the **next occurrences** as you type, so you can check it before saving. Ready-made presets cover the common cases ("every night from sunset to 11 pm", "first of every month", "Halloween week").

For sunrise/sunset to be exact, enter your **latitude, longitude and time zone** in Settings (there is a *Use my location* button). Until then the program uses 7:00 am and 7:00 pm and warns you.

### Settings

The controller's address and a connection test; how fast frames are sent; **master brightness**; what to do when nothing is scheduled (**turn off**, **leave as is**, or **show a color**); and your time zone and location.

---

## Everyday recipes

**Lights on every night until 11 pm, running my favorites**
1. Make a run list from a few effects (*Run lists → New run list*).
2. *Schedules → New*: pick that run list, start **sunset**, end **23:00**, repeat **daily**.

**A special show for Halloween week that overrides the normal one**
1. Use the **Halloween show** run list that comes with the program (or make your own).
2. New schedule: start **sunset**, end **23:30**, date range **Oct 24 – Oct 31**, repeat **daily**, **priority 10** (higher than your everyday schedule).

**Weekend-only party colors**
Schedule → repeat **weekly**, tick **Fri** and **Sat**.

**On the first of every month for a reminder glow**
Schedule → repeat **monthly on a day**, day **1**.

**Lights off but keep the schedules**
Press **Stop**. To keep them off all night, nothing more is needed; the schedule resumes at its next window.

---

## Installing as a service

`python3 -m tm1814_led --install` (or `tm1814-led --install` if you have installed the package) sets everything up in its own folder with its own Python environment, starts it, and makes it start again at boot.

Always run it once with **`--dry-run`** first to see every file it will write and command it will run.

| Option | Meaning |
| --- | --- |
| `--dry-run` | show what would happen; change nothing |
| `--system` / `--user` | Linux only: system-wide service (needs `sudo`) or personal service. Default: system if you are root, else personal. |
| `--port 8080` | web page port |
| `--host 0.0.0.0` | which network address to listen on (`127.0.0.1` = this computer only) |
| `--controller-host IP` | pre-fill the controller's address |
| `--password PW` / `--generate-password` | set the web page password. A password is **always** required; without these options the installer asks you to choose one (and keeps the existing one on a re-install). |

| Your computer | What you get |
| --- | --- |
| **Raspberry Pi / Linux** (with `sudo`) | A system service named `tm1814-led` run by its own unprivileged user. Starts at boot. Manage it with `sudo systemctl status tm1814-led`, `journalctl -u tm1814-led -f`. |
| **Linux** (without `sudo`) | A personal systemd service. It also tries to turn on "lingering" so it keeps running when you log out. |
| **macOS** | A LaunchAgent: starts when you log in and restarts if it stops. Logs are in `~/Library/Logs/tm1814-led/`. |
| **Windows** | A Scheduled Task that starts when you log in (or at startup if you run the installer as administrator). You may need to allow it through Windows Firewall to reach it from other devices. |

Check on it: `python3 -m tm1814_led --service-status`.
Remove it: `python3 -m tm1814_led --uninstall` (keeps your data) or add `--purge` to delete everything.

Your catalog, run lists, schedules and settings live in a single database file; back it up by copying it (see the data folder printed during install).

### Updating

```bash
cd TM1814_LED_Controller
git pull
python3 -m tm1814_led --install       # re-running it upgrades in place
```

Your data is kept.

---

## Security

* **A password is always required.** The web page shows a sign-in form; nothing — the lights, the catalog, the settings, the command line — works without it. The program refuses to start without a password, and the installer always sets one.
* Signing in keeps you signed in on that browser for 30 days (or until you press **Log out**). Eight wrong passwords within five minutes from one device are followed by a short lockout. Changing the password signs every browser out.
* The controller itself has **no password**: anyone on your Wi-Fi can control it directly with the vendor's app. Keep your Wi-Fi trustworthy.
* The page is served over plain HTTP, so the password can be read by someone watching your network. That is fine on a home network you trust, but **do not expose this to the internet**; for remote access use a VPN (for example WireGuard or Tailscale).
* The program never uses the vendor's cloud and never changes the controller's stored configuration (pixel count, IC type, wiring) — see the technical notes.

---

## FAQ

**Does "off" really turn the lights off?**
It makes every pixel black. The power supply and the pixels' chips stay powered, so there is a small standby draw. Only switching the supply's AC input removes it completely.

**Why aren't my custom animations as smooth as the built-in ones?**
Built-in animations run *inside* the controller. Custom and animated ones are sent from here, and the controller accepts only about 30 small commands per second (sending faster makes it stall). A busy frame with many colors needs many commands, so rainbows and gradients are limited to about 2–3 frames per second and are slightly banded. Simple scenes run at around 5.

**Both outputs show the same thing — can I set them separately?**
Not with what is known about this controller; both outputs receive identical data.

**Does it work with other controllers?**
It is built for and tested on the Zengge "symphony" model `0xB6` (Surplife app, TM1814 pixels). Related models share parts of the protocol but are untested.

**Can I use the phone app too?**
Yes, but not while an effect is *streaming* from this program, because both talk to the controller at once. Press **Stop** here first.

**What happens if the power fails?**
The service restarts at boot and the schedules pick up again. A controller that restarted is detected and set up again within 30 seconds.

---

## Troubleshooting

| Problem | Try this |
| --- | --- |
| *Find controllers* finds nothing | Be sure the computer and controller are on the same network (not a "guest" Wi-Fi). Some routers block broadcast traffic — enter the controller's IP address by hand (find it in your router, or in the vendor app under the device's *More device information*). |
| "controller: connect failed" | Wrong address, or the controller is off or busy. Try again in 10 seconds. Only one program should be talking to it. |
| Lights do something odd after changing settings | Press **Stop**, wait a few seconds and play again. |
| I forgot the password | Run the installer again with a new one: `python3 -m tm1814_led --install --generate-password` (or `--password YOUR_NEW_PASSWORD`). Your data is kept. |
| Sunset times look wrong | Set your **latitude, longitude and time zone** in Settings. |
| Something is not working and I want details | *Logs* in the web page, or `journalctl -u tm1814-led -f` on Linux. |

---

## Command line

The web page does everything, but the command line is handy for quick checks and scripts. **Every command asks for the web page's password once** (typed without showing) and then remembers the sign-in for that terminal window. Closing the terminal, `tm1814-led logout`, or 12 hours ends it. Only a signed-in token is remembered, never the password.

```bash
tm1814-led                      # list all commands        (or: python3 -m tm1814_led)
tm1814-led status               # what's playing, controller state, next scheduled runs
```

| Command | What it does |
| --- | --- |
| **Talk to the service** | |
| `status` | what is playing, controller connection, active and upcoming schedules |
| `effects [text]` | list the catalog (`--native` / `--fixed` / `--animated` to filter) |
| `runlists`, `schedules` | list run lists, and schedules with their next start |
| `play "name"` | play an effect or run list by name, part of a name, or id (`--effect` / `--runlist` to be specific, `--for 120` to stop an effect after two minutes) |
| `stop`, `next`, `pause`, `resume` | control playback (`stop` turns the lights off unless you chose another "when idle" action) |
| `brightness 60` | master brightness |
| `logs [-n 50] [-f]` | recent log lines, or follow them |
| **Talk to the controller directly** | |
| `discover` | find controllers on your network |
| `state` | power, what it is showing, LED count, firmware |
| `on`, `off`, `color orange` | power, or a solid color (`#rrggbb`, `255,90,0`, or a name like `warm`, `purple`; `--white 80` for the white LED) |
| `test halves` | light a **walk-around pattern** to learn how your strip is laid out: `halves`, `ruler`, `zones`, `blocks`, `colororder`, `gradient`, `chase`, `fillto 100`, `single 0`. Ctrl-C ends it and turns the lights off. `tm1814-led test` lists them. |
| **Session and service** | |
| `login`, `logout` | sign in now / forget this terminal's sign-in |
| `install`, `uninstall`, `service` | the same as `--install`, `--uninstall`, `--service-status` |
| `serve` | run the web page in the foreground (what the service does for you) |

Add `--json` to any listing for machine-readable output and `--no-color` for plain text. For scripts that cannot type a password, set the `TM1814_PASSWORD` environment variable. The direct commands (`on`, `off`, `color`, `test`...) talk straight to the controller, so run `tm1814-led stop` first if the service is playing something.

---

## For developers

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                          # 130+ tests, no hardware needed
python -m tm1814_led serve --simulate --port 8099
```

Everything technical — the controller's wire protocol, the streaming engine, effect and API reference, how to add your own effect kind — is in **[TECHNICAL_README.md](TECHNICAL_README.md)**.

## License

GPL-3.0 — see [LICENSE](LICENSE). Not affiliated with Zengge, Surplife, Magic Home or the TM1814 manufacturer.
