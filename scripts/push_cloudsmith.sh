#!/usr/bin/env bash
set -e

if [ -z "$CLOUDSMITH_API_KEY" ]; then
    echo "ERROR: CLOUDSMITH_API_KEY environment variable is not set!"
    echo "Please run: export CLOUDSMITH_API_KEY='your_api_key' before executing this script."
    exit 1
fi

echo "========================================================================"
echo " Pushing QECTOR Decoder Workbench v1.0.4 to Cloudsmith"
echo "========================================================================"

# Ensure build artifacts exist
python3.11 build_production.py --deb

mkdir -p dist/release_assets
if [ -f "dist/qector-workbench_1.0.4_amd64.deb" ]; then
    cp dist/qector-workbench_1.0.4_amd64.deb dist/release_assets/
fi

# Push Debian Package
echo "Pushing Debian (.deb) package..."
cloudsmith push deb qector/qector-decoder-workbench/any-distro/any-version \
  dist/qector-workbench_1.0.4_amd64.deb \
  --tags "qec,quantum,mcp,zero-egress,linux,deb" \
  --republish

# Push Raw Tarball Package
if [ -f "dist/release_assets/QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz" ]; then
    echo "Pushing Standalone Linux Tarball (.tar.gz)..."
    cloudsmith push raw qector/qector-decoder-workbench \
      dist/release_assets/QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz \
      --name "QectorWorkbench-v1.0.4-Linux-x64-Public.tar.gz" \
      --summary "QECTOR Decoder Workbench v1.0.4 - Standalone Linux Distribution" \
      --tags "qec,quantum,mcp,zero-egress,linux,tarball" \
      --republish
fi

echo "========================================================================"
echo " Cloudsmith Push Succeeded!"
echo "========================================================================"
