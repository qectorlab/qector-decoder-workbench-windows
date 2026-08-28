import subprocess
import json
import base64
from pathlib import Path

def download_file(remote_path, local_path):
    print(f"Downloading {remote_path} -> {local_path} in 3KB chunks...")
    offset = 0
    chunk_size = 3000
    all_bytes = bytearray()
    
    while True:
        script = f"python3.11 /tmp/qector-decoder-workbench-linux/scripts/read_chunk.py {remote_path} {offset} {chunk_size}"
        cmd = f'az vm run-command invoke --resource-group VM --name Ubuntu --command-id RunShellScript --scripts "{script}"'
        res = subprocess.check_output(cmd, shell=True).decode('utf-8')
        data = json.loads(res)
        msg = data['value'][0]['message']
        
        lines = msg.splitlines()
        b64_lines = [l for l in lines if not l.startswith("[") and not l.startswith("Enable")]
        b64_str = "".join(b64_lines).strip()
        
        if not b64_str:
            break
            
        chunk = base64.b64decode(b64_str)
        if not chunk:
            break
            
        all_bytes.extend(chunk)
        print(f"Downloaded {len(all_bytes)} bytes...")
        if len(chunk) < chunk_size:
            break
        offset += chunk_size

    out_path = Path(local_path)
    out_path.write_bytes(all_bytes)
    print(f"SUCCESS: Total downloaded size = {len(all_bytes)} bytes")

if __name__ == "__main__":
    download_file("/tmp/real_azure_app_main.png", r"C:\Users\Admin\.gemini\antigravity\brain\413118e5-1b8a-4a04-8c3e-888db97a7abb\real_azure_gui_main.png")
