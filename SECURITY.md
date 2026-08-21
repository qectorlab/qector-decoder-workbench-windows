# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | Yes       |
| 0.5.x   | Security fixes only |
| < 0.5   | No        |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email **admin@qector.store** with:

1. A description of the vulnerability
2. Steps to reproduce (or a proof-of-concept)
3. The affected version(s)
4. Any suggested fix, if you have one

## Response Timeline

| Stage | Target |
|-------|--------|
| Acknowledgement | Within 48 hours |
| Initial assessment | Within 5 business days |
| Fix development | Within 14 business days for critical issues |
| Public disclosure | After the fix is released, coordinated with the reporter |

## Disclosure Policy

- We follow **coordinated disclosure**: the vulnerability is kept private until a fix is available.
- Once a fix is released, we will publish a security advisory on the GitHub repository.
- Credit will be given to the reporter unless they request anonymity.
- We will not take legal action against researchers who report vulnerabilities in good faith.

## Scope

The following are in scope:

- The QECTOR Decoder Workbench application (GUI, CLI, MCP server)
- The `qector-decoder-v3` backend library
- Export/import file handling and path traversal
- License key storage and validation
- The MCP JSON-RPC transport layer

The following are out of scope:

- Vulnerabilities in third-party dependencies (report to the upstream project)
- Denial-of-service attacks requiring local access
- Social engineering attacks

## Security Measures

The QECTOR Decoder Workbench implements:

- **Path traversal protection**: All export paths are sanitized via `utils.sanitize_export_path()`, rejecting `..` components and absolute paths outside the export directory.
- **License key encryption**: License keys stored at `~/.qector/license.key` are encrypted using Fernet with a machine-derived key, preventing plaintext exposure.
- **Zero-egress attestation (v1.0.1)**: An AST scan of the shipped Python surface (`compliance.scan_python_surface`) attests that no unguarded network or telemetry imports ship in the app; `compliance.compliance_report()` produces a machine-readable attestation for infosec review.
- **EgressGuard (v1.0.1)**: In air-gap mode (`QECTOR_AIRGAP` / `QECTOR_OFFLINE`, or any frozen bundle) a runtime guard blocks DNS resolution and connections to non-loopback hosts, raises `EgressBlockedError`, and logs every attempt with a stack trace to `logs/egress.log`. Loopback stays allowed for local services such as `qector serve`.
- **Optional Entra ID sign-in (v1.0.1)**: Off by default; zero-egress by default (`msal` is imported lazily, only inside `login()`). Hard-disabled whenever air-gap mode is active. Token cache and configuration are encrypted at rest with the machine-derived Fernet key.
- **Input length limits**: Profile fields, file paths, and decoder parameters are bounded to prevent resource exhaustion.
- **No eval/exec**: The codebase contains no `eval()` or `exec()` calls on user input.
- **HTML escaping**: All user-provided content is escaped before interpolation into HTML reports.
- **File permissions**: License key files are written with mode 0o600 where the OS supports it.

## Contact

Security inquiries: **admin@qector.store**
