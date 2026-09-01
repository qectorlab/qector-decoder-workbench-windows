<p align="center">
  <img src="assets/logo_banner.png" alt="QECTOR Logo" width="80%" />
</p>

<h1 align="center">QECTOR Decoder Workbench (Windows Release)</h1>

<p align="center">
  <strong>Professional Quantum Error Correction Analysis Suite</strong><br/>
  <em>19 Decoders · 10 Code Families · 85-tool MCP Server · GPU Acceleration</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.6-0078D4?style=for-the-badge&logo=windows&logoColor=white" alt="Version"/>
  <img src="https://img.shields.io/badge/backend-v1.0.0_(Rust%2FPyO3)-E44D26?style=for-the-badge&logo=rust&logoColor=white" alt="Backend"/>
  <img src="https://img.shields.io/badge/python-=3.11-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/MCP_Tools-85-8A2BE2?style=for-the-badge" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/binaries-Windows_|_Linux-success?style=for-the-badge" alt="Platform"/>
  <img src="https://img.shields.io/badge/license-Source--Available-FFA500?style=for-the-badge" alt="License"/>
</p>

<p align="center">
  <a href="https://www.qector.store">Website</a> ·
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-downloads">Downloads</a> ·
  <a href="CHANGELOG.md">CHANGELOG</a> ·
  <a href="#-license">License</a>
</p>

---

## 🚀 Overview

**QECTOR Decoder Workbench** is a production-grade desktop application for quantum error correction (QEC) research, evaluation, and documentation. Built on the high-performance `qector-decoder-v3` Rust/PyO3 engine, it provides interactive decoding, batch simulation, hardware-accelerated compute, and a full local-only MCP server for LLM/AI agent integration.

> **Zero Install · Zero Config · Zero Dependencies**
> Download. Double-click. Decode.

