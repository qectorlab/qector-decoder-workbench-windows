# QECTOR Decoder Workbench v1.0.4 (Linux) — Cloudsmith Package Release Manifest

## Repository Details
* **Owner/Organization**: `qector`
* **Repository**: `qector-decoder-workbench`
* **Target Package Name**: `qector-workbench-linux`
* **Target OS / Architecture**: `Linux x86_64` (glibc 2.17+, Debian 10+, Ubuntu 20.04+, RHEL/Oracle Linux 8+)
* **Version**: `1.0.4`
* **Backend Version**: `v1.0.0` (Rust/PyO3 Linux Core)
* **License**: `EULA.txt` (Source-Available, Royalty-Free Academic & Non-Commercial Research)

---

## Shipped Linux Package Artifacts

| Package File | Format | Target Platform | Description |
| :--- | :--- | :--- | :--- |
| `qector-workbench_1.0.4_amd64.deb` | `.deb` | Linux (Debian/Ubuntu/RHEL) | Official Linux Debian package installing `/usr/local/bin/qector-workbench`, desktop entry, icons, and bundled Linux manylinux wheels. |
| `QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz` | `.tar.gz` | Linux x86_64 Portable | Portable Linux distribution tarball containing Linux wheels, tests, 85-tool MCP server, and GUI/CLI entry points. |

---

## Linux Package Metadata & Tagging

* **Short Summary**:
  `QECTOR Decoder Workbench v1.0.4 (Linux x86_64) - Enterprise Quantum Error Correction (QEC) Decoder Suite with 85-tool MCP Server, Zero-Egress Security Attestation, and Linux C++/Rust Extension Core.`

* **Full Description**:
  ```markdown
  QECTOR Decoder Workbench v1.0.4 (Linux x86_64) is the official Linux release featuring:
  - **Linux Rust/PyO3 C++ Extension Core** (`qector_decoder_v3` 1.0.0 manylinux2014) for high-throughput syndrome decoding.
  - **85-Tool Model Context Protocol (MCP) Server** operating under strict air-gapped isolation on Linux.
  - **Zero-Egress AST Attestation & Runtime EgressGuard** socket blocking non-loopback DNS and connections.
  - **Zero-Skip Test Suite**: 633 unit/integration tests (100% pass, 0 skipped on Linux).
  - **Executive Proof Verification**: 10/10 mathematical and decoder faithfulness proof tests.
  - **Linux CustomTkinter GUI & CLI**: Full multi-tab interactive workbench with Code Explorer, Decoder Lab, Benchmarking, and Diagnostics.
  ```

* **Tags**: `qec, quantum, decoding, mcp-server, zero-egress, linux, linux-x86_64, debian, ubuntu, rhel`

---

## Automated Linux Upload Commands

```bash
# Set Cloudsmith API Key
export CLOUDSMITH_API_KEY="<YOUR_CLOUDSMITH_API_KEY>"

# 1. Push Linux Debian Package
cloudsmith push deb qector/qector-decoder-workbench/any-distro/any-version \
  dist/qector-workbench_1.0.4_amd64.deb \
  --tags "qec,quantum,mcp,zero-egress,linux,linux-x86_64,deb" \
  --republish

# 2. Push Linux Portable Tarball Package
cloudsmith push raw qector/qector-decoder-workbench \
  dist/release_assets/QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz \
  --name "QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz" \
  --summary "QECTOR Decoder Workbench v1.0.4 (Linux x86_64) - Standalone Linux Distribution (.tar.gz)" \
  --description "$(<README_LINUX.md)" \
  --tags "qec,quantum,mcp,zero-egress,linux,linux-x86_64,tarball" \
  --republish
```
