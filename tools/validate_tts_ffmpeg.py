"""Trident TTS-to-ASR validation using FFmpeg + WASAPI loopback capture.

Strategy:
1. Start parakeet ASR server.
2. Start ffmpeg in the background to record the system default WASAPI output
   into a WAV file. The TTS audio that goes to the default speaker is captured
   here.
3. Start chatterbox TTS server.
4. Send the test text over the native TTS protocol.
5. Wait for ffmpeg to capture all audio (a bit of buffer after the last chunk).
6. Stop ffmpeg, send the captured WAV to parakeet for self-validation.
"""
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

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
RUNTIMES = ROOT / "tools" / "runtime"
TTS_EXE = RUNTIMES / "tts" / "chatterbox-server.exe"
PARAKEET_EXE = RUNTIMES / "parakeet" / "parakeet-v0.5.0-bin-win-vulkan-x64" / "parakeet-server.exe"
PARAKEET_MODEL = MODELS / "tdt-0.6b-v3-q4_k.gguf"
T3_MODEL = MODELS / "chatterbox-t3-mtl-v3-cangjie-q4_0.gguf"
S3GEN_MODEL = MODELS / "chatterbox-s3gen-mtl-v3-f16.gguf"
VOICE_REF = ROOT / "data" / "ref-trump.wav"
CAPTURE_WAV = ROOT / "tools" / "tts_capture.wav"
TEST_TEXT_FILE = ROOT / "tools" / "test_full_validation.txt"

PROTOCOL_MAGIC, PROTOCOL_VERSION = 0x32525454, 2
REQ_SYNTH, REQ_ADVANCE, REQ_CLOSE = 1, 2, 3
RESP_PCM, RESP_DONE, RESP_CANCELLED, RESP_ERROR, RESP_CLOSED = 1, 2, 3, 4, 5
PORTS = {"parakeet": 17931, "gemma": 17932, "chatterbox": 17933}


def wait_for_port(port, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def start_parakeet():
    print("[1/5] Starting parakeet ASR server...")
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


def start_ffmpeg_capture(duration_hint):
    """Capture the default WASAPI output (loopback) to a WAV file via ffmpeg.

    Uses dshow on the 'Remote Audio' device as a fallback if WASAPI loopback
    is not available. The default dshow 'Remote Audio' device is the mic input
    in this environment, so we use a Python sounddevice-based capture instead.
    """
    print("[2/5] Starting audio capture (sounddevice WASAPI loopback)...")
    import sounddevice as sd

    # Discover the default WASAPI output device
    wasapi = next((i for i, api in enumerate(sd.query_hostapis()) if "wasapi" in api["name"].casefold()), None)
    if wasapi is None:
        raise RuntimeError("WASAPI host API not available")
    out_idx = sd.query_hostapis()[wasapi]["default_output_device"]
    if out_idx < 0:
        raise RuntimeError("no default output device")

    sample_rate = 24000
    channels = 1
    captured = bytearray()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"      capture status: {status}")
        captured.extend(bytes(indata))

    # Use an InputStream with the output device's loopback by setting
    # sd.WasapiSettings(loopback=True)
    wasapi_settings = sd.WasapiSettings(loopback=True, auto_convert=True)
    stream = sd.InputStream(
        samplerate=sample_rate, channels=channels, dtype="float32",
        device=out_idx, extra_settings=wasapi_settings, callback=callback,
    )
    stream.start()
    return stream, captured, sample_rate


