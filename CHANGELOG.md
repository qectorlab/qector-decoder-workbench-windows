# Changelog

## 1.0.2

- **Air-Gapped Lab Certification**: Full offline compliance with sanitized documentation, offline-only licensing, and zero external pricing/purchase endpoints.
- **Window Management**: Window maximize, restore, and OS native control decorations restored across all desktop environments.
- **Hardened Runtime & Ad-Hoc Code Signing**: macOS app bundle configured with runtime entitlements and validated via `codesign --verify`.
- **Software Bill of Materials (SBOM)**: Cryptographic SHA-256 manifest generated during packaging for lab compliance audits.
- **Cross-Platform Uniformity**: macOS, Windows, and Linux releases synchronized to identical core source runtime.

## 1.0.1

- Windows, Linux, and macOS source trees share the same application runtime.
- Portable builds enforce mandatory zero-egress execution.
- External-link buttons and browser actions were removed from the application.
- Entra ID posture is fail-closed and reports disabled in air-gapped labs.
- MCP registry is synchronized at 85 tools.
- Decoder, DEM, matrix, memmap, import, CLI, and documentation workflows are
  covered by the release test suite.
- Benchmark measurements are no longer stored or shipped; benchmark runs are
  local to the target hardware.
- Release inputs use canonical `README.md`, `EULA.txt`, and platform assets.
