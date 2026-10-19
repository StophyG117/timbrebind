"""System-tray application: tray icon + menu, settings, monitor/calibration,
help and about windows."""

# Pedal Clicker - Copyright (C) 2026 Christoph Glennon
# Licensed under the GNU GPL v3 or later; see LICENSE.

import ctypes
import logging
import os
import logging.handlers
import queue
import sys
import threading
import time
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import messagebox, ttk

import pystray
from PIL import Image, ImageDraw, ImageTk

from . import config
from .config import APP_ID, APP_NAME, AUTHOR, LICENSE_NAME, VERSION
from .engine import Detector, Engine, Timing, analyse_capture, list_input_devices
from .humanize import RepeatInterval, click_hold_s

log = logging.getLogger(__name__)

COLORS = {"problem": "#d9822b", "paused": "#7a7a7a", "idle": "#3b6fd6", "down": "#2e9e5b"}


# --- icon --------------------------------------------------------------------

def make_icon(state, size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.ellipse((1, 1, size - 2, size - 2), fill=COLORS[state])
    plate = [(14, 41), (50, 41), (50, 45), (14, 45)] if state == "down" else \
            [(14, 27), (50, 39), (50, 45), (14, 33)]
    d.polygon([(x * s, y * s) for x, y in plate], fill="white")
    d.rectangle((11 * s, 46 * s, 53 * s, 51 * s), fill="white")
    return img


# --- run at login ------------------------------------------------------------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    script = Path(sys.argv[0]).resolve()
    return f'"{pyw if pyw.exists() else exe}" "{script}"'


def get_startup():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_ID)
            return True
    except OSError:
        return False


def set_startup(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, APP_ID)
            except FileNotFoundError:
                pass


# --- widgets -----------------------------------------------------------------

