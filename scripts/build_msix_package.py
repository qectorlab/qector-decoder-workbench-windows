"""Build official Microsoft MSIX package for QECTOR Decoder Workbench v1.0.4."""
import os
import shutil
import subprocess
import hashlib
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
def _find_makeappx() -> str:
    if shutil.which("makeappx"):
        return shutil.which("makeappx")
    pf = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    kits = Path(pf) / "Windows Kits" / "10" / "bin"
    if kits.exists():
        for p in sorted(kits.glob("*/x64/makeappx.exe"), reverse=True):
            return str(p)
    return "makeappx.exe"

MAKEAPPX = _find_makeappx()

def generate_msix_assets(assets_dir: Path):
    assets_dir.mkdir(parents=True, exist_ok=True)
    icon_src = ROOT / "assets" / "icon.png"
    if not icon_src.exists():
        icon_src = ROOT / "icon.png"
    img = Image.open(icon_src).convert("RGBA")
    
    sizes = {
        "Square44x44Logo.targetsize-44.png": (44, 44),
        "Square44x44Logo.png": (44, 44),
        "Square150x150Logo.png": (150, 150),
        "Square310x310Logo.png": (310, 310),
        "Wide310x150Logo.png": (310, 150),
        "StoreLogo.png": (50, 50),
    }
    
    for filename, (w, h) in sizes.items():
        resized = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        iw, ih = img.size
        scale = min(w / iw, h / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        scaled_img = img.resize((nw, nh), Image.LANCZOS)
        offset = ((w - nw) // 2, (h - nh) // 2)
        resized.paste(scaled_img, offset, scaled_img)
        resized.save(assets_dir / filename)
        print(f"  [asset] {filename} ({w}x{h})")

def write_appx_manifest(layout_dir: Path):
    manifest_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:mp="http://schemas.microsoft.com/appx/2014/phone/manifest"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap mp rescap">

  <Identity
    Name="qectorlab.qector-decoder-workbench"
    Publisher="CN=iD01t Productions, O=iD01t Productions, C=CA"
    Version="{VERSION}.0"
    ProcessorArchitecture="x64" />

  <Properties>
    <DisplayName>QECTOR Decoder Workbench</DisplayName>
    <PublisherDisplayName>iD01t Productions / Guillaume Lessard</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
    <Description>High-performance quantum error-correction (QEC) decoding workbench powered by qector-decoder-v3 v1.0.0 engine.</Description>
  </Properties>

  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>

  <Resources>
    <Resource Language="x-generate" />
  </Resources>

  <Applications>
    <Application Id="QectorWorkbench"
      Executable="QectorWorkbench-Portable.exe"
      EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements
        DisplayName="QECTOR Decoder Workbench"
        Description="QECTOR Quantum Error Correction Workbench"
        BackgroundColor="#12141a"
        Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" Square310x310Logo="Assets\\Square310x310Logo.png" />
      </uap:VisualElements>
    </Application>
  </Applications>

  <Capabilities>
    <Capability Name="internetClient" />
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""
    (layout_dir / "AppxManifest.xml").write_text(manifest_xml, encoding="utf-8")
    print("  [manifest] AppxManifest.xml written")

def build_msix():
    print(f"========================================================================")
    print(f"  Building Microsoft MSIX Package: QectorWorkbench v{VERSION}")
    print(f"========================================================================\n")
    
    layout_dir = ROOT / "build" / "msix_layout"
    if layout_dir.exists():
        shutil.rmtree(layout_dir)
    layout_dir.mkdir(parents=True, exist_ok=True)
    
    exe_src = ROOT / "dist" / "QectorWorkbench-Portable.exe"
    if not exe_src.exists():
        raise FileNotFoundError(f"Portable executable not found at: {exe_src}")
    
    shutil.copy2(exe_src, layout_dir / "QectorWorkbench-Portable.exe")
    print(f"  [copy] QectorWorkbench-Portable.exe ({exe_src.stat().st_size / 1e6:.1f} MB)")
    
    for doc in ["EULA.txt", "README.md", "SECURITY.md", "CHANGELOG.md"]:
        if (ROOT / doc).exists():
            shutil.copy2(ROOT / doc, layout_dir / doc)
    
    generate_msix_assets(layout_dir / "Assets")
    write_appx_manifest(layout_dir)
    
    out_dist = ROOT / "dist"
    out_dist.mkdir(parents=True, exist_ok=True)
    msix_out = out_dist / f"QectorWorkbench-v{VERSION}-Windows-x64.msix"
    
    cmd = [
        MAKEAPPX, "pack",
        "/d", str(layout_dir),
        "/p", str(msix_out),
        "/o"
    ]
    print(f"\nRunning makeappx: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    
    sha256 = ""
    if msix_out.exists():
        sha256 = hashlib.sha256(msix_out.read_bytes()).hexdigest()
        (out_dist / f"QectorWorkbench-v{VERSION}-Windows-x64.msix.sha256").write_text(
            f"{sha256}  QectorWorkbench-v{VERSION}-Windows-x64.msix\n", encoding="utf-8"
        )
        
        # Also copy to release_assets and Desktop
        rel_assets = ROOT / "release_assets"
        rel_assets.mkdir(parents=True, exist_ok=True)
        shutil.copy2(msix_out, rel_assets / msix_out.name)
        (rel_assets / f"{msix_out.name}.sha256").write_text(f"{sha256}  {msix_out.name}\n", encoding="utf-8")
        
        desktop = Path.home() / "Desktop"
        if desktop.exists():
            shutil.copy2(msix_out, desktop / msix_out.name)
            print(f"  [copy] Copied MSIX package to Desktop: {desktop / msix_out.name}")
    
    print("\n========================================================================")
    print(f"  [OK] MSIX Package Created Successfully!")
    print(f"       Path:   {msix_out} ({msix_out.stat().st_size / 1e6:.1f} MB)")
    print(f"       SHA256: {sha256}")
    print("========================================================================\n")

if __name__ == "__main__":
    build_msix()
