# Changelog

## 1.0.3

- **Security hardening**: MCP `get_server_env` redacts secrets; MCP token auth
  uses constant-time comparison and gates `tools/list`/`tools/call` behind an
  authenticated `initialize`; Entra token cache storage fails closed instead of
  silently falling back to plaintext base64; the REST API refuses non-loopback
  binds without `--allow-remote`; child processes no longer inherit the license
  key via the environment; VNC container requires a build-time password and
  binds to loopback only.
- **Reliability**: MCP tool execution reuses a persistent executor (no thread
  leak, real timeout semantics); egress log writes valid JSONL; shared server
  state is lock-guarded; version cache writes are atomic; `UiPump` post/close
  race removed.
- **CLI**: `python cli.py` now works directly (`__main__` guard); global flags
  such as `--json` are accepted both before and after any subcommand; `probe`
  wiring fixed.
- **UI**: Code Explorer action buttons moved to their own row (no truncation);
  apps boot maximized with native window buttons instead of borderless
  fullscreen; no double dashes anywhere in app UI, docs, or generated documents.
- **Supply chain**: runtime dependencies pinned; Dockerfile installs only from
  bundled wheels (`--no-index`); wheel extraction guards against zip-slip.

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
