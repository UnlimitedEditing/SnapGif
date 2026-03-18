#!/usr/bin/env python3
"""
SnapGIF Linux — Minimal floating screen-to-GIF recorder
--------------------------------------------------------
Works on X11 desktops and WSL2 + WSLg.

Requirements:
    pip install Pillow mss pynput

Key differences from Windows version:
  • No -transparentcolor trick → 4-panel overlay gives a real bright centre
  • No WS_EX_TRANSPARENT → border marker uses thin strips OUTSIDE the region
  • pynput instead of keyboard (no sudo needed)
  • xdg-open instead of os.startfile
"""

import os, sys, threading, time, math, subprocess
from datetime import datetime

# ── Auto-install ──────────────────────────────────────────────────────────────
try:
    from PIL import Image
    import mss
    from pynput import keyboard as pynput_kb
except ImportError:
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "Pillow", "mss", "pynput"
    ])
    from PIL import Image
    import mss
    from pynput import keyboard as pynput_kb

import tkinter as tk
from tkinter import filedialog, messagebox

# ── Font helper (Segoe UI doesn't exist on Linux) ────────────────────────────
def _f(size, weight="normal"):
    return ("DejaVu Sans", size, weight)


# ── Hotkey format converter ───────────────────────────────────────────────────
def _to_pynput_key(raw: str) -> str:
    """'f3' → '<f3>',  'ctrl+shift+r' → '<ctrl>+<shift>+r'"""
    parts = []
    for t in raw.strip().lower().split('+'):
        t = t.strip()
        parts.append(t if (len(t) == 1 and t.isalpha()) else f'<{t}>')
    return '+'.join(parts)


# ─────────────────────────────────────────────────────────────────────────────
#  Tooltip
# ─────────────────────────────────────────────────────────────────────────────
class Tooltip:
    DELAY_MS = 500

    def __init__(self, widget, text):
        self._w, self._text = widget, text
        self._win = self._job = None
        widget.bind("<Enter>",  self._schedule)
        widget.bind("<Leave>",  self._cancel)
        widget.bind("<Button>", self._cancel)

    def _schedule(self, _=None):
        self._cancel()
        self._job = self._w.after(self.DELAY_MS, self._show)

    def _cancel(self, _=None):
        if self._job:
            self._w.after_cancel(self._job); self._job = None
        if self._win:
            self._win.destroy(); self._win = None

    def _show(self):
        x = self._w.winfo_rootx() + self._w.winfo_width() // 2
        y = self._w.winfo_rooty() + self._w.winfo_height() + 5
        tw = self._win = tk.Toplevel(self._w)
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self._text, bg="#1a1a30", fg="#cccccc",
                 font=_f(8), padx=8, pady=4, relief="flat").pack()
        tw.geometry(f"+{x}+{y}")


# ─────────────────────────────────────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────────────────────────────────────
class Settings:
    def __init__(self):
        self.fps          = 10
        self.max_duration = 30
        self.output_dir   = os.path.expanduser("~/Desktop")
        self.loop_gif     = 0
        self.hotkey       = "f3"


