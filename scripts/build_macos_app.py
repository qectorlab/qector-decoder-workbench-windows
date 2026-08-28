#!/usr/bin/env python3
"""
build_macos_app.py — Bundles QECTOR Decoder Workbench into macOS .app and .dmg packages.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_BUNDLE = DIST / "QectorWorkbench.app"
CONTENTS = APP_BUNDLE / "Contents"
MACOS_BIN = CONTENTS / "MacOS"
RESOURCES = CONTENTS / "Resources"

def build_macos_bundle():
    print("========================================================================")
    print(" Building macOS Application Bundle: QectorWorkbench.app")
    print("========================================================================")
    
    if APP_BUNDLE.exists():
        shutil.rmtree(APP_BUNDLE)
        
    MACOS_BIN.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)
    
    # 1. Info.plist
    info_src = ROOT / "macOS" / "Info.plist"
    if info_src.exists():
        shutil.copy(info_src, CONTENTS / "Info.plist")
        
    # 2. Launcher executable script
    launcher = MACOS_BIN / "qector-workbench"
    launcher_content = """#!/bin/sh
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
export PYTHONPATH="$DIR:$PYTHONPATH"
exec python3 "$DIR/main.py" "$@"
"""
    with open(launcher, "w") as f:
        f.write(launcher_content)
    os.chmod(launcher, 0o755)

    # 3. Copy application source files & resources into Resources
    for item in ["main.py", "app.py", "backend.py", "cli.py", "mcp_server.py", "compliance.py", "version.py", "utils.py", "theme.py", "EULA.txt", "README.md"]:
        src = ROOT / item
        if src.exists():
            shutil.copy(src, RESOURCES / item)
            
    if (ROOT / "assets").exists():
        shutil.copytree(ROOT / "assets", RESOURCES / "assets", dirs_exist_ok=True)
        shutil.copy(ROOT / "assets" / "icon.png", RESOURCES / "icon.png")

    if (ROOT / "wheels-macos").exists():
        shutil.copytree(ROOT / "wheels-macos", RESOURCES / "wheels-macos", dirs_exist_ok=True)

    print(f"macOS .app bundle structure created successfully: {APP_BUNDLE}")
    
    # 4. Tarball packaging
    release_dir = DIST / "release_assets"
    release_dir.mkdir(parents=True, exist_ok=True)
    tar_path = release_dir / "QectorWorkbench-v1.0.4-macOS-Universal.tar.gz"
    
    subprocess.run(["tar", "-czvf", str(tar_path), "-C", str(DIST), "QectorWorkbench.app"], check=True)
    print(f"macOS standalone release tarball created: {tar_path}")

    # 5. Create DMG if hdiutil is available (native macOS)
    if shutil.which("hdiutil"):
        dmg_path = release_dir / "QectorWorkbench-v1.0.4-macOS-Universal.dmg"
        subprocess.run(["hdiutil", "create", "-volname", "QectorWorkbench", "-srcfolder", str(APP_BUNDLE), "-ov", "-format", "UDZO", str(dmg_path)], check=True)
        print(f"macOS DMG image created: {dmg_path}")

if __name__ == "__main__":
    build_macos_bundle()
