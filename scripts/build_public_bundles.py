"""Assemble the public release bundles (Windows and Linux) reproducibly.

The v0.5.2 bundles were assembled by hand: the staging folders ``winzip/`` and
``linuxzip/`` were populated manually and their manifests written once, so a
rebuild could not be reproduced and a stale file inside a bundle was invisible.
This script rebuilds both bundles from the tree, every time, from
``version.WORKBENCH_VERSION``.

For each platform it:

1. refreshes the staging folder (EULA, platform README, the platform's manuals
   set, and the platform payload);
2. writes ``RELEASE_MANIFEST.txt`` with real SHA-256 digests of what is
   actually in the folder;
3. zips the folder to
   ``release_assets/QectorWorkbench-v{VERSION}-{Platform}-x64-Public.zip``.

A payload that is missing or older than the newest source file is reported and,
unless ``--allow-stale`` is passed, aborts that platform. Shipping a bundle
whose binary predates the source is exactly how a "fixed" release goes out
without the fix.

Usage:
    python scripts/build_public_bundles.py                  # both platforms
    python scripts/build_public_bundles.py --only windows
    python scripts/build_public_bundles.py --allow-stale    # ship anyway
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import version as _version  # noqa: E402

VERSION = _version.WORKBENCH_VERSION
RELEASE_DIR = REPO / "release_assets"
MANUALS = REPO / "manuals"

#: Files copied into every bundle, whatever the platform.
COMMON_FILES = ("EULA.txt", "EULA.rtf", "LICENSE", "SECURITY.md", "CHANGELOG.md")

PLATFORMS = {
    "windows": {
        "staging": REPO / "winzip",
        "readme": "README.md",
        "manual_exclude": ("QECTOR_User_Manual_Linux.pdf", "QECTOR_User_Manual_macOS.pdf"),
        "label": "Windows",
        "payload": [
            (REPO / "dist" / "QectorWorkbench-Portable.exe", "QectorWorkbench-Portable.exe"),
        ],
        "vendored": [
            (REPO / "wheels" / f"qector_decoder_v3-{_version.BACKEND_VERSION}"
                                "-cp311-cp311-win_amd64.whl",
             f"qector_decoder_v3-{_version.BACKEND_VERSION}-cp311-cp311-win_amd64.whl"),
        ],
        "optional": [
            (REPO / "dist" / "QectorWorkbenchSetup.exe", "QectorWorkbenchSetup.exe"),
        ],
    },
    "linux": {
        "staging": REPO / "linuxzip",
        "readme": "README_LINUX.md",
        "manual_exclude": ("QECTOR_User_Manual_Windows.pdf", "QECTOR_User_Manual_macOS.pdf"),
        "label": "Linux",
        # Filled in by _discover_debs(): the build may produce a single package
        # or per-distro variants (ubuntu/antix), and pinning exact filenames
        # here would silently ship nothing when the naming changes.
        "payload": [],
        "optional": [],
        "vendored": [
            # The release zip must make a fully offline lab possible: the
            # README's offline instructions pip-install this wheel, so the
            # wheel has to be in the zip. Same parity as the Windows bundle,
            # which ships the win_amd64 wheel beside the portable exe.
            # Kept in wheels-linux/ (not wheels/) so the PyInstaller specs'
            # wheels/* glob never drags a Linux wheel into the Windows exe.
            (REPO / "wheels-linux" / f"qector_decoder_v3-{_version.BACKEND_VERSION}"
                                "-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
             f"qector_decoder_v3-{_version.BACKEND_VERSION}-cp311-cp311-"
             "manylinux_2_17_x86_64.manylinux2014_x86_64.whl"),
        ],
    },
}


def _discover_debs() -> list[tuple[Path, str]]:
    """Every .deb in dist/ matching the current version."""
    dist = REPO / "dist"
    if not dist.is_dir():
        return []
    found = sorted(dist.glob(f"qector-workbench_{VERSION}_*.deb"))
    return [(p, p.name) for p in found]


PLATFORMS["linux"]["payload"] = _discover_debs()


def _all_wheels(rel_dir: Path) -> list[tuple[Path, str]]:
    """Every decoder wheel staged for a platform (cp39..cp313 of that ABI)."""
    return [(p, p.name) for p in sorted(rel_dir.glob("qector_decoder_v3-*.whl"))]


# Ship every staged wheel in the public zips, not just cp311, so a lab running
# any supported Python (3.9-3.13) can pip-install the decoder fully offline.
PLATFORMS["windows"]["vendored"] = _all_wheels(REPO / "wheels")
PLATFORMS["linux"]["vendored"] = _all_wheels(REPO / "wheels-linux")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: Root-level modules that are build/verification tooling and never end up
#: inside a payload.  Editing one of them (a .deb control-field fix in
#: build_production.py, say) must not mark every payload stale, because no
#: payload embeds them.  generate_manuals.py and api_reference.py are NOT in
#: this set: they ship with the app (APP_MODULES) and DO belong to the
#: staleness comparison.
_TOOLING = frozenset({
    "build_production.py", "test_mcp_all.py",
    "docgen_repro.py", "check_exe_pyz.py",
    "e2e_export_check.py", "exe_toc_sizes.py",
})


def newest_source_mtime() -> float:
    """Newest mtime across the Python sources that end up in a build."""
    newest = 0.0
    for path in REPO.glob("*.py"):
        if path.name in _TOOLING:
            continue
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def refresh_staging(name: str, spec: dict, allow_stale: bool) -> tuple[Path, list[str]]:
    """Populate the staging folder; returns (folder, warnings)."""
    staging: Path = spec["staging"]
    warnings: list[str] = []
    staging.mkdir(parents=True, exist_ok=True)

    # Common documents, always refreshed from the tree.
    for fname in COMMON_FILES:
        src = REPO / fname
        if src.exists():
            shutil.copy2(src, staging / fname)
    readme_src = REPO / spec["readme"]
    if readme_src.exists():
        shutil.copy2(readme_src, staging / "README.md")

    # Manuals, refreshed wholesale so a bundle can never carry a stale subset.
    # manuals.zip is excluded: it is the same documents again, and inside a
    # bundle that already ships manuals/ it was 36% of the Linux zip.
    # manual_exclude drops the user manuals of the OTHER platforms: a Linux
    # download must not carry the Windows and macOS manuals, and vice versa.
    dst_manuals = staging / "manuals"
    if dst_manuals.exists():
        shutil.rmtree(dst_manuals, ignore_errors=True)
    if MANUALS.exists():
        shutil.copytree(
            MANUALS, dst_manuals,
            ignore=shutil.ignore_patterns("manuals.zip", *spec.get("manual_exclude", ())),
        )
    else:
        warnings.append(f"manuals/ not found at {MANUALS}; bundle has no documentation")

    # Drop stale payloads and loose Python source files from staging so no loose .py files remain.
    for stale in list(staging.iterdir()):
        if stale.is_file() and (stale.suffix in {".deb", ".exe", ".whl", ".AppImage", ".py", ".pyc", ".toml", ".txt", ".json", ".rtf"} or stale.is_dir()):
            expected = {n for _s, n in spec["payload"] + spec["optional"] + spec.get("vendored", [])}
            expected.update(COMMON_FILES)
            expected.add("README.md")
            expected.add("RELEASE_MANIFEST.txt")
            expected.add("manuals")
            if stale.name not in expected:
                if stale.is_dir():
                    shutil.rmtree(stale, ignore_errors=True)
                else:
                    stale.unlink()
                warnings.append(f"removed unneeded file from staging: {stale.name}")

    source_mtime = newest_source_mtime()

    def stage(src: Path, arcname: str, required: bool) -> None:
        """Copy one payload in, refusing anything older than the sources.

        The staleness check applies to optional payloads too. It did not at
        first, and a three-week-old installer was silently bundled into a
        release zip: exactly the artifact this check exists to keep out.
        """
        if not src.exists():
            if required:
                warnings.append(f"MISSING payload: {src}")
            return
        age = datetime.fromtimestamp(src.stat().st_mtime)
        if src.stat().st_mtime < source_mtime:
            label = "payload" if required else "optional payload"
            if allow_stale:
                warnings.append(f"bundling STALE {label} {src.name} "
                                f"({age:%Y-%m-%d %H:%M}) because --allow-stale was passed")
            else:
                warnings.append(
                    f"SKIPPED stale {label}: {src.name} ({age:%Y-%m-%d %H:%M}) "
                    f"predates the newest source file; rebuild it or pass --allow-stale"
                )
                # Remove any copy left in staging by an earlier run. Without
                # this the skip is cosmetic: the previous build's file is still
                # sitting there, is still an expected name, and ships anyway.
                leftover = staging / arcname
                if leftover.exists():
                    try:
                        leftover.unlink()
                        warnings.append("  and removed the stale copy already in staging")
                    except Exception as exc:
                        warnings.append(f"  could not remove stale {leftover}: {exc}")
                return
        if src.is_dir():
            dst = staging / arcname
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, staging / arcname)

    for src, arcname in spec["payload"]:
        stage(src, arcname, required=True)
    for src, arcname in spec["optional"]:
        stage(src, arcname, required=False)
    for src, arcname in spec.get("vendored", []):
        if src.exists():
            if src.is_dir():
                dst = staging / arcname
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, staging / arcname)
        else:
            warnings.append(f"MISSING vendored input: {src}")

    return staging, warnings


def write_manifest(staging: Path, label: str) -> Path:
    """Write RELEASE_MANIFEST.txt describing exactly what is in the folder."""
    manifest = staging / "RELEASE_MANIFEST.txt"
    if manifest.exists():
        manifest.unlink()

    entries = sorted(
        (p for p in staging.rglob("*") if p.is_file()),
        key=lambda p: str(p.relative_to(staging)).lower(),
    )
    lines = [
        f"QECTOR Decoder Workbench v{VERSION} - {label} x64 - Public Release",
        "=" * 60,
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Backend:   qector-decoder-v3 {_version.BACKEND_VERSION}",
        f"MCP tools: {_version.MCP_TOOLS}",
        "",
        "SHA-256 checksums:",
        "",
    ]
    for path in entries:
        lines.append(f"{sha256_of(path)}  {path.relative_to(staging).as_posix()}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def build_zip(staging: Path, label: str) -> Path:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = RELEASE_DIR / f"QectorWorkbench-v{VERSION}-{label}-x64-Public.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(staging).as_posix())
    return zip_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", choices=sorted(PLATFORMS), help="Build one platform only.")
    ap.add_argument("--allow-stale", action="store_true",
                    help="Bundle a payload even if it predates the newest source file.")
    args = ap.parse_args(argv)

    targets = [args.only] if args.only else list(PLATFORMS)
    failed = False

    print(f"Building public bundles for v{VERSION}")
    for name in targets:
        spec = PLATFORMS[name]
        print(f"\n--- {spec['label']} ---")
        staging, warnings = refresh_staging(name, spec, args.allow_stale)
        for warning in warnings:
            print(f"  [WARN] {warning}")
        present = [n for _s, n in spec["payload"] + spec.get("vendored", [])
                   if (staging / n).exists()]
        if not present:
            print(f"  [FAIL] no payload staged for {spec['label']}; skipping zip")
            failed = True
            continue
        manifest = write_manifest(staging, spec["label"])
        zip_path = build_zip(staging, spec["label"])
        size_mb = zip_path.stat().st_size / 1024 / 1024
        print(f"  payload:  {', '.join(present)}")
        print(f"  manifest: {manifest.relative_to(REPO)}")
        print(f"  bundle:   {zip_path.relative_to(REPO)}  ({size_mb:.1f} MB)")
        print(f"  sha256:   {sha256_of(zip_path)}")

    if failed:
        print("\nOne or more platforms did not produce a bundle (see FAIL above).")
        return 1
    print("\nPublic bundles ready. Run scripts/update_release_assets.py to refresh "
          "the top-level manifest and checksums.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
