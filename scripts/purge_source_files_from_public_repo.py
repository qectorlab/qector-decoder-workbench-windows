"""Purge all loose .py and internal dev source files from qectorlab/qector-decoder-workbench-windows main branch.

The public Windows repository MUST ONLY contain documentation and release manifests:
  - README.md
  - EULA.txt & EULA.rtf
  - SECURITY.md
  - CHANGELOG.md
  - CODE_OF_CONDUCT.md
  - CONTRIBUTING.md
  - assets/
  - winget/
  - CLOUDSMITH_PGP_KEY.asc
  - PUBLIC_KEY_ED25519.pem
  - PUBLIC_KEY_RSA.pem

ZERO loose .py source files or .spec build files!
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOTE_URL = "git@github.com:qectorlab/qector-decoder-workbench-windows.git"

PUBLIC_FILES = [
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "EULA.rtf",
    "EULA.txt",
    "README.md",
    "SECURITY.md",
    "CLOUDSMITH_PGP_KEY.asc",
    "PUBLIC_KEY_ED25519.pem",
    "PUBLIC_KEY_RSA.pem",
]

PUBLIC_DIRS = [
    "assets",
    "winget",
]

def main():
    print(f"=== Purging loose .py source files from public repo: {REMOTE_URL} ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        repo_dir = tmppath / "repo"
        
        # 1. Clone current public repo
        print("Cloning public repository...")
        subprocess.run(["git", "clone", REMOTE_URL, "repo"], cwd=tmppath, check=True)
        
        # 2. Reset to clean docs-only commit b25281ee
        print("Resetting to clean docs-only commit b25281ee...")
        subprocess.run(["git", "reset", "--hard", "b25281eeb46bcdd77d4e5c9e319b669182df4b79"], cwd=repo_dir, check=True)
        
        # 3. Copy updated public documentation files from workspace
        for f in PUBLIC_FILES:
            src = ROOT / f
            dst = repo_dir / f
            if src.exists():
                shutil.copy2(src, dst)
                print(f"  [copy file] {f}")
        
        # 4. Copy updated public directories from workspace
        for d in PUBLIC_DIRS:
            src = ROOT / d
            dst = repo_dir / d
            if src.exists():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                print(f"  [copy dir]  {d}")
        
        # 5. Stage all clean docs files
        subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
        
        # 6. Verify zero .py files in public repo
        tree_res = subprocess.run(["git", "ls-files"], cwd=repo_dir, capture_output=True, text=True)
        files = tree_res.stdout.strip().splitlines()
        py_files = [f for f in files if f.endswith(".py")]
        
        print("\nPublic Repository Contents Summary:")
        print(f"  Total files:    {len(files)}")
        print(f"  Loose .py files: {len(py_files)}")
        if py_files:
            print("  ERROR: Loose .py files found:", py_files)
            raise ValueError("Loose .py files found in public repo layout!")
        
        print("\nTracked files in public repo:")
        for f in sorted(files):
            print(f"  - {f}")
        
        # 7. Commit clean docs-only update
        subprocess.run(["git", "commit", "-m", "docs & release: update v1.0.4 documentation, EULA, security, and winget manifests"], cwd=repo_dir, check=True)
        
        # 8. Force push clean docs-only tree to windows main
        print("\nForce-pushing clean docs-only tree to origin main...")
        subprocess.run(["git", "push", "origin", "main", "--force"], cwd=repo_dir, check=True)
        print("\n[OK] Clean public repository restored with ZERO loose .py source files!")

if __name__ == "__main__":
    main()
