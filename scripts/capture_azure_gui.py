import subprocess
import time
import os
import sys
import json
from pathlib import Path

def main():
    # Set EULA accepted to bypass modal and reach the main workbench window
    pref_dir = Path("/root/.local/share/QectorWorkbench")
    pref_dir.mkdir(parents=True, exist_ok=True)
    (pref_dir / "preferences.json").write_text(json.dumps({"eula_accepted": True}), encoding="utf-8")

    print("Starting QECTOR main.py in Xvfb display to capture main window...")
    env = os.environ.copy()
    proc = subprocess.Popen([sys.executable, "main.py"], env=env)
    
    # Wait for main window to initialize
    time.sleep(8)
    
    output_png = "/tmp/real_azure_app_main.png"
    print("Capturing X11 root window screenshot...")
    os.system(f"import -window root {output_png}")
    
    proc.terminate()
    
    if os.path.exists(output_png):
        size = os.path.getsize(output_png)
        print(f"REAL_SCREENSHOT_SUCCESS: {output_png} (Size: {size} bytes)")
    else:
        print("REAL_SCREENSHOT_FAILED")

if __name__ == "__main__":
    main()
