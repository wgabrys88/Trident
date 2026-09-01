"""Start chatterbox in background, run protocol test, stop chatterbox."""
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
LOG = Path(r"C:\Users\px-wjt\Downloads\Trident\tools\proto_chatterbox.log")


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
    str(TTS_EXE), "--run-id", "proto-test", "--family", "v3",
    "--model", str(T3_MODEL), "--s3gen-gguf", str(S3GEN_MODEL),
    "--reference", str(VOICE_REF), "--language", "pl", "--port", "17933",
    "--n-gpu-layers", "99", "--context", "2048", "--threads", "4",
    "--seed", "42", "--max-tokens", "1000", "--top-k", "0", "--top-p", "1.0",
    "--min-p", "0.05", "--temperature", "0.8", "--repeat-penalty", "1.2",
    "--cfg-weight", "0.5", "--exaggeration", "0.5", "--cfm-steps", "5",
    "--fastconv", "1",
]
print("starting chatterbox...")
LOG.unlink(missing_ok=True)
proc = subprocess.Popen(cmd, cwd=str(TTS_EXE.parent),
                         stdout=open(LOG, "wb"), stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW)
print(f"pid={proc.pid}, waiting for port 17933...")
if not wait_port(17933, 300):
    print("FAILED: chatterbox did not start")
    proc.terminate()
    sys.exit(1)
print("chatterbox is ready")

# Run protocol test
print("connecting to chatterbox...")
sock = socket.create_connection(("127.0.0.1", 17933), timeout=30)
text = "Test."
payload = text.encode("utf-8")
header = struct.pack("<IIIIII", 0x32525454, 2, 1, 0, 1, 1) + struct.pack("<I", len(payload))
print(f"sending {len(header)} header bytes + {len(payload)} payload bytes")
print(f"header hex: {header.hex()}")
sock.sendall(header + payload)
print("sent, waiting for response...")

# Read 8 fields
data = b""
try:
    while len(data) < 32:
        chunk = sock.recv(32 - len(data))
        if not chunk:
            print("connection closed by peer during header read")
            break
        data += chunk
    if len(data) == 32:
        magic, ver, kind, e, r, p, c, length = struct.unpack("<IIIIIIII", data)
        print(f"response header: magic={magic:08x} ver={ver} kind={kind} epoch={e} response={r} piece={p} chunk={c} length={length}")
        if length:
            payload_data = b""
            while len(payload_data) < length:
                chunk = sock.recv(length - len(payload_data))
                if not chunk:
                    break
                payload_data += chunk
            print(f"payload: {payload_data[:100]!r}")
        # Try to read more
        try:
            sock.settimeout(2.0)
            more = sock.recv(4096)
            print(f"more data: {more[:200]!r}")
        except socket.timeout:
            print("no more data (timeout)")
except Exception as e:
    print(f"exception during recv: {e!r}")
sock.close()
print(f"shutting down chatterbox pid={proc.pid}")
proc.terminate()
try:
    proc.wait(timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()
print("chatterbox log tail:")
print(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-10:])
