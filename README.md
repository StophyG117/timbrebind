# Pedal Clicker

Turn a keyboard sustain pedal into a mouse button on Windows. Plug the pedal
into an audio interface input, and Pedal Clicker clicks for you: one click per
press, and human-like auto-repeat while you hold the pedal down.

It's a system-tray app for Windows 10/11. It was built for a PreSonus AudioBox
USB 96, but any audio input should work.

## How it works

A sustain pedal is just a switch on a 1/4" jack and makes no sound. What
changes is the input's noise floor: an open contact picks up hum and noise,
a shorted one is close to silent. The two levels are far enough apart to
detect reliably, even with the input gain turned all the way down.
Pedal Clicker measures the level every 5 ms and calls the pedal up or down
using a calibrated threshold.

To stop noise and half-presses from clicking, detection has three filters on
top of the threshold's hysteresis band:

| Filter | Default | What it rejects |
| --- | --- | --- |
| Median spike filter | 15 ms | the pop when the contact makes or breaks, glitches |
| Debounce (must persist) | 20 ms | half-presses hovering near the threshold |
| Lockout after a change | 40 ms | contact chatter and bounce |

## What the pedal does

- **Press:** one click, right away.
- **Hold:** the next click comes at 1.0 s, then repeats every 0.64-0.83 s. The
  gaps follow a soft curve that leans slightly long, with a slow tempo drift,
  so it doesn't sound like a metronome.
- **Each click** holds the button down for a natural, log-normally distributed
  time: about 95 ms median, between 55 and 180 ms.
- **Release:** repeating stops.

Every one of these values can be changed in Settings, and the Timing tab shows
live charts of the click timing.

## Install

Download the latest release:

- **`PedalClicker-<version>-setup.exe`**: a per-user install that doesn't need
  admin. It adds a Start menu entry and can optionally start the app when you
  sign in. Uninstall it from Windows Settings > Apps.
- **`PedalClicker-<version>-portable-win64.zip`**: unzip it anywhere, including
  a USB stick, and run `PedalClicker.exe`. Settings stay in a `data` folder
  next to the exe.

Windows SmartScreen may warn you the first time, because the builds aren't
code-signed. Click **More info > Run anyway**.

## First-time setup

1. Plug the pedal into input 1. You can choose a different device or input in
   Settings > Input.
2. Open **Monitor & Calibration** from the tray menu. It opens automatically on
   the first run.
3. With the pedal released, click **Capture pedal UP**. Then hold the pedal down
   and click **Capture pedal DOWN**.
4. Check that the PEDAL UP / PEDAL DOWN indicator follows your foot, then click
   **Apply & Save**.

Calibration also works out whether your pedal is **NO** (normally open) or
**NC** (normally closed). If presses and releases come out reversed, switch it
in the tray menu under **Pedal type**.

## Tray menu

Status, Clicking enabled, Pedal type (NO/NC), Monitor & Calibration, Settings,
Start with Windows, Help, About, Quit.

The icon color shows the current state: blue is ready, green is pedal down,
grey is paused, and orange means the device is missing or the app isn't
calibrated yet.

## Safety

- To pause, uncheck **Clicking enabled**, or move the cursor into the top-left
  corner of the screen.
- No clicks are sent while Monitor & Calibration is open.
- If the interface is unplugged, clicking stops, and the app reconnects by
  itself when it's plugged back in.
- Windows won't let a normal app click inside programs running as
  administrator. To click in those, run Pedal Clicker as administrator too.
- Pedal Clicker is a tray app rather than a Windows service on purpose:
  services run in an isolated session and can't click on your desktop.

## Run from source

```powershell
py -m pip install -r requirements.txt
pyw PedalClicker.pyw
```

Settings and the log are stored in `%APPDATA%\PedalClicker\`.

## Build the release

```powershell
py -m pip install -r requirements.txt pyinstaller
winget install JRSoftware.InnoSetup
.\build.ps1
```

This writes the portable zip and the installer to `release\`. The version
number comes from `pedalclicker/config.py`.

## License

Copyright (C) 2026 Christoph Glennon.

Pedal Clicker is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. It is distributed WITHOUT ANY WARRANTY. See [LICENSE](LICENSE).
