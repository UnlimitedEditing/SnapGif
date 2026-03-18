#!/usr/bin/env python3
"""
SnapGIF — Minimal floating screen-to-GIF recorder for Windows 10
-----------------------------------------------------------------
Requirements: pip install Pillow mss
"""

import ctypes, os, sys, threading, time, math
from datetime import datetime

try:
    from PIL import Image
    import mss
    import keyboard
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "mss", "keyboard"])
    from PIL import Image
    import mss
    import keyboard

import tkinter as tk
from tkinter import filedialog, messagebox

# ── DPI awareness ─────────────────────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Tooltip
# ─────────────────────────────────────────────────────────────────────────────
class Tooltip:
    DELAY_MS = 500

    def __init__(self, widget, text):
        self._w     = widget
        self._text  = text
        self._win   = None
        self._job   = None
        widget.bind("<Enter>",  self._schedule)
        widget.bind("<Leave>",  self._cancel)
        widget.bind("<Button>", self._cancel)

    def _schedule(self, _=None):
        self._cancel()
        self._job = self._w.after(self.DELAY_MS, self._show)

    def _cancel(self, _=None):
        if self._job:
            self._w.after_cancel(self._job)
            self._job = None
        if self._win:
            self._win.destroy()
            self._win = None

    def _show(self):
        x = self._w.winfo_rootx() + self._w.winfo_width() // 2
        y = self._w.winfo_rooty() + self._w.winfo_height() + 5
        self._win = tw = tk.Toplevel(self._w)
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self._text, bg="#1a1a30", fg="#cccccc",
                 font=("Segoe UI", 8), padx=8, pady=4, relief="flat").pack()
        tw.geometry(f"+{x}+{y}")


# ─────────────────────────────────────────────────────────────────────────────
#  Settings container
# ─────────────────────────────────────────────────────────────────────────────
class Settings:
    def __init__(self):
        self.fps          = 10
        self.max_duration = 30
        self.output_dir   = os.path.expanduser("~/Desktop")
        self.loop_gif     = 0
        self.hotkey       = "f3"


# ─────────────────────────────────────────────────────────────────────────────
#  Region marker  (subtle border shown over the capture area during recording)
# ─────────────────────────────────────────────────────────────────────────────
class RegionMarker:
    """
    A click-through borderless window that draws a glowing dashed border
    around the capture region while recording is active.
    Uses WS_EX_TRANSPARENT so all mouse events fall through to windows below.
    """
    CORNER_LEN  = 18      # px length of each corner bracket arm
    LINE_W      = 2
    COLOR_A     = "#00ff88"
    COLOR_B     = "#007744"
    PULSE_MS    = 700
    PADDING     = 3       # px outside the actual region

    def __init__(self, parent: tk.Tk, x1: int, y1: int, x2: int, y2: int):
        pad = self.PADDING
        wx, wy = x1 - pad, y1 - pad
        ww, wh = (x2 - x1) + pad * 2, (y2 - y1) + pad * 2

        win = tk.Toplevel(parent)
        win.overrideredirect(True)
        win.geometry(f"{ww}x{wh}+{wx}+{wy}")
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", "#010101")   # unique bg → transparent
        win.configure(bg="#010101")
        win.wm_attributes("-alpha", 1.0)
        self._win = win

        # Make the window click-through via WS_EX_TRANSPARENT
        try:
            win.update_idletasks()
            GWL_EXSTYLE      = -20
            WS_EX_LAYERED    = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
            )
        except Exception:
            pass

        cv = tk.Canvas(win, width=ww, height=wh, bg="#010101",
                       highlightthickness=0)
        cv.pack()
        self._cv    = cv
        self._ww    = ww
        self._wh    = wh
        self._phase = True
        self._job   = None
        self._draw(self.COLOR_A)
        self._pulse()

    def _draw(self, color: str):
        cv = self._cv
        cv.delete("all")
        W, H = self._ww, self._wh
        CL   = self.CORNER_LEN
        LW   = self.LINE_W

        # Dashed border
        cv.create_rectangle(LW, LW, W - LW, H - LW,
                             outline=color, width=LW,
                             dash=(6, 5))

        # Corner brackets (solid, brighter than the dashes)
        bright = self.COLOR_A
        segs = [
            # top-left
            (LW, LW, LW + CL, LW), (LW, LW, LW, LW + CL),
            # top-right
            (W - LW - CL, LW, W - LW, LW), (W - LW, LW, W - LW, LW + CL),
            # bottom-left
            (LW, H - LW - CL, LW, H - LW), (LW, H - LW, LW + CL, H - LW),
            # bottom-right
            (W - LW, H - LW - CL, W - LW, H - LW),
            (W - LW - CL, H - LW, W - LW, H - LW),
        ]
        for x0, y0, x1, y1 in segs:
            cv.create_line(x0, y0, x1, y1, fill=bright, width=LW + 1)

    def _pulse(self):
        self._phase = not self._phase
        self._draw(self.COLOR_A if self._phase else self.COLOR_B)
        self._job = self._win.after(self.PULSE_MS, self._pulse)

    def destroy(self):
        if self._job:
            try:
                self._win.after_cancel(self._job)
            except Exception:
                pass
        try:
            self._win.destroy()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
