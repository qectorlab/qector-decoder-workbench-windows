#!/usr/bin/env python3
"""
build_appimage.py — Automates AppImage creation for QECTOR Decoder Workbench.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APPDIR = DIST / "QectorWorkbench.AppDir"

def build_appimage():
    print("========================================================================")
    print(" Building Portable AppImage: QectorWorkbench-v1.0.4-x86_64.AppImage")
    print("========================================================================")
    
    if APPDIR.exists():
        shutil.rmtree(APPDIR)
        
    (APPDIR / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (APPDIR / "usr" / "share" / "qector-workbench").mkdir(parents=True, exist_ok=True)
    (APPDIR / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps").mkdir(parents=True, exist_ok=True)
    (APPDIR / "usr" / "share" / "applications").mkdir(parents=True, exist_ok=True)
    
    # Copy source files into AppDir
    for item in ["main.py", "app.py", "backend.py", "cli.py", "mcp_server.py", "compliance.py", "version.py", "utils.py", "theme.py", "EULA.txt", "README.md"]:
        src = ROOT / item
        if src.exists():
            shutil.copy(src, APPDIR / "usr" / "share" / "qector-workbench" / item)
            
    if (ROOT / "assets").exists():
        shutil.copytree(ROOT / "assets", APPDIR / "usr" / "share" / "qector-workbench" / "assets", dirs_exist_ok=True)
        shutil.copy(ROOT / "assets" / "icon_256.png", APPDIR / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "qector-workbench.png")
        shutil.copy(ROOT / "assets" / "icon_256.png", APPDIR / "qector-workbench.png")

    if (ROOT / "wheels-linux").exists():
        shutil.copytree(ROOT / "wheels-linux", APPDIR / "usr" / "share" / "qector-workbench" / "wheels-linux", dirs_exist_ok=True)

    # AppRun entry point
    apprun_content = """#!/bin/sh
HERE="$(dirname "$(readlink -f "${0}")")"
export PYTHONPATH="${HERE}/usr/share/qector-workbench:${PYTHONPATH}"
exec python3 "${HERE}/usr/share/qector-workbench/main.py" "$@"
"""
    with open(APPDIR / "AppRun", "w") as f:
        f.write(apprun_content)
    os.chmod(APPDIR / "AppRun", 0o755)

    # Desktop file
    desktop_content = """[Desktop Entry]
Name=QECTOR Decoder Workbench
Comment=Quantum Error Correction Decoder Suite with 85-tool MCP Server
Exec=qector-workbench
Icon=qector-workbench
Terminal=false
Type=Application
Categories=Development;Science;Quantum;
"""
    with open(APPDIR / "qector-workbench.desktop", "w") as f:
        f.write(desktop_content)
    shutil.copy(APPDIR / "qector-workbench.desktop", APPDIR / "usr" / "share" / "applications" / "qector-workbench.desktop")

    print(f"AppDir prepared successfully at: {APPDIR}")
    print("Packaging AppImage with appimagetool (if installed)...")
    
    appimage_out = DIST / "release_assets" / "QectorWorkbench-v1.0.4-x86_64.AppImage"
    appimage_out.parent.mkdir(parents=True, exist_ok=True)
    
    if shutil.which("appimagetool"):
        subprocess.run(["appimagetool", str(APPDIR), str(appimage_out)], check=True)
        print(f"AppImage successfully generated: {appimage_out}")
    else:
        print("NOTE: appimagetool not found on PATH. AppDir layout created ready for packaging.")

if __name__ == "__main__":
    build_appimage()
