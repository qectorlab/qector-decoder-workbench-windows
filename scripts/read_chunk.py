import sys
import base64

if len(sys.argv) < 4:
    sys.exit(1)

filepath = sys.argv[1]
offset = int(sys.argv[2])
length = int(sys.argv[3])

with open(filepath, "rb") as f:
    f.seek(offset)
    data = f.read(length)
    print(base64.b64encode(data).decode('utf-8'))