def start_chatterbox():
    print("[3/5] Starting chatterbox TTS server...")
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
        cmd, cwd=str(TTS_EXE.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if not wait_for_port(PORTS["chatterbox"], timeout=300):
        proc.terminate()
        raise RuntimeError("chatterbox failed to start")
    print(f"      chatterbox ready on port {PORTS['chatterbox']}")
    return proc


def synthesize(text):
    """Send sentences to chatterbox over the native TTS protocol."""
    print("[4/5] Sending text to TTS and capturing audio...")
    sock = socket.create_connection(("127.0.0.1", PORTS["chatterbox"]), timeout=3600)
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    chunk_id = 0
    pcm_bytes_total = 0
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
                chunk_id += 1
                pcm_bytes_total += len(data)
                dur = len(data) / 48000.0
                print(f"      chunk {chunk_id}: {len(data):>6} bytes ({dur:5.2f}s) text={sent[:50]!r}")
            elif kind == RESP_DONE:
                break
            elif kind == RESP_ERROR:
                print(f"      ERROR: {data.decode('utf-8', errors='replace')}")
                return 0
    # Close cleanly
    header = struct.pack("<IIIIIII", PROTOCOL_MAGIC, PROTOCOL_VERSION, REQ_CLOSE, 0, 0, 0, 0)
    sock.sendall(header)
    sock.close()
    print(f"      total PCM produced: {pcm_bytes_total} bytes ({pcm_bytes_total/48000:.2f}s)")
    return pcm_bytes_total


def write_capture_to_wav(captured, sample_rate, path):
    """Convert the captured float32 buffer to a 16-bit PCM WAV at 16kHz (parakeet rate)."""
    print("      writing captured WAV file (resampled to 16kHz)...")
    audio = np.frombuffer(bytes(captured), dtype="<f4")
    if audio.size == 0:
        raise RuntimeError("no audio captured")
    # Resample from sample_rate to 16000 using numpy simple decimation
    # Use scipy if available, else simple linear interpolation
    try:
        from scipy.signal import resample_poly
        target_rate = 16000
        from math import gcd
        g = gcd(sample_rate, target_rate)
        up = target_rate // g
        down = sample_rate // g
        audio_16k = resample_poly(audio, up, down)
    except ImportError:
        # Simple linear interpolation fallback
        duration = len(audio) / sample_rate
        target_len = int(duration * 16000)
        x_old = np.linspace(0, duration, len(audio))
        x_new = np.linspace(0, duration, target_len)
        audio_16k = np.interp(x_new, x_old, audio).astype(np.float32)

    pcm = (np.clip(audio_16k, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(pcm)
    return len(pcm), len(audio_16k) / 16000.0


def transcribe(wav_path):
    print("[5/5] Transcribing captured audio with parakeet...")
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
    text = TEST_TEXT_FILE.read_text(encoding="utf-8").strip()
    parakeet_proc = chatterbox_proc = None
    capture_stream = None
    captured = None
    sample_rate = 24000
    try:
        parakeet_proc = start_parakeet()
        capture_stream, captured, sample_rate = start_ffmpeg_capture(duration_hint=60)
        # Small delay to ensure the capture is hot
        time.sleep(1.0)
        chatterbox_proc = start_chatterbox()
        # Send all text
        t0 = time.time()
        total_pcm = synthesize(text)
        # Wait for audio to finish playing through the output device
        # 24kHz mono, 4 bytes per sample (float32)
        expected_seconds = total_pcm / (sample_rate * 4) if total_pcm else 30
        wait = max(2.0, expected_seconds + 3.0)
        print(f"      waiting {wait:.1f}s for audio playback to complete...")
        time.sleep(wait)
        capture_stream.stop()
        capture_stream.close()
        capture_stream = None
        elapsed = time.time() - t0
        print(f"      total elapsed: {elapsed:.1f}s")
        nbytes, dur = write_capture_to_wav(captured, sample_rate, CAPTURE_WAV)
        print(f"      captured WAV: {nbytes} bytes ({dur:.2f}s)")
        if dur < 0.5:
            print("FAILED: captured audio is too short; default output device may not be loopback-capable")
            return 1
        asr_text = transcribe(CAPTURE_WAV)
        print()
        print("=" * 70)
        print("ORIGINAL TEXT:")
        print("=" * 70)
        print(text)
        print()
        print("=" * 70)
        print(f"ASR TRANSCRIPTION (parakeet, {dur:.1f}s of audio):")
        print("=" * 70)
        print(asr_text)
        print("=" * 70)
        return 0
    finally:
        if capture_stream is not None:
            try: capture_stream.stop(); capture_stream.close()
            except Exception: pass
        for proc, name in [(chatterbox_proc, "chatterbox"), (parakeet_proc, "parakeet")]:
            if proc and proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill()


if __name__ == "__main__":
    sys.exit(main())
