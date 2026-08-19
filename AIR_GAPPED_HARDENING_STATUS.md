# Air-Gapped Hardening Development Status

Status date: 2026-08-19

This document records the current public hardening status for QECTOR Decoder Workbench v1.0.1. It is intended for offline and controlled-network lab deployments.

## Verified Release Baseline

| Item | Value |
|:--|:--|
| Workbench app | `1.0.1` |
| Decoder backend | `qector-decoder-v3 1.0.0` bundled wheel |
| MCP server | `85` tools |
| MCP protocol | `2024-11-05` over stdio |
| Decoders | `17` |
| Code families | `10` |

The Windows executable was checked with `QectorWorkbench-Portable.exe --version` and by launching `QectorWorkbench-Portable.exe --mcp`, initializing the JSON-RPC server, and calling `tools/list`.

## Implemented

- The decoder backend is shipped as a bundled wheel and activated from local package data on first launch.
- The published MCP server runs over stdin/stdout JSON-RPC and does not require an HTTP listener.
- Version status resolves against the local bundled baseline rather than a network update service.
- Runtime output can be redirected with `QECTOR_DATA_DIR` for controlled storage policies.
- Public packaging excludes source `.py` files, development folders, build caches, internal PyInstaller trees, test data, and private runtime data.
- Release packages include `RELEASE_MANIFEST.txt` and `SHA256SUMS.txt` for file inventory and checksum verification.

## In Development

- Signed release attestations and reproducible-build provenance.
- A formal deployment checklist for disconnected enterprise and lab images.
- Independent third-party security review.
- Optional organization policy templates for approved runtime directories and MCP client configuration.

## Not Claimed

- This package is not currently advertised as FIPS-certified.
- This package is not currently advertised as independently penetration-tested.
- The release repository is not a source distribution; it contains public release metadata only.
