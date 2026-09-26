"""Mouth speak entry. Synthesize each TEXT with chatterbox.exe; play on Speakers with one-chunk overlap."""

import argparse
import concurrent.futures
import ctypes
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
DROP_KEYS = ("chatterbox.variant", "chatterbox.language", "chatterbox.play")
SND_FILENAME = 0x00020000
SND_NODEFAULT = 0x0002


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def physical_lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def kept_lines(raw):
    lines = physical_lines(raw)
    if lines and lines[-1] == "":
        lines = lines[:-1]
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            index += 1
            continue
        if len(stripped) >= 2 and stripped.endswith("<<"):
            key = stripped[:-2].rstrip(" \t")
            if key == "chatterbox.text":
                index += 1
                while index < len(lines) and lines[index] != "<<":
                    index += 1
                if index < len(lines):
                    index += 1
                continue
        key = stripped.split(" ", 1)[0]
        if key in DROP_KEYS or key == "chatterbox.text":
            index += 1
            continue
        kept.append(line)
        index += 1
    return kept


def settings_text(model, lang, sentence, play):
    source = ROOT / "chatterbox.txt"
    if not source.is_file():
        die("missing chatterbox.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body:
        body += "\n"
    block = "\n".join(physical_lines(sentence))
    body += (
        "chatterbox.variant " + model + "\n"
        "chatterbox.language " + lang + "\n"
        "chatterbox.play " + play + "\n"
        "chatterbox.text <<\n"
        + block
        + "\n<<\n"
    )
    return body


def play_wav(path):
    path = Path(path).resolve()
    if not path.is_file():
        die("missing wav: " + str(path))
    ok = ctypes.windll.winmm.PlaySoundW(str(path), None, SND_FILENAME | SND_NODEFAULT)
    if not ok:
        die("PlaySoundW failed: " + str(path))


def synthesize(model, lang, sentence):
    mouth = ROOT / "mouth.txt"
    payload = settings_text(model, lang, sentence, "off")
    try:
        mouth.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write mouth.txt: " + str(exc))
    before = set(ROOT.glob("*_chatterbox_out_*.txt"))
    try:
        completed = subprocess.run(
            [".\\chatterbox.exe", "mouth.txt"],
            cwd=ROOT,
            shell=False,
        )
    except OSError as exc:
        die("cannot run chatterbox.exe: " + str(exc))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    after = set(ROOT.glob("*_chatterbox_out_*.txt"))
    new_files = sorted(after - before)
    if not new_files:
        die("chatterbox wrote no output text")
    out_txt = new_files[-1]
    name = out_txt.read_text(encoding="utf-8").strip()
    if not name:
        die("empty chatterbox output text")
    wav = ROOT / name
    if not wav.is_file():
        die("missing wav named by chatterbox: " + name)
    return wav


def main():
    parser = argparse.ArgumentParser(prog="mouth.py")
    parser.add_argument("text", nargs="*")
    parser.add_argument("--model", default="nano")
    parser.add_argument("--lang", default=None)
    args = parser.parse_args()
    if not args.text:
        die("usage: mouth.py [--model nano|turbo|v3] [--lang TAG] TEXT [TEXT ...]")
    if args.model not in MODELS:
        die("unknown model")
    if args.lang is None:
        lang = "pl" if args.model == "v3" else "en"
    elif args.lang.strip() == "":
        die("empty language")
    else:
        lang = args.lang
    for sentence in args.text:
        if sentence.strip() == "":
            die("empty text")
        if any(line == "<<" for line in physical_lines(sentence)):
            die("text line is only <<")

    os.chdir(ROOT)
    chunks = list(args.text)
    ready = synthesize(args.model, lang, chunks[0])
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        for index, sentence in enumerate(chunks):
            play_future = pool.submit(play_wav, ready)
            if index + 1 < len(chunks):
                ready = synthesize(args.model, lang, chunks[index + 1])
            play_future.result()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
