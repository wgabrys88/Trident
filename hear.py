import argparse
import json
import re
import subprocess
import sys
import unicodedata
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


_PL = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
_LEAK = re.compile(r"\s*<[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?>\s*")


def primary_tag(raw):
    tag = (raw or "").strip().lower().replace("_", "-")
    if tag in {"polish", "pol"}:
        return "pl"
    if tag in {"english", "eng"}:
        return "en"
    if not tag or tag == "auto":
        return ""
    return tag.split("-", 1)[0]


def language_tag(data, text):
    found = []
    one = data.get("language")
    if isinstance(one, str):
        found.append(one)
    langs = data.get("languages")
    if isinstance(langs, str):
        found.append(langs)
    elif isinstance(langs, list):
        found.extend(item for item in langs if isinstance(item, str))
    tag = ""
    for item in found:
        tag = primary_tag(item)
        if tag:
            break
    if any(ch in _PL for ch in text):
        return "pl"
    return tag


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
    text = " ".join(unicodedata.normalize("NFC", _LEAK.sub(" ", text)).split())
    if not text:
        die("hear empty")
    return text, language_tag(data, text)


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