> This repository is a **release-only distribution mirror** of the v1.0.6
> Windows build. It contains no source files · only the release artifacts listed
> below. Binary packages are published on the
> [Releases](https://github.com/qectorlab/qector-decoder-workbench-windows/releases)
> page.

---

## ⚡ Quick Start

### Portable `.exe` (Recommended)

```
1.  Download  QectorWorkbench-v1.0.6-Windows-x64-Public.zip  from Releases
2.  Extract the archive to a folder of your choice
3.  Double-click  QectorWorkbench-Portable.exe  · no installation required
4.  The bundled decoder activates automatically on first run
```

### CLI Mode

```bash
# Launch directly from the portable .exe
QectorWorkbench-Portable.exe decode --family rotated_surface --distance 5 --decoder blossom
QectorWorkbench-Portable.exe benchmark --family toric --distance 7 --samples 10000
QectorWorkbench-Portable.exe diagnostics
```

### MCP Server (AI/LLM Integration)

```bash
QectorWorkbench-Portable.exe --mcp
```

Launches a stdio JSON-RPC 2.0 MCP server with all **86 tools**. The server is
local-only and communicates through stdio; it does not open an external network
connection. No window is required to run the headless MCP mode.

> **Note for Claude users:** To seamlessly integrate these MCP tools directly
> into Claude Desktop or Claude Code, check out the official plugin at
> [https://github.com/GuillaumeLessard/qector-claude-plugin](https://github.com/GuillaumeLessard/qector-claude-plugin)
> · both the Windows and Linux releases of this workbench are fully compatible
> with it. See the [Claude Plugin Compatibility](#-claude-plugin-compatibility)
> section below.

---

## 📥 Downloads

**v1.0.6 release assets** (all available on the
[Releases](https://github.com/qectorlab/qector-decoder-workbench-windows/releases/tag/v1.0.6)
page, each with SHA-256 digests in `SHA256SUMS.txt`):

| Artifact | Contents |
|:---------|:---------|
| **`QectorWorkbench-v1.0.6-Windows-x64-Public.zip`** | `QectorWorkbench-Portable.exe` + `qector_decoder_v3` 1.0.0 wheel (5 wheels) + `manuals/` (Windows) + `EULA.txt`/`EULA.rtf` + `LICENSE` + `CITATION.cff` + `RELEASE_MANIFEST.txt` + `SHA256SUMS.txt` + `SBOM.json` |
| **`QectorWorkbench-Portable.exe`** | The Windows executable on its own, if you do not want the bundle |

> **Fully local, no network required.** The portable executable embeds the decoder
> wheel and provisions it into a per-user managed site on first launch, so a lab
> machine with no internet access runs the complete workbench — including the
> MCP server. The same wheel ships loose in the bundle for labs that would
> rather `pip install` the decoder into their own Python.

**Runtime data** (logs, exported documents, managed decoder site) is written to
`%LOCALAPPDATA%\QectorWorkbench`. Override the location entirely with the
`QECTOR_DATA_DIR` environment variable.

macOS requires a build on Apple hardware and is not included until that build
is produced and signed. Windows and Linux artifacts are built from the same
air-gapped source policy and include SHA-256 manifests.

### Verified v1.0.6 Build Facts

| Item | Value |
|:-----|:------|
| Workbench app | `1.0.6` |
| Decoder backend | `qector-decoder-v3 1.0.0` bundled wheel |
| MCP server | `85` tools over stdio JSON-RPC 2.0 |
| MCP protocol | `2024-11-05` |
| Decoders | `19` |
| Code families | `10` |
| Bundled Python runtime | Python `3.12.0` |

### Repository Files

| File | Purpose |
|:-----|:--------|
| `README.md` | This document |
| `CHANGELOG.md` | Release history |
| `EULA.txt` | End User License Agreement |
| `EULA.rtf` | End User License Agreement (RTF) |
| `LICENSE` | Proprietary license |
| `SECURITY.md` | Security policy |
| `CODE_OF_CONDUCT.md` | Code of conduct |
| `CONTRIBUTING.md` | Contribution guidelines |
| `assets/logo_banner.png` | Project banner |

---

##  Features

###  Nine Interactive Tabs + Live Console

<table>
<tr>
<td width="50%">

#### 🟢 Code Explorer
Build and inspect **10 code families** with configurable parameters. View qubit/check counts, distance, code rate, and interactive Tanner graph visualizations.

#### 🧪 Decoder Lab
Interactive single-syndrome decoding with **19 decoder algorithms**. Tunable BP-OSD parameters, resilient fallback mode, clear cache controls, and detailed correction analysis.

#### 📈 Benchmark Suite
Configurable decode benchmarks with throughput, latency (mean / p50 / p99 / min / max), and multi-panel Matplotlib charts. Export results to JSON.

#### ? Batch & Streaming
Batch decoding with explicit CPU / CUDA / OpenCL routing. Streaming decode with sliding-window commit semantics and live logical error rate tracking.

</td>
<td width="50%">

#### 🕰️ History
Persistent decode-session history with re-inspection of past syndromes, corrections, and exported artifacts.

#### 🖥️ Hardware & System
Auto-detect CUDA, OpenCL, and CPU backends. Hardware-aware decoder recommendations via `hardware_routing.recommend()`.

#### 🩺 Diagnostics & Auto-Debug
Full environment/decoder/hardware self-diagnostics and `qd.doctor` health checks. Automatic multi-decoder fallback with complete attempt trace analysis.

#### 📚 Documentation Studio
Deposit-ready export in **8 formats**: Markdown, JSON, HTML, LaTeX, PDF, SVG, plus `.zenodo.json` and `CITATION.cff`. Reports carry a five-figure publication suite (Tanner graph, parity sparsity, decoder latency, logical failure fraction, speed/accuracy Pareto front), a Methods section, a Data Availability statement and a formatted citation.

#### 🔐 Lab Info & Microsoft Entra ID
Deposit metadata for generated reports: lead author, ORCID, institution, DOI,
funding and keywords. It also exposes a fail-closed Microsoft Entra ID posture
for lab evaluation; live identity sign-in is disabled in the air-gapped build.

</td>
</tr>
</table>

---

### 🧬 Supported Code Families (10)

| Family | Type | Description |
|:-------|:-----|:------------|
| `repetition` | Graphlike | 1D repetition code |
| `ring` | Graphlike | Ring topology |
| `rotated_surface` | Graphlike | Rotated planar surface code |
| `unrotated_surface` | Graphlike | Standard planar surface code |
| `toric` | Graphlike | Periodic toric code |
| `heavy_hex` | Graphlike | IBM heavy-hexagon lattice |
| `hypergraph_product` | Graphlike | CSS code from repetition-code seed |
| `bicycle` | qLDPC | Quantum LDPC bicycle code |
| `bivariate_bicycle` | qLDPC | IBM bivariate bicycle (BB) code family |
| `color_code` | Color | Triangular & 2D color codes |

---

### 🧠 Decoder Algorithms (19)

| Decoder | Strategy | Notes |
|:--------|:---------|:------|
| `union_find` | Approximate | Fast cluster-growth matching |
| `fast_union_find` | Approximate | Optimized UF variant |
| `blossom` | Exact MWPM | Weight-optimal, matches PyMatching LER |
| `sparse_blossom` | Near-optimal | Sparse graph MWPM approximation |
| `bp_osd` | Iterative | Belief propagation + OSD for qLDPC codes |
| `spacetime_mwpm` | Multi-round | Space-time lattice MWPM for phenomenological & circuit-level decoding |
| `soft_llr` | Soft-decision | Log-likelihood-ratio-weighted decoding |
| `auto` | Auto-select | Self-selects best backend |
| `hybrid` | Combined | Multi-strategy hybrid decoder |
| `lookup_table` | Exact | Table-based for small codes (= 20 checks) |
| `predecoded` | Staged | Pre-decoded syndrome correction |
| `auto_router` | Policy | Dispatches best decoder per code topology |
| `hybrid_cascade` | Staged | UF pre-filter ? Blossom/BP-OSD escalation |
| `gnn_belief_matching` | Neural | GNN-weighted belief matching |
| `belief_matching` | Hybrid | BP posteriors reweight exact Blossom MWPM |
| `two_stage` | Decoupled | Independent X/Z sector decoders for CSS/color codes |
| `ambiguity_cluster` | Cluster | Partition ambiguous checks into local clusters |
| `colour_code` | DEM-native | BP-OSD hypergraph decoder for 3-body color codes |
| `space_time` | Multi-round | Space-time decoder for phenomenological & circuit-level decoding |

> **Resilient mode:** When enabled, the workbench automatically falls back through compatible decoders if the selected one cannot handle the current code family · and reports exactly what happened.

---

### 🛠️ 86-tool MCP Server

Full Model Context Protocol integration for headless AI/LLM workflows:

- **Transport:** stdio JSON-RPC 2.0 (protocol version `2024-11-05`)
- **No HTTP bridge, no port binding** · pure stdin/stdout newline-delimited JSON-RPC
- **All 86 tools** wired to the live backend API
- **Per-tool 60-second timeouts**, busy guards, and 10 MB frame limits protect long-running agents

<details>
<summary><strong>Tool Categories</strong></summary>

| Category | Tools |
|:---------|:------|
| **Decoding** | `decode_syndrome`, `decode_single`, `decode_with_options`, `decode_syndrome_blossom`, `decode_syndrome_cascade`, `diagnostic_decode`, `resilient_decode`, `probe_decoders`, `sparse_blossom_radix_neighbors`, `gnn_belief_match_decode`, `belief_match_decode`, `two_stage_decode`, `ambiguity_cluster_decode`, `colour_code_decode`, `decode_hyperedge`, `decode_mmap`, `decode_dem`, `compare_all_decoders` |
| **Batch & Streaming** | `batch_decode`, `batch_decode_gpu`, `parallel_batch_decode`, `native_streaming`, `stream_decode` |
| **Benchmarking** | `benchmark_decoder`, `run_benchmark`, `run_ler_benchmark`, `hybrid_cascade_stats`, `compare_benchmarks`, `decoder_benchmark_suite`, `estimate_threshold`, `finite_size_scaling`, `get_statistics` |
| **Code Management** | `list_code_families`, `list_codes`, `build_code_from_matrix`, `get_code_properties`, `analyze_code_family`, `analyze_error_patterns`, `analyze_logicals`, `generate_parity_check`, `compatible_decoders`, `compat_report`, `compatibility_matrix`, `get_decoder_info`, `list_decoders` |
| **DEM & Stim** | `build_dem`, `import_stim` |
| **Hardware & System** | `get_hardware_info`, `get_system_info`, `get_backend_health`, `native_recommend`, `recommend_decoder` |
| **Diagnostics & Compliance** | `self_diagnostics`, `doctor_diagnostics`, `version_info`, `check_updates`, `compliance_attestation`, `get_entra_posture`, `get_identity_info`, `mcp_health`, `mcp_status`, `get_server_env` |
| **Documentation & Export** | `generate_documentation`, `export_benchmark`, `export_figure`, `export_session`, `generate_reproducibility_package` |
| **Resources & Config** | `get_resources`, `get_resource`, `get_results`, `clear_results`, `delete_resource`, `register_client`, `list_clients`, `get_config`, `set_config`, `reset_config`, `clear_decoder_cache` |
| **Licensing** | `get_license_info`, `verify_license_token`, `set_license_key_file`, `flush_usage` |
| **Research** | `neural_predecoder_train`, `import_syndrome` |
| **Meta** | `list_tools` |

</details>

---

### ⚡ Local Benchmarking

Benchmark output is intentionally not stored or shipped because throughput and
latency depend on the user's hardware, drivers, seed, and workload. Run the
Benchmark tab or CLI locally when measurements are required.

---

## 💻 CLI Reference

```
QectorWorkbench-Portable.exe <command> [options]
```

### Global Flags
These flags are available on all commands:

| Flag | Description |
|:-----|:------------|
| `--json` | Output raw results in JSON format |
| `--no-color` | Disable ANSI colors |
| `--no-banner` | Suppress ASCII header banner |
| `--output, -o` | Redirect output to a file (sanitized) |
| `--verbose, -v` | Enable verbose logging / details |
| `--quiet, -q` | Suppress banners and warning prints |
| `--config, -c` | Path to JSON config file to load parameters |
| `--version, -V` | Show version information and exit |

### Subcommands (22)

| Command | Description |
|:--------|:------------|
| `decode` | Decode a single syndrome (supports `--dry-run`) |
| `benchmark` | Run decoder benchmarks (supports `--dry-run`) |
| `probe` | Probe compatible decoders for a code |
| `diagnostics`| Full environment & decoder diagnostics |
| `hardware` | Detect and report hardware backends |
| `list-codes` | List all available code families |
| `list-decoders`| List all decoder algorithms |
| `docgen` | Generate documentation in multiple formats |
| `version` | Show version information |
| `compare` | Compare multiple decoders on the same code |
| `batch` | Batch decode multiple syndromes |
| `stream` | Streaming decode workflow (supports `--dry-run`) |
| `train` | Train neural predecoder |
| `export` | Export a complete decode session |
| `import` | Import external syndrome data (CSV, JSON, .npy) |
| `matrix` | Return the full decoder/code compatibility matrix |
| `serve` | Launch local REST API service |
| `doctor` | Run 15-check environment diagnostic |
| `compliance` | Run zero-egress / offline compliance attestation |
| `entra` | Optional Microsoft Entra ID SSO readiness (off by default) |
| `decode_mmap` | Out-of-core memmap decoding of large syndromes |
| `completions` | Generate shell completions for bash/zsh/PowerShell |

### Examples

```powershell
# Decode with exact Blossom MWPM on a rotated surface code
QectorWorkbench-Portable.exe decode `
    --family rotated_surface --distance 5 `
    --decoder blossom --error-rate 0.05

# Compare multiple decoders on a rotated surface code
QectorWorkbench-Portable.exe compare `
    --family rotated_surface --distance 5 `
    --decoders blossom,bp_osd,union_find

# Batch decode 1000 samples on CPU
QectorWorkbench-Portable.exe batch `
    --family rotated_surface --distance 5 `
    --backend cpu --samples 1000

# Run a sliding window streaming decode session
QectorWorkbench-Portable.exe stream `
    --family rotated_surface --distance 5 `
    --window 5 --n-rounds 100

# Train neural predecoder for a repetition code
QectorWorkbench-Portable.exe train `
    --family repetition --distance 3 `
    --samples 200 --epochs 5

# Export a complete session to a zip file
QectorWorkbench-Portable.exe export `
    --family rotated_surface --decoder blossom --output session.zip

# Import external syndrome and decode it
QectorWorkbench-Portable.exe import `
    --file syndrome.csv --decoder blossom --family rotated_surface --distance 5

# Display compatibility matrix as an ASCII table
QectorWorkbench-Portable.exe matrix --format table

# Run 15-check environment diagnostic
QectorWorkbench-Portable.exe doctor

# Verify the air-gapped / zero-egress compliance posture
QectorWorkbench-Portable.exe compliance
```

---

## 🖥️ System Requirements

| Component | Requirement |
|:----------|:------------|
| **OS** | Windows 10/11 (x64), Linux (glibc = 2.30), macOS 12+ |
| **Runtime** | None · portable `.exe` bundles everything (Python 3.12) |
| **RAM** | 4 GB minimum, 8 GB recommended |
| **GPU** | Optional · CUDA for GPU-accelerated batch decode |
| **Disk** | ~130 MB (portable .exe) |
| **Display** | Not required for CLI / MCP headless modes |

---

## 🔬 Decoder Runtime Provisioning

The workbench uses a **zero-config runtime provisioner** for the `qector-decoder-v3` backend:

1. **Bundled wheel** · The portable `.exe` ships with an embedded, ABI-matched wheel
2. **Managed site** · Falls back to a per-user, ABI-partitioned managed site (`decoder_site/<abi_tag>`)
3. **PyPI fallback** · If neither is available, downloads the correct wheel from PyPI
4. **Self-heal** · On corruption, extracts the bundled wheel and rebuilds the managed site
5. **Version purge** · Outdated managed decoders from older releases are removed automatically

> No internet connection is required for normal operation with the portable build.

---

## 🛡️ Air-Gapped Hardening Status

The v1.0.6 public package is designed for offline lab use after download and
extraction. Implemented in this release:

- Bundled decoder wheel activation works without internet access
- MCP transport is stdio only; the packaged MCP mode does not bind an HTTP port
- Version checks resolve against the bundled local baseline, not a network update service
- Runtime data can be redirected with `QECTOR_DATA_DIR`
- License keys are encrypted at rest with machine-derived Fernet keys; export path traversal is sanitized
- All documentation exports and deposit sidecars carry SHA-256 sidecar manifests

Status tracking ships in the release package as `AIR_GAPPED_HARDENING_STATUS.md`.

---

## 🤖 Claude Plugin Compatibility

Both the **Windows** and **Linux** v1.0.6 releases of QECTOR Decoder Workbench are
**fully compatible** with the official
[QECTOR Claude Plugin](https://github.com/GuillaumeLessard/qector-claude-plugin)
(`qector-claude-plugin`) for **Claude Code** and **Claude Desktop**:

- The plugin's bundled `qector-library` (8 stable tools) and `qector-bench`
  (29 research tools) MCP servers run against the same `qector-decoder-v3 1.0.0`
  backend that the workbench provisions · versions match exactly
  (`qector-decoder-v3==1.0.0`).
- Use the workbench's `--mcp` server, the plugin's two stdio MCP servers, or
  both at once · all are local-only with zero network egress.
- **Claude Desktop extension:** run `scripts\install_windows_connector.cmd` from
  the plugin repository to register QECTOR as a first-class Extension inside
  Claude Desktop Settings → Connectors (`manifest_version: 0.3`).
- **Claude Code marketplace:**
  ```bash
  claude plugin marketplace add GuillaumeLessard/qector-claude-plugin
  claude plugin install qector@qector-tools
  ```
- The plugin ships 13 slash commands (`/qec-decode`, `/qec-threshold-sweep`,
  `/qec-benchmark`, `/qec-dem`, ·), 5 specialized agents (researcher, developer,
  validator, sysadmin, hardware engineer), 28 domain skills, and enforces the
  same `H·c ≡ s mod 2` fail-closed verification used by the workbench.

> **Plugin requirements:** Python 3.10+, `qector-decoder-v3==1.0.0`, `mcp==1.2.0`,
> NumPy.

---

## ⚠️ Honest Performance Posture

Following the upstream QECTOR Decoder v3 documentation:

> All logical-error-rate, throughput, and latency figures are hardware-, driver-, seed-, and workload-dependent simulation results · **regenerate them on your own target hardware before quoting.**

- **PyMatching** remains the speed leader on standard surface-code MWPM
- QECTOR's exact `blossom` decoder matches PyMatching's logical error rate but is not faster
- Key strengths: batch throughput via approximate Union-Find, qLDPC coverage via BP-OSD, and **GF(2) syndrome correctness** (`H·c ≡ s mod 2`)
- This is a **research and evaluation platform**, not a real-time fault-tolerant hardware decoding stack

---

## 📚 Documentation

| Document | Where |
|:---------|:------|
| [Quick Start Guide](manuals/QECTOR_Quick_Start_Guide.pdf) | In the release `.zip` (`manuals/`) |
| [Windows User Manual](manuals/QECTOR_User_Manual_Windows.pdf) | In the release `.zip` (`manuals/`) |
| [API Reference](manuals/QECTOR_API_Reference.md) | In the release `.zip` (`manuals/`) |
| [API Reference PDF](manuals/QECTOR_API_Reference.pdf) | In the release `.zip` (`manuals/`) |
| [MCP Integration Guide](manuals/QECTOR_MCP_Integration_Guide.pdf) | In the release `.zip` (`manuals/`) |
| [LLM Reference Manual](manuals/QECTOR_LLM_Manual.json) | In the release `.zip` (`manuals/`) |
| [Air-Gapped Hardening Status](AIR_GAPPED_HARDENING_STATUS.md) | In the release `.zip` |
| [CHANGELOG](CHANGELOG.md) | This repository |
| [EULA](EULA.txt) | This repository |
| [SECURITY](SECURITY.md) | This repository |

> Full documentation is included inside the `manuals/` directory of the release
> package. This repository is release-only and does not host the docs folder.

---

## ⚖️ License

### Workbench (this application)

Source-available under [EULA.txt](EULA.txt). Grants a **royalty-free, worldwide license** to use, execute, copy, and distribute the software for **any purpose** · including commercial, academic, and personal use · provided embedded "QECTOR" notices and watermarks are retained (EULA §2).

### Backend (`qector-decoder-v3`)

Separately licensed, source-available Rust/Python platform by the same author:

- ✅ **Free** for personal, academic, educational, and non-commercial research
- 💼 **Commercial use** (company R&D, SaaS, hosted API, OEM, redistribution) **requires a [paid license](https://qector.store/pricing)**
- 🔄 60-day commercial evaluation available, creditable against a license

> The workbench depends on `qector-decoder-v3` at runtime. Honor the backend's license terms for any commercial deployment.

---

## 📧 Support & Contact

| | |
|:--|:--|
| **Website** | [www.qector.store](https://www.qector.store) |
| **Commercial Licensing** | [admin@qector.store](mailto:admin@qector.store) |
| **Support** | [admin@qector.store](mailto:admin@qector.store) |
| **Pricing** | [qector.store/pricing](https://qector.store/pricing) |

---

<p align="center">
  <strong>QECTOR Decoder Workbench v1.0.6</strong><br/>
  Built on <code>qector-decoder-v3</code> v1.0.0 (Rust/PyO3 core)<br/><br/>
  · 2026 Guillaume Lessard / iD01t Productions<br/>
  ORCID <a href="https://orcid.org/0009-0000-3465-3753">0009-0000-3465-3753</a><br/><br/>
  <em>Powered by QECTOR</em>
</p>
