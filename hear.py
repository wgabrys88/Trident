import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def configure_stdio_utf8():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def emit(message, file=sys.stderr):
    try:
        print(message, file=file, flush=True)
        return
    except UnicodeEncodeError:
        pass
    encoding = getattr(file, "encoding", None) or "utf-8"
    data = (message + "\n").encode(encoding, errors="replace")
    buffer = getattr(file, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:
        file.write(data.decode(encoding, errors="replace"))
        file.flush()


def hostapi_name(sd, info):
    try:
        return sd.query_hostapis(int(info["hostapi"]))["name"]
    except Exception:
        return ""


def prefer_wasapi(matches, sd):
    ranked = []
    for index, info in matches:
        api = hostapi_name(sd, info).lower()
        if "wasapi" in api:
            rank = 0
        elif "directsound" in api:
            rank = 1
        elif "mme" in api:
            rank = 2
        else:
            rank = 3
        ranked.append((rank, index, info))
    ranked.sort(key=lambda item: (item[0], item[1]))
    index, info = ranked[0][1], ranked[0][2]
    return index, info["name"]


def cable_capture_hits(devices):
    hits = []
    for index, info in enumerate(devices):
        if info["max_input_channels"] < 1:
            continue
        name = info["name"].lower()
        if "cable output" not in name or "vb-audio" not in name:
            continue
        hits.append((index, info))
    virtual = [item for item in hits if "virtual" in item[1]["name"].lower()]
    return virtual or hits


def pick_mic(prefer: str | None, allow_cable: bool = False):
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
        matches = []
        for index, info in enumerate(devices):
            if info["max_input_channels"] < 1:
                continue
            name = info["name"]
            if prefer_l not in name.lower():
                continue
            if not allow_cable and "cable" in name.lower():
                continue
            matches.append((index, info))
        if not matches:
            die("unknown mic: " + prefer)
        if allow_cable and len(matches) > 1:
            return prefer_wasapi(matches, sd)
        return matches[0][0], matches[0][1]["name"]
    if allow_cable:
        hits = cable_capture_hits(devices)
        if not hits:
            die("VB-Cable capture device not found")
        return prefer_wasapi(hits, sd)
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


def transcribe_wav(wav: Path, args):
    command = [
        str(ROOT / "nemo-speech.exe"),
        "transcribe",
        str(wav),
        "--model",
        str(Path(args.model)),
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
        completed = subprocess.run(
            command,
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        die("cannot run nemo-speech.exe: " + str(exc))
    if completed.stderr:
        emit(completed.stderr.rstrip("\n"), file=sys.stderr)
    text = completed.stdout or ""
    sys.stdout.write(text)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")
    raise SystemExit(completed.returncode)


def resample_linear(pcm, src_rate, dst_rate):
    import numpy as np

    pcm = np.asarray(pcm, dtype=np.float32)
    if src_rate == dst_rate or len(pcm) == 0:
        return pcm
    new_len = max(1, int(round(len(pcm) * float(dst_rate) / float(src_rate))))
    if len(pcm) == 1:
        return np.full(new_len, pcm[0], dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, num=len(pcm), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
    return np.interp(x_new, x_old, pcm.astype(np.float64)).astype(np.float32)


def record_wav(path: Path, seconds: float, mic_index: int, rate: int):
    import numpy as np
    import sounddevice as sd

    info = sd.query_devices(mic_index)
    device_rate = int(round(float(info["default_samplerate"])))

    def capture(sample_rate):
        frames = int(seconds * sample_rate)
        audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32", device=mic_index)
        sd.wait()
        return audio

    capture_rate = rate
    try:
        audio = capture(capture_rate)
    except sd.PortAudioError as first:
        sd.stop()
        if device_rate == rate:
            die("cannot record: " + str(first))
        capture_rate = device_rate
        emit("hear rate: device " + str(capture_rate) + " -> wav " + str(rate))
        try:
            audio = capture(capture_rate)
        except sd.PortAudioError as second:
            die("cannot record: " + str(second))
    pcm = np.clip(audio.reshape(-1), -1.0, 1.0)
    if capture_rate != rate:
        pcm = resample_linear(pcm, capture_rate, rate)
        pcm = np.clip(pcm, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm16.tobytes())


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="hear.py")
    parser.add_argument("seconds", nargs="?", type=float, default=None, help="how long to listen on the PC mic")
    parser.add_argument("--wav", default=None, help="transcribe this wav and skip the mic")
    parser.add_argument("--model", default=str(ROOT / "ear.gguf"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--language", default=None)
    parser.add_argument("--format", default="text")
    parser.add_argument("--rate", type=int, default=16000)
    parser.add_argument("--mic", default=None, help="device index or name substring (never VB-Cable by default)")
    parser.add_argument("--vb-cable", action="store_true", help="record CABLE Output (VB-Audio Virtual Cable)")
    parser.add_argument("--save-wav", default=None, help="copy the recording to this path before transcribe")
    parser.add_argument("--endpointing", default="on", choices=("on", "off"))
    parser.add_argument("--stop-history-eou-ms", default="1200")
    parser.add_argument("--verbatim", action="store_true")
    parser.add_argument("--no-punctuation", action="store_true")
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()

    if args.wav is not None and args.wav.strip() == "":
        die("empty wav")
    wav_path = None
    if args.wav is not None:
        wav_path = Path(args.wav).expanduser()
        if not wav_path.is_file():
            die("missing wav: " + str(wav_path))
        wav_path = wav_path.resolve()
    elif args.seconds is None or args.seconds <= 0:
        die("seconds must be > 0")
    if args.rate < 8000:
        die("rate too low")

    nemo = ROOT / "nemo-speech.exe"
    if not nemo.is_file():
        die("missing nemo-speech.exe")
    model = Path(args.model)
    if not model.is_file():
        die("missing model: " + str(model))

    if wav_path is not None:
        emit("hear wav: " + str(wav_path), file=sys.stderr)
        os.chdir(ROOT)
        transcribe_wav(wav_path, args)

    mic_index, mic_name = pick_mic(args.mic, allow_cable=args.vb_cable)
    emit("hear mic: " + mic_name, file=sys.stderr)

    os.chdir(ROOT)
    with tempfile.TemporaryDirectory(prefix="hear_", dir=str(ROOT)) as tmp:
        wav = Path(tmp) / "utterance.wav"
        record_wav(wav, args.seconds, mic_index, args.rate)
        if args.save_wav:
            dest = Path(args.save_wav).expanduser()
            if not dest.is_absolute():
                dest = (ROOT / dest).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(wav, dest)
            emit("hear wav saved: " + str(dest), file=sys.stderr)
        transcribe_wav(wav, args)


if __name__ == "__main__":
    main()
