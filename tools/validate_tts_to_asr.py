"""Trident TTS-to-ASR validation script.

Runs TTS on a test text, captures the audio to a WAV file, then transcribes
it with parakeet to validate that the generated speech matches the input text.
"""
import io
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

# Paths
ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
RUNTIMES = ROOT / "tools" / "runtime"
TTS_EXE = RUNTIMES / "tts" / "chatterbox-server.exe"
PARAKEET_EXE = RUNTIMES / "parakeet" / "parakeet-v0.5.0-bin-win-vulkan-x64" / "parakeet-server.exe"
PARAKEET_MODEL = MODELS / "tdt-0.6b-v3-q4_k.gguf"
T3_MODEL = MODELS / "chatterbox-t3-mtl-v3-cangjie-q4_0.gguf"
S3GEN_MODEL = MODELS / "chatterbox-s3gen-mtl-v3-f16.gguf"
VOICE_REF = ROOT / "data" / "ref-trump.wav"
OUTPUT_WAV = ROOT / "tools" / "tts_validation_output.wav"

# Protocol constants (from runtime.py)
PROTOCOL_MAGIC, PROTOCOL_VERSION = 0x32525454, 2
REQ_SYNTH, REQ_ADVANCE, REQ_CLOSE = 1, 2, 3
RESP_PCM, RESP_DONE, RESP_CANCELLED, RESP_ERROR, RESP_CLOSED = 1, 2, 3, 4, 5
PORTS = {"parakeet": 17931, "gemma": 17932, "chatterbox": 17933}

# Test text
TEST_TEXT = """Jeden. Dwa. Trzy. Cztery. Pięć. Sześć. Siedem. Osiem. Dziewięć. Dziesięć.

Kot siedzi na macie i patrzy w niebo. Słońce świeci jasno nad naszym miastem.

Wczoraj wieczorem czytałem ciekawą książkę o podróżach morskich i żegludze oceanicznej.

Mały pies biega po zielonej trawie i głośno szczeka na przechodniów.

Na stole leżą czerwone jabłka, gruszki i kilka brzoskwiń, które pachną słodko i soczyście.

Wojna światowa była największym konfliktem zbrojnym w historii ludzkości.

Czasami lubię spacerować brzegiem morza i słuchać szumu fal o zmierzchu.

Dzieci bawią się w parku w piłkę nożną i śmieją się głośno z radości.

Wieczorem zjem kolację i obejrzę nowy film przyrodniczy o Afryce.

Rano wstaję o siódmej, jem śniadanie i idę do pracy na piechotę."""


def wait_for_port(port, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def start_parakeet():
    print("[1/4] Starting parakeet ASR server...")
    env = os.environ.copy()
    proc = subprocess.Popen(
        [str(PARAKEET_EXE), "--model", str(PARAKEET_MODEL), "--port", str(PORTS["parakeet"])],
        cwd=str(PARAKEET_EXE.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if not wait_for_port(PORTS["parakeet"], timeout=180):
        raise RuntimeError("parakeet failed to start")
    print(f"      parakeet ready on port {PORTS['parakeet']}")
    return proc


def start_chatterbox():
    print("[2/4] Starting chatterbox TTS server...")
    env = os.environ.copy()
    # Match runtime.py VULKAN_ENV behavior
    flags = [
        "--n-gpu-layers", "99", "--context", "2048", "--threads", "4",
        "--seed", "42", "--max-tokens", "1000", "--top-k", "0", "--top-p", "1.0",
        "--min-p", "0.05", "--temperature", "0.8", "--repeat-penalty", "1.2",
        "--cfg-weight", "0.5", "--exaggeration", "0.5", "--cfm-steps", "5",
        "--fastconv", "1",
    ]
    cmd = [
        str(TTS_EXE), "--run-id", "validation-test",
        "--family", "v3", "--model", str(T3_MODEL),
        "--s3gen-gguf", str(S3GEN_MODEL), "--reference", str(VOICE_REF),
        "--language", "pl", "--port", str(PORTS["chatterbox"]),
        *flags,
    ]
    proc = subprocess.Popen(
        cmd, env=env, cwd=str(TTS_EXE.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if not wait_for_port(PORTS["chatterbox"], timeout=300):
        raise RuntimeError("chatterbox failed to start")
    print(f"      chatterbox ready on port {PORTS['chatterbox']}")
    return proc


def synthesize_and_capture(text):
    print("[3/4] Synthesizing and capturing audio...")
    sock = socket.create_connection(("127.0.0.1", PORTS["chatterbox"]), timeout=3600)
    # Send REQ_SYNTH for each sentence
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    audio_chunks = []
    chunk_id = 0
    for i, sent in enumerate(sentences):
        if not sent.endswith("."):
            sent += "."
        epoch, response_id, piece_id = 0, i + 1, 1
        payload = sent.encode("utf-8")
        header = struct.pack("<IIIIIII", PROTOCOL_MAGIC, PROTOCOL_VERSION, REQ_SYNTH,
                             epoch, response_id, piece_id, len(payload))
        sock.sendall(header + payload)
        # Read response frames until RESP_DONE
        while True:
            resp_header = sock.recv(32)
            if len(resp_header) < 32:
                break
            magic, ver, kind, e, r, p, c, length = struct.unpack("<IIIIIIII", resp_header)
            assert magic == PROTOCOL_MAGIC and ver == PROTOCOL_VERSION
            data = b""
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    break
                data += chunk
            if kind == RESP_PCM:
                # PCM is 16-bit signed at 24kHz
                audio_chunks.append(data)
                chunk_id += 1
                print(f"      chunk {chunk_id}: {len(data)} bytes ({len(data)/48000:.2f}s)")
            elif kind == RESP_DONE:
                break
            elif kind == RESP_ERROR:
                print(f"      ERROR: {data.decode('utf-8', errors='replace')}")
                return None
    # Close cleanly
    header = struct.pack("<IIIIIII", PROTOCOL_MAGIC, PROTOCOL_VERSION, REQ_CLOSE, 0, 0, 0, 0)
    sock.sendall(header)
    sock.close()
    return audio_chunks


def write_wav(chunks, path):
    print(f"      writing {path}...")
    pcm = b"".join(chunks)
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        out.writeframes(pcm)
    return len(pcm)


def transcribe(wav_path):
    print("[4/4] Transcribing with parakeet...")
    url = f"http://127.0.0.1:{PORTS['parakeet']}/v1/audio/transcriptions"
    boundary = "----trident" + os.urandom(4).hex()
    with open(wav_path, "rb") as f:
        wav_data = f.read()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"utterance.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
        + wav_data + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nparakeet\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        result = json.loads(resp.read())
    return result.get("text", "")


def main():
    parakeet_proc = chatterbox_proc = None
    try:
        parakeet_proc = start_parakeet()
        chatterbox_proc = start_chatterbox()
        chunks = synthesize_and_capture(TEST_TEXT)
        if not chunks:
            print("FAILED: no audio generated")
            return 1
        write_wav(chunks, OUTPUT_WAV)
        text = transcribe(OUTPUT_WAV)
        print()
        print("=" * 70)
        print("ORIGINAL TEXT:")
        print("=" * 70)
        print(TEST_TEXT)
        print()
        print("=" * 70)
        print("ASR TRANSCRIPTION (parakeet):")
        print("=" * 70)
        print(text)
        print("=" * 70)
        return 0
    finally:
        for proc, name in [(parakeet_proc, "parakeet"), (chatterbox_proc, "chatterbox")]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    sys.exit(main())