#  Selection overlay
# ─────────────────────────────────────────────────────────────────────────────
class SelectionOverlay:
    HOLE_COLOR   = "#fefefe"
    BORDER_COLOR = "#00ff88"
    TEXT_COLOR   = "#00ff88"
    HINT_COLOR   = "#c0c0c0"
    DIM_ALPHA    = 0.55

    def __init__(self, parent, on_select):
        self._parent    = parent
        self._on_select = on_select
        self._sx = self._sy = 0
        self._cx = self._cy = 0
        self._dragging  = False

        sw = parent.winfo_screenwidth()
        sh = parent.winfo_screenheight()

        win = tk.Toplevel(parent)
        win.overrideredirect(True)
        win.geometry(f"{sw}x{sh}+0+0")
        win.attributes("-topmost", True)
        win.configure(bg="black")
        win.attributes("-alpha", self.DIM_ALPHA)
        win.attributes("-transparentcolor", self.HOLE_COLOR)
        self._win = win

        cv = tk.Canvas(win, bg="black", highlightthickness=0, cursor="crosshair")
        cv.pack(fill="both", expand=True)
        self._cv = cv

        cv.create_text(sw // 2, 34,
            text="Drag to select recording area    |    ESC to cancel",
            fill=self.HINT_COLOR, font=("Segoe UI", 13), tags="hint")

        cv.bind("<ButtonPress-1>",   self._press)
        cv.bind("<B1-Motion>",       self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        win.bind("<Escape>",         self._cancel)
        win.focus_force()

    def _redraw(self):
        self._cv.delete("sel")
        x1, y1 = min(self._sx, self._cx), min(self._sy, self._cy)
        x2, y2 = max(self._sx, self._cx), max(self._sy, self._cy)
        self._cv.create_rectangle(x1, y1, x2, y2,
            fill=self.HOLE_COLOR, outline=self.BORDER_COLOR,
            width=2, dash=(8, 4), tags="sel")
        H = 6
        for hx, hy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
            self._cv.create_rectangle(hx-H, hy-H, hx+H, hy+H,
                fill=self.BORDER_COLOR, outline="", tags="sel")
        lbl_y = y1 - 16 if y1 > 28 else y2 + 16
        self._cv.create_text((x1+x2)//2, lbl_y,
            text=f"{x2-x1} x {y2-y1} px",
            fill=self.TEXT_COLOR, font=("Segoe UI", 10, "bold"), tags="sel")

    def _press(self, e):
        self._sx, self._sy = e.x, e.y
        self._dragging = True
        self._cv.delete("hint")

    def _drag(self, e):
        self._cx, self._cy = e.x, e.y
        if self._dragging:
            self._redraw()

    def _release(self, e):
        if not self._dragging:
            return
        self._dragging = False
        x1, y1 = min(self._sx, e.x), min(self._sy, e.y)
        x2, y2 = max(self._sx, e.x), max(self._sy, e.y)
        self._win.destroy()
        if (x2 - x1) > 20 and (y2 - y1) > 20:
            self._on_select(int(x1), int(y1), int(x2), int(y2))
        else:
            self._on_select(None, None, None, None)

    def _cancel(self, _=None):
        self._win.destroy()
        self._on_select(None, None, None, None)


# ─────────────────────────────────────────────────────────────────────────────
#  Settings dialog
# ─────────────────────────────────────────────────────────────────────────────
class SettingsDialog:
    def __init__(self, parent, settings):
        self._s = settings
        BG   = "#141428"
        DARK = "#0f1a30"

        win = tk.Toplevel(parent)
        win.title("SnapGIF  —  Settings")
        win.geometry("360x370")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg=BG)
        win.grab_set()
        self._win = win
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"360x370+{(sw-360)//2}+{(sh-370)//2}")

        def hdg(text):
            tk.Label(win, text=text, bg=BG, fg="#9090b0",
                     font=("Segoe UI", 9), anchor="w").pack(
                fill="x", padx=20, pady=(12, 3))

        # FPS
        hdg("Frames per second  (higher = smoother, bigger file)")
        fps_row = tk.Frame(win, bg=BG)
        fps_row.pack(fill="x", padx=24)
        self._fps = tk.IntVar(value=settings.fps)
        for v, label in [(5, "5 fps"), (10, "10 fps"), (15, "15 fps"), (24, "24 fps")]:
            tk.Radiobutton(fps_row, text=label, variable=self._fps, value=v,
                bg=BG, fg="white", selectcolor="#0f3460",
                activebackground=BG, activeforeground="white",
                font=("Segoe UI", 10)).pack(side="left", padx=6)

        # Duration
        hdg("Max recording duration:")
        dur_row = tk.Frame(win, bg=BG)
        dur_row.pack(fill="x", padx=24)
        self._dur = tk.IntVar(value=settings.max_duration)
        tk.Scale(dur_row, from_=5, to=120, orient="horizontal",
            variable=self._dur, bg=BG, fg="white",
            troughcolor="#0f3460", highlightthickness=0,
            length=220, font=("Segoe UI", 8)).pack(side="left")
        tk.Label(dur_row, textvariable=self._dur, bg=BG, fg="#00ff88",
            font=("Segoe UI", 10, "bold"), width=3).pack(side="left")
        tk.Label(dur_row, text="sec", bg=BG, fg="#9090b0",
            font=("Segoe UI", 9)).pack(side="left")

        # Output folder
        hdg("Save GIFs to:")
        dir_row = tk.Frame(win, bg=BG)
        dir_row.pack(fill="x", padx=24)
        self._dir = tk.StringVar(value=settings.output_dir)
        tk.Entry(dir_row, textvariable=self._dir,
            bg=DARK, fg="white", insertbackground="white",
            relief="flat", font=("Segoe UI", 9), bd=4).pack(
            side="left", fill="x", expand=True)
        tk.Button(dir_row, text=" … ", command=self._browse,
            bg="#e94560", fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), cursor="hand2").pack(
            side="left", padx=(4, 0))

        # Hotkey
        hdg("Record / Stop hotkey:")
        hk_row = tk.Frame(win, bg=BG)
        hk_row.pack(fill="x", padx=24)
        self._hotkey = tk.StringVar(value=settings.hotkey)
        hk_entry = tk.Entry(hk_row, textvariable=self._hotkey,
            bg=DARK, fg="#00ff88", insertbackground="white",
            relief="flat", font=("Courier New", 11, "bold"), bd=4, width=10)
        hk_entry.pack(side="left")
        tk.Label(hk_row, text="  e.g.  f3  /  f9  /  ctrl+shift+r",
            bg=BG, fg="#555570", font=("Segoe UI", 8)).pack(side="left")

        tk.Button(win, text="  Save Settings  ", command=self._save,
            bg="#e94560", fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2").pack(pady=14)

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
        # Notify app to rebind hotkey (app sets this callback before opening dialog)
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
        self._marker: RegionMarker | None = None
        self._hotkey_bound: str | None = None

        # Let settings dialog trigger rebind after save
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

        # Drag strip (green accent bar at top)
        strip = tk.Frame(root, bg="#00aa55", height=3, cursor="fleur")
        strip.pack(fill="x")
        strip.bind("<ButtonPress-1>", self._drag_start)
        strip.bind("<B1-Motion>",     self._drag_move)

        row = tk.Frame(root, bg=self.BG, padx=5, pady=5)
        row.pack(fill="both", expand=True)
        row.bind("<ButtonPress-1>", self._drag_start)
        row.bind("<B1-Motion>",     self._drag_move)

        B = dict(relief="flat", bd=0, cursor="hand2",
                 font=("Segoe UI", 9, "bold"), pady=2)

        # REC button
        self._rec_btn = tk.Button(row, text="⏺  REC",
            bg=self.BTN_REC, fg=self.FG_BRIGHT, padx=9,
            command=self._start_selection, **B)
        self._rec_btn.pack(side="left", padx=(0, 3))
        Tooltip(self._rec_btn, "Select region and start recording  (or press hotkey)")

        # STOP button
        self._stop_btn = tk.Button(row, text="⏹  STOP",
            bg=self.BTN_DARK, fg=self.FG_DIM, padx=6,
            command=self._stop_recording, state="disabled", **B)
        self._stop_btn.pack(side="left", padx=(0, 3))
        Tooltip(self._stop_btn, "Stop recording and save GIF  (or press hotkey)")

        # Gear (canvas-drawn so it always renders correctly)
        gear = tk.Canvas(row, width=24, height=24, bg=self.BG,
                         highlightthickness=0, cursor="hand2")
        gear.pack(side="left", padx=(0, 3))
        self._draw_gear(gear)
        gear.bind("<ButtonRelease-1>", lambda _: self._open_settings())
        Tooltip(gear, "Settings  (FPS / duration / output folder)")

        # Status dot
        self._dot = tk.Label(row, text="●", bg=self.BG, fg=self.FG_DIM,
                              font=("Segoe UI", 8))
        self._dot.pack(side="right", padx=(0, 2))
        Tooltip(self._dot, "idle  /  recording  /  saving  /  done")

        # Close button
        close = tk.Button(row, text="✕", bg=self.BG, fg=self.FG_DIM,
            padx=3, command=root.destroy, **B)
        close.pack(side="right")
        Tooltip(close, "Quit SnapGIF")

    def _draw_gear(self, canvas, color="#8888aa"):
        """Paint a procedural gear icon onto a Canvas."""
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

    # ── Window dragging ───────────────────────────────────────────────────
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
        # Show the region marker overlay
        self._show_marker()
        self._blink()
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _blink(self):
        if not self._recording:
            self._dot.config(fg=self.FG_DIM)
            return
        self._dot.config(fg="#ff3355" if self._dot.cget("fg") != "#ff3355" else self.FG_DIM)
        self._blink_id = self._root.after(500, self._blink)

    # ── Capture (THE FIX: raw.rgb instead of raw.bgra) ───────────────────
    def _capture_loop(self):
        x1, y1, x2, y2 = self._region
        monitor    = {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}
        fps        = self._settings.fps
        interval   = 1.0 / fps
        max_frames = self._settings.max_duration * fps

        with mss.mss() as sct:
            while self._recording and len(self._frames) < max_frames:
                t0  = time.perf_counter()
                raw = sct.grab(monitor)
                # raw.rgb gives correct RGB bytes (raw.bgra was the old bug)
                img = Image.frombytes("RGB", raw.size, raw.rgb)
                self._frames.append(img)
                wait = interval - (time.perf_counter() - t0)
                if wait > 0:
                    time.sleep(wait)

        if self._recording:
            self._root.after(0, self._stop_recording)

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
            os.startfile(os.path.dirname(path))

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

    # ── Hotkey ────────────────────────────────────────────────────────────
    def _rebind_hotkey(self):
        """Unbind old hotkey (if any) and bind the current one."""
        if self._hotkey_bound:
            try:
                keyboard.remove_hotkey(self._hotkey_bound)
            except Exception:
                pass
            self._hotkey_bound = None
        key = self._settings.hotkey.strip()
        if key:
            try:
                keyboard.add_hotkey(key, self._hotkey_pressed, suppress=False)
                self._hotkey_bound = key
            except Exception as e:
                print(f"[SnapGIF] Could not bind hotkey '{key}': {e}")

    def _hotkey_pressed(self):
        """Called from keyboard thread — schedule on tk main thread."""
        self._root.after(0, self._toggle_record)

    def _toggle_record(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_selection()

    # ── Settings ──────────────────────────────────────────────────────────
    def _open_settings(self):
        SettingsDialog(self._root, self._settings)

    def run(self):
        try:
            self._root.mainloop()
        finally:
            if self._hotkey_bound:
                try:
                    keyboard.remove_hotkey(self._hotkey_bound)
                except Exception:
                    pass


if __name__ == "__main__":
    SnapGIF().run()
