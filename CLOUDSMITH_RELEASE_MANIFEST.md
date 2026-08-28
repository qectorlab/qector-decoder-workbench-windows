# QECTOR Decoder Workbench v1.0.4 — Cloudsmith Package Release Manifest

## Repository Details
* **Owner/Organization**: `qector`
* **Repository**: `qector-decoder-workbench`
* **Target Package Name**: `qector-workbench`
* **Version**: `1.0.4`
* **Backend Version**: `v1.0.0` (Rust/PyO3 Core)
* **License**: `EULA.txt` (Source-Available, Royalty-Free Academic & Non-Commercial Research)

---

## Shipped Package Artifacts

| Package File | Format | Target Platform | Description |
| :--- | :--- | :--- | :--- |
| `qector-workbench_1.0.4_amd64.deb` | `.deb` | Debian / Ubuntu / Linux Mint / RHEL | Debian system package installing binary launcher `/usr/local/bin/qector-workbench`, desktop entry, icons, and bundled wheels. |
| `QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz` | `.tar.gz` | Portable Linux x86_64 | Portable Linux distribution tarball containing source, wheels, test suite, and CLI/GUI entry points. |

---

## Package Metadata & Tagging

* **Short Summary**:
  `QECTOR Decoder Workbench v1.0.4 - Enterprise Quantum Error Correction (QEC) Decoder Suite with 85-tool MCP Server, Zero-Egress Security Attestation, and C++/Rust Extension Core.`

* **Full Description**:
  ```markdown
  QECTOR Decoder Workbench v1.0.4 is a production-grade Quantum Error Correction (QEC) research environment featuring:
  - **Rust/PyO3 C++ Extension Core** (`qector_decoder_v3` 1.0.0) for high-throughput syndrome decoding.
  - **85-Tool Model Context Protocol (MCP) Server** operating under strict air-gapped isolation.
  - **Zero-Egress AST Attestation & Runtime EgressGuard** socket blocking non-loopback DNS and connections.
  - **Zero-Skip Test Suite**: 633 unit/integration tests (100% pass, 0 skipped).
  - **Executive Proof Verification**: 10/10 mathematical and decoder faithfulness proof tests.
  - **CustomTkinter GUI & CLI**: Full multi-tab interactive workbench with Code Explorer, Decoder Lab, Benchmarking, and Diagnostics.
  ```

* **Tags**: `qec, quantum, decoding, mcp-server, zero-egress, rust, pyo3, rotated-surface, bivariate-bicycle`

---

## Automated Upload Commands

```bash
# Set Cloudsmith API Key
export CLOUDSMITH_API_KEY="<YOUR_CLOUDSMITH_API_KEY>"

# 1. Push Debian Package
cloudsmith push deb qector/qector-decoder-workbench/any-distro/any-version \
  dist/qector-workbench_1.0.4_amd64.deb \
  --tags "qec,quantum,mcp,zero-egress,linux,deb" \
  --republish

# 2. Push Standalone Tarball Package
cloudsmith push raw qector/qector-decoder-workbench \
  dist/release_assets/QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz \
  --name "QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz" \
  --summary "QECTOR Decoder Workbench v1.0.4 - Standalone Linux Distribution (.tar.gz)" \
  --description "$(<README_LINUX.md)" \
  --tags "qec,quantum,mcp,zero-egress,linux,tarball" \
  --republish
```
