#!/usr/bin/env python3
"""
SnapGIF — Minimal floating screen-to-GIF recorder for Windows 10
-----------------------------------------------------------------
Requirements: pip install Pillow mss

Usage: python snapgif.py
  • Drag the floating widget anywhere on screen
  • Click ⏺ REC → drag to select the region you want to record
  • The screen dims; the selected area stays bright
  • Click ⏹ STOP (or wait for max duration) → GIF is saved to Desktop
  • Click ⚙ to change FPS, duration, and output folder
"""

import ctypes
import os
import sys
import threading
import time
from datetime import datetime

try:
    from PIL import Image, ImageGrab, ImageTk
    import mss
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "mss"])
    from PIL import Image, ImageGrab, ImageTk
    import mss

import tkinter as tk
from tkinter import filedialog, messagebox

# ── DPI awareness so coordinates are accurate on HiDPI screens ──────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Settings container
# ─────────────────────────────────────────────────────────────────────────────
class Settings:
    def __init__(self):
        self.fps: int = 10
        self.max_duration: int = 30   # seconds
        self.output_dir: str = os.path.expanduser("~/Desktop")
        self.loop_gif: int = 0        # 0 = infinite loop


# ─────────────────────────────────────────────────────────────────────────────
#  Selection overlay  (dims screen, bright cut-out on drag)
# ─────────────────────────────────────────────────────────────────────────────
class SelectionOverlay:
    """
    Full-screen darkened overlay.
    The dragged rectangle becomes a transparent "hole" showing the screen
    at full brightness underneath — achieved via Windows -transparentcolor trick.
    """

    HOLE_COLOR   = "#fefefe"   # Must be unique; used as the transparent key
    BORDER_COLOR = "#00ff88"
    TEXT_COLOR   = "#00ff88"
    HINT_COLOR   = "#cccccc"
    DIM_ALPHA    = 0.55        # How dark the un-selected area is

    def __init__(self, parent: tk.Tk, on_select):
        self._parent    = parent
        self._on_select = on_select
        self._sx = self._sy = 0
        self._cx = self._cy = 0
        self._dragging  = False

        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()
        self._sw, self._sh = sw, sh

        # ── Overlay window ────────────────────────────────────────────────
        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)
        self._win.geometry(f"{sw}x{sh}+0+0")
        self._win.attributes("-topmost", True)
        self._win.configure(bg="black")
        self._win.attributes("-alpha", self.DIM_ALPHA)
        # Make HOLE_COLOR pixels fully transparent (shows screen at 100 %)
        self._win.attributes("-transparentcolor", self.HOLE_COLOR)

        # ── Canvas ────────────────────────────────────────────────────────
        self._cv = tk.Canvas(
            self._win,
            bg="black",
            highlightthickness=0,
            cursor="crosshair",
        )
        self._cv.pack(fill="both", expand=True)

        # Hint text
        self._hint = self._cv.create_text(
            sw // 2, 36,
            text="Click and drag to select a region   ·   ESC to cancel",
            fill=self.HINT_COLOR,
            font=("Segoe UI", 13),
        )

        # ── Bindings ──────────────────────────────────────────────────────
        self._cv.bind("<ButtonPress-1>",   self._press)
        self._cv.bind("<B1-Motion>",       self._drag)
        self._cv.bind("<ButtonRelease-1>", self._release)
        self._win.bind("<Escape>",         self._cancel)
        self._win.focus_force()

    # ── Drawing ──────────────────────────────────────────────────────────
    def _redraw(self):
        self._cv.delete("sel")
        x1 = min(self._sx, self._cx);  y1 = min(self._sy, self._cy)
        x2 = max(self._sx, self._cx);  y2 = max(self._sy, self._cy)
        w, h = x2 - x1, y2 - y1

        # Transparent "hole" — the magic fill colour
        self._cv.create_rectangle(
            x1, y1, x2, y2,
            fill=self.HOLE_COLOR,
            outline=self.BORDER_COLOR,
            width=2,
            dash=(7, 4),
            tags="sel",
        )

        # Corner squares
        S = 7
        for cx, cy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
            self._cv.create_rectangle(
                cx - S, cy - S, cx + S, cy + S,
                fill=self.BORDER_COLOR, outline="", tags="sel",
            )

        # Dimension label above selection
        lbl_y = y1 - 18 if y1 > 30 else y2 + 18
        self._cv.create_text(
            (x1 + x2) // 2, lbl_y,
            text=f"{w} × {h} px",
            fill=self.TEXT_COLOR,
            font=("Segoe UI", 10, "bold"),
            tags="sel",
        )

    # ── Event handlers ────────────────────────────────────────────────────
    def _press(self, e):
        self._sx, self._sy = e.x, e.y
        self._dragging = True
        self._cv.delete("hint_text")

    def _drag(self, e):
        self._cx, self._cy = e.x, e.y
        if self._dragging:
            self._redraw()

    def _release(self, e):
        if not self._dragging:
            return
        self._dragging = False
        x1 = min(self._sx, e.x);  y1 = min(self._sy, e.y)
        x2 = max(self._sx, e.x);  y2 = max(self._sy, e.y)
        self._win.destroy()
        if (x2 - x1) > 20 and (y2 - y1) > 20:
            self._on_select(int(x1), int(y1), int(x2), int(y2))
        else:
            self._on_select(None, None, None, None)

    def _cancel(self, _event=None):
        self._win.destroy()
        self._on_select(None, None, None, None)


