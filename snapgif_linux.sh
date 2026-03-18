#!/usr/bin/env bash
# SnapGIF Linux launcher
set -e
pip install --quiet Pillow mss pynput 2>/dev/null || true
python3 "$(dirname "$0")/snapgif_linux.py" &
