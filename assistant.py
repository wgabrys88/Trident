"""Local voice turns. Chain hear.py, qwen.py or gemma.py, and mouth.py.

qwen.py keeps sense.exe loaded across turns. mouth.py keeps chatterbox.exe loaded across turns.
Hear and Gemma still exit after each turn.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
BRAINS = ("qwen", "gemma")
QUIT_WORDS = {"quit", "exit", "stop"}
EN_LIMIT = 65
PL_LIMIT = 55
EN_FLOOR = 50
PL_FLOOR = 45
CONJUNCTIONS = {
    "en": {"and", "but", "or", "so", "because", "however"},
    "pl": {"i", "oraz", "ale", "lub", "albo", "więc", "wiec", "bo", "jednak"},
}
TOKENS = (
    "<|im_start|>",
    "<|im_end|>",
    "<|turn>",
    "<turn|>",
    "<|channel>thought",
    "<channel|>",
    "<bos>",
    "<eos>",
    "<think>",
    "</think>",
)


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def configure_stdio_utf8():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def venv_python():
    path = ROOT / ".venv" / "Scripts" / "python.exe"
    if not path.is_file():
        die("missing .venv python: " + str(path))
    return str(path)


def show(text):
    sys.stdout.write(text)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def run_child(stage, argv, keep_stdout, keep_stderr):
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if keep_stdout else None,
            stderr=subprocess.PIPE if keep_stderr else None,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        print("assistant: " + stage + " failed to start: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
    if completed.returncode != 0:
        err = completed.stderr or ""
        if err.strip():
            print(err.rstrip("\n"), file=sys.stderr)
        print(
            "assistant: " + stage + " failed (exit " + str(completed.returncode) + ")",
            file=sys.stderr,
        )
        raise SystemExit(completed.returncode)
    return completed.stdout or ""


def speakable(generation):
    text = generation.replace("\r\n", "\n").replace("\r", "\n")
    if "</think>" in text:
        text = text.split("</think>")[-1]
    elif "<channel|>" in text:
        text = text.split("<channel|>")[-1]
    elif "<think>" in text:
        return ""
    for token in TOKENS:
        text = text.replace(token, "")
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def _flush(parts, buf):
    text = "".join(buf).strip()
    buf.clear()
    if text:
        parts.append(" ".join(text.split()))


def _closing_quote(opener):
    return {'"': '"', "“": "”", "«": "»"}[opener]


def breath_parts(text):
    parts = []
    buf = []
    quote = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == _closing_quote(quote):
                quote = None
            i += 1
            continue
        if ch in "\"“«":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\n":
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] == "\n":
                _flush(parts, buf)
                i = j + 1
                continue
            buf.append(" ")
            i += 1
            continue
        if ch in ".!?;":
            buf.append(ch)
            if i + 1 < n and text[i + 1].isspace():
                _flush(parts, buf)
            i += 1
            continue
        if ch in "—–":
            buf.append(ch)
            _flush(parts, buf)
            i += 1
            continue
        if ch == ":":
            prev_digit = bool(buf) and buf[-1].isdigit()
            next_digit = i + 1 < n and text[i + 1].isdigit()
            buf.append(ch)
            if not (prev_digit and next_digit) and i + 1 < n and text[i + 1].isspace():
                _flush(parts, buf)
            i += 1
            continue
        buf.append(ch)
        i += 1
    _flush(parts, buf)
    return parts


def _bare(word):
    return word.strip(".,;:!?—–\"“”«»()[]").casefold()


def split_long(text, limit, lang):
    words = text.split()
    conj = CONJUNCTIONS["pl"] if lang == "pl" else CONJUNCTIONS["en"]
    floor = PL_FLOOR if lang == "pl" else EN_FLOOR
    chunks = []
    start = 0
    while start < len(words):
        remaining = len(words) - start
        if remaining <= limit:
            chunks.append(" ".join(words[start:]))
            break
        end = start + limit
        split_at = end
        low = start + floor
        if low < end:
            for j in range(end - 1, low - 1, -1):
                if _bare(words[j]) in conj:
                    split_at = j
                    break
        if split_at <= start:
            split_at = end
        chunks.append(" ".join(words[start:split_at]))
        start = split_at
    return [chunk for chunk in chunks if chunk]


def chunks_for_mouth(text, lang):
    limit = PL_LIMIT if lang == "pl" else EN_LIMIT
    flat = " ".join(text.split())
    if not flat:
        return []
    if len(flat.split()) <= limit:
        return [flat]
    parts = breath_parts(text)
    chunks = []
    buf = []
    count = 0
    for part in parts:
        piece = part.split()
        n = len(piece)
        if n > limit:
            if buf:
                chunks.append(" ".join(buf))
                buf = []
                count = 0
            chunks.extend(split_long(part, limit, lang))
            continue
        if count and count + n > limit:
            chunks.append(" ".join(buf))
            buf = piece
            count = n
        else:
            buf.extend(piece)
            count += n
    if buf:
        chunks.append(" ".join(buf))
    return [chunk for chunk in chunks if chunk]


def resolved_lang(model, lang):
    if lang is None:
        return "pl" if model == "v3" else "en"
    return lang


def is_quit(text):
    word = text.strip().strip(" .!?").casefold()
    return word in QUIT_WORDS


def say(py, args, question):
    script = "qwen.py" if args.brain == "qwen" else "gemma.py"
    argv = [py, str(ROOT / script), "--verbose"]
    if args.image:
        argv.extend(["--image", args.image])
    argv.extend(["--", question])
    print("assistant: " + args.brain, file=sys.stderr)
    raw = run_child(args.brain, argv, keep_stdout=True, keep_stderr=True)
    show(raw)
    spoken = speakable(raw)
    lang = resolved_lang(args.model, args.lang)
    parts = chunks_for_mouth(spoken, lang) if spoken else []
    if not parts:
        print("assistant: no speakable answer", file=sys.stderr)
        return
    print("assistant: mouth " + str(len(parts)) + " chunk(s)", file=sys.stderr)
    argv = [py, str(ROOT / "mouth.py"), "--model", args.model]
    if args.lang is not None:
        argv.extend(["--lang", args.lang])
    argv.append("--")
    argv.extend(parts)
    run_child("mouth", argv, keep_stdout=False, keep_stderr=False)


def listen(py, seconds):
    print("assistant: listening " + format(seconds, "g") + "s", file=sys.stderr)
    raw = run_child(
        "hear",
        [py, str(ROOT / "hear.py"), format(seconds, "g")],
        keep_stdout=True,
        keep_stderr=False,
    )
    show(raw)
    return raw.strip()


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="assistant.py")
    parser.add_argument("--once", action="store_true", help="one turn, then exit")
    parser.add_argument("--text", default=None, help="skip the mic; one brain then mouth round")
    parser.add_argument("--seconds", type=float, default=8, help="hear.py seconds (default 8)")
    parser.add_argument("--brain", default="qwen", choices=BRAINS, help="default qwen")
    parser.add_argument("--model", default="nano", choices=MODELS, help="mouth model, default nano")
    parser.add_argument("--lang", default=None, help="mouth language; omit for en, or pl when model is v3")
    parser.add_argument("--image", default=None, help="image file; requires --brain gemma")
    args = parser.parse_args()

    if args.seconds <= 0:
        die("seconds must be > 0")
    if args.lang is not None and args.lang.strip() == "":
        die("empty language")
    if args.image is not None and args.image.strip() == "":
        die("empty image")
    if args.image and args.brain != "gemma":
        die("image asks use --brain gemma")
    if args.text is not None and args.text.strip() == "":
        die("empty text")

    py = venv_python()
    if args.text is not None:
        say(py, args, args.text.strip())
        return

    while True:
        question = listen(py, args.seconds)
        if not question:
            print("assistant: hear returned no transcript", file=sys.stderr)
            if args.once:
                return
            continue
        if is_quit(question):
            print("assistant: quit", file=sys.stderr)
            return
        say(py, args, question)
        if args.once:
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("assistant: stopped", file=sys.stderr)
        raise SystemExit(0)
