import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def die(message):
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


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
        die("hear json")
    if not isinstance(data, dict):
        die("hear json")
    text = data.get("text")
    if not isinstance(text, str):
        die("hear json")
    text = " ".join(text.split())
    langs = data.get("languages")
    lang = langs[0].strip() if isinstance(langs, list) and langs and isinstance(langs[0], str) else ""
    if not text:
        die("hear empty")
    return text, lang


def transcribe(wav):
    wav = Path(wav)
    if not wav.is_file():
        die("missing wav: " + str(wav))
    if wav.stat().st_size <= 0:
        die("hear empty")
    if not (ROOT / "nemo-speech.exe").is_file():
        die("missing nemo-speech.exe")
    try:
        completed = subprocess.run(
            asr_argv(wav),
            cwd=str(ROOT),
            shell=False,
            capture_output=True,
        )
    except OSError as exc:
        die("cannot run nemo-speech.exe: " + str(exc))
    if completed.returncode != 0:
        err = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        die(err or ("hear exit " + str(completed.returncode)))
    raw = (completed.stdout or b"").decode("utf-8", errors="replace")
    return parse_transcript(raw)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="hear.py")
    parser.add_argument("--wav", default=None)
    args = parser.parse_args()
    if args.wav is None:
        die("ear closed")
    if not str(args.wav).strip():
        die("empty wav")
    text, lang = transcribe(Path(args.wav))
    sys.stdout.write(text + "\n")
    print("hear: " + lang, file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
