"""Qwen (sense) brain one-shot. Text question → stdout generation. Vision is N/A (text-only Qwen3-0.6B)."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_KEYS = ("sense.text",)
SIDECAR = ROOT / "sense_run.txt"


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
            if key in DROP_KEYS:
                index += 1
                while index < len(lines) and lines[index] != "<<":
                    index += 1
                if index < len(lines):
                    index += 1
                continue
        key = stripped.split(" ", 1)[0]
        if key in DROP_KEYS:
            index += 1
            continue
        kept.append(line)
        index += 1
    return kept


def qwen_prompt(question):
    q = question.strip("\r\n")
    return (
        "<|im_start|>user\n"
        + q
        + "<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def settings_text(question):
    source = ROOT / "sense.txt"
    if not source.is_file():
        die("missing sense.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body:
        body += "\n"
    prompt = qwen_prompt(question)
    body += "sense.text <<\n" + prompt
    if not prompt.endswith("\n"):
        body += "\n"
    body += "<<\n"
    return body


def newest_out(before):
    after = set(ROOT.glob("*_sense_out_*.txt"))
    new_files = sorted(after - before)
    if not new_files:
        die("sense wrote no output text")
    return new_files[-1]


def main():
    parser = argparse.ArgumentParser(prog="qwen.py")
    parser.add_argument("question", help="text question")
    parser.add_argument(
        "--image",
        metavar="PATH",
        help="not supported: Qwen3-0.6B is text-only (vision N/A)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="pass sense.exe stderr through; default discards it",
    )
    args = parser.parse_args()
    if not args.question.strip():
        die("empty question")
    if args.image:
        die("qwen/sense vision is N/A: Qwen3-0.6B is text-only. Use gemma.py --image on Nvidia.")
    exe = ROOT / "sense.exe"
    if not exe.is_file():
        die("missing sense.exe")
    payload = settings_text(args.question)
    try:
        SIDECAR.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write sense_run.txt: " + str(exc))
    before = set(ROOT.glob("*_sense_out_*.txt"))
    try:
        completed = subprocess.run(
            [".\\sense.exe", "sense_run.txt"],
            cwd=ROOT,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=None if args.verbose else subprocess.DEVNULL,
        )
    except OSError as exc:
        die("cannot run sense.exe: " + str(exc))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    out_txt = newest_out(before)
    text = out_txt.read_text(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    raise SystemExit(0)


if __name__ == "__main__":
    main()