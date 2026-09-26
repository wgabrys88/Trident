"""Hearing one-shot. Record the PC mic for SECONDS, then run nemo-speech once; print transcript to stdout."""

import argparse
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def pick_mic(prefer: str | None):
    try:
        import sounddevice as sd
    except ImportError:
        die("missing sounddevice; install with: .venv\\Scripts\\python.exe -m pip install sounddevice")
    devices = sd.query_devices()
    default_in = sd.default.device[0]
    if prefer is not None:
        prefer_l = prefer.lower()
        if prefer.isdigit():
            index = int(prefer)
            if index < 0 or index >= len(devices) or devices[index]["max_input_channels"] < 1:
                die("unknown mic: " + prefer)
            return index, devices[index]["name"]
        for index, info in enumerate(devices):
            if info["max_input_channels"] < 1:
                continue
            name = info["name"]
            if prefer_l in name.lower() and "cable" not in name.lower():
                return index, name
        die("unknown mic: " + prefer)
    # Prefer default input when it is not VB-Cable; else first non-cable mic.
    if default_in is not None and 0 <= default_in < len(devices):
        name = devices[default_in]["name"]
        if devices[default_in]["max_input_channels"] >= 1 and "cable" not in name.lower():
            return default_in, name
    for index, info in enumerate(devices):
        if info["max_input_channels"] < 1:
            continue
        name = info["name"]
        if "cable" in name.lower():
            continue
        if "microphone" in name.lower() or "mic" in name.lower():
            return index, name
    die("no non-cable microphone found")


def record_wav(path: Path, seconds: float, mic_index: int, rate: int):
    import numpy as np
    import sounddevice as sd

    frames = int(seconds * rate)
    audio = sd.rec(frames, samplerate=rate, channels=1, dtype="float32", device=mic_index)
    sd.wait()
    pcm = np.clip(audio.reshape(-1), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm16.tobytes())


def main():
    parser = argparse.ArgumentParser(prog="hear.py")
    parser.add_argument("seconds", type=float, help="how long to listen on the PC mic")
    parser.add_argument("--model", default=str(ROOT / "ear.gguf"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--language", default=None)
    parser.add_argument("--format", default="text")
    parser.add_argument("--rate", type=int, default=16000)
    parser.add_argument("--mic", default=None, help="device index or name substring (never VB-Cable by default)")
    parser.add_argument("--endpointing", default="on", choices=("on", "off"))
    parser.add_argument("--stop-history-eou-ms", default="1200")
    parser.add_argument("--verbatim", action="store_true")
    parser.add_argument("--no-punctuation", action="store_true")
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()

    if args.seconds <= 0:
        die("seconds must be > 0")
    if args.rate < 8000:
        die("rate too low")

    nemo = ROOT / "nemo-speech.exe"
    if not nemo.is_file():
        die("missing nemo-speech.exe")
    model = Path(args.model)
    if not model.is_file():
        die("missing model: " + str(model))

    mic_index, mic_name = pick_mic(args.mic)
    print("hear mic: " + mic_name, file=sys.stderr)

    os.chdir(ROOT)
    with tempfile.TemporaryDirectory(prefix="hear_", dir=str(ROOT)) as tmp:
        wav = Path(tmp) / "utterance.wav"
        record_wav(wav, args.seconds, mic_index, args.rate)
        command = [
            str(nemo),
            "transcribe",
            str(wav),
            "--model",
            str(model),
            "--device",
            args.device,
            "--format",
            args.format,
            "--stop-history-eou-ms",
            str(args.stop_history_eou_ms),
            "--quiet",
        ]
        if args.language is not None and args.language.strip() != "":
            command.extend(["--language", args.language])
        if args.endpointing == "off":
            command.append("--endpointing=false")
        else:
            command.append("--endpointing=true")
        if args.verbatim:
            command.append("--verbatim")
        if args.no_punctuation:
            command.append("--no-punctuation")
        if args.stream:
            command.append("--stream")
        try:
            completed = subprocess.run(command, cwd=ROOT, shell=False, capture_output=True, text=True)
        except OSError as exc:
            die("cannot run nemo-speech.exe: " + str(exc))
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        text = completed.stdout
        if text is None:
            text = ""
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
