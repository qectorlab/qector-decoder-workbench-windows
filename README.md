# QECTOR Decoder Workbench v1.0.0 - Windows x64 Portable

[![Release](https://img.shields.io/github/v/release/qectorlab/qector-decoder-workbench-windows?label=Latest%20Release&style=flat-square)](https://github.com/qectorlab/qector-decoder-workbench-windows/releases/latest)
[![Platform](https://img.shields.io/badge/Platform-Windows%20x64-blue?style=flat-square)](#install)

Windows build running the `qector-decoder-v3 1.0.0` backend. This is a single-executable distribution requiring no installer, no admin rights, and no internet connection to run.

## 🚀 Install & Quick Start

Download the latest release package: `QectorWorkbench-v1.0.0-Portable-Windows-x64.zip`.

1. Extract the `.zip` archive to a folder of your choice.
2. Double-click **`QectorWorkbench.exe`** to launch the graphical interface.

**Headless MCP Server**
To run the 82-tool `stdio` MCP server (no display needed), launch via command prompt:
```cmd
QectorWorkbench.exe --mcp

```

*Note: Runtime data (logs, exported documents) is written to `%LOCALAPPDATA%\QectorWorkbench`. You can override this behavior by setting the `QECTOR_DATA_DIR` environment variable.*

---

## ✨ What's in this Release

* **15 Decoders & 10 Code Families:** Full support for surface codes, qLDPC (bicycle / bivariate bicycle), heavy-hex, color codes, and hypergraph products, fully wired to the bundled `qector-decoder-v3 1.0.0` backend.
* **82-Tool MCP Server:** `stdio` JSON-RPC 2.0 server for programmatic and AI agent interaction with per-tool 60-second timeouts, busy guards, and 1 MB result limits.
* **Offline Backend Bundling:** Ships with the platform-specific `qector_decoder_v3-1.0.0` wheel pre-packaged inside the executable. Automatically activated into a per-user managed site on first boot; purges outdated managed decoders from older releases automatically.
* **Full CLI Infrastructure:** Adds 12 CLI subcommands (`compare`, `batch`, `stream`, `train`, `export`, `import`, `matrix`, `serve`, `doctor`, `completions`) with shell auto-completion support (Bash, Zsh, PowerShell).
* **Self-Diagnostics & Auto-Debug:** Environment, decoder, and hardware self-tests with resilient multi-decoder fallback, verifying $H \cdot c \equiv s \pmod{2}$ at every step with full attempt tracing.
* **Security Hardening:** License keys encrypted at rest using machine-derived Fernet keys; export path traversal protection via path sanitization.
* **Hardware Dashboard:** Auto-detects CUDA/OpenCL/CPU capability; honest OpenCL status reporting with a working `QECTOR_DISABLE_OPENCL=1` probe-skip escape hatch.
* **Data Export Integrity:** All documentation exports (`.md`, `.html`, `.json`, `.tex`, `.pdf`, `.svg`), benchmark reports, and deposit sidecars (`.zenodo.json`, `CITATION.cff`) include SHA-256 sidecar manifests.
* **Distance Slider:** Extended distance range supporting $d=3$ to $d=63$ (matching the Enterprise tier limit).

---

## 📦 Assets in this Release

* **`QectorWorkbench.exe`**: The application executable; bundles its own Python runtime, scientific stack, and the `qector-decoder-v3==1.0.0` wheel (activated fully offline on first launch).
* **`manuals/`**: Full documentation suite including the API Reference (`.md`, `.html`, `.pdf`), MCP Integration Guide, Quick Start Guide, Windows User Manual, Extended Reference Manual, and the machine-readable `QECTOR_LLM_Manual.json`.
* **`RELEASE_REPORT.md`** & **`RELEASE_MANIFEST.txt`**: Detailed package release report and build manifest.
* **`EULA.txt`** & **`CHANGELOG.md`**: End User License Agreement and complete release history.

---

## 🔒 Verify Your Download

We highly recommend verifying your download using the provided SHA-256 checksums:

```text
8e1c7fea5dfb7f0bcb6ccfd2ec42f5866958a7a1f8405cb70a5be4c1df39fb *QectorWorkbench.exe
0cb025ed429806ac444df5d2c29efd594c69ebd92b9072375903041e98573e66 *EULA.txt
3d014d3ad2635f53cf44fab334602110c525ec49d195c9706a74af9be038b705 *CHANGELOG.md
df7c277081dae7cda5e20f01fef542788c3432b0a7c9e8ff4eb33d1a5dd96fdb *QECTOR_Decoder_v3_v1.0.0_User_Manual.md

```

**How to verify on Windows PowerShell:**
You can verify individual files:

```powershell
Get-FileHash -Algorithm SHA256 .\QectorWorkbench.exe

```

Or verify the complete `.zip` package against its known hash:

```powershell
(Get-FileHash .\QectorWorkbench-v1.0.0-Portable-Windows-x64.zip -Algorithm SHA256).Hash.ToLower()

```

---

*Full documentation is included inside the `manuals/` directory within the `.zip` release. Visit the main repository at [qectorlab/qector-decoder-workbench-windows](https://www.google.com/search?q=https://github.com/qectorlab/qector-decoder-workbench-windows/) for more information.*

```

```
