"""Minimal protocol test - just send one synthesize request and read response."""
import socket
import struct
import sys

MAGIC = 0x32525454
VERSION = 2
REQ_SYNTH = 1

sock = socket.create_connection(("127.0.0.1", 17933), timeout=30)
print(f"connected, peer={sock.getpeername()}")
text = "Test."
payload = text.encode("utf-8")
# 7 fields: magic, version, kind, epoch, response, piece, text_length
header = struct.pack("<IIIIII", MAGIC, VERSION, REQ_SYNTH, 0, 1, 1) + struct.pack("<I", len(payload))
print(f"sending {len(header)} header bytes + {len(payload)} payload bytes")
print(f"header hex: {header.hex()}")
sock.sendall(header + payload)
print("sent, waiting for response...")
# Read 8 fields for response
data = b""
while len(data) < 32:
    chunk = sock.recv(32 - len(data))
    if not chunk:
        print("connection closed by peer during header read")
        sys.exit(1)
    data += chunk
magic, ver, kind, e, r, p, c, length = struct.unpack("<IIIIIIII", data)
print(f"response header: magic={magic:08x} ver={ver} kind={kind} epoch={e} response={r} piece={p} chunk={c} length={length}")
if length:
    payload_data = b""
    while len(payload_data) < length:
        chunk = sock.recv(length - len(payload_data))
        if not chunk:
            print("connection closed during payload read")
            sys.exit(1)
        payload_data += chunk
    print(f"payload: {payload_data[:100]!r}")
sock.close()
print("done")
