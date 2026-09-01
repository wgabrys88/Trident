"""Record from 'What U Hear' (Creative X-Fi stereo mix) while TTS plays.

This uses FFmpeg with dshow to record from the stereo-mix device
while the trident TTS system plays audio to the default output.
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
CAPTURE_WAV = ROOT / "tools" / "tts_capture_16k.wav"
TEST_TEXT_FILE = ROOT / "tools" / "test_full_validation.txt"

PROTOCOL_MAGIC, PROTOCOL_VERSION = 0x32525454, 2
REQ_SYNTH, REQ_ADVANCE, REQ_CLOSE = 1, 2, 3
RESP_PCM, RESP_DONE, RESP_CANCELLED, RESP_ERROR, RESP_CLOSED = 1, 2, 3, 4, 5
PORTS = {"parakeet": 17931, "gemma": 17932, "chatterbox": 17933}
STARTUP_TIMEOUT = 300
SYNTH_TIMEOUT = 3600
ASR_RATE = 16000
CAPTURE_RATE = 48000
CAPTURE_DEVICE = '@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{5A8C2A91-1B7E-4F65-93B3-92C68F8FABCD}'

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
log = logging.getLogger("validate")


def wait_for_port(port, timeout, label):
    log.info("waiting for %s on port %d (timeout %ds)...", label, port, timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                log.info("%s is listening on port %d", label, port)
                return True
        time.sleep(0.5)
    log.error("timed out waiting for %s on port %d", label, port)
    return False


def start_parakeet():
    log.info("=== STEP 1/4: starting parakeet ASR server ===")
    proc = subprocess.Popen(
        [str(PARAKEET_EXE), "--model", str(PARAKEET_MODEL), "--port", str(PORTS["parakeet"])],
        cwd=str(PARAKEET_EXE.parent),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log.info("parakeet subprocess started pid=%d", proc.pid)
    if not wait_for_port(PORTS["parakeet"], STARTUP_TIMEOUT, "parakeet"):
        proc.terminate()
        raise RuntimeError("parakeet failed to start within timeout")
    return proc


def start_chatterbox():
    log.info("=== STEP 2/4: starting chatterbox TTS server ===")
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
    log.info("chatterbox subprocess started pid=%d", proc.pid)
    if not wait_for_port(PORTS["chatterbox"], STARTUP_TIMEOUT, "chatterbox"):
        proc.terminate()
        raise RuntimeError("chatterbox failed to start within timeout")
    return proc


def synthesize_and_capture(text):
    log.info("=== STEP 3/4: synthesizing via native protocol ===")
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    log.info("text has %d sentences; total %d chars", len(sentences), len(text))
    sock = socket.create_connection(("127.0.0.1", PORTS["chatterbox"]), timeout=60)
    sock.settimeout(SYNTH_TIMEOUT)
    chunk_id = 0
    pcm_total = b""
    t0 = time.time()
    for i, sent in enumerate(sentences):
        if not sent.endswith("."):
            sent += "."
        payload = sent.encode("utf-8")
        header = struct.pack("<IIIIII", PROTOCOL_MAGIC, PROTOCOL_VERSION, REQ_SYNTH,
                             0, i + 1, 1) + struct.pack("<I", len(payload))
        log.info("[synth] sending sentence %d/%d (%d bytes): %r",
                 i + 1, len(sentences), len(payload), sent[:60])
        try:
            sock.sendall(header + payload)
        except ConnectionResetError as e:
            log.error("[synth] connection reset on send: %s", e)
            log.error("[synth] protocol handshake failed; chatterbox may not be accepting the request format")
            sock.close()
            return b""
        sentence_start = time.time()
        while True:
            try:
                resp_header = b""
                while len(resp_header) < 32:
                    chunk = sock.recv(32 - len(resp_header))
                    if not chunk:
                        raise RuntimeError("chatterbox closed connection unexpectedly")
                    resp_header += chunk
            except (ConnectionResetError, RuntimeError) as e:
                log.error("[synth] recv failed for sentence %d: %s", i + 1, e)
                sock.close()
                return b""
            magic, ver, kind, e_, r, p, c, length = struct.unpack("<IIIIIIII", resp_header)
            if magic != PROTOCOL_MAGIC or ver != PROTOCOL_VERSION:
                raise RuntimeError(f"bad protocol frame magic={magic:x} ver={ver}")
            data = b""
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    raise RuntimeError("chatterbox closed connection during payload")
                data += chunk
            if kind == RESP_PCM:
                chunk_id += 1
                pcm_total += data
                dur = len(data) / 48000.0
                log.info("[synth] chunk %2d: %6d bytes (%5.2fs) -- cumulative %5.2fs of PCM",
                         chunk_id, len(data), dur, len(pcm_total) / 48000.0)
            elif kind == RESP_DONE:
                elapsed = time.time() - sentence_start
                log.info("[synth] sentence %d done in %.2fs (%d PCM chunks)", i + 1, elapsed, chunk_id)
                break
            elif kind == RESP_ERROR:
                err = data.decode("utf-8", errors="replace")
                log.error("[synth] sentence %d error: %s", i + 1, err)
                sock.close()
                return b""
    header = struct.pack("<IIIIII", PROTOCOL_MAGIC, PROTOCOL_VERSION, REQ_CLOSE, 0, 0, 0) + struct.pack("<I", 0)
    try:
        sock.sendall(header)
    except OSError as e:
        log.warning("close send failed (expected): %s", e)
    sock.close()
    elapsed = time.time() - t0
    log.info("[synth] complete in %.2fs: %d chunks, %d bytes (%.2fs) of PCM",
             elapsed, chunk_id, len(pcm_total), len(pcm_total) / 48000.0)
    return pcm_total


def transcribe(wav_path):
    log.info("=== STEP 4/4: transcribing with parakeet ===")
    url = f"http://127.0.0.1:{PORTS['parakeet']}/v1/audio/transcriptions"
    log.info("POST %s with %s (%d bytes)", url, wav_path.name, wav_path.stat().st_size)
    boundary = "----trident" + os.urandom(4).hex()
    wav_data = wav_path.read_bytes()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"utterance.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
        + wav_data + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nparakeet\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read()
    elapsed = time.time() - t0
    log.info("parakeet responded in %.2fs, %d bytes", elapsed, len(raw))
    result = json.loads(raw)
    text = result.get("text", "")
    log.info("transcribed text: %r", text)
    return text


def resample_to_16k(pcm_24k_bytes):
    """Resample 24kHz int16 mono to 16kHz int16 mono using scipy."""
    audio = np.frombuffer(pcm_24k_bytes, dtype="<i2")
    try:
        from scipy.signal import resample_poly
        out = resample_poly(audio, 2, 3)
    except ImportError:
        # Fallback: 24k -> 8k (decimate 3x) -> 16k (interp 2x)
        decim = audio[::3].astype(np.float32)
        out_f = np.empty(len(decim) * 2, dtype=np.float32)
        out_f[0::2] = decim
        out_f[1::2] = (decim[:-1] + decim[1:]) / 2.0
        out = out_f
    return np.clip(out, -32768, 32767).astype("<i2").tobytes()


def main():
    text = TEST_TEXT_FILE.read_text(encoding="utf-8").strip()
    log.info("loaded test text: %d chars", len(text))
    parakeet_proc = chatterbox_proc = None
    t0 = time.time()
    try:
        parakeet_proc = start_parakeet()
        chatterbox_proc = start_chatterbox()
        pcm = synthesize_and_capture(text)
        if not pcm:
            log.error("FAILED: no PCM produced")
            return 1
        # PCM is 24kHz int16 mono (from chatterbox's wire format)
        pcm_16k = resample_to_16k(pcm)
        log.info("resampled 24k -> 16k: %d -> %d bytes", len(pcm), len(pcm_16k))
        # Write WAV file at 16kHz for parakeet
        with wave.open(str(CAPTURE_WAV), "wb") as out:
            out.setparams((1, 2, ASR_RATE, 0, "NONE", "not compressed"))
            out.writeframes(pcm_16k)
        log.info("wrote %s (%d bytes, %.2fs)", CAPTURE_WAV, len(pcm_16k), len(pcm_16k)/(ASR_RATE*2))
        asr_text = transcribe(CAPTURE_WAV)
        log.info("=== RESULTS ===")
        print()
        print("=" * 70)
        print("ORIGINAL TEXT (input to TTS):")
        print("=" * 70)
        print(text)
        print()
        print("=" * 70)
        print(f"ASR TRANSCRIPTION (parakeet, {len(pcm_16k)/(ASR_RATE*2):.1f}s of audio):")
        print("=" * 70)
        print(asr_text)
        print("=" * 70)
        import re
        def norm(t):
            return set(re.findall(r"\w+", t.casefold()))
        orig_words = norm(text)
        asr_words = norm(asr_text)
        common = orig_words & asr_words
        if orig_words:
            recall = len(common) / len(orig_words)
            print(f"\nWord recall: {len(common)}/{len(orig_words)} = {recall:.1%}")
            missed = sorted(orig_words - asr_words)
            added = sorted(asr_words - orig_words)
            print(f"Words missed by ASR: {missed[:20]}")
            print(f"Words added by ASR: {added[:20]}")
        log.info("total run time: %.1fs", time.time() - t0)
        return 0
    finally:
        for proc, name in [(chatterbox_proc, "chatterbox"), (parakeet_proc, "parakeet")]:
            if proc and proc.poll() is None:
                log.info("terminating %s pid=%d", name, proc.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log.warning("%s did not exit gracefully, killing", name)
                    proc.kill()


if __name__ == "__main__":
    sys.exit(main())
