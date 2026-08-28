#!/usr/bin/env bash
set -e

if [ -z "$CLOUDSMITH_API_KEY" ]; then
    echo "ERROR: CLOUDSMITH_API_KEY environment variable is not set!"
    echo "Please run: export CLOUDSMITH_API_KEY='your_api_key' before executing this script."
    exit 1
fi

echo "========================================================================"
echo " Pushing QECTOR Decoder Workbench v1.0.4 (Linux x86_64) to Cloudsmith"
echo "========================================================================"

# Ensure build artifacts exist
python3.11 build_production.py --deb

mkdir -p dist/release_assets
if [ -f "dist/qector-workbench_1.0.4_amd64.deb" ]; then
    cp dist/qector-workbench_1.0.4_amd64.deb dist/release_assets/
fi

# Push Linux Debian Package
echo "Pushing Linux Debian (.deb) package..."
cloudsmith push deb qector/qector-decoder-workbench/any-distro/any-version \
  dist/qector-workbench_1.0.4_amd64.deb \
  --tags "qec,quantum,mcp,zero-egress,linux,linux-x86_64,deb" \
  --republish

# Push Linux Portable Raw Tarball Package
if [ -f "dist/release_assets/QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz" ]; then
    echo "Pushing Standalone Linux Tarball (.tar.gz)..."
    cloudsmith push raw qector/qector-decoder-workbench \
      dist/release_assets/QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz \
      --name "QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz" \
      --summary "QECTOR Decoder Workbench v1.0.4 (Linux x86_64) - Standalone Linux Distribution" \
      --description "QECTOR Decoder Workbench v1.0.4 (Linux x86_64) Standalone Package containing Linux Rust/PyO3 core, 85-tool MCP server, zero-egress compliance attestation, and CustomTkinter GUI." \
      --tags "qec,quantum,mcp,zero-egress,linux,linux-x86_64,tarball" \
      --republish
fi

echo "========================================================================"
echo " Cloudsmith Linux Push Succeeded!"
echo "========================================================================"
