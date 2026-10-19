"""Settings: defaults, load/save to %APPDATA%\\Timbrebind (or a data folder
beside the exe in portable mode), validation."""

# Timbrebind - turn any instrument into a controller.
# Copyright (C) 2026 Christoph Glennon
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE.

import json
import logging
import os
import sys
from pathlib import Path

APP_NAME = "Timbrebind"
APP_ID = "Timbrebind"
VERSION = "1.0.0"
AUTHOR = "Christoph Glennon"
LICENSE_NAME = "GNU GPL v3 or later"

# Portable mode: a file named "portable" next to the exe keeps all settings
# and logs in a "data" folder beside it instead of %APPDATA%.
APP_DIR = Path(sys.executable if getattr(sys, "frozen", False)
               else sys.argv[0] or __file__).resolve().parent
PORTABLE = (APP_DIR / "portable").exists()
DATA_DIR = (APP_DIR / "data" if PORTABLE
            else Path(os.environ.get("APPDATA", Path.home())) / APP_ID)
SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = DATA_DIR / "timbrebind.log"

BUTTONS = ("left", "right", "middle")

DEFAULTS = {
    # Input
    "device_name": "AudioBox",     # substring match against input device names
    "channel": 1,                  # 1-based input number
    "block_ms": 5,                 # analysis block length
    "smoothing_ms": 15,            # median filter: kills single-block pops/spikes
    "debounce_ms": 20,             # new state must persist this long
    "lockout_ms": 40,              # ignore further changes this long after one
    "pedal_type": "NO",            # NO: pressing closes the contact; NC: opens it
    # Calibration (None until calibrated). The shorted (closed) jack is the
    # quiet state, the open jack picks up hum and is the loud state.
    "open_db": None,
    "closed_db": None,
    "threshold_db": None,
    "hysteresis_db": None,
    # Clicking
    "enabled": True,
    "button": "left",
    "failsafe_corner": True,       # cursor in top-left corner pauses clicking
    # Timing
    "repeat_delay_s": 1.0,         # hold this long before auto-repeat starts
    "repeat_min_s": 0.64,          # repeat interval bounds (down-to-down)
    "repeat_max_s": 0.83,
    "repeat_drift_ms": 8.0,        # slow tempo wander between repeats
    "hold_median_ms": 95.0,        # button down->up time per click
    "hold_spread": 0.22,           # log-normal sigma
    "hold_min_ms": 55.0,
    "hold_max_ms": 180.0,
}

AUDIO_KEYS = ("device_name", "channel", "block_ms", "smoothing_ms", "debounce_ms", "lockout_ms")

log = logging.getLogger(__name__)


def load():
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Could not read %s; using defaults", SETTINGS_FILE)
    return cfg


def save(cfg):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def is_calibrated(cfg):
    return cfg["threshold_db"] is not None and cfg["hysteresis_db"] is not None


def validate(c):
    errs = []
    if not 1 <= c["channel"] <= 8:
        errs.append("Input number must be 1-8.")
    if not 2 <= c["block_ms"] <= 50:
        errs.append("Block length must be 2-50 ms.")
    if not 0 <= c["smoothing_ms"] <= 200:
        errs.append("Spike filter must be 0-200 ms.")
    if not 0 <= c["debounce_ms"] <= 500:
        errs.append("Debounce must be 0-500 ms.")
    if not 0 <= c["lockout_ms"] <= 1000:
        errs.append("Lockout must be 0-1000 ms.")
    if c["pedal_type"] not in ("NO", "NC"):
        errs.append("Pedal type must be NO or NC.")
    if c["button"] not in BUTTONS:
        errs.append("Mouse button must be left, right or middle.")
    if c["repeat_delay_s"] < 0.05:
        errs.append("Repeat delay must be at least 0.05 s.")
    if not 0.05 <= c["repeat_min_s"] < c["repeat_max_s"]:
        errs.append("Repeat interval: min must be >= 0.05 s and less than max.")
    if not 0 <= c["repeat_drift_ms"] <= 200:
        errs.append("Rhythm drift must be 0-200 ms.")
    if not 0 < c["hold_min_ms"] <= c["hold_median_ms"] <= c["hold_max_ms"]:
        errs.append("Click hold: need 0 < min <= median <= max.")
    if not 0 <= c["hold_spread"] <= 1.5:
        errs.append("Click hold spread must be 0-1.5.")
    if c["hold_max_ms"] >= c["repeat_min_s"] * 1000 - 20:
        errs.append("Longest click hold must be at least 20 ms shorter than the "
                    "shortest repeat interval.")
    return errs
