import subprocess
import json
import base64
from pathlib import Path

def main():
    cmd = 'az vm run-command invoke --resource-group VM --name Ubuntu --command-id RunShellScript --scripts "base64 -w 0 /tmp/real_azure_app_boot.png"'
    res = subprocess.check_output(cmd, shell=True).decode('utf-8')
    data = json.loads(res)
    msg = data['value'][0]['message']
    
    # Extract the base64 string between [stdout]\n and \n[stderr]
    parts = msg.split('[stdout]\n')
    if len(parts) > 1:
        content = parts[1].split('\n[stderr]')[0].strip()
        img_bytes = base64.b64decode(content)
        out_path = Path(r"C:\Users\Admin\.gemini\antigravity\brain\413118e5-1b8a-4a04-8c3e-888db97a7abb\real_azure_gui_boot.png")
        out_path.write_bytes(img_bytes)
        print(f"SUCCESS: Saved {len(img_bytes)} bytes to {out_path}")
    else:
        print("ERROR: Could not parse stdout from Azure output:", msg)

if __name__ == "__main__":
    main()
