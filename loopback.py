import difflib
import os
import re
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import hear
import mouth

ROOT = Path(__file__).resolve().parent
PROOF = ROOT / "loopback-proof"
PHRASE = "Trident cable loopback"
LEAD = 1.0
TAIL = 1.3


def die(message, code=2):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def python_exe():
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.is_file():
        return str(venv)
    return sys.executable


def norm(text):
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def score(phrase, transcript):
    left = norm(phrase)
    right = norm(transcript)
    if not left or not right:
        return 0.0, False
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    squeezed = difflib.SequenceMatcher(None, left.replace(" ", ""), right.replace(" ", "")).ratio()
    words = [word for word in left.split() if len(word) > 2]
    hits = sum(1 for word in words if word in right)
    ok = (
        left in right
        or right in left
        or ratio >= 0.6
        or squeezed >= 0.75
        or (len(words) >= 2 and hits >= len(words) - 1 and hits >= 2)
    )
    return max(ratio, squeezed), ok


def wav_seconds(path):
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.getnframes()
    if rate <= 0:
        return 0.0
    return frames / float(rate)


def wav_levels(path):
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        width = handle.getsampwidth()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    if width != 2 or not raw:
        return 0.0, 0.0
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    peak = float(np.max(np.abs(pcm))) if len(pcm) else 0.0
    rms = float(np.sqrt(np.mean(pcm ** 2))) if len(pcm) else 0.0
    return peak, rms


def write(name, text):
    PROOF.mkdir(parents=True, exist_ok=True)
    (PROOF / name).write_text(text, encoding="utf-8")


def cable_device_dump():
    import sounddevice as sd

    lines = ["default " + repr(list(sd.default.device))]
    for index, info in enumerate(sd.query_devices()):
        name = info["name"]
        lowered = name.lower()
        if "cable" not in lowered and "vb-audio" not in lowered:
            continue
        api = sd.query_hostapis(info["hostapi"])["name"]
        lines.append(
            str(index)
            + "\t"
            + api
            + "\tin "
            + str(info["max_input_channels"])
            + "\tout "
            + str(info["max_output_channels"])
            + "\tsr "
            + str(info["default_samplerate"])
            + "\t"
            + name
        )
    return "\n".join(lines) + "\n"