class LevelGraph(tk.Canvas):
    """Scrolling plot of input level with threshold / hysteresis / reference lines."""

    def __init__(self, master, scale, seconds=6.0):
        super().__init__(master, width=int(660 * scale), height=int(240 * scale),
                         bg="#14161a", highlightthickness=0)
        self.seconds = seconds
        self.lo, self.hi = -100.0, -20.0

    def draw(self, pts, open_db, closed_db, thr, hyst):
        self.delete("all")
        w = max(self.winfo_width(), self.winfo_reqwidth())
        h = max(self.winfo_height(), self.winfo_reqheight())
        left, pad = 46, 10
        now = time.perf_counter()
        vals = [d for _, d in pts] + [v for v in (open_db, closed_db, thr) if v is not None]
        if vals:
            lo, hi = min(vals) - 6, max(vals) + 6
            if hi - lo < 20:
                mid = (hi + lo) / 2
                lo, hi = mid - 10, mid + 10
            # expand at once, shrink slowly, so the scale doesn't twitch
            self.lo = lo if lo < self.lo else self.lo + (lo - self.lo) * 0.03
            self.hi = hi if hi > self.hi else self.hi + (hi - self.hi) * 0.03

        def y(db):
            return pad + (self.hi - db) / (self.hi - self.lo) * (h - 2 * pad)

        def x(t):
            return left + (t - (now - self.seconds)) / self.seconds * (w - left - pad)

        step = 10 if self.hi - self.lo > 30 else 5
        g = int(self.lo // step + 1) * step
        while g < self.hi:
            self.create_line(left, y(g), w - pad, y(g), fill="#262a31")
            self.create_text(left - 6, y(g), text=f"{g}", fill="#7d8590", anchor="e",
                             font=("Segoe UI", 8))
            g += step
        self.create_text(left - 6, pad - 2, text="dBFS", fill="#7d8590", anchor="ne",
                         font=("Segoe UI", 7))
        if thr is not None and hyst is not None:
            self.create_rectangle(left, y(thr + hyst), w - pad, y(thr - hyst),
                                  fill="#2b2a17", outline="")
            self.create_line(left, y(thr), w - pad, y(thr), fill="#e3c34a")
            self.create_text(w - pad - 4, y(thr) - 2, text=f"threshold {thr:.1f}",
                             fill="#e3c34a", anchor="se", font=("Segoe UI", 8))
        for v, name, col in ((open_db, "contact open", "#e0884a"),
                             (closed_db, "contact closed", "#56b6e8")):
            if v is not None:
                self.create_line(left, y(v), w - pad, y(v), fill=col, dash=(4, 3))
                self.create_text(left + 4, y(v) - 2, text=f"{name} {v:.1f}", fill=col,
                                 anchor="sw", font=("Segoe UI", 8))
        if len(pts) > 1:
            k = max(1, len(pts) // (w - left))
            coords = []
            for t, d in pts[::k]:
                coords += (x(t), y(min(max(d, self.lo), self.hi)))
            self.create_line(*coords, fill="#7ee787", width=1.5)


class Histogram(tk.Canvas):
    def __init__(self, master, scale, title):
        super().__init__(master, width=int(300 * scale), height=int(120 * scale),
                         bg="#14161a", highlightthickness=0)
        self.title = title

    def draw(self, samples, lo, hi, unit_scale, unit):
        self.delete("all")
        w = max(self.winfo_width(), self.winfo_reqwidth())
        h = max(self.winfo_height(), self.winfo_reqheight())
        bins = 30
        counts = [0] * bins
        for v in samples:
            counts[min(bins - 1, max(0, int((v - lo) / (hi - lo) * bins)))] += 1
        top = max(counts) or 1
        bw = (w - 20) / bins
        for i, c in enumerate(counts):
            x0 = 10 + i * bw
            self.create_rectangle(x0, h - 22 - c / top * (h - 46), x0 + bw - 1, h - 22,
                                  fill="#3b6fd6", outline="")
        mean = sum(samples) / len(samples)
        self.create_text(10, 6, anchor="nw", fill="#c9d1d9", font=("Segoe UI", 8),
                         text=f"{self.title}  (mean {mean * unit_scale:.0f} {unit})")
        self.create_text(10, h - 4, anchor="sw", fill="#7d8590", font=("Segoe UI", 8),
                         text=f"{lo * unit_scale:.0f}")
        self.create_text(w - 10, h - 4, anchor="se", fill="#7d8590", font=("Segoe UI", 8),
                         text=f"{hi * unit_scale:.0f} {unit}")


# --- windows -----------------------------------------------------------------

class Window(tk.Toplevel):
    def __init__(self, app, key, title):
        super().__init__(app.root)
        self.app, self.key = app, key
        self.title(f"{APP_NAME} - {title}")
        self.iconphoto(False, app.tk_icon)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.resizable(False, False)

    def close(self):
        self.app.windows.pop(self.key, None)
        self.destroy()

    def present(self):
        self.deiconify()
        self.lift()
        self.focus_force()


class MonitorWindow(Window):
    """Live level graph + pedal up/down calibration + NO/NC."""

    def __init__(self, app):
        super().__init__(app, "monitor", "Monitor & Calibration")
        e, c = app.engine, app.cfg
        self.pending = {k: c[k] for k in
                        ("open_db", "closed_db", "threshold_db", "hysteresis_db", "pedal_type")}
        self.samples = {}
        self.preview = Detector()
        self.last_t = 0.0
        self.suppressing = False

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        top = ttk.Frame(body)
        top.pack(fill="x")
        self.state_lbl = tk.Label(top, text="", width=14, font=("Segoe UI", 16, "bold"),
                                  fg="white", bg=COLORS["paused"], padx=10, pady=6)
        self.state_lbl.pack(side="left")
        info = ttk.Frame(top, padding=(12, 0))
        info.pack(side="left", fill="x", expand=True)
        self.level_var = tk.StringVar()
        self.contact_var = tk.StringVar()
        self.dev_var = tk.StringVar()
        ttk.Label(info, textvariable=self.level_var, font=("Segoe UI", 11)).pack(anchor="w")
        ttk.Label(info, textvariable=self.contact_var).pack(anchor="w")
        ttk.Label(info, textvariable=self.dev_var, foreground="#666").pack(anchor="w")

        self.graph = LevelGraph(body, app.scale)
        self.graph.pack(pady=10, fill="x")

        cal = ttk.LabelFrame(body, text="Calibrate", padding=10)
        cal.pack(fill="x")
        ttk.Label(cal, text="Capture each state for 1.5 s. The pedal type (NO/NC), "
                            "threshold and hysteresis are set from the result.",
                  wraplength=int(620 * app.scale)).grid(row=0, column=0, columnspan=3,
                                                       sticky="w", pady=(0, 6))
        self.up_btn = ttk.Button(cal, text="1. Capture pedal UP (released)",
                                 command=lambda: self.capture("up"))
        self.down_btn = ttk.Button(cal, text="2. Capture pedal DOWN (held)",
                                   command=lambda: self.capture("down"))
        self.up_btn.grid(row=1, column=0, sticky="ew", pady=2)
        self.down_btn.grid(row=2, column=0, sticky="ew", pady=2)
        self.up_var, self.down_var, self.result_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(cal, textvariable=self.up_var).grid(row=1, column=1, sticky="w", padx=10)
        ttk.Label(cal, textvariable=self.down_var).grid(row=2, column=1, sticky="w", padx=10)
        ttk.Label(cal, textvariable=self.result_var, wraplength=int(620 * app.scale),
                  foreground="#1f6feb").grid(row=3, column=0, columnspan=3, sticky="w",
                                             pady=(6, 0))

        adj = ttk.LabelFrame(body, text="Detection", padding=10)
        adj.pack(fill="x", pady=(10, 0))
        ttk.Label(adj, text="Threshold (dBFS)").grid(row=0, column=0, sticky="w")
        self.thr_scale = tk.Scale(adj, from_=-140, to=0, resolution=0.5, orient="horizontal",
                                  length=int(380 * app.scale), command=self._set_thr)
        self.thr_scale.grid(row=0, column=1, sticky="w")
        ttk.Label(adj, text="Hysteresis (± dB)").grid(row=1, column=0, sticky="w")
        self.hyst_scale = tk.Scale(adj, from_=0.5, to=20, resolution=0.5, orient="horizontal",
                                   length=int(380 * app.scale), command=self._set_hyst)
        self.hyst_scale.grid(row=1, column=1, sticky="w")
        ttk.Label(adj, text="Pedal type").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.type_var = tk.StringVar(value=self.pending["pedal_type"])
        tf = ttk.Frame(adj)
        tf.grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Radiobutton(tf, text="NO - normally open (pressing closes)", value="NO",
                        variable=self.type_var, command=self._set_type).pack(anchor="w")
        ttk.Radiobutton(tf, text="NC - normally closed (pressing opens)", value="NC",
                        variable=self.type_var, command=self._set_type).pack(anchor="w")
        self._load_scales()

        bottom = ttk.Frame(body)
        bottom.pack(fill="x", pady=(10, 0))
        self.suppress_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom, text="Don't click while this window is open",
                        variable=self.suppress_var, command=self._apply_suppress).pack(side="left")
        ttk.Button(bottom, text="Close", command=self.close).pack(side="right")
        ttk.Button(bottom, text="Apply & Save", command=self.apply).pack(side="right", padx=6)
        self._apply_suppress()
        self.after(50, self._tick)

    def _load_scales(self):
        # Scale.set() fires the command callback later; remember what we set so
        # those echoes aren't mistaken for the user dragging a slider.
        p = self.pending
        thr = p["threshold_db"] if p["threshold_db"] is not None else -60.0
        hyst = p["hysteresis_db"] if p["hysteresis_db"] is not None else 3.0
        self._echo = {"thr": float(self.thr_scale.get()) != thr and thr,
                      "hyst": float(self.hyst_scale.get()) != hyst and hyst}
        self.thr_scale.set(thr)
        self.hyst_scale.set(hyst)
        self.type_var.set(p["pedal_type"])

    def _is_echo(self, which, v):
        if self._echo.get(which) is not False and abs(float(v) - self._echo[which]) < 1e-6:
            self._echo[which] = False
            return True
        return False

    def _set_thr(self, v):
        if self._is_echo("thr", v):
            return
        self.pending["threshold_db"] = float(v)
        if self.pending["hysteresis_db"] is None:
            self.pending["hysteresis_db"] = float(self.hyst_scale.get())
        self.preview.reset()

    def _set_hyst(self, v):
        if self._is_echo("hyst", v):
            return
        self.pending["hysteresis_db"] = float(v)
        if self.pending["threshold_db"] is None:
            self.pending["threshold_db"] = float(self.thr_scale.get())
        self.preview.reset()

    def _set_type(self):
        self.pending["pedal_type"] = self.type_var.get()

    def _apply_suppress(self):
        want = self.suppress_var.get()
        if want and not self.suppressing:
            self.app.engine.suppress += 1
        elif not want and self.suppressing:
            self.app.engine.suppress -= 1
        self.suppressing = want

    def capture(self, which):
        self.up_btn.state(["disabled"])
        self.down_btn.state(["disabled"])
        var = self.up_var if which == "up" else self.down_var
        var.set("Recording... keep the pedal " + ("released" if which == "up" else "held down"))
        t0 = time.perf_counter() + 0.15
        self.after(1700, lambda: self._finish_capture(which, t0, t0 + 1.5))

    def _finish_capture(self, which, t0, t1):
        self.up_btn.state(["!disabled"])
        self.down_btn.state(["!disabled"])
        var = self.up_var if which == "up" else self.down_var
        vals = [d for t, d in self.app.engine.levels_since(t0) if t <= t1]
        if len(vals) < 20:
            var.set("No audio received - is the device connected?")
            return
        self.samples[which] = vals
        var.set(f"median {sorted(vals)[len(vals) // 2]:.1f} dB  "
                f"(range {min(vals):.1f} .. {max(vals):.1f})")
        if which == "up" and "down" not in self.samples:
            self.result_var.set("Now hold the pedal down and capture step 2.")
        if len(self.samples) == 2:
            values, msg = analyse_capture(self.samples["up"], self.samples["down"])
            self.result_var.set(msg + ("  Press Apply & Save to use it." if values else ""))
            if values:
                self.pending.update(values)
                self._load_scales()
                self.preview.reset()

    def apply(self):
        if self.pending["threshold_db"] is None:
            messagebox.showwarning(APP_NAME, "Capture pedal up and down first "
                                   "(or set a threshold).", parent=self)
            return
        self.app.cfg.update(self.pending)
        config.save(self.app.cfg)
        self.app.engine.resync()
        self.app.refresh_menu()
        self.result_var.set("Saved. Detection is now using these values.")

    def close(self):
        if self.suppressing:
            self.app.engine.suppress -= 1
            self.suppressing = False
        super().close()

    def _tick(self):
        if not self.winfo_exists():
            return
        e, p = self.app.engine, self.pending
        pts = e.levels_since(time.perf_counter() - self.graph.seconds)
        self.graph.draw(pts, p["open_db"], p["closed_db"], p["threshold_db"], p["hysteresis_db"])
        self.dev_var.set(e.device_label if e.ok else e.status)
        if pts:
            self.level_var.set(f"Input level {pts[-1][1]:.1f} dBFS")
            # Preview detection with the values on screen (not yet saved).
            if p["threshold_db"] is not None:
                timing = Timing(self.app.cfg)
                for t, d in pts:
                    if t > self.last_t:
                        self.preview.feed(d, p["threshold_db"], p["hysteresis_db"], timing)
            self.last_t = pts[-1][0]
        closed = self.preview.closed
        if p["threshold_db"] is None or closed is None or not e.ok:
            self.state_lbl.config(text="NOT CALIBRATED" if e.ok else "NO DEVICE",
                                  bg=COLORS["problem"])
            self.contact_var.set("")
        else:
            down = closed if p["pedal_type"] == "NO" else not closed
            self.state_lbl.config(text="PEDAL DOWN" if down else "PEDAL UP",
                                  bg=COLORS["down"] if down else COLORS["idle"])
            self.contact_var.set(f"Contact {'closed' if closed else 'open'} - "
                                 f"{p['pedal_type']} pedal")
        self.after(50, self._tick)


class SettingsWindow(Window):
    NUM_FIELDS = {
        # key: (label, from, to, increment, is_int)
        "channel": ("Input number", 1, 8, 1, True),
        "block_ms": ("Analysis block (ms)", 2, 50, 1, True),
        "smoothing_ms": ("Spike filter / median (ms)", 0, 200, 1, True),
        "debounce_ms": ("Debounce - must persist (ms)", 0, 500, 1, True),
        "lockout_ms": ("Lockout after change (ms)", 0, 1000, 5, True),
        "repeat_delay_s": ("Hold before repeating (s)", 0.05, 10, 0.05, False),
        "repeat_min_s": ("Repeat interval min (s)", 0.05, 10, 0.01, False),
        "repeat_max_s": ("Repeat interval max (s)", 0.05, 10, 0.01, False),
        "repeat_drift_ms": ("Rhythm drift (ms)", 0, 200, 1, False),
        "hold_median_ms": ("Click hold median (ms)", 5, 1000, 1, False),
        "hold_spread": ("Click hold spread (sigma)", 0, 1.5, 0.01, False),
        "hold_min_ms": ("Click hold min (ms)", 1, 1000, 1, False),
        "hold_max_ms": ("Click hold max (ms)", 1, 1000, 1, False),
    }

    def __init__(self, app):
        super().__init__(app, "settings", "Settings")
        self.vars = {}
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True)

        tab = self._tab(nb, "Input")
        ttk.Label(tab, text="Audio device").grid(row=0, column=0, sticky="w", pady=3)
        self.vars["device_name"] = tk.StringVar()
        ttk.Combobox(tab, textvariable=self.vars["device_name"], width=40,
                     values=list_input_devices()).grid(row=0, column=1, sticky="w")
        r = 1
        for k in ("channel", "block_ms", "smoothing_ms", "debounce_ms", "lockout_ms"):
            r = self._num(tab, k, r)
        ttk.Label(tab, text="Pedal type").grid(row=r, column=0, sticky="nw", pady=3)
        self.vars["pedal_type"] = tk.StringVar()
        f = ttk.Frame(tab)
        f.grid(row=r, column=1, sticky="w")
        ttk.Radiobutton(f, text="NO - normally open (pressing closes)", value="NO",
                        variable=self.vars["pedal_type"]).pack(anchor="w")
        ttk.Radiobutton(f, text="NC - normally closed (pressing opens)", value="NC",
                        variable=self.vars["pedal_type"]).pack(anchor="w")
        ttk.Label(tab, foreground="#666", wraplength=int(420 * app.scale),
                  text="Device names match as a substring; the best host API (WASAPI) "
                       "is picked automatically. Threshold and hysteresis are set in "
                       "Monitor & Calibration.\n\nTransient rejection: the spike filter "
                       "drops short pops, debounce makes a new state hold before it "
                       "counts, and lockout ignores contact chatter right after a "
                       "change. Raise them if partial presses register; lower them for "
                       "faster response.").grid(row=r + 1, column=0, columnspan=2,
                                                      sticky="w", pady=(10, 0))

        tab = self._tab(nb, "Clicking")
        ttk.Label(tab, text="Mouse button").grid(row=0, column=0, sticky="w", pady=3)
        self.vars["button"] = tk.StringVar()
        ttk.Combobox(tab, textvariable=self.vars["button"], values=config.BUTTONS,
                     state="readonly", width=10).grid(row=0, column=1, sticky="w")
        for r, (k, text) in enumerate((
                ("enabled", "Clicking enabled"),
                ("failsafe_corner", "Pause while the cursor is in the top-left screen corner"),
                ("startup", "Start with Windows")), start=1):
            self.vars[k] = tk.BooleanVar()
            ttk.Checkbutton(tab, text=text, variable=self.vars[k]).grid(
                row=r, column=0, columnspan=2, sticky="w", pady=3)

        tab = self._tab(nb, "Timing")
        r = 0
        for k in ("repeat_delay_s", "repeat_min_s", "repeat_max_s", "repeat_drift_ms",
                  "hold_median_ms", "hold_spread", "hold_min_ms", "hold_max_ms"):
            r = self._num(tab, k, r)
        prev = ttk.Frame(tab)
        prev.grid(row=0, column=2, rowspan=r, padx=(16, 0), sticky="n")
        self.int_hist = Histogram(prev, app.scale, "Repeat interval")
        self.hold_hist = Histogram(prev, app.scale, "Click hold")
        self.int_hist.pack(pady=(0, 8))
        self.hold_hist.pack()
        self._preview_job = None
        for k in self.NUM_FIELDS:
            self.vars[k].trace_add("write", lambda *a: self._schedule_preview())

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="Restore defaults", command=self.defaults).pack(side="left")
        ttk.Button(btns, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(btns, text="Save", command=self.save).pack(side="right", padx=6)
        self.load(app.cfg)
        self.after(100, self._preview)

    def _tab(self, nb, name):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=name)
        return f

    def _num(self, tab, k, row):
        label, lo, hi, inc, _ = self.NUM_FIELDS[k]
        ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=3)
        self.vars[k] = tk.StringVar()
        ttk.Spinbox(tab, textvariable=self.vars[k], from_=lo, to=hi, increment=inc,
                    width=10).grid(row=row, column=1, sticky="w")
        return row + 1

    def load(self, c):
        for k, v in self.vars.items():
            if k == "startup":
                v.set(get_startup())
            elif k in self.NUM_FIELDS:
                v.set(f"{c[k]:g}")
            else:
                v.set(c[k])

    def defaults(self):
        startup = self.vars["startup"].get()
        self.load(config.DEFAULTS)
        self.vars["startup"].set(startup)

    def collect(self):
        new = {}
        for k, v in self.vars.items():
            if k == "startup":
                continue
            if k in self.NUM_FIELDS:
                is_int = self.NUM_FIELDS[k][4]
                new[k] = int(float(v.get())) if is_int else float(v.get())
            else:
                new[k] = v.get()
        new["device_name"] = new["device_name"].strip()
        return new

    def _schedule_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(300, self._preview)

    def _preview(self):
        self._preview_job = None
        try:
            c = dict(self.app.cfg, **self.collect())
            if config.validate(c):
                return
        except ValueError:
            return
        ri = RepeatInterval(c)
        ri.reset()
        self.int_hist.draw([ri.next() for _ in range(3000)],
                           c["repeat_min_s"], c["repeat_max_s"], 1000, "ms")
        self.hold_hist.draw([click_hold_s(c) for _ in range(3000)],
                            c["hold_min_ms"] / 1000, c["hold_max_ms"] / 1000, 1000, "ms")

    def save(self):
        try:
            new = self.collect()
        except ValueError:
            messagebox.showerror(APP_NAME, "All numeric fields need a number.", parent=self)
            return
        errs = config.validate(dict(self.app.cfg, **new))
        if not new["device_name"]:
            errs.insert(0, "Audio device can't be empty.")
        if errs:
            messagebox.showerror(APP_NAME, "\n".join(errs), parent=self)
            return
        cfg, engine = self.app.cfg, self.app.engine
        audio_changed = any(new[k] != cfg[k] for k in config.AUDIO_KEYS)
        type_changed = new["pedal_type"] != cfg["pedal_type"]
        cfg.update(new)
        config.save(cfg)
        try:
            if self.vars["startup"].get() != get_startup():
                set_startup(self.vars["startup"].get())
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't change start-up setting: {e}", parent=self)
        if audio_changed:
            engine.restart_audio()
        elif type_changed:
            engine.resync()
        self.app.refresh_menu()
        self.close()


