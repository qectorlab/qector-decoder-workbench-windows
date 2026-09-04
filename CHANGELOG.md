## 1.0.7 - 2026-09-04

- **Aggressive MCP Capability Control**: Never expose 80+ tools in the global system prompt. Clustered into `qec.decode`/`qec.benchmark`/`qec.verify`/`qec.export` (3-6 tools per phase) with `always` control tools (`list_tools`, `mcp_status`). Per-call `task_scope` override and `initialize(task_scope=...)` scoping with `tools/list(task_scope=...)` filtering. Enforcement in `_ToolRegistry.execute` and `_handle_tools_call`.
- **Explicit Schema Versioning**: Strict `x-schema-version`/`x-manifest-version` `1.0.0` on every `inputSchema`/`outputSchema` with self-contained schemas (no `$schema` network fetch). Each tool exposes `capabilities`/`schema_version`/`manifest_version`.
- **Immutable Experiment Manifests**: Every `tools/call` now bound to `manifest_version`/`schema_version`/`engine`/`engine_git_hash` (baked `__version_hash__` with `FileNotFoundError`/`CalledProcessError` fallback for air-gapped wheels/bundles where `.git` is absent)/`engine_semver`/`workbench_version`/`backend_version`/`decoder_route`/`parity_check_matrix_digest` (SHA-256 of `parity_check_matrix` or `check_to_qubits`)/`seed`/`execution_seeds`/`hardware_target`+`hardware_path`/`fingerprint` (SHA-256 of canonical `tool+params` excluding volatile `request_id`/`task_scope`).
# Changelog

## 1.0.7 - 2026-08-29

- **Ed25519-Signed Certification Artifacts**: Per-boot certification JSON is now cryptographically signed (Ed25519), not merely self-hashed; `.sig` sidecars and public-key embedding added for auditor verification.
- **Real Hardware-Fingerprint Authorization**: `hpc_slurm_generator.py`'s `authorized` field is now a real computed boolean backed by an offline per-machine allowlist (`qector lab status|authorize|revoke|list` CLI added), replacing a hardcoded `True`.
- **EgressGuard Self-Compliance Fix**: `compliance.py`'s own zero-egress AST scanner no longer flags itself as non-compliant; added functional `airgap_mode()` guards to six previously-unguarded internal patch functions.
- **GPG Release Signing Enabled**: `build_production.py`'s `sign_artifacts()` now fails loudly (not silently) when signing is configured but GPG is unavailable, and now also signs `checksums-sha256.txt` and the SBOM, not just binaries. A real RSA-4096 release-signing key is provisioned in Azure Key Vault (`qector-release-signing`).
- **CI Signing Failure Now Fails the Build**: Removed a `|| true` in the Linux release workflow that was silently swallowing Cosign signing failures.
- **MCP Cancellation Support**: Long-running MCP tools (benchmark, batch decode) now support real in-flight cancellation via a `request_id`/`cancel_request` mechanism, backed by a thread-safe server-side registry.
- **Fixed Cancellation Correctness Bugs**: `CancelToken.is_set()` added (previously missing, would have crashed on first real use); `run_benchmark()` no longer fabricates plausible-looking statistics from uninitialized memory when cancelled mid-run.
- **Hardened Path Denylist**: `sanitize_export_path` extended to block additional sensitive system directories (Program Files, System32\config, /root, ~/.ssh, ~/.gnupg, /var, etc).
- **Entra Secret Scrubbing Extended**: `_scrubbed_env()` now covers all four `QECTOR_ENTRA_*` environment variables, with a test that introspects `entra_auth.py`'s real constants to prevent future drift.
- **Removed Hardcoded Cloudsmith API Key**: `delete_105.py` now reads `CLOUDSMITH_API_KEY` from the environment instead of a hardcoded plaintext key; `scripts/check_secrets.py`'s secret-scanning patterns fixed to be case-insensitive (previously missed all-caps `API_KEY`-style assignments).
- **Documentation Accuracy**: `AGENT.md` decoder count corrected (17â†’19 wired decoders, matching `backend.DECODER_KINDS` live).

## 1.0.5 - 2026-08-28

- **Release Integrity & SemVer 2.0.0**: Version bump to v1.0.5 ensuring strict immutable release tags and cryptographic SHA256 checksum consistency across all distribution mirrors.
- **Pure Standalone Portable Executable**: Shipped `QectorWorkbench-Portable.exe` as primary zero-install binary asset for Windows distributions.
- **Purged AI Dashes**: Sanitized all em-dashes (`-`) and en-dashes (`-`) to standard hyphens (`-`) across all repository documentation.
- **Clean 6-7 File Distribution Structure**: Standardized release repositories (`qector-decoder-workbench-linux`, `qector-decoder-workbench-macos`, `qector-decoder-workbench-windows`) with OS-specific manifests and native logo banners.

## 1.0.4

- **EULA Display**: Fixed EULA display and acceptance dialog flow to ensure license agreement prompts show properly and preferences persist cleanly.
- **Export Security & Path Sanitization**: Fixed `utils.sanitize_export_path` to permit valid user-selected export paths (e.g. saving to Desktop or custom directories via file dialogs) while maintaining strict directory traversal (`..`) protection and system directory guards.
- **CLI Boot Test & Terminal Window**: Registered `test` in CLI commands, automatically attaching/allocating a terminal console window on Windows when launched, and streaming full verbose test output after EULA verification.
- **Boot Test Scheduling**: Fixed boot test runner scheduling logic to ensure background boot tests run reliably on workbench startup.
- **Documentation & Versioning**: Updated all version references to v1.0.4 across core files, manuals, and platform packages.

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

