import subprocess
import json
import base64
from pathlib import Path

cmd = "az vm run-command invoke --resource-group VM --name Ubuntu --command-id RunShellScript --scripts \"base64 -w 0 /tmp/real_azure_app_boot.png\""
res = subprocess.check_output(cmd, shell=True).decode('utf-8')
data = json.loads(res)
msg = data['value'][0]['message']

# Save raw output for inspection
print("Message raw header:", msg[:200])

lines = msg.splitlines()
b64_lines = [l for l in lines if not l.startswith("[") and not l.startswith("Enable")]
b64_str = "".join(b64_lines).strip()

print("Base64 string length:", len(b64_str))
img_bytes = base64.b64decode(b64_str)
out_path = Path(r"C:\Users\Admin\.gemini\antigravity\brain\413118e5-1b8a-4a04-8c3e-888db97a7abb\real_azure_gui_boot.png")
out_path.write_bytes(img_bytes)
print(f"Decoded image size: {len(img_bytes)} bytes")
