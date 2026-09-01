"""Debug: see if create_connection triggers an immediate close."""
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

TTS_EXE = Path(r"C:\Users\px-wjt\Downloads\Trident\tools\runtime\tts\chatterbox-server.exe")
T3_MODEL = Path(r"C:\Users\px-wjt\Downloads\Trident\models\chatterbox-t3-mtl-v3-cangjie-q4_0.gguf")
S3GEN_MODEL = Path(r"C:\Users\px-wjt\Downloads\Trident\models\chatterbox-s3gen-mtl-v3-f16.gguf")
VOICE_REF = Path(r"C:\Users\px-wjt\Downloads\Trident\data\ref-trump.wav")
LOG = Path(r"C:\Users\px-wjt\Downloads\Trident\tools\debug_chatterbox.log")


def wait_port(port, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


cmd = [
    str(TTS_EXE), "--run-id", "debug", "--family", "v3",
    "--model", str(T3_MODEL), "--s3gen-gguf", str(S3GEN_MODEL),
    "--reference", str(VOICE_REF), "--language", "pl", "--port", "17933",
    "--n-gpu-layers", "99", "--context", "2048", "--threads", "4",
    "--seed", "42", "--max-tokens", "1000", "--top-k", "0", "--top-p", "1.0",
    "--min-p", "0.05", "--temperature", "0.8", "--repeat-penalty", "1.2",
    "--cfg-weight", "0.5", "--exaggeration", "0.5", "--cfm-steps", "5",
    "--fastconv", "1",
]
LOG.unlink(missing_ok=True)
proc = subprocess.Popen(cmd, cwd=str(TTS_EXE.parent),
                         stdout=open(LOG, "wb"), stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
if not wait_port(17933, 300):
    print("FAILED")
    proc.terminate()
    sys.exit(1)
print("chatterbox ready")

# Test 1: Just connect, do nothing for 5s, then close
print("\n=== Test 1: connect, idle 5s, close ===")
sock = socket.create_connection(("127.0.0.1", 17933))
print(f"connected, peer={sock.getpeername()}")
time.sleep(5)
sock.close()
print("closed")

# Test 2: Connect, send 28 bytes header only, no payload
print("\n=== Test 2: connect, send 28 bytes header only ===")
sock = socket.create_connection(("127.0.0.1", 17933))
print("connected")
# 7 fields: magic, version, kind=1, epoch=0, response=1, piece=1, text_length=5
header = struct.pack("<IIIIII", 0x32525454, 2, 1, 0, 1, 1) + struct.pack("<I", 5)
print(f"sending 28-byte header only: {header.hex()}")
try:
    sock.sendall(header)
    print("sendall ok, sleeping 3s")
    time.sleep(3)
except Exception as e:
    print(f"sendall error: {e!r}")
sock.close()
print("closed")

# Test 3: Connect, then send everything together
print("\n=== Test 3: connect, sleep 1s, send 34 bytes ===")
sock = socket.create_connection(("127.0.0.1", 17933))
print("connected")
time.sleep(1)
header = struct.pack("<IIIIII", 0x32525454, 2, 1, 0, 2, 1) + struct.pack("<I", 5)
payload = b"Test."
try:
    sock.sendall(header + payload)
    print("sendall ok")
    time.sleep(3)
except Exception as e:
    print(f"sendall error: {e!r}")
sock.close()
print("closed")

proc.terminate()
try: proc.wait(timeout=10)
except: proc.kill()
print("\n=== chatterbox log ===")
for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
    print(line)
