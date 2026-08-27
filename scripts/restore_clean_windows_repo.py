"""Restore clean public structure on windows remote (qectorlab/qector-decoder-workbench-windows)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PUBLIC_ITEMS = [
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "EULA.txt",
    "README.md",
    "SECURITY.md",
    "CLOUDSMITH_PGP_KEY.asc",
    "PUBLIC_KEY_ED25519.pem",
    "PUBLIC_KEY_RSA.pem",
    "assets",
    "winget",
]

def main():
    print("=== Restoring Clean Public Repository Structure for Windows v1.0.4 ===")
    
    # 1. Create temporary orphan/clean branch from b25281ee
    print("Creating clean branch from b25281ee...")
    subprocess.run(["git", "branch", "-D", "windows-clean-temp"], cwd=ROOT, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "windows-clean-temp", "b25281eeb46bcdd77d4e5c9e319b669182df4b79"], cwd=ROOT, check=True)
    
    # 2. Checkout updated v1.0.4 public files from main branch
    for item in PUBLIC_ITEMS:
        subprocess.run(["git", "checkout", "main", "--", item], cwd=ROOT, check=True)
        print(f"  + checked out updated v1.0.4: {item}")
    
    # 3. Stage and commit
    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
    
    res = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    print("\nTotal files in public repo:", len(res.stdout.strip().splitlines()))
    
    subprocess.run(["git", "commit", "-m", "docs & release: update v1.0.4 documentation, EULA, public keys, and winget manifests"], cwd=ROOT, check=True)
    
    # 4. Push to windows main
    print("\nPushing clean tree to windows main...")
    subprocess.run(["git", "push", "windows", "windows-clean-temp:main", "--force"], cwd=ROOT, check=True)
    
    # 5. Return to main branch
    subprocess.run(["git", "checkout", "main"], cwd=ROOT, check=True)
    subprocess.run(["git", "branch", "-D", "windows-clean-temp"], cwd=ROOT, check=True)
    
    print("\n[OK] Successfully restored clean public structure on qector-decoder-workbench-windows main!")

if __name__ == "__main__":
    main()
