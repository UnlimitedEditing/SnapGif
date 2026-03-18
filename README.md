# SnapGIF 🎬
*Tiny floating screen-to-GIF recorder for Windows 10*

---

## Quick Start

### 1. Install Python (once)
Download from https://python.org — tick **"Add Python to PATH"** during install.

### 2. Install dependencies (once)
Open a terminal in this folder and run:
```
pip install Pillow mss
```

### 3. Launch
Double-click **`SnapGIF.bat`**  
*(or run `pythonw snapgif.py` directly)*

---

## How to Use

| Action | What happens |
|---|---|
| **Drag** the widget | Move it anywhere on screen — it stays on top |
| **⏺ REC** | Screen dims; drag to draw the region you want to record |
| **ESC** during selection | Cancels without recording |
| **⏹ STOP** | Stops recording and saves the GIF |
| **⚙** | Opens settings |
| **✕** | Closes SnapGIF |

The **green dot** blinks while recording.  
A **yellow dot** means saving is in progress.  
A **solid green dot** means the GIF was saved successfully.

---

## Settings

| Setting | Default | Notes |
|---|---|---|
| FPS | 10 | Higher = smoother but larger file |
| Max duration | 30 sec | Recording auto-stops at this limit |
| Output folder | Desktop | Where GIFs are saved |

GIFs are named `snap_YYYYMMDD_HHMMSS.gif`.

---

## Tips for Telegram sharing

- **10 FPS, small region** → compact file, sends fast
- **5 FPS** for mostly-static UI demonstrations
- **15–24 FPS** for animations or fast mouse movements
- Telegram supports GIFs up to ~50 MB; typical screen snippets are 1–5 MB

---

## Requirements

- Windows 10 (uses `-transparentcolor` and DPI APIs)
- Python 3.9+
- `Pillow` ≥ 9.0
- `mss` ≥ 9.0