HELP_TEXT = f"""\
{APP_NAME} turns a keyboard sustain pedal plugged into an audio interface input \
into a mouse button.

HOW IT WORKS
A sustain pedal is just a switch on a 1/4" jack. It makes no sound, but the \
interface input hears a different noise floor when the contact is open (hum and \
noise) than when it is shorted (near silence). That difference is detected \
reliably even with the input gain turned all the way down.

WHAT THE PEDAL DOES
- Press: one click, right away.
- Hold: after the repeat delay (default 1.0 s) it keeps clicking at a human-like \
rhythm, every 0.64 - 0.83 s by default. Intervals follow a soft, slightly \
right-skewed curve with a slow tempo drift, not a metronome.
- Every click holds the button down for a natural, log-normally distributed time \
(median about 95 ms) before releasing.
- Release: repeating stops.

FIRST-TIME SETUP
1. Plug the pedal into input 1 (or pick another input in Settings).
2. Tray menu > Monitor & Calibration.
3. With the pedal released, click "Capture pedal UP".
4. Hold the pedal down and click "Capture pedal DOWN".
5. Check the PEDAL UP / PEDAL DOWN indicator follows your foot, then Apply & Save.

NO / NC
Pedals are wired either normally open (NO: pressing closes the contact) or \
normally closed (NC: pressing opens it); many have a polarity switch. \
Calibration detects which one you have. If presses and releases are reversed, \
switch it in the tray menu > Pedal type.

TRAY ICON
Blue: ready. Green: pedal down. Grey: clicking paused or calibrating. Orange: \
device missing or not calibrated. Click the icon to open the monitor.

TRANSIENTS AND PARTIAL PRESSES
Three filters (Settings > Input) stop noise and half-presses from clicking: a \
median spike filter (default 15 ms), a debounce that makes the new state hold \
(default 20 ms) and a lockout that ignores chatter right after a change \
(default 40 ms). Together with the hysteresis band, a press registers about \
30 ms after the contact settles. If a slow or partial press still \
double-clicks, raise debounce or lockout, and watch the effect live in \
Monitor & Calibration.

SAFETY
- Uncheck "Clicking enabled" in the tray menu to pause at any time.
- Move the cursor into the top-left screen corner to pause clicking (Settings > \
Clicking can turn this off).
- No clicks are sent while Monitor & Calibration is open (unless you untick it).
- If the interface is unplugged, clicking stops and the app reconnects on its own.

NOTES
- Windows won't let a normal app click inside windows running as administrator. \
Run {APP_NAME} as administrator if you need that.
- This runs as a tray app rather than a Windows service on purpose: services run \
in an isolated session and can't move or click the mouse on your desktop. \
Use "Start with Windows" to launch it at sign-in.

- Portable copy: a file named "portable" next to PedalClicker.exe keeps \
settings and the log in a "data" folder beside it, so the whole folder can live \
on a USB stick.

Mode: {"portable" if config.PORTABLE else "installed"}
Settings: {config.SETTINGS_FILE}
Log: {config.LOG_FILE}
"""