# ─────────────────────────────────────────────────────────────────────────────
#  Region marker (Linux version)
# ─────────────────────────────────────────────────────────────────────────────
class RegionMarker:
    """
    Four thin neon strips placed just OUTSIDE the selection boundary.
    Because they sit outside the capture region they don't affect the recording,
    and because they don't overlap the centre there's no need for click-through.
    Corner bracket canvases extend slightly to frame the corners visually.
    """
    T        = 4      # strip thickness (px)
    PAD      = 4      # gap between selection edge and strip
    CL       = 18     # corner bracket arm length
    COLOR_A  = "#00ff88"
    COLOR_B  = "#007744"
    PULSE_MS = 700

    def __init__(self, parent, x1, y1, x2, y2):
        self._parent = parent
        self._wins   = []
        self._phase  = True
        self._job    = None
        P, T, CL = self.PAD, self.T, self.CL

        # ── 4 thin strip windows ─────────────────────────────────────────
        #  Each is a Canvas so we can draw corner brackets on the end ones.
        #
        #  Layout (all coords in screen px):
        #    Top strip   : spans full width + pad, just above y1
        #    Bottom strip: spans full width + pad, just below y2
        #    Left strip  : spans height between top/bottom strips, left of x1
        #    Right strip : mirror of left

        TW = (x2 - x1) + P * 2          # total width of top/bottom strips
        LH = (y2 - y1) + P * 2 - T * 2  # height of side strips

        specs = [
            # (screen_x, screen_y, canvas_w, canvas_h, tag)
            (x1 - P,         y1 - P - T,  TW,  T + CL, "top"),
            (x1 - P,         y2 + P - CL, TW,  T + CL, "bot"),
            (x1 - P - T - CL, y1 - P + T, T + CL, LH, "lft"),
            (x2 + P - CL,    y1 - P + T,  T + CL, LH, "rgt"),
        ]

        for (wx, wy, ww, wh, tag) in specs:
            if ww <= 0 or wh <= 0:
                continue
            win = tk.Toplevel(parent)
            win.overrideredirect(True)
            win.geometry(f"{ww}x{wh}+{wx}+{wy}")
            win.attributes("-topmost", True)
            win.configure(bg="#010101")
            win.wm_attributes("-alpha", 1.0)
            try:
                # Works on some Linux compositors
                win.attributes("-transparentcolor", "#010101")
            except tk.TclError:
                pass
            cv = tk.Canvas(win, width=ww, height=wh,
                           bg="#010101", highlightthickness=0)
            cv.pack()
            self._wins.append({"win": win, "cv": cv, "tag": tag,
                                "ww": ww, "wh": wh})

        self._draw(self.COLOR_A)
        self._pulse()

    # ── Drawing ───────────────────────────────────────────────────────────
    def _draw(self, color):
        T, CL, LW = self.T, self.CL, 3
        for item in self._wins:
            cv, tag = item["cv"], item["tag"]
            W, H = item["ww"], item["wh"]
            cv.delete("all")

            if tag == "top":
                # Horizontal bar at bottom of canvas
                cv.create_line(0, H - T, W, H - T,
                               fill=color, width=T)
                # Left corner bracket arm (vertical)
                cv.create_line(0, H - T - CL, 0, H - T,
                               fill=self.COLOR_A, width=LW)
                # Right corner bracket arm (vertical)
                cv.create_line(W, H - T - CL, W, H - T,
                               fill=self.COLOR_A, width=LW)

            elif tag == "bot":
                # Horizontal bar at top of canvas
                cv.create_line(0, T, W, T, fill=color, width=T)
                cv.create_line(0, T, 0, T + CL,
                               fill=self.COLOR_A, width=LW)
                cv.create_line(W, T, W, T + CL,
                               fill=self.COLOR_A, width=LW)

            elif tag == "lft":
                # Vertical bar at right of canvas
                cv.create_line(W - T, 0, W - T, H,
                               fill=color, width=T)

            elif tag == "rgt":
                # Vertical bar at left of canvas
                cv.create_line(T, 0, T, H, fill=color, width=T)

    def _pulse(self):
        self._phase = not self._phase
        self._draw(self.COLOR_A if self._phase else self.COLOR_B)
        self._job = self._wins[0]["win"].after(self.PULSE_MS, self._pulse)

    def destroy(self):
        if self._job:
            try:
                self._wins[0]["win"].after_cancel(self._job)
            except Exception:
                pass
        for item in self._wins:
            try:
                item["win"].destroy()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
