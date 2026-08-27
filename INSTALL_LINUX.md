# QECTOR Decoder Workbench v1.0.3 - Linux

The Linux release is a portable, air-gapped application. It includes the decoder wheel locally and never downloads packages or contacts an external service at runtime. Available as a standalone portable binary and a Debian package.

## Portable Binary (Recommended)

```bash
# From the release zip:
unzip QectorWorkbench-Linux-v1.0.3.zip
chmod +x QectorWorkbench-Portable
./QectorWorkbench-Portable
```

The portable binary contains the GUI, CLI, MCP server, documentation generators, and the bundled `qector_decoder_v3-1.0.0` wheel. First launch activates the wheel into a per-user managed site (`~/.local/share/QectorWorkbench/decoder_site/<abi_tag>`); it does not use PyPI and works fully offline.

## Debian Package

```bash
sudo dpkg -i qector-workbench_1.0.3_amd64.deb
qector-workbench          # GUI
qector-workbench --cli diagnostics
qector-workbench --mcp    # 85-tool MCP server
```

The `.deb` declares `python3 (>= 3.10)` dependencies and ships the application layer to `/opt/qector-workbench`. The decoder backend is provisioned on first launch from the bundled `offline_wheel/` manylinux wheel (no internet required). For air-gapped labs without internet, the portable binary is pre-provisioned and needs no `dpkg` install.

## Verification

```bash
./QectorWorkbench-Portable --cli --json entra status
./QectorWorkbench-Portable --cli --json compliance
./QectorWorkbench-Portable --mcp
# Debian install equivalent:
qector-workbench --cli --json entra status
qector-workbench --cli --json compliance
qector-workbench --mcp
```

Expected Entra state is `disabled` with `airgapped: true`. Expected compliance state is `compliant: true` with an active egress guard.

## Local Data

Runtime state is stored under `~/.local/share/QectorWorkbench` (or `$XDG_DATA_HOME/QectorWorkbench` if set). Override it with `QECTOR_DATA_DIR` when a lab policy requires an explicitly managed location.
No cloud synchronization, telemetry, update check, or browser action is performed by the application.

## Checksums (v1.0.3)

```
SHA-256 (ZIP):      TBD  QectorWorkbench-Linux-v1.0.3.zip
SHA-256 (Portable): TBD  QectorWorkbench-Portable
```

Verify locally: `sha256sum QectorWorkbench-Portable` and `sha256sum QectorWorkbench-Linux-v1.0.3.zip`.

## Hardware Measurements

Benchmark results are not included in the release. Run benchmarks locally on the target machine when hardware-specific measurements are required. Test evidence for this build: `627 passed, 18 skipped, 0 failed` on Ubuntu 24.04.4 LTS, Python 3.12.3 (11.96 s)  -  see `test_results_linux.txt`.

## Support Files

- `EULA.txt` - license terms
- `SBOM-linux.json` - Software Bill of Materials with SHA-256 and provenance
- `test_results_linux.txt` - full pytest report
- `screenshots/` - 10 GUI tab screenshots
- `checksums-sha256.txt` - release integrity manifest (when generated via `build_production.py`)
