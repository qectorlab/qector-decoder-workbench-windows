# QECTOR Decoder Workbench v1.0.1 - Windows x64 Portable

[![Release](https://img.shields.io/github/v/release/qectorlab/qector-decoder-workbench-windows?label=Latest%20Release&style=flat-square)](https://github.com/qectorlab/qector-decoder-workbench-windows/releases/latest)
[![Platform](https://img.shields.io/badge/Platform-Windows%20x64-blue?style=flat-square)](#install)

Public Windows build of QECTOR Decoder Workbench. The release package contains the portable Windows executable, the complete manuals set, release manifests, license text, citation metadata, and SHA-256 checksums.

Verified on August 19, 2026 from the packaged executable:

| Item | Value |
|:--|:--|
| Workbench app | `1.0.1` |
| Decoder backend | `qector-decoder-v3 1.0.0` bundled wheel |
| MCP server | `85` tools over stdio JSON-RPC 2.0 |
| MCP protocol | `2024-11-05` |
| Decoders | `17` |
| Code families | `10` |
| Bundled Python runtime | Python `3.12.0` |

## Install

Download `QectorWorkbench-v1.0.1-Windows-x64-Public.zip` from the latest release, extract it, and run:

```cmd
QectorWorkbench-Portable.exe
```

To run the headless MCP server:

```cmd
QectorWorkbench-Portable.exe --mcp
```

Runtime data is written to the user data directory by default. Set `QECTOR_DATA_DIR` to place logs, generated reports, and other runtime files in a controlled location.

## Release Contents

The public zip contains:

| Path | Purpose |
|:--|:--|
| `QectorWorkbench-Portable.exe` | Portable Windows application executable |
| `manuals/` | API reference, MCP integration guide, user manuals, LLM manual JSON, and figures |
| `AIR_GAPPED_HARDENING_STATUS.md` | Current air-gapped hardening status and remaining work |
| `README.md` | Package overview and launch instructions |
| `EULA.txt` | End User License Agreement |
| `CITATION.cff` | Citation metadata |
| `RELEASE_MANIFEST.txt` | File inventory for the package |
| `SHA256SUMS.txt` | SHA-256 checksums for packaged files |

No source `.py` files, development folders, build caches, `_internal` directory dumps, test data, or private runtime data are included in the public repository or release package.

## Feature Summary

- 17 decoder algorithms, including `space_time`, `blossom`, `sparse_blossom`, `bp_osd`, `belief_matching`, `gnn_belief_matching`, `hybrid_cascade`, `two_stage`, `ambiguity_cluster`, and `colour_code`.
- 10 code families: repetition, ring, rotated surface, unrotated surface, toric, heavy-hex, bicycle, bivariate bicycle, hypergraph product, and color code.
- 85-tool MCP server for local AI/agent workflows using stdio only.
- Offline backend bundling: the `qector-decoder-v3 1.0.0` wheel is included with the application and activated locally on first launch.
- Documentation exports and reproducibility helpers include checksum-oriented release metadata.

## Air-Gapped Hardening Development Status

The v1.0.1 public package is designed for offline lab use after download and extraction. Current hardening work is tracked in `AIR_GAPPED_HARDENING_STATUS.md`.

Implemented in this package:

- Bundled decoder wheel activation works without internet access.
- MCP transport is stdio only; the packaged MCP mode does not bind an HTTP port.
- Version checks resolve against the bundled local baseline instead of a network update service.
- Runtime data can be redirected with `QECTOR_DATA_DIR`.
- Public packaging excludes source scripts, development folders, caches, internal build trees, and test data.

Still in development:

- Signed release attestations and reproducible-build provenance.
- Formal enterprise hardening guide for locked-down lab images.
- Independent third-party security review.

## Verify

Use the release `SHA256SUMS.txt` file to verify the zip and each packaged file. On Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 .\QectorWorkbench-v1.0.1-Windows-x64-Public.zip
Get-FileHash -Algorithm SHA256 .\QectorWorkbench-Portable.exe
```

## Links

- Release repository: [qectorlab/qector-decoder-workbench-windows](https://github.com/qectorlab/qector-decoder-workbench-windows)
- Product site: [qector.store](https://www.qector.store)
