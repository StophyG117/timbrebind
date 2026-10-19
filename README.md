# Timbrebind

**Turn any instrument into a controller.**

Timbrebind listens to an audio interface input, learns the acoustic signature
of the sounds you make, and binds each one to an action on your computer: a
key, a mouse click, a MIDI message. The long-term goal is that a guitarist,
drummer or keyboard player can drive a computer with their instrument: a chord
as a hotkey, a rimshot as a click, a pedal as a button.

Version 1.0 ships the first and simplest case: **a keyboard sustain pedal as a
mouse button**. It's a system-tray app for Windows 10/11. It was built for a
PreSonus AudioBox USB 96, but any audio input should work.

## The idea

Timbrebind treats every input as a *recognition* problem, not a transcription
problem. You don't need to know which notes are in a chord, only which of
*your* trained sounds just happened. That makes the approach the same at every
scale:

1. **Detect:** measure features of the incoming audio. Today that's loudness;
   later it can be pitch, spectrum and onsets.
2. **Calibrate:** capture examples of each sound you want to use. Today that's
   two states, pedal up and pedal down.
3. **Bind:** map each recognised sound to an action, with natural timing.
   Today that's a left click with human-like auto-repeat.

The sustain pedal is the one-feature, two-class version of that loop.

## Preset: sustain pedal to mouse click

### How it detects the pedal

A sustain pedal is just a switch on a 1/4" jack and makes no sound. What
changes is the input's noise floor: an open contact picks up hum and noise,
a shorted one is close to silent. The two levels are far enough apart to
detect reliably, even with the input gain turned all the way down.
Timbrebind measures the level every 5 ms and calls the pedal up or down
using a calibrated threshold.

To stop noise and half-presses from clicking, detection has three filters on
top of the threshold's hysteresis band:

| Filter | Default | What it rejects |
| --- | --- | --- |
| Median spike filter | 15 ms | the pop when the contact makes or breaks, glitches |
| Debounce (must persist) | 20 ms | half-presses hovering near the threshold |
| Lockout after a change | 40 ms | contact chatter and bounce |

### What the pedal does

- **Press:** one click, right away.
- **Hold:** the next click comes at 1.0 s, then repeats every 0.64-0.83 s. The
  gaps follow a soft curve that leans slightly long, with a slow tempo drift,
  so it doesn't sound like a metronome.
- **Each click** holds the button down for a natural, log-normally distributed
  time: about 95 ms median, between 55 and 180 ms.
- **Release:** repeating stops.

Every one of these values can be changed in Settings, and the Timing tab shows
live charts of the click timing. You can also choose the right or middle mouse
button instead.

## Install

Download the latest version from
[Releases](https://github.com/StophyG117/timbrebind/releases/latest):

- **`Timbrebind-<version>-setup.exe`**: a per-user install that doesn't need
  admin. It adds a Start menu entry and can optionally start the app when you
  sign in. Uninstall it from Windows Settings > Apps.
- **`Timbrebind-<version>-portable-win64.zip`**: unzip it anywhere, including
  a USB stick, and run `Timbrebind.exe`. Settings stay in a `data` folder next
  to the exe.

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
  administrator. To click in those, run Timbrebind as administrator too.
- Timbrebind is a tray app rather than a Windows service on purpose: services
  run in an isolated session and can't click on your desktop.

## Where it's headed

Ideas, not promises:

- **More detectors:** drum hits with velocity, single notes, trained chord
  shapes and playing techniques, plus a "none of these" class so stray noise
  doesn't fire anything.
- **More actions:** keyboard keys and shortcuts, macros, MIDI out and OSC.
- **MIDI in:** use real MIDI controllers and electronic kits with the same
  bindings.
- **Profiles:** one set of sounds and bindings per instrument or game.

Ideas and pull requests are welcome.

## Run from source

```powershell
py -m pip install -r requirements.txt
pyw Timbrebind.pyw
```

Settings and the log are stored in `%APPDATA%\Timbrebind\`.

## Build the release

```powershell
py -m pip install -r requirements.txt pyinstaller
winget install JRSoftware.InnoSetup
.\build.ps1
```

This writes the portable zip and the installer to `release\`. The version
number comes from `timbrebind/config.py`.

## License

Copyright (C) 2026 Christoph Glennon.

Timbrebind is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It is distributed WITHOUT ANY WARRANTY. See [LICENSE](LICENSE).