#  Selection overlay (Linux 4-panel approach)
# ─────────────────────────────────────────────────────────────────────────────
class SelectionOverlay:
    """
    Four semi-transparent dark Toplevels surround the selection area.
    The centre is completely unobstructed — real screen at full brightness.

    Mouse events are captured by the top panel (which starts full-screen),
    then mouse grab keeps events flowing even after panels shrink.
    """
    DIM_ALPHA    = 0.60
    DIM_BG       = "#000000"
    BORDER_COLOR = "#00ff88"
    HINT_COLOR   = "#c0c0c0"
    CORNER_LEN   = 16

    def __init__(self, parent, on_select):
        self._parent    = parent
        self._on_select = on_select
        self._sx = self._sy = 0
        self._dragging  = False

        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        self._sw, self._sh = sw, sh

        # ── 4 dim panels ─────────────────────────────────────────────────
        self._p = {}
        for name in ("top", "bot", "lft", "rgt"):
            win = tk.Toplevel(parent)
            win.overrideredirect(True)
            win.geometry("1x1+0+0")
            win.attributes("-topmost", True)
            win.configure(bg=self.DIM_BG)
            win.attributes("-alpha", self.DIM_ALPHA)
            cv = tk.Canvas(win, bg=self.DIM_BG, highlightthickness=0)
            cv.pack(fill="both", expand=True)
            self._p[name] = {"win": win, "cv": cv}

        # Top panel starts full-screen → receives initial click
        self._p["top"]["win"].geometry(f"{sw}x{sh}+0+0")
        for name in ("bot", "lft", "rgt"):
            self._p[name]["win"].withdraw()

        # Hint on top panel
        self._p["top"]["cv"].create_text(
            sw // 2, 36,
            text="Drag to select recording area    |    ESC to cancel",
            fill=self.HINT_COLOR, font=_f(13),
            tags="hint",
        )

        # ── Bind events to top panel canvas (grab keeps them here) ───────
        tc = self._p["top"]["cv"]
        tc.bind("<ButtonPress-1>",   self._press)
        tc.bind("<B1-Motion>",       self._drag)
        tc.bind("<ButtonRelease-1>", self._release)
        self._p["top"]["win"].bind("<Escape>", self._cancel)
        self._p["top"]["win"].focus_force()

    # ── Panel geometry update ─────────────────────────────────────────────
    def _update(self, x1, y1, x2, y2):
        sw, sh   = self._sw, self._sh
        BC       = self.BORDER_COLOR
        CL       = self.CORNER_LEN
        LW, LWB  = 2, 3          # dash width, bracket width

        # ── Top panel (0,0 → sw, y1) ─────────────────────────────────────
        h_top = max(y1, 1)
        self._p["top"]["win"].geometry(f"{sw}x{h_top}+0+0")
        cv = self._p["top"]["cv"]
        cv.delete("border", "label")
        # Inner edge = bottom edge of this panel
        cy = h_top - 1
        cv.create_line(x1, cy, x2, cy,
                       fill=BC, width=LW, dash=(8, 4), tags="border")
        # Size label
        lbl_y = max(cy - 16, 10)
        cv.create_text((x1 + x2) // 2, lbl_y,
                       text=f"{x2-x1} × {y2-y1} px",
                       fill=BC, font=_f(10, "bold"), tags="label")
        # Corner brackets (TL, TR)
        cv.create_line(x1, cy, x1 + CL, cy,    fill=BC, width=LWB, tags="border")
        cv.create_line(x1, cy - CL, x1, cy,    fill=BC, width=LWB, tags="border")
        cv.create_line(x2 - CL, cy, x2, cy,    fill=BC, width=LWB, tags="border")
        cv.create_line(x2, cy - CL, x2, cy,    fill=BC, width=LWB, tags="border")

        # ── Bottom panel (0, y2 → sw, sh) ────────────────────────────────
        h_bot = max(sh - y2, 1)
        self._p["bot"]["win"].deiconify()
        self._p["bot"]["win"].geometry(f"{sw}x{h_bot}+0+{y2}")
        cv = self._p["bot"]["cv"]
        cv.delete("border")
        cv.create_line(x1, 0, x2, 0,
                       fill=BC, width=LW, dash=(8, 4), tags="border")
        cv.create_line(x1, 0, x1 + CL, 0,  fill=BC, width=LWB, tags="border")
        cv.create_line(x1, 0, x1, CL,       fill=BC, width=LWB, tags="border")
        cv.create_line(x2 - CL, 0, x2, 0,  fill=BC, width=LWB, tags="border")
        cv.create_line(x2, 0, x2, CL,       fill=BC, width=LWB, tags="border")

        # ── Left panel (0, y1 → x1, y2) ──────────────────────────────────
        w_lft = max(x1, 1)
        h_mid = max(y2 - y1, 1)
        self._p["lft"]["win"].deiconify()
        self._p["lft"]["win"].geometry(f"{w_lft}x{h_mid}+0+{y1}")
        cv = self._p["lft"]["cv"]
        cv.delete("border")
        cv.create_line(w_lft - 1, 0, w_lft - 1, h_mid,
                       fill=BC, width=LW, dash=(8, 4), tags="border")

        # ── Right panel (x2, y1 → sw, y2) ────────────────────────────────
        w_rgt = max(sw - x2, 1)
        self._p["rgt"]["win"].deiconify()
        self._p["rgt"]["win"].geometry(f"{w_rgt}x{h_mid}+{x2}+{y1}")
        cv = self._p["rgt"]["cv"]
        cv.delete("border")
        cv.create_line(0, 0, 0, h_mid,
                       fill=BC, width=LW, dash=(8, 4), tags="border")

    # ── Mouse events ──────────────────────────────────────────────────────
    def _press(self, e):
        self._sx, self._sy = e.x, e.y
        self._dragging = True
        self._p["top"]["cv"].grab_set()     # capture all future events here
        self._p["top"]["cv"].delete("hint")
        self._update(e.x, e.y, e.x, e.y)

    def _drag(self, e):
        if not self._dragging:
            return
        x1, y1 = min(self._sx, e.x), min(self._sy, e.y)
        x2, y2 = max(self._sx, e.x), max(self._sy, e.y)
        self._update(x1, y1, x2, y2)

    def _release(self, e):
        if not self._dragging:
            return
        self._dragging = False
        try:
            self._p["top"]["cv"].grab_release()
        except Exception:
            pass
        x1, y1 = min(self._sx, e.x), min(self._sy, e.y)
        x2, y2 = max(self._sx, e.x), max(self._sy, e.y)
        self._close()
        if (x2 - x1) > 20 and (y2 - y1) > 20:
            self._on_select(int(x1), int(y1), int(x2), int(y2))
        else:
            self._on_select(None, None, None, None)

    def _cancel(self, _=None):
        self._close()
        self._on_select(None, None, None, None)

    def _close(self):
        for p in self._p.values():
            try:
                p["win"].destroy()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
