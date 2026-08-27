"""Generate Winget manifests for QECTOR Decoder Workbench v1.0.4 using Azure storage URLs."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.4"
AZURE_URL = "https://qectordist23386.blob.core.windows.net/releases/windows/v1.0.4/QectorWorkbench-v1.0.4-Windows-x64-Public.zip"
ZIP_PATH = ROOT / "release_assets" / f"QectorWorkbench-v{VERSION}-Windows-x64-Public.zip"

if ZIP_PATH.exists():
    sha256 = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
else:
    sha256 = "0cbe09788be103168c61052f6c68384de859fc1b9a93b08de4005e609c96a1f0"

out_dir = ROOT / "winget" / "manifests" / "q" / "qectorlab" / "qector-decoder-workbench" / VERSION
out_dir.mkdir(parents=True, exist_ok=True)

# 1. Version manifest
version_yaml = f"""PackageIdentifier: qectorlab.qector-decoder-workbench
PackageVersion: {VERSION}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: 1.6.0
"""
(out_dir / "qectorlab.qector-decoder-workbench.yaml").write_text(version_yaml, encoding="utf-8")

# 2. Installer manifest
installer_yaml = f"""PackageIdentifier: qectorlab.qector-decoder-workbench
PackageVersion: {VERSION}
MinimumOSVersion: 10.0.0.0
InstallModes:
  - interactive
  - silent
Installers:
  - Architecture: x64
    InstallerType: zip
    InstallerUrl: {AZURE_URL}
    InstallerSha256: {sha256}
ManifestType: installer
ManifestVersion: 1.6.0
"""
(out_dir / "qectorlab.qector-decoder-workbench.installer.yaml").write_text(installer_yaml, encoding="utf-8")

# 3. Locale manifest
locale_yaml = f"""PackageIdentifier: qectorlab.qector-decoder-workbench
PackageVersion: {VERSION}
PackageLocale: en-US
Publisher: iD01t Productions / Guillaume Lessard
PublisherUrl: https://www.qector.store
PublisherSupportUrl: https://www.qector.store
Author: Guillaume Lessard
PackageName: QECTOR Decoder Workbench
PackageUrl: https://www.qector.store
License: Source-available
LicenseUrl: https://github.com/qectorlab/qector-decoder-workbench-windows/blob/main/EULA.txt
ShortDescription: QECTOR Decoder Workbench - Quantum error-correction analysis suite
Description: High-performance quantum error-correction (QEC) decoding workbench powered by qector-decoder-v3 v1.0.0.
Tags:
  - quantum
  - qec
  - decoder
  - error-correction
  - surface-code
  - quantum-computing
ManifestType: defaultLocale
ManifestVersion: 1.6.0
"""
(out_dir / "qectorlab.qector-decoder-workbench.locale.en-US.yaml").write_text(locale_yaml, encoding="utf-8")

print(f"[OK] Winget manifests created in: {out_dir}")
print(f"     Installer URL: {AZURE_URL}")
print(f"     SHA256:        {sha256}")
