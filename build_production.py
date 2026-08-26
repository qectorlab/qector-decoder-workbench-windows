#!/usr/bin/env python3
"""
build_production.py -- QECTOR Decoder Workbench Production Build
================================================================
Creates final release artefacts.  The Windows executables embed the decoder
(qector-decoder-v3) as a .whl data file; decoder_provisioner extracts and
activates it offline on first launch.  The .deb is a thin application-layer
package: the scientific stack comes from the system, and the decoder is
provisioned on first launch (offline labs pre-install the bundled manylinux
wheel).

Outputs:
  1. dist/QectorWorkbench-Portable.exe      (Windows single-file portable)
  2. dist/QectorWorkbench/                   (Windows onedir for Inno Setup)
  3. dist/qector-workbench_<version>_amd64/  (.deb package tree)

Usage:
  python build_production.py              # build all for current OS
  python build_production.py --exe        # Windows .exe only
  python build_production.py --deb        # .deb package tree only
  python build_production.py --all        # both
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import version

APP_NAME = "QectorWorkbench"
APP_VERSION = version.WORKBENCH_VERSION
BACKEND_VER = version.BACKEND_VERSION
DEB_PACKAGE = "qector-workbench"
MAINTAINER = "Guillaume Lessard <admin@qector.store>"
DESCRIPTION = "QECTOR Decoder Workbench: quantum error-correction analysis suite"
HOMEPAGE = "https://www.qector.store"

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")

APP_MODULES = [
    "main.py", "app.py", "backend.py", "cli.py", "mcp_server.py",
    "autodebug.py", "decoder_provisioner.py", "doc_generator.py",
    "theme.py", "figure_cache.py", "i18n.py", "state.py", "version.py",
    "version_service.py", "utils.py", "logger.py", "console.py",
    "hardware_routing.py", "mcp_resources.py", "dialogs.py",
    "threading_utils.py", "results_tracker.py",
    "code_explorer_tab.py", "decoder_lab_tab.py", "benchmark_tab.py",
    "batch_streaming_tab.py", "hardware_tab.py", "diagnostics_tab.py",
    "documentation_tab.py", "lab_info_tab.py", "history_tab.py", "generate_manuals.py",
    "api_reference.py", "docs_exporter.py",
    # v1.0.1: zero-egress enforcement + optional Entra ID SSO (enterprise)
    "compliance.py", "entra_auth.py",
]
DATA_FILES = ["icon.jpg", "icon.ico", "icon.png", "EULA.txt", "README.md", "requirements.txt"]

# Root-level modules that exist for building/testing only. The installed app
# must never import them, and the .deb never ships them.
BUILD_TOOLING = {"build_production", "test_mcp_all"}

# Platform-specific offline wheels
WHEEL_FILES = {
    "windows": f"wheels\\qector_decoder_v3-{BACKEND_VER}-cp311-cp311-win_amd64.whl",
    "linux": f"wheels-linux/qector_decoder_v3-{BACKEND_VER}-cp311-cp311-"
             "manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
}

_IMPORT_RX = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", re.M)


def _check_module_closure(shipped: list) -> None:
    """Fail the build when a first-party import of a shipped module is unshipped.

    The .deb contains exactly the modules in APP_MODULES. A module they import
    but which is absent from that list simply does not exist for the installed
    app: v0.5.3 first shipped without lab_info_tab.py, so the installed GUI
    crashed at startup with ModuleNotFoundError while the frozen exe (which
    discovers its own imports) was fine. Scans transitively, over top-level
    and in-function imports alike.
    """
    first_party = {p.stem for p in Path(ROOT).glob("*.py")}
    shipped_stems = {Path(m).stem for m in shipped}
    missing: dict = {}
    seen: set = set()
    stack = list(shipped_stems)
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        src = Path(ROOT) / f"{mod}.py"
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in _IMPORT_RX.findall(text):
            if name not in first_party or name in BUILD_TOOLING:
                continue
            if name not in seen:
                stack.append(name)
            if name not in shipped_stems:
                missing.setdefault(mod, set()).add(name)
    if missing:
        detail = "; ".join(f"{src}.py imports {sorted(names)}"
                           for src, names in sorted(missing.items()))
        print(f"\n  [FAIL] .deb would ship without: {detail}")
        print("         Add those modules to APP_MODULES (or to BUILD_TOOLING")
        print("         if they really are build-only tooling).")
        sys.exit(1)


def banner(msg):
    print(f"\n{'=' * 72}\n  {msg}\n{'=' * 72}\n")


def run(cmd, **kw):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT, **kw)


def kill_running_instances():
    """A running .exe holds a file lock and makes PyInstaller silently fail."""
    if platform.system() != "Windows":
        return
    for name in (f"{APP_NAME}-Portable.exe", f"{APP_NAME}.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)


def assert_fresh(path, t_start, label):
    """Fail loudly if the artefact is missing or was not rewritten by this build."""
    if not os.path.isfile(path):
        print(f"\n  [FAIL] {label} was NOT produced: {path}")
        sys.exit(1)
    if os.path.getmtime(path) < t_start:
        print(f"\n  [FAIL] {label} is STALE (not rewritten by this build): {path}")
        sys.exit(1)
    mb = os.path.getsize(path) / (1024 * 1024)
    print(f"\n  [OK] {label}: {path}  ({mb:.1f} MB, freshly built)")


def rmtree_safe(path):
    if not os.path.exists(path):
        return

    def _onerr(_f, _p, _e):
        os.chmod(_p, stat.S_IWRITE)
        os.unlink(_p)

    shutil.rmtree(path, onerror=_onerr)


# ---------------------------------------------------------------------------
# Windows .exe
# ---------------------------------------------------------------------------
def build_exe(dev: bool = False):
    banner("Windows .exe Build (PyInstaller -- bundled offline decoder wheel)")
    print("  The decoder (qector-decoder-v3) IS bundled as a .whl data file.")
    print("  decoder_provisioner extracts it offline on first launch.\n")

    def pyi_command(spec: str) -> list:
        cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm"]
        if dev:
            cmd.append("--noupx")  # devv1 §7.3: skip UPX for faster iteration
        return cmd + [spec]

    for mod in ["customtkinter", "cryptography", "PyInstaller"]:
        if importlib.util.find_spec(mod) is None:
            print(f"  [ERR] {mod} not installed")
            sys.exit(1)
        print(f"  [OK] {mod}")

    print("  Closing any running QectorWorkbench instances (file locks)...")
    kill_running_instances()

    t0 = time.time()

    # Onefile portable .exe
    banner("Building QectorWorkbench-Portable.exe (single file, bundled wheel)")
    spec = os.path.join(ROOT, "QectorWorkbench-onefile.spec")
    r = run(pyi_command(spec))
    if r.returncode != 0:
        print(f"\n  [FAIL] PyInstaller onefile build exited {r.returncode}")
        sys.exit(1)

    exe = os.path.join(DIST, f"{APP_NAME}-Portable.exe")
    assert_fresh(exe, t0, "Portable .exe")

    # Onedir (for Inno Setup installer)
    banner("Building QectorWorkbench/ directory (bundled wheel)")
    spec = os.path.join(ROOT, "QectorWorkbench.spec")
    r = run(pyi_command(spec))
    if r.returncode != 0:
        print(f"\n  [FAIL] PyInstaller onedir build exited {r.returncode}")
        sys.exit(1)

    d = os.path.join(DIST, APP_NAME, f"{APP_NAME}.exe")
    assert_fresh(d, t0, "Onedir launcher")

    print(f"\n  Build time: {time.time() - t0:.0f}s")



# ---------------------------------------------------------------------------
# Debian .deb
# ---------------------------------------------------------------------------
def build_deb():
    banner("Debian .deb Package (thin app layer; decoder provisioned on first launch)")

    deb_name = f"{DEB_PACKAGE}_{APP_VERSION}_amd64"
    deb_root = os.path.join(DIST, deb_name)
    rmtree_safe(deb_root)

    install = os.path.join(deb_root, "opt", "qector-workbench")
    bindir = os.path.join(deb_root, "usr", "local", "bin")
    desktop = os.path.join(deb_root, "usr", "share", "applications")
    icondir = os.path.join(deb_root, "usr", "share", "icons", "hicolor", "256x256", "apps")
    debian = os.path.join(deb_root, "DEBIAN")
    for d in [install, bindir, desktop, icondir, debian]:
        os.makedirs(d, exist_ok=True)

    # Copy app modules
    print("  Copying application modules...")
    for mod in APP_MODULES:
        src = os.path.join(ROOT, mod)
        if os.path.isfile(src):
            shutil.copy2(src, install)
            print(f"    [OK] {mod}")
    _check_module_closure(APP_MODULES)
    
    # Copy platform-appropriate wheels for offline capability
    print("  Copying decoder wheels for offline bundling...")
    wheel_dir = None
    if platform.system() == "Windows":
        wheel_dir = os.path.join(ROOT, "wheels")
    elif platform.system() == "Linux":
        wheel_dir = os.path.join(ROOT, "wheels-linux")

    wheel_paths: list[str] = []
    if wheel_dir and os.path.isdir(wheel_dir):
        for fn in sorted(os.listdir(wheel_dir)):
            if fn.startswith("qector_decoder_v3-") and fn.endswith(".whl"):
                wheel_paths.append(os.path.join(wheel_dir, fn))

    if wheel_paths:
        offline = os.path.join(install, "offline_wheel")
        os.makedirs(offline, exist_ok=True)
        for wp in wheel_paths:
            dst = os.path.join(offline, os.path.basename(wp))
            shutil.copy2(wp, dst)
            print(f"    [OK] Copied offline wheel: {os.path.basename(wp)}")

            # Modify provisioner instructions to use local wheel
            provisioner_path = os.path.join(install, "decoder_provisioner.py")
            if os.path.exists(provisioner_path):
                with open(provisioner_path, "r", encoding="utf-8") as f:
                    content = f.read()
                ref_wheel = os.path.basename(wp)
                new_content = content.replace(
                    "Install from PyPI",
                    f"Install from bundled wheel: offline_wheel/{ref_wheel}"
                )
                new_content = new_content.replace(
                    "pip_install_qector()",
                    "local_wheel = os.path.join(workbench_root, 'offline_wheel', os.path.basename(WHEEL_PATH))\n"
                    "            run_pip_install(['-I', '--no-warn-conflicts', local_wheel])"
                )
                with open(provisioner_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                    print("    [OK] Updated decoder_provisioner.py to use local wheel")
    else:
        print("    [WARN] No offline wheels found")

    # Copy data files
    print("  Copying data files...")
    for df in DATA_FILES:
        src = os.path.join(ROOT, df)
        if os.path.isfile(src):
            shutil.copy2(src, install)
            print(f"    [OK] {df}")

    # Icon
    src = os.path.join(ROOT, "icon.png")
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(icondir, "qector-workbench.png"))

    # DEBIAN/control
    with open(os.path.join(debian, "control"), "w", newline="\n") as f:
        f.write(textwrap.dedent(f"""\
            Package: {DEB_PACKAGE}
            Version: {APP_VERSION}
            Section: science
            Priority: optional
            Architecture: amd64
            Depends: python3 (>= 3.10), python3-pip, python3-numpy, python3-scipy, python3-matplotlib, python3-pil, python3-tk
            Suggests: python3-customtkinter
            Maintainer: {MAINTAINER}
            Homepage: {HOMEPAGE}
            Description: {DESCRIPTION}
             Backend engine: qector-decoder-v3 v{BACKEND_VER} (Rust/PyO3 core)
             with 16 decoders, 10 code families and a 56-tool MCP server. This
             package is the application layer: the scientific stack comes from
             your distribution (see Depends). The decoder backend is
             provisioned into a per-user site on first launch; on a machine
             without internet, pip-install the manylinux wheel from the release
             bundle first.
             .
             Decoders: union_find, fast_union_find, blossom, sparse_blossom,
             bp_osd, auto, hybrid, lookup_table, predecoded, auto_router,
             hybrid_cascade, gnn_belief_matching, belief_matching, two_stage,
             ambiguity_cluster, colour_code.
             .
             Code families: repetition, ring, rotated_surface, unrotated_surface,
             toric, heavy_hex, bicycle, bivariate_bicycle, hypergraph_product,
             color_code.
        """))

    # DEBIAN/postinst -- real pip install, multiple fallback methods
    postinst = textwrap.dedent(f"""\
        #!/bin/bash
        set -e

        INSTALL_DIR="/opt/qector-workbench"
        DECODER_PKG="qector-decoder-v3"
        DECODER_VER="{BACKEND_VER}"

        echo "================================================================"
        echo "  QECTOR Decoder Workbench v{APP_VERSION}"
        echo "  Backend: $DECODER_PKG v$DECODER_VER (live install from PyPI)"
        echo "================================================================"

        # ---------- Step 1: Install Python dependencies ----------
        echo ""
        echo "[1/3] Installing Python dependencies from requirements.txt..."
        if [ -f "$INSTALL_DIR/requirements.txt" ]; then
            install_deps() {{
                pip3 install --break-system-packages -r "$INSTALL_DIR/requirements.txt" 2>/dev/null && return 0
                pip3 install -r "$INSTALL_DIR/requirements.txt" 2>/dev/null && return 0
                python3 -m pip install -r "$INSTALL_DIR/requirements.txt" 2>/dev/null && return 0
                pip3 install --user -r "$INSTALL_DIR/requirements.txt" 2>/dev/null && return 0
                return 1
            }}
            if install_deps; then
                echo "  [OK] Dependencies installed"
            else
                echo "  [WARN] Could not auto-install dependencies."
                echo "    Run manually: pip3 install -r $INSTALL_DIR/requirements.txt"
            fi
        fi

        # ---------- Step 2: Live install qector-decoder-v3 from PyPI ----------
        echo ""
        echo "[2/3] Downloading and installing $DECODER_PKG >= $DECODER_VER from PyPI..."

        install_decoder() {{
            pip3 install --break-system-packages "$DECODER_PKG>=$DECODER_VER" 2>/dev/null && return 0
            pip3 install "$DECODER_PKG>=$DECODER_VER" 2>/dev/null && return 0
            python3 -m pip install "$DECODER_PKG>=$DECODER_VER" 2>/dev/null && return 0
            pip3 install --user "$DECODER_PKG>=$DECODER_VER" 2>/dev/null && return 0
            pip3 install --break-system-packages --user "$DECODER_PKG>=$DECODER_VER" 2>/dev/null && return 0
            return 1
        }}

        if install_decoder; then
            echo "  [OK] $DECODER_PKG installed successfully from PyPI"
        else
            echo "  [WARN] Automatic install failed. Install manually:"
            echo '    pip3 install "$DECODER_PKG>=$DECODER_VER"'
        fi


        # Verify
        if python3 -c "import qector_decoder_v3; print(f'  [OK] Verified: qector_decoder_v3 v{{qector_decoder_v3.__version__}}')" 2>/dev/null; then
            :
        else
            echo "  [WARN] Decoder not yet importable. The app will auto-install on first launch."
        fi

        # ---------- Step 3: Set permissions ----------
        echo ""
        echo "[3/3] Setting permissions..."
        chmod +x "$INSTALL_DIR/main.py" 2>/dev/null || true
        chmod +x /usr/local/bin/qector-workbench 2>/dev/null || true

        echo ""
        echo "  ================================================"
        echo "  [OK] Installation complete!"
        echo ""
        echo "  Launch GUI:    qector-workbench"
        echo "  CLI mode:      qector-workbench --cli diagnostics"
        echo "  MCP server:    qector-workbench --mcp"
        echo "  ================================================"
        echo ""
    """)

    p = os.path.join(debian, "postinst")
    with open(p, "w", newline="\n") as f:
        f.write(postinst)
    os.chmod(p, 0o755)

    # /usr/local/bin/qector-workbench launcher
    launcher = textwrap.dedent("""\
        #!/bin/bash
        cd /opt/qector-workbench
        exec python3 main.py "$@"
    """)
    launcher_path = os.path.join(bindir, "qector-workbench")
    with open(launcher_path, "w", newline="\n") as f:
        f.write(launcher)
    os.chmod(launcher_path, 0o755)

    # Desktop entry
    with open(os.path.join(desktop, "qector-workbench.desktop"), "w", newline="\n") as f:
        f.write(textwrap.dedent("""\
            [Desktop Entry]
            Name=QECTOR Decoder Workbench
            Comment=Quantum Error Correction decoder workbench
            Exec=/usr/local/bin/qector-workbench
            Icon=qector-workbench
            Terminal=false
            Type=Application
            Categories=Science;Education;Math;
            Keywords=quantum;error;correction;decoder;QEC;
            StartupNotify=true
            Version=1.0
        """))

    # Build .deb if dpkg-deb available
    deb_file = os.path.join(DIST, f"{deb_name}.deb")
    if shutil.which("dpkg-deb"):
        # dpkg-deb cannot record correct permissions when the tree lives on a
        # Windows mount (/mnt/... in WSL). Use build_deb_wsl.sh in that case.
        if sys.platform.startswith("linux") and deb_root.startswith("/mnt/"):
            print("\n  [INFO] dpkg-deb is available but the package tree is on a Windows mount.")
            print("         Skipping dpkg-deb here to avoid permission issues.")
            print("         Run 'bash build_deb_wsl.sh' in WSL to build the final .deb.")
        else:
            run(["dpkg-deb", "--build", "--root-owner-group", deb_root, deb_file], check=False)
            if os.path.isfile(deb_file):
                mb = os.path.getsize(deb_file) / (1024 * 1024)
                print(f"\n  [OK] .deb: {deb_file}  ({mb:.1f} MB)")
                print(f"       Install: sudo dpkg -i {os.path.basename(deb_file)}")
    else:
        print(f"\n  [INFO] dpkg-deb not on this OS. Package tree at: {deb_root}")
        print(f"         On Linux run: dpkg-deb --build --root-owner-group {deb_name} {deb_name}.deb")
        print("         From Windows run: wsl -d <distro> -- bash -lc \"cd '/mnt/d/QECTOR APP' && bash build_deb_wsl.sh\"")
        # A leftover .deb from an earlier run is the dangerous case: the tree is
        # fresh, the .deb is not, and nothing says so.  Call it out explicitly.
        if os.path.isfile(deb_file) and os.path.getmtime(deb_file) < os.path.getmtime(deb_root):
            print(f"\n  [STALE] {os.path.basename(deb_file)} is OLDER than the package tree just built.")
            print("          It does NOT contain these changes. Rebuild it with build_deb_wsl.sh")
            print("          before shipping, or delete it so it cannot be mistaken for current.")

    fc = sum(len(files) for _, _, files in os.walk(deb_root))
    print(f"\n  Package tree: {fc} files")



# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify():
    banner("Post-Build Verification")
    checks = []
    exe = os.path.join(DIST, f"{APP_NAME}-Portable.exe")
    if os.path.isfile(exe):
        checks.append(("Portable .exe", f"[OK] {os.path.getsize(exe) / (1024 * 1024):.1f} MB", exe))
    else:
        checks.append(("Portable .exe", "--  not built", ""))

    d = os.path.join(DIST, APP_NAME, f"{APP_NAME}.exe")
    if os.path.isfile(d):
        checks.append(("Onedir launcher", f"[OK] {os.path.getsize(d) / (1024 * 1024):.1f} MB", d))
    else:
        checks.append(("Onedir launcher", "--  not built", ""))

    deb = os.path.join(DIST, f"{DEB_PACKAGE}_{APP_VERSION}_amd64")
    ctrl = os.path.join(deb, "DEBIAN", "control")
    if os.path.isfile(ctrl):
        fc = sum(len(files) for _, _, files in os.walk(deb))
        checks.append((".deb package tree", f"[OK] {fc} files", deb))
    else:
        checks.append((".deb package tree", "--  not built", ""))

    print(f"  {'Artefact':<25} {'Status':<20} Path")
    print(f"  {'-' * 25} {'-' * 20} {'-' * 50}")
    for n, s, p in checks:
        print(f"  {n:<25} {s:<20} {p}")

    print(f"\n  Platform:  {platform.system()} {platform.machine()}")
    print(f"  Python:    {sys.version.split()[0]}")
    print(f"  App:       {APP_NAME} v{APP_VERSION}")
    print(f"  Backend:   qector-decoder-v3 v{BACKEND_VER}  (bundled wheel, offline-ready)")
    print("  Strategy:  decoder wheel bundled; provisioned offline on first launch")


# ---------------------------------------------------------------------------
# Checksum manifest
# ---------------------------------------------------------------------------
def generate_checksum_manifest():
    """Generate SHA-256 checksums for all release artefacts in dist/."""
    banner("Generating SHA-256 Checksum Manifest")
    dist_path = Path(DIST)
    if not dist_path.is_dir():
        print("  [SKIP] dist/ directory does not exist")
        return

    artefacts = []
    for p in sorted(dist_path.rglob("*")):
        if p.is_file():
            artefacts.append(p)

    if not artefacts:
        print("  [SKIP] No artefacts in dist/")
        return

    sha256_lines = []
    for artefact in artefacts:
        rel = artefact.relative_to(dist_path)
        h = hashlib.sha256(artefact.read_bytes()).hexdigest()
        sha256_lines.append(f"{h}  {rel}")
        print(f"  {h[:16]}...  {rel}")

    manifest = dist_path / "checksums-sha256.txt"
    manifest.write_text("\n".join(sha256_lines) + "\n", encoding="utf-8")
    print(f"\n  [OK] Manifest written: {manifest}")
    print(f"       {len(artefacts)} artefacts, {manifest.stat().st_size} bytes")


# ---------------------------------------------------------------------------
# Artifact signing
# ---------------------------------------------------------------------------
def sign_artifacts():
    """Sign release artefacts with GPG (detached signatures).

    Requires GPG installed and a signing key available.  When GPG is
    unavailable the step is skipped with a clear warning -- checksum
    manifest is still useful for integrity verification.
    """
    banner("Artifact Signing (GPG)")
    dist_path = Path(DIST)
    if not dist_path.is_dir():
        print("  [SKIP] dist/ directory does not exist")
        return

    gpg = shutil.which("gpg") or shutil.which("gpg2")
    if gpg is None:
        print("  [WARN] GPG not found on PATH -- signatures NOT generated")
        print("         Install GPG and set QECTOR_SIGNING_KEY env var to sign artefacts")
        print("         Integrity verified via SHA-256 checksums only")
        return

    signing_key = os.environ.get("QECTOR_SIGNING_KEY", "")
    if not signing_key:
        print("  [WARN] QECTOR_SIGNING_KEY env var not set -- skipping GPG signing")
        print("         Set it to your GPG key ID or email to enable artifact signing")
        return

    target_exts = {".exe", ".deb", ".whl", ".zip", ".tar.gz"}
    signed = 0
    for artefact in sorted(dist_path.rglob("*")):
        if not artefact.is_file():
            continue
        if artefact.suffix not in target_exts and not artefact.name.endswith("-Portable.exe"):
            continue
        cmd = [gpg, "--batch", "--yes", "--detach-sign", "--armor",
               "--local-user", signing_key, str(artefact)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            sig_path = Path(str(artefact) + ".asc")
            print(f"  [OK] Signed: {artefact.name} -> {sig_path.name}")
            signed += 1
        else:
            print(f"  [WARN] Failed to sign {artefact.name}: {r.stderr.strip()}")

    if signed:
        print(f"\n  [OK] Signed {signed} artefacts")
    else:
        print("\n  [SKIP] No artefacts signed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="QECTOR Production Builder")
    parser.add_argument("--exe", action="store_true")
    parser.add_argument("--deb", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dev", action="store_true",
                        help="faster iteration: skip UPX compression in PyInstaller builds")
    parser.add_argument("--no-sign", action="store_true",
                        help="skip GPG artifact signing")
    parser.add_argument("--no-checksum", action="store_true",
                        help="skip SHA-256 checksum manifest generation")
    args = parser.parse_args()
    do_all = args.all or (not args.exe and not args.deb)

    banner(f"QECTOR Decoder Workbench v{APP_VERSION} -- Production Build (bundled decoder wheel)")
    print("  Strategy:  decoder wheel bundled; provisioned offline on first launch")
    print(f"  Platform:  {platform.system()} {platform.machine()}")
    print(f"  Python:    {sys.version.split()[0]}")

    if args.exe or do_all:
        if platform.system() == "Windows":
            build_exe(dev=args.dev)
        else:
            print("\n  [SKIP] .exe build only on Windows")

    if args.deb or do_all:
        build_deb()

    verify()
    if not args.no_checksum:
        generate_checksum_manifest()
    if not args.no_sign:
        sign_artifacts()
    banner("BUILD COMPLETE")


if __name__ == "__main__":
    main()
