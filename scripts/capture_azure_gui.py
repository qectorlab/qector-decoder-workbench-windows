import subprocess
import time
import os
import sys

def main():
    print("Starting QECTOR main.py in Xvfb display...")
    env = os.environ.copy()
    proc = subprocess.Popen([sys.executable, "main.py"], env=env)
    
    # Wait for GUI / EULA window to render
    time.sleep(5)
    
    output_png = "/tmp/real_azure_app_boot.png"
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