#  Settings dialog
# ─────────────────────────────────────────────────────────────────────────────
class SettingsDialog:
    def __init__(self, parent, settings):
        self._s = settings
        BG, DARK = "#141428", "#0f1a30"

        win = tk.Toplevel(parent)
        win.title("SnapGIF  —  Settings")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg=BG)
        win.grab_set()
        self._win = win
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"380x390+{(sw-380)//2}+{(sh-390)//2}")

        def hdg(text):
            tk.Label(win, text=text, bg=BG, fg="#9090b0",
                     font=_f(9), anchor="w").pack(
                fill="x", padx=20, pady=(12, 3))

        # FPS
        hdg("Frames per second  (higher = smoother, bigger file)")
        fps_row = tk.Frame(win, bg=BG)
        fps_row.pack(fill="x", padx=24)
        self._fps = tk.IntVar(value=settings.fps)
        for v, lbl in [(5, "5 fps"), (10, "10 fps"), (15, "15 fps"), (24, "24 fps")]:
            tk.Radiobutton(fps_row, text=lbl, variable=self._fps, value=v,
                bg=BG, fg="white", selectcolor="#0f3460",
                activebackground=BG, activeforeground="white",
                font=_f(10)).pack(side="left", padx=6)

        # Duration
        hdg("Max recording duration:")
        dur_row = tk.Frame(win, bg=BG)
        dur_row.pack(fill="x", padx=24)
        self._dur = tk.IntVar(value=settings.max_duration)
        tk.Scale(dur_row, from_=5, to=120, orient="horizontal",
            variable=self._dur, bg=BG, fg="white",
            troughcolor="#0f3460", highlightthickness=0,
            length=230, font=_f(8)).pack(side="left")
        tk.Label(dur_row, textvariable=self._dur, bg=BG, fg="#00ff88",
            font=_f(10, "bold"), width=3).pack(side="left")
        tk.Label(dur_row, text="sec", bg=BG, fg="#9090b0",
            font=_f(9)).pack(side="left")

        # Output folder
        hdg("Save GIFs to:")
        dir_row = tk.Frame(win, bg=BG)
        dir_row.pack(fill="x", padx=24)
        self._dir = tk.StringVar(value=settings.output_dir)
        tk.Entry(dir_row, textvariable=self._dir,
            bg=DARK, fg="white", insertbackground="white",
            relief="flat", font=_f(9), bd=4).pack(
            side="left", fill="x", expand=True)
        tk.Button(dir_row, text=" … ", command=self._browse,
            bg="#e94560", fg="white", relief="flat",
            font=_f(9, "bold"), cursor="hand2").pack(
            side="left", padx=(4, 0))

        # Hotkey
        hdg("Record / Stop hotkey:")
        hk_row = tk.Frame(win, bg=BG)
        hk_row.pack(fill="x", padx=24)
        self._hotkey = tk.StringVar(value=settings.hotkey)
        tk.Entry(hk_row, textvariable=self._hotkey,
            bg=DARK, fg="#00ff88", insertbackground="white",
            relief="flat", font=("Monospace", 11, "bold"),
            bd=4, width=12).pack(side="left")
        tk.Label(hk_row, text="  e.g.  f3  /  f9  /  ctrl+shift+r",
            bg=BG, fg="#555570", font=_f(8)).pack(side="left")

        # Save
        tk.Button(win, text="  Save Settings  ", command=self._save,
            bg="#e94560", fg="white", relief="flat",
            font=_f(10, "bold"), pady=8, cursor="hand2").pack(pady=14)

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self._s.output_dir, parent=self._win)
        if d:
            self._dir.set(d)

    def _save(self):
        self._s.fps          = self._fps.get()
        self._s.max_duration = self._dur.get()
        self._s.output_dir   = self._dir.get()
        self._s.hotkey       = self._hotkey.get().strip().lower()
        self._win.destroy()
        if callable(getattr(self._s, "_on_hotkey_changed", None)):
            self._s._on_hotkey_changed()