# ─────────────────────────────────────────────────────────────────────────────
#  Settings dialog
# ─────────────────────────────────────────────────────────────────────────────
class SettingsDialog:
    def __init__(self, parent: tk.Tk, settings: Settings):
        self._s = settings

        win = tk.Toplevel(parent)
        win.title("SnapGIF — Settings")
        win.geometry("340x270")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg="#141428")
        win.grab_set()
        self._win = win

        PAD = {"padx": 20, "pady": 5}
        LBL = {"bg": "#141428", "fg": "#9090b0", "font": ("Segoe UI", 9), "anchor": "w"}
        FRM = {"bg": "#141428"}

        # FPS
        tk.Label(win, text="Frames per second (FPS):", **LBL).pack(fill="x", **PAD)
        fps_row = tk.Frame(win, **FRM)
        fps_row.pack(fill="x", padx=20)
        self._fps = tk.IntVar(value=settings.fps)
        for v, lbl in [(5, "5  Smooth"), (10, "10  Normal"), (15, "15  Fluid"), (24, "24  Cinema")]:
            tk.Radiobutton(
                fps_row, text=lbl, variable=self._fps, value=v,
                bg="#141428", fg="white", selectcolor="#0f3460",
                activebackground="#141428", activeforeground="white",
                font=("Segoe UI", 9),
            ).pack(side="left", padx=6)

        # Max duration
        tk.Label(win, text="Max recording duration (seconds):", **LBL).pack(fill="x", **PAD)
        self._dur = tk.IntVar(value=settings.max_duration)
        dur_row = tk.Frame(win, **FRM)
        dur_row.pack(fill="x", padx=20)
        tk.Scale(
            dur_row, from_=5, to=60, orient="horizontal",
            variable=self._dur, bg="#141428", fg="white",
            troughcolor="#0f3460", highlightthickness=0,
            length=200, font=("Segoe UI", 8),
        ).pack(side="left")
        self._dur_lbl = tk.Label(dur_row, textvariable=self._dur,
                                  bg="#141428", fg="#00ff88",
                                  font=("Segoe UI", 10, "bold"), width=3)
        self._dur_lbl.pack(side="left", padx=6)
        tk.Label(dur_row, text="sec", bg="#141428", fg="#9090b0",
                 font=("Segoe UI", 9)).pack(side="left")

        # Output folder
        tk.Label(win, text="Save GIFs to:", **LBL).pack(fill="x", **PAD)
        dir_row = tk.Frame(win, **FRM)
        dir_row.pack(fill="x", padx=20)
        self._dir = tk.StringVar(value=settings.output_dir)
        tk.Entry(
            dir_row, textvariable=self._dir,
            bg="#0f1a30", fg="white", insertbackground="white",
            relief="flat", font=("Segoe UI", 9), bd=4,
        ).pack(side="left", fill="x", expand=True)
        tk.Button(
            dir_row, text="…", command=self._browse,
            bg="#e94560", fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=6, cursor="hand2",
        ).pack(side="left", padx=(4, 0))

        # Save
        tk.Button(
            win, text="  Save Settings  ", command=self._save,
            bg="#e94560", fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"), pady=7, cursor="hand2",
        ).pack(pady=16)

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self._s.output_dir, parent=self._win)
        if d:
            self._dir.set(d)

    def _save(self):
        self._s.fps          = self._fps.get()
        self._s.max_duration = self._dur.get()
        self._s.output_dir   = self._dir.get()
        self._win.destroy()


