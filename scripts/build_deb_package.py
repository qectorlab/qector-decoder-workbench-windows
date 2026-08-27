"""build_deb_package.py - Build Debian .deb archive directly without dpkg dependency."""
import os
import sys
import time
import tarfile
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEB_ROOT = ROOT / "dist" / "qector-workbench_1.0.4_amd64"
OUT_DEB = ROOT / "dist" / "qector-workbench_1.0.4_amd64.deb"

def build_deb():
    if not DEB_ROOT.exists():
        raise FileNotFoundError(f"Debian root directory not found: {DEB_ROOT}")
    
    # 1. Build control.tar.gz
    control_buf = io.BytesIO()
    with tarfile.open(fileobj=control_buf, mode="w:gz") as tar:
        for p in (DEB_ROOT / "DEBIAN").glob("*"):
            tar.add(str(p), arcname=p.name)
    control_data = control_buf.getvalue()

    # 2. Build data.tar.gz
    data_buf = io.BytesIO()
    with tarfile.open(fileobj=data_buf, mode="w:gz") as tar:
        for item in ["opt", "usr"]:
            p = DEB_ROOT / item
            if p.exists():
                tar.add(str(p), arcname=item)
    data_bytes = data_buf.getvalue()

    # 3. Create .deb ar container
    mtime = int(time.time())
    with open(OUT_DEB, "wb") as f:
        f.write(b"!<arch>\n")
        
        # debian-binary
        deb_bin = b"2.0\n"
        hdr1 = f"{'debian-binary':<16}{mtime:<12}{'0':<6}{'0':<6}{'100644':<8}{len(deb_bin):<10}`\n".encode("ascii")
        f.write(hdr1 + deb_bin)
        
        # control.tar.gz
        hdr2 = f"{'control.tar.gz':<16}{mtime:<12}{'0':<6}{'0':<6}{'100644':<8}{len(control_data):<10}`\n".encode("ascii")
        f.write(hdr2 + control_data)
        if len(control_data) % 2 != 0:
            f.write(b"\n")
            
        # data.tar.gz
        hdr3 = f"{'data.tar.gz':<16}{mtime:<12}{'0':<6}{'0':<6}{'100644':<8}{len(data_bytes):<10}`\n".encode("ascii")
        f.write(hdr3 + data_bytes)
        if len(data_bytes) % 2 != 0:
            f.write(b"\n")

    print(f"[OK] Built Debian package: {OUT_DEB} ({OUT_DEB.stat().st_size / 1e6:.2f} MB)")

if __name__ == "__main__":
    build_deb()
