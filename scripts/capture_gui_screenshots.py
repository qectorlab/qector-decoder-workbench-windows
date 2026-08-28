#!/usr/bin/env python3
"""
capture_gui_screenshots.py — Renders high-fidelity GUI screenshots of QECTOR Decoder Workbench v1.0.4.
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import version
from app import QectorApp, _START_MAXIMIZED

def capture():
    print("Capturing live QECTOR Decoder Workbench v1.0.4 GUI UI screenshots...")
    import customtkinter as ctk
    from PIL import ImageGrab, Image, ImageDraw, ImageFont

    app = QectorApp(start_maximized=False)
    app._app.update_idletasks()
    app._app.update()

    # Get window geometry
    x = app._app.winfo_rootx()
    y = app._app.winfo_rooty()
    w = app._app.winfo_width()
    h = app._app.winfo_height()

    artifacts_dir = Path(r"C:\Users\Admin\.gemini\antigravity\brain\413118e5-1b8a-4a04-8c3e-888db97a7abb")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    img_path = artifacts_dir / "macos_v104_gui_main.png"
    
    try:
        # Take screenshot of app window region
        bbox = (x, y, x + w, y + h)
        img = ImageGrab.grab(bbox=bbox)
        img.save(img_path)
        print(f"Captured live GUI window screenshot to: {img_path}")
    except Exception as e:
        print(f"ImageGrab fallback: {e}")
        
    try:
        app.destroy()
    except Exception:
        pass

if __name__ == "__main__":
    capture()
