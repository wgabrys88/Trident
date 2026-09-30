import argparse
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_SPEECH_MS = 400
MIN_PEAK = 800
VAD_PROC = None


class EarError(Exception):
    pass


def die(message):
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(2)


def configure_stdio_utf8():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def capture_open():
    return (ROOT / "ear.go").is_file()


def asr_argv(wav):
    return [
        str(ROOT / "nemo-speech.exe"),
        "transcribe",
        str(wav),
        "--model",
        str(ROOT / "ear.gguf"),
        "--device",
        "cpu",
        "--format",
        "json",
        "--verbatim",
        "--quiet",
        "--endpointing=true",
        "--stop-history-eou-ms",
        "1200",
    ]


def parse_transcript(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise EarError("hear json")
    if not isinstance(data, dict):
        raise EarError("hear json")
    text = data.get("text")
    if not isinstance(text, str):
        raise EarError("hear json")
    text = " ".join(text.split())
    langs = data.get("languages")
    if not isinstance(langs, list) or not langs or not isinstance(langs[0], str) or not langs[0].strip():
        raise EarError("hear language")
    if not text:
        raise EarError("hear empty")
    return text, langs[0].strip()


def transcribe(wav):
    wav = Path(wav)
    if not wav.is_file():
        raise EarError("missing wav: " + str(wav))
    if wav.stat().st_size <= 0:
        raise EarError("hear empty")
    try:
        completed = subprocess.run(
            asr_argv(wav),
            cwd=str(ROOT),
            shell=False,
            capture_output=True,
        )
    except OSError as exc:
        raise EarError("cannot run nemo-speech.exe: " + str(exc))
    if completed.returncode != 0:
        err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise EarError(err or ("hear exit " + str(completed.returncode)))
    raw = (completed.stdout or b"").decode("utf-8", errors="replace")
    return parse_transcript(raw)


def wasapi_capture_name():
    try:
        import sounddevice as sd
    except ImportError:
        die("missing sounddevice")
    for api in sd.query_hostapis():
        if "wasapi" not in api["name"].lower():
            continue
        index = api["default_input_device"]
        if index is None or index < 0:
            continue
        name = sd.query_devices(index)["name"]
        if "cable" in name.lower():
            continue
        return name
    die("no non-cable WASAPI microphone")


def write_listen_card(name):
    source = ROOT / "vad.txt"
    if not source.is_file():
        die("missing vad.txt")
    lines = []
    replaced = False
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("vad.device "):
            lines.append("vad.device " + name)
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        die("vad.txt missing vad.device")
    (ROOT / "vad_run.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_file(name):
    path = ROOT / name
    if not path.is_file():
        return
    try:
        path.unlink()
    except OSError as exc:
        die("cannot remove " + path.name + ": " + str(exc))


def start_vad(name):
    global VAD_PROC
    exe = ROOT / "vad.exe"
    if not exe.is_file():
        die("missing vad.exe")
    write_listen_card(name)
    for name_in in ("vad.utterance.txt", "vad.hold", "vad.stop"):
        remove_file(name_in)
    VAD_PROC = subprocess.Popen(
        [str(exe), "--resident", "vad_run.txt"],
        cwd=str(ROOT),
        shell=False,
        stdin=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    pid_path = ROOT / "vad.pid"
    while time.time() < deadline:
        if VAD_PROC.poll() is not None:
            die("vad exited " + str(VAD_PROC.returncode))
        if pid_path.is_file():
            text = pid_path.read_text(encoding="utf-8", errors="replace")
            if "ready" in text.split():
                return
        time.sleep(0.05)
    die("vad did not become ready")


def stop_vad():
    global VAD_PROC
    proc = VAD_PROC
    if proc is None:
        return
    VAD_PROC = None
    try:
        (ROOT / "vad.stop").write_bytes(b"")
    except OSError as exc:
        print("cannot write vad.stop: " + str(exc), file=sys.stderr, flush=True)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    for name in ("vad.stop", "vad.hold"):
        try:
            remove_file(name)
        except SystemExit:
            pass


def set_hold(on):
    path = ROOT / "vad.hold"
    if on:
        path.write_bytes(b"")
        return
    if path.is_file():
        path.unlink()


def judge_wav(path):
    try:
        handle = wave.open(str(path), "rb")
    except (wave.Error, OSError, EOFError):
        return "unreadable", 0
    with handle:
        rate = handle.getframerate() or 0
        width = handle.getsampwidth()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if rate <= 0:
        return "unreadable", 0
    ms = int(frames * 1000 / rate)
    if ms < MIN_SPEECH_MS:
        return "short", ms
    if pcm_peak(raw, width) < MIN_PEAK:
        return "quiet", ms
    return "keep", ms


def pcm_peak(raw, width):
    if not raw or width <= 0:
        return 0
    if width == 2:
        count = len(raw) // 2
        peak = 0
        for index in range(count):
            sample = int.from_bytes(raw[index * 2 : index * 2 + 2], "little", signed=True)
            value = sample if sample >= 0 else -sample
            if value > peak:
                peak = value
        return peak
    peak = 0
    for byte in raw:
        if byte > peak:
            peak = byte
    return peak


def log_vad(kind, ms):
    if kind == "keep":
        print("vad: keep " + str(ms) + "ms", file=sys.stderr, flush=True)
        return
    if kind == "short":
        print("vad: drop short " + str(ms) + "ms", file=sys.stderr, flush=True)
        return
    if kind == "quiet":
        print("vad: drop quiet", file=sys.stderr, flush=True)
        return
    print("vad: drop " + kind, file=sys.stderr, flush=True)


def take_utterance(timeout=None):
    path = ROOT / "vad.utterance.txt"
    deadline = None if timeout is None else time.monotonic() + timeout
    while not path.is_file():
        if VAD_PROC is None or VAD_PROC.poll() is not None:
            die("vad exited")
        if deadline is not None and time.monotonic() >= deadline:
            die("no utterance")
        time.sleep(0.05)
    name = path.read_text(encoding="utf-8").strip()
    remove_file("vad.utterance.txt")
    if not name or "/" in name or "\\" in name:
        die("bad vad utterance")
    wav = ROOT / name
    if not wav.is_file():
        die("missing vad wav: " + name)
    return wav


def listen():
    if not capture_open():
        raise EarError("ear closed")
    name = wasapi_capture_name()
    start_vad(name)
    try:
        wav = take_utterance(None)
        kind, ms = judge_wav(wav)
        log_vad(kind, ms)
        if kind != "keep":
            raise EarError("vad " + kind)
        return transcribe(wav)
    finally:
        stop_vad()


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="hear.py")
    parser.add_argument("--wav", default=None)
    args = parser.parse_args()
    if args.wav is not None:
        if not str(args.wav).strip():
            die("empty wav")
        text, lang = transcribe(Path(args.wav))
    else:
        text, lang = listen()
    sys.stdout.write(text + "\n")
    print("hear: " + lang, file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except EarError as exc:
        die(str(exc))