# ─────────────────────────────────────────────────────────────────────────────
#  Main floating widget
# ─────────────────────────────────────────────────────────────────────────────
class SnapGIF:
    BG        = "#12121e"
    BTN_REC   = "#e94560"
    BTN_DARK  = "#1e1e36"
    FG_DIM    = "#44445a"
    FG_BRIGHT = "white"
    ACCENT    = "#00ff88"

    def __init__(self):
        self._settings = Settings()
        self._recording = False
        self._frames    = []
        self._region    = None
        self._blink_id  = None
        self._marker    = None
        self._hk_listener = None
        self._hk_bound    = None

        self._settings._on_hotkey_changed = self._rebind_hotkey

        root = tk.Tk()
        root.title("SnapGIF")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=self.BG)
        root.attributes("-alpha", 0.93)
        sw = root.winfo_screenwidth()
        root.geometry(f"232x44+{sw - 248}+18")
        self._root = root
        self._build_ui()
        self._rebind_hotkey()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = self._root

        strip = tk.Frame(root, bg="#00aa55", height=3, cursor="fleur")
        strip.pack(fill="x")
        strip.bind("<ButtonPress-1>", self._drag_start)
        strip.bind("<B1-Motion>",     self._drag_move)

        row = tk.Frame(root, bg=self.BG, padx=5, pady=5)
        row.pack(fill="both", expand=True)
        row.bind("<ButtonPress-1>", self._drag_start)
        row.bind("<B1-Motion>",     self._drag_move)

        B = dict(relief="flat", bd=0, cursor="hand2",
                 font=_f(9, "bold"), pady=2)

        self._rec_btn = tk.Button(row, text="⏺  REC",
            bg=self.BTN_REC, fg=self.FG_BRIGHT, padx=9,
            command=self._start_selection, **B)
        self._rec_btn.pack(side="left", padx=(0, 3))
        Tooltip(self._rec_btn, "Select region and start recording  (or press hotkey)")

        self._stop_btn = tk.Button(row, text="⏹  STOP",
            bg=self.BTN_DARK, fg=self.FG_DIM, padx=6,
            command=self._stop_recording, state="disabled", **B)
        self._stop_btn.pack(side="left", padx=(0, 3))
        Tooltip(self._stop_btn, "Stop recording and save GIF  (or press hotkey)")

        gear = tk.Canvas(row, width=24, height=24, bg=self.BG,
                         highlightthickness=0, cursor="hand2")
        gear.pack(side="left", padx=(0, 3))
        self._draw_gear(gear)
        gear.bind("<ButtonRelease-1>", lambda _: self._open_settings())
        Tooltip(gear, "Settings  (FPS / duration / output folder / hotkey)")

        self._dot = tk.Label(row, text="●", bg=self.BG, fg=self.FG_DIM,
                              font=_f(8))
        self._dot.pack(side="right", padx=(0, 2))
        Tooltip(self._dot, "idle  /  recording  /  saving  /  done")

        close = tk.Button(row, text="✕", bg=self.BG, fg=self.FG_DIM,
            padx=3, command=self._quit, **B)
        close.pack(side="right")
        Tooltip(close, "Quit SnapGIF")

    def _draw_gear(self, canvas, color="#8888aa"):
        canvas.delete("all")
        cx, cy = 12, 12
        r_out, r_in, r_hub = 9, 7, 3
        teeth = 8
        pts = []
        for i in range(teeth * 2):
            angle = math.radians(i * 180 / teeth - 90)
            r = r_out if i % 2 == 0 else r_in
            pts += [cx + r * math.cos(angle), cy + r * math.sin(angle)]
        canvas.create_polygon(pts, fill=color, outline="", smooth=False)
        canvas.create_oval(cx - r_hub, cy - r_hub, cx + r_hub, cy + r_hub,
                           fill=self.BG, outline="")

    # ── Dragging ──────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._dx = e.x_root - self._root.winfo_x()
        self._dy = e.y_root - self._root.winfo_y()

    def _drag_move(self, e):
        self._root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ── Recording flow ────────────────────────────────────────────────────
    def _start_selection(self):
        if self._recording:
            return
        self._root.withdraw()
        self._root.after(150, lambda: SelectionOverlay(
            self._root, self._on_region_selected))

    def _on_region_selected(self, x1, y1, x2, y2):
        self._root.deiconify()
        if x1 is None:
            return
        self._region = (x1, y1, x2, y2)
        self._begin_recording()

    def _begin_recording(self):
        self._recording = True
        self._frames    = []
        self._rec_btn.config(state="disabled", bg="#2a2a44", fg=self.FG_DIM)
        self._stop_btn.config(state="normal",  bg=self.BTN_REC, fg=self.FG_BRIGHT)
        self._show_marker()
        self._blink()
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _blink(self):
        if not self._recording:
            self._dot.config(fg=self.FG_DIM); return
        self._dot.config(
            fg="#ff3355" if self._dot.cget("fg") != "#ff3355" else self.FG_DIM)
        self._blink_id = self._root.after(500, self._blink)

    # ── Capture ───────────────────────────────────────────────────────────
    def _capture_loop(self):
        x1, y1, x2, y2 = self._region
        monitor    = {"left": x1, "top": y1, "width": x2-x1, "height": y2-y1}
        fps        = self._settings.fps
        interval   = 1.0 / fps
        max_frames = self._settings.max_duration * fps

        with mss.mss() as sct:
            while self._recording and len(self._frames) < max_frames:
                t0  = time.perf_counter()
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, raw.rgb)
                self._frames.append(img)
                wait = interval - (time.perf_counter() - t0)
                if wait > 0:
                    time.sleep(wait)

        if self._recording:
            self._root.after(0, self._stop_recording)

    # ── Stop ──────────────────────────────────────────────────────────────
    def _stop_recording(self):
        if not self._recording:
            return
        self._recording = False
        self._hide_marker()
        if self._blink_id:
            self._root.after_cancel(self._blink_id)
            self._blink_id = None
        self._rec_btn.config(state="normal",    bg=self.BTN_REC,  fg=self.FG_BRIGHT)
        self._stop_btn.config(state="disabled", bg=self.BTN_DARK, fg=self.FG_DIM)
        self._dot.config(fg="#ffaa00")
        threading.Thread(target=self._save_gif, daemon=True).start()

    # ── GIF export ────────────────────────────────────────────────────────
    def _save_gif(self):
        frames = self._frames[:]
        self._frames = []
        if not frames:
            self._root.after(0, lambda: self._dot.config(fg=self.FG_DIM))
            return

        os.makedirs(self._settings.output_dir, exist_ok=True)
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        path   = os.path.join(self._settings.output_dir, f"snap_{ts}.gif")
        dur_ms = int(1000 / self._settings.fps)

        pal_frames = [
            f.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=0)
            for f in frames
        ]
        pal_frames[0].save(
            path,
            save_all=True,
            append_images=pal_frames[1:],
            optimize=True,
            loop=self._settings.loop_gif,
            duration=dur_ms,
        )
        self._root.after(0, lambda: self._on_saved(path, len(frames)))

    def _on_saved(self, path, frame_count):
        self._dot.config(fg=self.ACCENT)
        self._root.after(4000, lambda: self._dot.config(fg=self.FG_DIM))
        size_kb = os.path.getsize(path) / 1024
        if messagebox.askyesno("SnapGIF — Saved!",
            f"Saved {frame_count} frames  ({size_kb:.0f} KB)\n\n{path}\n\nOpen folder?",
            parent=self._root):
            subprocess.Popen(["xdg-open", os.path.dirname(path)])

    # ── Region marker ─────────────────────────────────────────────────────
    def _show_marker(self):
        self._hide_marker()
        if self._region:
            x1, y1, x2, y2 = self._region
            self._marker = RegionMarker(self._root, x1, y1, x2, y2)

    def _hide_marker(self):
        if self._marker:
            self._marker.destroy()
            self._marker = None

    # ── Hotkey (pynput) ───────────────────────────────────────────────────
    def _rebind_hotkey(self):
        if self._hk_listener:
            try:
                self._hk_listener.stop()
            except Exception:
                pass
            self._hk_listener = None
            self._hk_bound    = None

        key_raw = self._settings.hotkey.strip()
        if not key_raw:
            return
        pynput_key = _to_pynput_key(key_raw)
        try:
            listener = pynput_kb.GlobalHotKeys(
                {pynput_key: self._hotkey_pressed}
            )
            listener.start()
            self._hk_listener = listener
            self._hk_bound    = pynput_key
        except Exception as e:
            print(f"[SnapGIF] Could not bind hotkey '{pynput_key}': {e}")

    def _hotkey_pressed(self):
        self._root.after(0, self._toggle_record)

    def _toggle_record(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_selection()

    # ── Settings / quit ───────────────────────────────────────────────────
    def _open_settings(self):
        SettingsDialog(self._root, self._settings)

    def _quit(self):
        if self._hk_listener:
            try:
                self._hk_listener.stop()
            except Exception:
                pass
        self._root.destroy()

    def run(self):
        self._root.mainloop()


if __name__ == "__main__":
    SnapGIF().run()
