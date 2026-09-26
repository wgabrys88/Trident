"""Mouth speak entry. For each TEXT, write mouth.txt and run chatterbox.exe once."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
DROP_KEYS = ("chatterbox.variant", "chatterbox.language")


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


def settings_text(model, lang, sentence):
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
        "chatterbox.text <<\n"
        + block
        + "\n<<\n"
    )
    return body


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
    mouth = ROOT / "mouth.txt"
    for sentence in args.text:
        payload = settings_text(args.model, lang, sentence)
        try:
            mouth.write_bytes(payload.encode("utf-8"))
        except OSError as exc:
            die("cannot write mouth.txt: " + str(exc))
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
    raise SystemExit(0)


if __name__ == "__main__":
    main()