class HelpWindow(Window):
    def __init__(self, app):
        super().__init__(app, "help", "Help")
        self.resizable(True, True)
        frame = ttk.Frame(self, padding=8)
        frame.pack(fill="both", expand=True)
        txt = tk.Text(frame, wrap="word", width=80, height=34, font=("Segoe UI", 10),
                      relief="flat", padx=10, pady=8)
        sb = ttk.Scrollbar(frame, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", HELP_TEXT)
        for heading in ("HOW IT WORKS", "WHAT THE PEDAL DOES", "FIRST-TIME SETUP",
                        "NO / NC", "TRAY ICON", "TRANSIENTS AND PARTIAL PRESSES",
                        "SAFETY", "NOTES"):
            idx = txt.search(heading, "1.0")
            if idx:
                txt.tag_add("h", idx, f"{idx} lineend")
        txt.tag_configure("h", font=("Segoe UI", 10, "bold"), spacing1=6)
        txt.configure(state="disabled")


class AboutWindow(Window):
    def __init__(self, app):
        super().__init__(app, "about", "About")
        f = ttk.Frame(self, padding=20)
        f.pack()
        self._img = ImageTk.PhotoImage(make_icon("idle", 96))
        ttk.Label(f, image=self._img).grid(row=0, column=0, rowspan=4, padx=(0, 16))
        ttk.Label(f, text=APP_NAME, font=("Segoe UI", 16, "bold")).grid(row=0, column=1, sticky="w")
        ttk.Label(f, text=f"Version {VERSION}").grid(row=1, column=1, sticky="w")
        ttk.Label(f, text=f"Author: {AUTHOR}").grid(row=2, column=1, sticky="w")
        ttk.Label(f, text="Sustain pedal to mouse click, via an audio input.",
                  foreground="#666").grid(row=3, column=1, sticky="w")
        ttk.Label(f, foreground="#666", justify="center", text=(
            f"Copyright © 2026 {AUTHOR}\n"
            f"Free software under the {LICENSE_NAME}.\n"
            "You may share and change it under its terms.\n"
            "It comes with ABSOLUTELY NO WARRANTY.")).grid(
            row=4, column=0, columnspan=2, pady=(16, 0))
        btns = ttk.Frame(f)
        btns.grid(row=5, column=0, columnspan=2, pady=(12, 0))
        lic = license_file()
        if lic:
            ttk.Button(btns, text="View license",
                       command=lambda: os.startfile(lic)).pack(side="left", padx=4)
        ttk.Button(btns, text="OK", command=self.close).pack(side="left", padx=4)


def license_file():
    for name in ("LICENSE.txt", "LICENSE"):
        for base in (config.APP_DIR, Path(__file__).resolve().parent.parent):
            if (base / name).exists():
                return str(base / name)
    return None


# --- application -------------------------------------------------------------

class App:
    def __init__(self):
        self.cfg = config.load()
        self.engine = Engine(self.cfg)
        self.q = queue.Queue()
        self.windows = {}
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = lambda *exc: log.error(
            "UI error", exc_info=exc)
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.tk_icon = ImageTk.PhotoImage(make_icon("idle"))
        self.root.iconphoto(True, self.tk_icon)
        self.images = {k: make_icon(k) for k in COLORS}
        self._shown = None
        self.icon = pystray.Icon(APP_ID, self.images["problem"], APP_NAME, self._menu())

    def _ui(self, fn):
        """Menu callbacks run on pystray's thread; hop to the Tk thread."""
        return lambda icon, item: self.q.put(fn)

    def _menu(self):
        I, M = pystray.MenuItem, pystray.Menu
        return M(
            I(lambda i: self._status_text(), lambda icon, item: None, enabled=False),
            M.SEPARATOR,
            I("Clicking enabled", self._ui(self.toggle_enabled),
              checked=lambda i: self.cfg["enabled"]),
            I("Pedal type", M(
                I("NO - normally open", self._ui(lambda: self.set_type("NO")),
                  checked=lambda i: self.cfg["pedal_type"] == "NO", radio=True),
                I("NC - normally closed", self._ui(lambda: self.set_type("NC")),
                  checked=lambda i: self.cfg["pedal_type"] == "NC", radio=True))),
            M.SEPARATOR,
            I("Monitor && Calibration...", self._ui(lambda: self.open(MonitorWindow)),
              default=True),
            I("Settings...", self._ui(lambda: self.open(SettingsWindow))),
            I("Start with Windows", self._ui(self.toggle_startup),
              checked=lambda i: get_startup()),
            M.SEPARATOR,
            I("Help", self._ui(lambda: self.open(HelpWindow))),
            I("About", self._ui(lambda: self.open(AboutWindow))),
            M.SEPARATOR,
            I("Quit", self._ui(self.quit)),
        )

    def _state(self):
        e = self.engine
        if not e.ok or not e.calibrated:
            return "problem"
        if not self.cfg["enabled"] or e.suppress:
            return "paused"
        return "down" if e.pedal_down() else "idle"

    def _status_text(self):
        e = self.engine
        if not e.ok:
            return "No audio device"
        if not e.calibrated:
            return "Not calibrated"
        if e.suppress:
            return "Paused while calibrating"
        if not self.cfg["enabled"]:
            return "Clicking paused"
        return f"Pedal {'DOWN' if e.pedal_down() else 'up'} - {e.click_count} clicks"

    def refresh_menu(self):
        self.icon.update_menu()
        self._shown = None

    def open(self, cls):
        key = {MonitorWindow: "monitor", SettingsWindow: "settings",
               HelpWindow: "help", AboutWindow: "about"}[cls]
        w = self.windows.get(key)
        if w is None or not w.winfo_exists():
            w = self.windows[key] = cls(self)
        w.present()

    def toggle_enabled(self):
        self.cfg["enabled"] = not self.cfg["enabled"]
        config.save(self.cfg)
        self.refresh_menu()

    def set_type(self, t):
        if self.cfg["pedal_type"] != t:
            self.cfg["pedal_type"] = t
            config.save(self.cfg)
            self.engine.resync()
        self.refresh_menu()

    def toggle_startup(self):
        try:
            set_startup(not get_startup())
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't change start-up setting: {e}")
        self.refresh_menu()

    def quit(self):
        self.icon.stop()
        self.engine.stop()
        self.root.quit()

    def _poll(self):
        while True:
            try:
                fn = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("UI action failed")
        state, text = self._state(), self._status_text()
        if (state, text) != self._shown:
            self._shown = (state, text)
            self.icon.icon = self.images[state]
            self.icon.title = f"{APP_NAME} - {text}"[:127]
            self.icon.update_menu()
        self.root.after(100, self._poll)

    def run(self):
        self.engine.start()
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()
        if not self.engine.calibrated:
            self.root.after(1500, lambda: self.open(MonitorWindow))
        self.root.after(100, self._poll)
        self.root.mainloop()


def _single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, f"Local\\{APP_ID}-tray")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def main():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.handlers.RotatingFileHandler(
            config.LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")])
    sys.excepthook = lambda *exc: log.critical("unhandled", exc_info=exc)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    mutex = _single_instance()
    if mutex is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_NAME, f"{APP_NAME} is already running in the system tray.")
        return
    log.info("%s %s starting", APP_NAME, VERSION)
    App().run()
    log.info("exited")
