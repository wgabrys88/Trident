"""Gemma brain one-shot. Text question, or question + image file → stdout generation (thinking included)."""

import argparse
import base64
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_KEYS = ("gemma.text", "gemma.image")
SIDECAR = ROOT / "gemma_run.txt"
MEDIA = "<__media__>"


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


def gemma_prompt(question, with_image):
    q = question.strip("\r\n")
    if with_image and MEDIA not in q:
        user = MEDIA + "\n" + q
    else:
        user = q
    return (
        "<bos><|turn>user\n"
        + user
        + "<turn|>\n"
        "<|turn>model\n"
        "<|channel>thought\n"
        "<channel|>\n"
    )


def settings_text(question, image_b64):
    source = ROOT / "gemma.txt"
    if not source.is_file():
        die("missing gemma.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body:
        body += "\n"
    prompt = gemma_prompt(question, bool(image_b64))
    body += "gemma.text <<\n" + prompt
    if not prompt.endswith("\n"):
        body += "\n"
    body += "<<\n"
    body += "gemma.image <<\n"
    if image_b64:
        body += image_b64
        if not image_b64.endswith("\n"):
            body += "\n"
    body += "<<\n"
    return body


def image_to_b64(path):
    path = Path(path)
    if not path.is_file():
        die("missing image: " + str(path))
    data = path.read_bytes()
    if not data:
        die("empty image: " + str(path))
    return base64.b64encode(data).decode("ascii")


def newest_out(before):
    after = set(ROOT.glob("*_gemma_out_*.txt"))
    new_files = sorted(after - before)
    if not new_files:
        die("gemma-brain wrote no output text")
    return new_files[-1]


def main():
    parser = argparse.ArgumentParser(prog="gemma.py")
    parser.add_argument("question", help="text question / analysis prompt")
    parser.add_argument(
        "--image",
        metavar="PATH",
        help="image file (png/jpeg/...); encoded as raw base64 into gemma.image",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="pass gemma-brain.exe stderr through; default discards it",
    )
    args = parser.parse_args()
    if not args.question.strip():
        die("empty question")
    image_b64 = image_to_b64(args.image) if args.image else ""
    exe = ROOT / "gemma-brain.exe"
    if not exe.is_file():
        die("missing gemma-brain.exe")
    payload = settings_text(args.question, image_b64)
    try:
        SIDECAR.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write gemma_run.txt: " + str(exc))
    before = set(ROOT.glob("*_gemma_out_*.txt"))
    try:
        completed = subprocess.run(
            [".\\gemma-brain.exe", "gemma_run.txt"],
            cwd=ROOT,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=None if args.verbose else subprocess.DEVNULL,
        )
    except OSError as exc:
        die("cannot run gemma-brain.exe: " + str(exc))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    out_txt = newest_out(before)
    text = out_txt.read_text(encoding="utf-8")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    raise SystemExit(0)


if __name__ == "__main__":
    main()