def run_captured(cmd, timeout):
    completed = subprocess.run(
        cmd,
        cwd=str(ROOT),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def git_head():
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def read_pipe(pipe, sink, ready, marker):
    try:
        for line in pipe:
            sink.append(line)
            if marker and marker in line:
                ready.set()
    finally:
        ready.set()


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="loopback.py")
    parser.add_argument("--phrase", default=PHRASE)
    parser.add_argument("--once", action="store_true", help="one-shot chatterbox for the spoken wav")
    args = parser.parse_args()
    phrase = args.phrase.strip()
    if not phrase:
        die("empty phrase")
    for name in ("chatterbox.exe", "nemo-speech.exe", "ear.gguf"):
        if not (ROOT / name).is_file():
            die("missing " + name)

    PROOF.mkdir(parents=True, exist_ok=True)
    py = python_exe()
    try:
        devices = cable_device_dump()
    except Exception as exc:
        devices = "device list failed: " + str(exc) + "\n"
    write("devices.txt", devices)

    try:
        capture_index, capture_name = hear.pick_mic(None, allow_cable=True)
        play_index, play_name = mouth.pick_out(None, True)
    except SystemExit:
        write(
            "status.txt",
            "STATUS FAIL\nreason: VB-Cable device missing\n" + devices,
        )
        die("VB-Cable device missing")

    intent = (
        "phrase " + phrase + "\n"
        "play_index " + str(play_index) + "\n"
        "play_device " + play_name + "\n"
        "capture_index " + str(capture_index) + "\n"
        "capture_device " + capture_name + "\n"
    )
    write("intent.txt", intent)
    print(intent, end="", file=sys.stderr)

    synth_cmd = [py, str(ROOT / "mouth.py"), "--no-play"]
    if args.once:
        synth_cmd.append("--once")
    synth_cmd.append(phrase)
    try:
        synth_code, synth_out, synth_err = run_captured(synth_cmd, 300)
    except subprocess.TimeoutExpired:
        write("status.txt", "STATUS FAIL\nreason: mouth synthesize timed out\n" + intent)
        die("mouth synthesize timed out")
    write("synth.txt", "exit " + str(synth_code) + "\n\nstdout:\n" + synth_out + "\nstderr:\n" + synth_err)
    spoken = ""
    for line in synth_out.splitlines():
        if line.strip():
            spoken = line.strip()
    spoken_path = Path(spoken) if spoken else None
    if synth_code != 0 or spoken_path is None or not spoken_path.is_file():
        write(
            "status.txt",
            "STATUS FAIL\nreason: mouth synthesize failed\nsynth_exit "
            + str(synth_code)
            + "\n"
            + intent,
        )
        raise SystemExit(synth_code or 2)

    duration = wav_seconds(spoken_path)
    if duration <= 0:
        die("spoken wav is empty")
    seconds = max(2.0, LEAD + duration + TAIL)
    hear_wav = PROOF / "hear.wav"
    hear_cmd = [
        py,
        str(ROOT / "hear.py"),
        "--vb-cable",
        "--language",
        "en",
        "--save-wav",
        str(hear_wav),
        f"{seconds:.2f}",
    ]
    hear_proc = subprocess.Popen(
        hear_cmd,
        cwd=str(ROOT),
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    hear_err = []
    hear_out = []
    ready = threading.Event()
    err_thread = threading.Thread(target=read_pipe, args=(hear_proc.stderr, hear_err, ready, "hear mic:"), daemon=True)
    out_thread = threading.Thread(target=read_pipe, args=(hear_proc.stdout, hear_out, threading.Event(), ""), daemon=True)
    err_thread.start()
    out_thread.start()

    deadline = time.time() + 30
    while time.time() < deadline:
        if any("hear mic:" in line for line in list(hear_err)):
            break
        if hear_proc.poll() is not None:
            break
        time.sleep(0.05)
    if not any("hear mic:" in line for line in list(hear_err)):
        if hear_proc.poll() is None:
            hear_proc.kill()
        hear_proc.wait(timeout=15)
        err_text = "".join(hear_err)
        write("hear.txt", "exit " + str(hear_proc.returncode) + "\n\nstderr:\n" + err_text)
        write("status.txt", "STATUS FAIL\nreason: hear did not open the cable\n" + intent + "\n" + err_text)
        die("hear did not open the cable")

    time.sleep(LEAD)
    play_cmd = [py, str(ROOT / "mouth.py"), "--play-wav", str(spoken_path), "--vb-cable"]
    try:
        play_code, play_out, play_err = run_captured(play_cmd, max(60, int(duration) + 30))
    except subprocess.TimeoutExpired:
        play_code, play_out, play_err = 2, "", "play timed out\n"
    write("play.txt", "exit " + str(play_code) + "\n\nstdout:\n" + play_out + "\nstderr:\n" + play_err)

    try:
        hear_proc.wait(timeout=seconds + 180)
    except subprocess.TimeoutExpired:
        hear_proc.kill()
        hear_proc.wait(timeout=15)
    err_thread.join(timeout=5)
    out_thread.join(timeout=5)
    transcript = "".join(hear_out).strip()
    err_text = "".join(hear_err)
    write("hear.txt", "exit " + str(hear_proc.returncode) + "\n\nstdout:\n" + transcript + "\n\nstderr:\n" + err_text)
    write("transcript.txt", transcript + ("\n" if transcript else ""))

    peak, rms = (0.0, 0.0)
    if hear_wav.is_file():
        try:
            peak, rms = wav_levels(hear_wav)
        except Exception as exc:
            peak, rms = 0.0, 0.0
            err_text += "\nlevel read failed: " + str(exc) + "\n"
    ratio, ok = score(phrase, transcript)
    passed = ok and hear_proc.returncode == 0 and play_code == 0
    head = git_head()
    status = "PASS" if passed else "FAIL"
    body = (
        "STATUS " + status + "\n"
        "phrase: " + phrase + "\n"
        "transcript: " + transcript + "\n"
        "ratio: " + f"{ratio:.3f}" + "\n"
        "synth_exit: " + str(synth_code) + "\n"
        "play_exit: " + str(play_code) + "\n"
        "hear_exit: " + str(hear_proc.returncode) + "\n"
        "play_device: " + play_name + "\n"
        "capture_device: " + capture_name + "\n"
        "spoken_wav: " + str(spoken_path) + "\n"
        "hear_wav: " + str(hear_wav) + "\n"
        "peak: " + f"{peak:.5f}" + "\n"
        "rms: " + f"{rms:.5f}" + "\n"
        "seconds: " + f"{seconds:.2f}" + "\n"
        "head: " + head + "\n"
        "synth_cmd: " + subprocess.list2cmdline(synth_cmd) + "\n"
        "hear_cmd: " + subprocess.list2cmdline(hear_cmd) + "\n"
        "play_cmd: " + subprocess.list2cmdline(play_cmd) + "\n"
    )
    write("status.txt", body)
    print(body, end="")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
