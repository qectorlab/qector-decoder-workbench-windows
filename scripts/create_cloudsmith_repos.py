#!/usr/bin/env python3
"""
create_cloudsmith_repos.py — Creates 3 OS-isolated Cloudsmith repositories:
  1. qector-decoder-workbench-linux   (Debian .deb, .tar.gz, AppImage, RPM)
  2. qector-decoder-workbench-windows (Portable .exe, .msix, WinGet)
  3. qector-decoder-workbench-docs    (User Manuals, API references, SBOMs, SLSA Attestations)
"""
import os
import sys
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_ASSETS = ROOT / "dist" / "release_assets"

REPOS = {
    "qector-decoder-workbench-linux": {
        "name": "qector-decoder-workbench-linux",
        "description": "QECTOR Decoder Workbench - Linux Distributions (.deb, .tar.gz, AppImage, RPM)",
        "repository_type_str": "Public",
        "slug": "qector-decoder-workbench-linux"
    },
    "qector-decoder-workbench-windows": {
        "name": "qector-decoder-workbench-windows",
        "description": "QECTOR Decoder Workbench - Windows Distributions (.exe, .msix, WinGet)",
        "repository_type_str": "Public",
        "slug": "qector-decoder-workbench-windows"
    },
    "qector-decoder-workbench-docs": {
        "name": "qector-decoder-workbench-docs",
        "description": "QECTOR Decoder Workbench - Documentation, Manuals, SBOMs and Attestations",
        "repository_type_str": "Public",
        "slug": "qector-decoder-workbench-docs"
    }
}

def create_repos():
    api_key = os.environ.get("CLOUDSMITH_API_KEY")
    if not api_key:
        print("ERROR: CLOUDSMITH_API_KEY environment variable is not set!")
        sys.exit(1)
        
    print("========================================================================")
    print(" Creating 3 OS-Isolated Repositories on Cloudsmith (qector)")
    print("========================================================================")
    
    for repo_slug, config in REPOS.items():
        cfg_file = ROOT / f".cloudsmith_{repo_slug}.json"
        with open(cfg_file, "w") as f:
            json.dump(config, f, indent=2)
            
        print(f"Creating repository: qector/{repo_slug}...")
        res = subprocess.run(["cloudsmith", "repos", "create", "qector", str(cfg_file)], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"  [OK] Repository qector/{repo_slug} created successfully!")
        else:
            print(f"  [INFO] Repository qector/{repo_slug} creation response: {res.stdout.strip() or res.stderr.strip()}")

def push_artifacts():
    print("========================================================================")
    print(" Pushing OS-Specific Packages to Cloudsmith Repositories")
    print("========================================================================")
    
    # 1. Linux Repo
    deb_pkg = ROOT / "dist" / "qector-workbench_1.0.4_amd64.deb"
    if deb_pkg.exists():
        print(f"Pushing Linux Debian package to qector/qector-decoder-workbench-linux...")
        subprocess.run([
            "cloudsmith", "push", "deb", "qector/qector-decoder-workbench-linux/any-distro/any-version",
            str(deb_pkg), "--tags", "qec,quantum,mcp,zero-egress,linux,linux-x86_64,deb", "--republish"
        ])
        
    tar_pkg = DIST_ASSETS / "QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz"
    if tar_pkg.exists():
        print(f"Pushing Linux Tarball to qector/qector-decoder-workbench-linux...")
        subprocess.run([
            "cloudsmith", "push", "raw", "qector/qector-decoder-workbench-linux",
            str(tar_pkg), "--name", "QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz",
            "--summary", "QECTOR Decoder Workbench v1.0.4 (Linux x86_64) - Standalone Linux Distribution (.tar.gz)",
            "--tags", "qec,quantum,mcp,zero-egress,linux,linux-x86_64,tarball", "--republish"
        ])
        
    # 2. Windows Repo (if files exist on host)
    exe_pkg = DIST_ASSETS / "QectorWorkbench-v1.0.4-Windows-x64.zip"
    if not exe_pkg.exists():
        exe_pkg = ROOT / "dist" / "QectorWorkbench-v1.0.4-Windows-x64.zip"
    if exe_pkg.exists():
        print(f"Pushing Windows Package to qector/qector-decoder-workbench-windows...")
        subprocess.run([
            "cloudsmith", "push", "raw", "qector/qector-decoder-workbench-windows",
            str(exe_pkg), "--name", "QectorWorkbench-v1.0.4-Windows-x64.zip",
            "--summary", "QECTOR Decoder Workbench v1.0.4 (Windows x64) - Portable Distribution (.zip)",
            "--tags", "qec,quantum,mcp,zero-egress,windows,windows-x64,exe", "--republish"
        ])

    # 3. Docs & Attestations Repo
    sbom_file = DIST_ASSETS / "qector-workbench_1.0.4_sbom.json"
    if sbom_file.exists():
        print(f"Pushing SPDX SBOM to qector/qector-decoder-workbench-docs...")
        subprocess.run([
            "cloudsmith", "push", "raw", "qector/qector-decoder-workbench-docs",
            str(sbom_file), "--name", "qector-workbench_1.0.4_sbom.json",
            "--summary", "SPDX v2.3 Software Bill of Materials (SBOM) for QECTOR v1.0.4",
            "--tags", "sbom,spdx,security,compliance", "--republish"
        ])

    slsa_file = DIST_ASSETS / "attestation.intoto.jsonl"
    if slsa_file.exists():
        print(f"Pushing SLSA Level 3 Attestation to qector/qector-decoder-workbench-docs...")
        subprocess.run([
            "cloudsmith", "push", "raw", "qector/qector-decoder-workbench-docs",
            str(slsa_file), "--name", "attestation.intoto.jsonl",
            "--summary", "SLSA Level 3 Provenance Attestation for QECTOR v1.0.4",
            "--tags", "slsa,provenance,attestation,security", "--republish"
        ])

    manual_pdf = ROOT / "manuals" / "QECTOR_User_Manual_Linux.pdf"
    if manual_pdf.exists():
        print(f"Pushing User Manual PDF to qector/qector-decoder-workbench-docs...")
        subprocess.run([
            "cloudsmith", "push", "raw", "qector/qector-decoder-workbench-docs",
            str(manual_pdf), "--name", "QECTOR_User_Manual_Linux.pdf",
            "--summary", "Official QECTOR User Manual (Linux)",
            "--tags", "documentation,manual,pdf,linux", "--republish"
        ])

if __name__ == "__main__":
    create_repos()
    push_artifacts()
