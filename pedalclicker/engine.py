"""Audio capture, pedal detection and mouse clicking.

The pedal is a plain switch across the 1/4" jack. It makes no sound, but an
open input picks up hum and noise while a shorted input is near-silent, so the
noise floor on the input flips between two very distinct levels, even with the
gain turned all the way down. We measure block RMS in dBFS and track the
contact state with a threshold, hysteresis and a short debounce.
"""

import ctypes
import logging
import math
import threading
import time
from collections import deque
from ctypes import wintypes

import numpy as np
import sounddevice as sd

from . import config
from .humanize import RepeatInterval, click_hold_s

log = logging.getLogger(__name__)
HISTORY_S = 10

# --- mouse output (SendInput) ------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
INPUT_MOUSE = 0
BUTTON_FLAGS = {  # (down, up)
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        # KEYBDINPUT/HARDWAREINPUT are never larger than MOUSEINPUT
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send_mouse(flags):
    inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def cursor_in_failsafe_corner():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x <= 2 and pt.y <= 2


# --- audio helpers -----------------------------------------------------------

API_RANK = {"Windows WASAPI": 0, "Windows DirectSound": 1, "MME": 2}


def block_db(x):
    x = x.astype(np.float64)
    x -= x.mean()
    return 20 * math.log10(math.sqrt(float(np.dot(x, x)) / len(x)) + 1e-12)


def list_input_devices():
    names = []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and d["name"] not in names:
            names.append(d["name"])
    return names


def find_device(match, channel):
    """Best host API for the first device whose name contains `match`."""
    candidates = [(i, d) for i, d in enumerate(sd.query_devices())
                  if match.lower() in d["name"].lower()
                  and d["max_input_channels"] >= channel]
    if not candidates:
        return None
    idx, dev = min(candidates, key=lambda c: API_RANK.get(
        sd.query_hostapis(c[1]["hostapi"])["name"], 9))
    return idx, dev, sd.query_hostapis(dev["hostapi"])["name"]


def analyse_capture(up, down):
    """Turn level samples captured with the pedal up and down into calibration
    values. Returns (values, message); values is None if the states overlap."""
    up, down = np.asarray(up), np.asarray(down)
    up_med, down_med = float(np.median(up)), float(np.median(down))
    gap = abs(up_med - down_med)
    if up_med > down_med:
        margin = np.percentile(up, 2) - np.percentile(down, 98)
    else:
        margin = np.percentile(down, 2) - np.percentile(up, 98)
    if gap < 4 or margin <= 0:
        return None, (f"Up and down are not distinguishable (gap {gap:.1f} dB, "
                      f"margin {margin:.1f} dB). Check the pedal is on the selected "
                      "input and capture again.")
    values = {
        "open_db": max(up_med, down_med),
        "closed_db": min(up_med, down_med),
        "threshold_db": round((up_med + down_med) / 2, 1),
        "hysteresis_db": round(max(0.5, min(gap * 0.15, margin / 2)), 1),
        # Pressing closed the contact (went quiet) -> normally open pedal.
        "pedal_type": "NO" if down_med < up_med else "NC",
    }
    return values, (f"Separation {gap:.1f} dB (worst-case margin {margin:.1f} dB). "
                    f"Detected a normally {'open' if values['pedal_type'] == 'NO' else 'closed'}"
                    f" ({values['pedal_type']}) pedal.")


class Timing:
    """Detector timing in blocks, derived from the ms settings."""

    def __init__(self, cfg):
        b = cfg["block_ms"]
        self.smooth = max(1, round(cfg["smoothing_ms"] / b))
        self.debounce = max(1, round(cfg["debounce_ms"] / b))
        self.lockout = round(cfg["lockout_ms"] / b)


class Detector:
    """Tracks contact state (True = closed/quiet). Three layers keep partial
    presses and transients from registering:
      1. a median filter over `smooth` blocks drops single-block spikes (the pop
         when the contact makes/breaks, USB glitches);
      2. hysteresis: the level must cross threshold +/- hysteresis, and any
         sample back inside the band restarts the count;
      3. debounce: the new state must persist `debounce` blocks, then a
         `lockout` stops a chattering contact from flipping straight back.
    """

    NONE, CHANGED, INITIAL = 0, 1, 2

    def __init__(self):
        self.reset()

    def reset(self):
        self.closed = None
        self.pending = 0
        self.hold_off = 0
        self.recent = deque()

    def feed(self, db, thr, hyst, timing):
        self.recent.append(db)
        while len(self.recent) > timing.smooth:
            self.recent.popleft()
        db = sorted(self.recent)[len(self.recent) // 2]
        if self.closed is None:
            if len(self.recent) < timing.smooth:
                return self.NONE
            self.closed = db < thr
            return self.INITIAL
        if self.hold_off > 0:
            self.hold_off -= 1
            self.pending = 0
            return self.NONE
        if not self.closed and db < thr - hyst:
            want = True
        elif self.closed and db > thr + hyst:
            want = False
        else:
            self.pending = 0
            return self.NONE
        self.pending += 1
        if self.pending < timing.debounce:
            return self.NONE
        self.closed, self.pending, self.hold_off = want, 0, timing.lockout
        return self.CHANGED


# --- clicker -----------------------------------------------------------------

class Clicker(threading.Thread):
    """Turns pedal down/up events into clicks. Press -> one click right away;
    still held after the repeat delay -> repeat at human intervals."""

    def __init__(self, engine):
        super().__init__(name="clicker", daemon=True)
        self.engine = engine
        self.cfg = engine.cfg
        self.intervals = RepeatInterval(self.cfg)
        self.pressed = threading.Event()
        self.changed = threading.Event()
        self.stopping = threading.Event()
        self.press_seq = 0       # bumps on every real press, so a quick tap is never lost
        self.handled_seq = 0
        self.press_time = 0.0
        self.button_down = None

    def set_pedal(self, down, silent=False):
        """silent=True updates the state without clicking (start-up, settings
        change, device loss)."""
        if down and not self.pressed.is_set():
            self.pressed.set()
            if not silent:
                self.press_time = time.perf_counter()
                self.press_seq += 1
        elif not down:
            self.pressed.clear()
        self.changed.set()

    def _held(self):
        return (self.pressed.is_set() and self.press_seq == self.handled_seq
                and not self.stopping.is_set())

    def _wait_until(self, deadline):
        """Sleep until deadline; False if the pedal was released meanwhile."""
        while self._held():
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return True
            self.changed.wait(min(remaining, 0.05))
            self.changed.clear()
        return False

    def _click(self):
        if not self.engine.clicks_allowed():
            return
        if self.cfg["failsafe_corner"] and cursor_in_failsafe_corner():
            log.info("click skipped: cursor in failsafe corner")
            return
        hold = click_hold_s(self.cfg)
        down, up = BUTTON_FLAGS[self.cfg["button"]]
        send_mouse(down)
        self.button_down = up
        try:
            time.sleep(hold)
        finally:
            send_mouse(up)
            self.button_down = None
        self.engine.click_count += 1

    def release_button(self):
        if self.button_down is not None:
            send_mouse(self.button_down)
            self.button_down = None

    def run(self):
        while not self.stopping.is_set():
            self.changed.wait(0.1)
            self.changed.clear()
            if self.press_seq == self.handled_seq:
                continue
            self.handled_seq = self.press_seq
            t0 = self.press_time
            self._click()
            next_t = t0 + self.cfg["repeat_delay_s"]
            if not self._wait_until(next_t):
                continue
            self.intervals.reset()
            while self._held():
                self._click()
                next_t += self.intervals.next()
                if not self._wait_until(next_t):
                    break

    def shutdown(self):
        self.stopping.set()
        self.pressed.clear()
        self.changed.set()
        self.join(timeout=1)
        self.release_button()


# --- engine ------------------------------------------------------------------

class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.levels = deque(maxlen=2000)     # (perf_counter time, dB)
        self.detector = Detector()
        self.clicker = Clicker(self)
        self.ok = False
        self.status = "Starting"
        self.device_label = ""
        self.suppress = 0                    # >0 while calibration window holds it
        self.click_count = 0
        self._stream = None
        self._ch = 0
        self._timing = Timing(cfg)
        self._restart = threading.Event()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._supervise, name="audio", daemon=True)

    # state -------------------------------------------------------------
    @property
    def calibrated(self):
        return config.is_calibrated(self.cfg)

    def pedal_down(self):
        closed = self.detector.closed
        if closed is None:
            return False
        return closed if self.cfg["pedal_type"] == "NO" else not closed

    def clicks_allowed(self):
        return self.cfg["enabled"] and self.suppress == 0 and self.calibrated and self.ok

    def levels_since(self, t0):
        with self.lock:
            return [p for p in self.levels if p[0] >= t0]

    # control -----------------------------------------------------------
    def start(self):
        self.clicker.start()
        self._thread.start()

    def stop(self):
        self._stopping.set()
        self._thread.join(timeout=2)
        self._close()
        self.clicker.shutdown()

    def restart_audio(self):
        self._restart.set()

    def resync(self):
        """Re-derive pedal state silently after calibration / NO-NC changes,
        so a settings change never fires a click."""
        with self.lock:
            self.detector.reset()
            self.clicker.set_pedal(False, silent=True)

    # audio -------------------------------------------------------------
    def _callback(self, indata, frames, t, status):
        try:
            db = block_db(indata[:, self._ch])
            with self.lock:
                self.levels.append((time.perf_counter(), db))
                if not self.calibrated:
                    return
                c = self.cfg
                r = self.detector.feed(db, c["threshold_db"], c["hysteresis_db"],
                                       self._timing)
                if r != Detector.NONE:
                    self.clicker.set_pedal(self.pedal_down(), silent=(r == Detector.INITIAL))
        except Exception:
            log.exception("audio callback error")

    def _open(self):
        c = self.cfg
        found = find_device(c["device_name"], c["channel"])
        if not found:
            raise RuntimeError(f"no input device matching '{c['device_name']}' "
                               f"with {c['channel']}+ inputs")
        idx, dev, api = found
        rate = int(dev["default_samplerate"])
        frames = max(16, int(rate * c["block_ms"] / 1000))
        self._ch = c["channel"] - 1
        self._timing = Timing(c)
        with self.lock:
            self.levels = deque(maxlen=int(HISTORY_S * 1000 / c["block_ms"]))
            self.detector.reset()
        stream = sd.InputStream(device=idx, channels=c["channel"], samplerate=rate,
                                blocksize=frames, dtype="float32", latency="low",
                                callback=self._callback)
        stream.start()
        self._stream = stream
        self.device_label = f"{dev['name']} via {api}, {rate} Hz, input {c['channel']}"
        self.ok = True
        self.status = "Listening"
        log.info("opened %s", self.device_label)

    def _close(self):
        self.ok = False
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                log.exception("closing stream")
            self._stream = None
        with self.lock:
            self.detector.reset()
            self.clicker.set_pedal(False, silent=True)

    def _supervise(self):
        """Keep the stream open; reconnect after USB unplug or settings change."""
        # WASAPI streams fail to start on this thread unless PortAudio was
        # initialised on it (seen with the AudioBox), so re-init here first.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            log.exception("PortAudio re-init")
        while not self._stopping.is_set():
            if self._restart.is_set() or self._stream is None or not self._stream.active:
                self._restart.clear()
                self._close()
                try:
                    self._open()
                except Exception as e:
                    self.status = f"Audio device unavailable: {e}"
                    log.warning(self.status)
                    try:  # refresh PortAudio's device list for hot-plug
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        log.exception("PortAudio re-init")
                    self._stopping.wait(3)
                    continue
            self._stopping.wait(0.5)