# ─────────────────────────────────────────────────────────────────────────────
#  Main floating widget
# ─────────────────────────────────────────────────────────────────────────────
class SnapGIF:
    # Colours
    BG        = "#12121e"
    BTN_REC   = "#e94560"
    BTN_DARK  = "#1e1e36"
    FG_DIM    = "#44445a"
    FG_BRIGHT = "white"
    ACCENT    = "#00ff88"

    def __init__(self):
        self._settings = Settings()
        self._recording = False
        self._frames: list[Image.Image] = []
        self._region: tuple[int, int, int, int] | None = None
        self._capture_thread: threading.Thread | None = None
        self._blink_id = None

        # ── Root window ──────────────────────────────────────────────────
        root = tk.Tk()
        root.title("SnapGIF")
        root.overrideredirect(True)          # No title bar
        root.attributes("-topmost", True)    # Always on top
        root.configure(bg=self.BG)
        root.attributes("-alpha", 0.93)

        # Start top-right corner
        sw = root.winfo_screenwidth()
        root.geometry(f"204x42+{sw - 220}+18")

        self._root = root
        self._build_ui()
        self._make_draggable()

    # ── UI construction ──────────────────────────────────────────────────
    def _build_ui(self):
        root = self._root

        # Thin top drag strip with subtle accent line
        strip = tk.Frame(root, bg="#00aa55", height=2)
        strip.pack(fill="x")
        strip.bind("<ButtonPress-1>",  self._drag_start)
        strip.bind("<B1-Motion>",      self._drag_move)

        row = tk.Frame(root, bg=self.BG, padx=6, pady=6)
        row.pack(fill="both", expand=True)

        btn = dict(relief="flat", bd=0, cursor="hand2",
                   font=("Segoe UI", 9, "bold"), pady=3)

        # REC
        self._rec_btn = tk.Button(
            row, text="⏺  REC", bg=self.BTN_REC, fg=self.FG_BRIGHT,
            padx=10, command=self._start_selection, **btn,
        )
        self._rec_btn.pack(side="left", padx=(0, 3))

        # STOP
        self._stop_btn = tk.Button(
            row, text="⏹", bg=self.BTN_DARK, fg=self.FG_DIM,
            padx=8, command=self._stop_recording, state="disabled", **btn,
        )
        self._stop_btn.pack(side="left", padx=(0, 3))

        # Settings
        tk.Button(
            row, text="⚙", bg=self.BTN_DARK, fg="#8888aa",
            padx=7, command=self._open_settings, **btn,
        ).pack(side="left", padx=(0, 3))

        # Status dot  (right side)
        self._dot = tk.Label(row, text="●", bg=self.BG, fg=self.FG_DIM,
                              font=("Segoe UI", 8))
        self._dot.pack(side="right", padx=(0, 2))

        # Close ×
        tk.Button(
            row, text="✕", bg=self.BG, fg="#44445a",
            padx=4, command=root.destroy,
            relief="flat", bd=0, cursor="hand2",
            font=("Segoe UI", 8),
        ).pack(side="right")

    # ── Dragging ─────────────────────────────────────────────────────────
    def _make_draggable(self):
        self._root.bind("<ButtonPress-1>",  self._drag_start)
        self._root.bind("<B1-Motion>",      self._drag_move)

    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self._root.winfo_x() + (e.x - self._dx)
        y = self._root.winfo_y() + (e.y - self._dy)
        self._root.geometry(f"+{x}+{y}")

    # ── Recording flow ────────────────────────────────────────────────────
    def _start_selection(self):
        if self._recording:
            return
        self._root.withdraw()
        self._root.after(120, lambda: SelectionOverlay(
            self._root, self._on_region_selected
        ))

    def _on_region_selected(self, x1, y1, x2, y2):
        self._root.deiconify()
        if x1 is None:
            return
        self._region = (x1, y1, x2, y2)
        self._begin_recording()

    def _begin_recording(self):
        self._recording = True
        self._frames    = []

        self._rec_btn.config(state="disabled", bg="#333350", fg=self.FG_DIM)
        self._stop_btn.config(state="normal",  bg=self.BTN_REC, fg=self.FG_BRIGHT)

        self._blink()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True
        )
        self._capture_thread.start()

    def _blink(self):
        if not self._recording:
            self._dot.config(fg=self.FG_DIM)
            return
        cur = self._dot.cget("fg")
        nxt = "#ff3355" if cur != "#ff3355" else self.FG_DIM
        self._dot.config(fg=nxt)
        self._blink_id = self._root.after(500, self._blink)

    def _capture_loop(self):
        x1, y1, x2, y2 = self._region
        monitor = {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}
        fps       = self._settings.fps
        interval  = 1.0 / fps
        max_frames = self._settings.max_duration * fps

        with mss.mss() as sct:
            while self._recording and len(self._frames) < max_frames:
                t0  = time.perf_counter()
                raw = sct.grab(monitor)
                img = Image.frombytes(
                    "RGB", raw.size, raw.bgra, "raw", "BGRX"
                )
                self._frames.append(img)
                wait = interval - (time.perf_counter() - t0)
                if wait > 0:
                    time.sleep(wait)

        # Auto-stop when max duration hit
        if self._recording:
            self._root.after(0, self._stop_recording)

    def _stop_recording(self):
        if not self._recording:
            return
        self._recording = False

        if self._blink_id:
            self._root.after_cancel(self._blink_id)
            self._blink_id = None

        self._rec_btn.config(state="normal",   bg=self.BTN_REC,  fg=self.FG_BRIGHT)
        self._stop_btn.config(state="disabled", bg=self.BTN_DARK, fg=self.FG_DIM)
        self._dot.config(fg="#ffaa00")   # yellow = saving

        threading.Thread(target=self._save_gif, daemon=True).start()

    # ── GIF export ────────────────────────────────────────────────────────
    def _save_gif(self):
        frames = self._frames[:]
        self._frames = []

        if not frames:
            self._root.after(0, lambda: self._dot.config(fg=self.FG_DIM))
            return

        os.makedirs(self._settings.output_dir, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._settings.output_dir, f"snap_{ts}.gif")

        duration_ms = int(1000 / self._settings.fps)

        # Quantise each frame to 256 colours for compact GIF
        pal_frames = []
        for f in frames:
            q = f.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=0)
            pal_frames.append(q)

        pal_frames[0].save(
            path,
            save_all=True,
            append_images=pal_frames[1:],
            optimize=True,
            loop=self._settings.loop_gif,
            duration=duration_ms,
        )

        self._root.after(0, lambda: self._on_saved(path))

    def _on_saved(self, path: str):
        self._dot.config(fg=self.ACCENT)     # green = done ✓
        self._root.after(4000, lambda: self._dot.config(fg=self.FG_DIM))

        size_kb = os.path.getsize(path) / 1024
        open_folder = messagebox.askyesno(
            "SnapGIF — Saved!",
            f"GIF saved  ({size_kb:.0f} KB)\n\n{path}\n\nOpen folder?",
            parent=self._root,
        )
        if open_folder:
            os.startfile(os.path.dirname(path))

    # ── Settings ──────────────────────────────────────────────────────────
    def _open_settings(self):
        SettingsDialog(self._root, self._settings)

    # ── Run ───────────────────────────────────────────────────────────────
    def run(self):
        self._root.mainloop()


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = SnapGIF()
    app.run()
