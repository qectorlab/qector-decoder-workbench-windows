#!/usr/bin/env python3
"""
build_macos_app.py — Builds a flawless, standalone macOS .app bundle and drag-and-drop .dmg installer.
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
RELEASE_ASSETS = DIST / "release_assets"

def build_macos_bundle():
    print("========================================================================")
    print(" Building macOS Application Bundle & Drag-and-Drop .dmg Installer")
    print("========================================================================")
    
    RELEASE_ASSETS.mkdir(parents=True, exist_ok=True)
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

    # 3. Copy application source modules into Resources
    MODULES = [
        "main.py", "app.py", "backend.py", "cli.py", "mcp_server.py", 
        "compliance.py", "version.py", "utils.py", "theme.py", 
        "decoder_provisioner.py", "code_explorer_tab.py", "decoder_lab_tab.py",
        "benchmark_tab.py", "batch_streaming_tab.py", "history_tab.py",
        "hardware_tab.py", "diagnostics_tab.py", "documentation_tab.py",
        "lab_info_tab.py", "console.py", "dialogs.py", "errors.py",
        "doc_generator.py", "docs_exporter.py", "state.py", "results_tracker.py",
        "EULA.txt", "README.md"
    ]
    for item in MODULES:
        src = ROOT / item
        if src.exists():
            shutil.copy(src, RESOURCES / item)
            
    if (ROOT / "assets").exists():
        shutil.copytree(ROOT / "assets", RESOURCES / "assets", dirs_exist_ok=True)
        shutil.copy(ROOT / "assets" / "icon.png", RESOURCES / "icon.png")

    if (ROOT / "wheels-macos").exists():
        shutil.copytree(ROOT / "wheels-macos", RESOURCES / "wheels-macos", dirs_exist_ok=True)

    print(f"  [OK] macOS .app bundle created: {APP_BUNDLE}")
    
    # 4. Tarball packaging
    tar_path = RELEASE_ASSETS / "QectorWorkbench-v1.0.4-macOS-Universal.tar.gz"
    subprocess.run(["tar", "-czvf", str(tar_path), "-C", str(DIST), "QectorWorkbench.app"], check=True)
    print(f"  [OK] macOS tarball created: {tar_path}")

    # 5. Drag-and-Drop DMG Installer creation with /Applications symlink
    dmg_staging = DIST / "dmg_staging"
    if dmg_staging.exists():
        shutil.rmtree(dmg_staging)
    dmg_staging.mkdir(parents=True, exist_ok=True)

    # Copy .app to staging
    shutil.copytree(APP_BUNDLE, dmg_staging / "QectorWorkbench.app", symlinks=True)

    # Create Applications link inside staging
    app_link = dmg_staging / "Applications"
    if not app_link.exists():
        try:
            os.symlink("/Applications", app_link)
        except Exception:
            pass

    dmg_path = RELEASE_ASSETS / "QectorWorkbench-v1.0.4-macOS-Universal.dmg"
    if shutil.which("hdiutil"):
        print("  [INFO] Packaging .dmg installer using native hdiutil...")
        if dmg_path.exists():
            dmg_path.unlink()
        subprocess.run([
            "hdiutil", "create",
            "-volname", "QECTOR Workbench",
            "-srcfolder", str(dmg_staging),
            "-ov",
            "-format", "UDZO",
            str(dmg_path)
        ], check=True)
        print(f"  [SUCCESS] macOS Drag-and-Drop .dmg created: {dmg_path}")
    else:
        print("  [INFO] hdiutil not found (non-macOS host). DMG staging layout prepared.")

if __name__ == "__main__":
    build_macos_bundle()
