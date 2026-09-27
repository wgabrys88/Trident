"""Gemma brain one-shot. Text question, or question + image file → stdout generation (thinking included).

Text turns declare one local tool, hello. A Gemma 4 <|tool_call> is run here, then the brain
is asked once more with the tool result so the spoken answer can follow.
"""

import argparse
import base64
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_KEYS = ("gemma.text", "gemma.image")
SIDECAR = ROOT / "gemma_run.txt"
HELLO_PATH = ROOT / "tool_hello.txt"
MEDIA = "<__media__>"
Q = '<|"|>'
HELLO_DECL = (
    "<|tool>declaration:hello{description:"
    + Q
    + "Write one line into tool_hello.txt. Call this when the user asks to write Hello World or to use the hello tool."
    + Q
    + ",parameters:{properties:{line:{description:"
    + Q
    + "Exact line to write. Use Hello World when asked to write Hello World."
    + Q
    + ",type:"
    + Q
    + "STRING"
    + Q
    + "}},required:["
    + Q
    + "line"
    + Q
    + "],type:"
    + Q
    + "OBJECT"
    + Q
    + "}}<tool|>"
)
CALL_RE = re.compile(
    r"<\|tool_call>\s*call:([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*?)\}\s*<tool_call\|>",
    re.DOTALL,
)
ARG_RE = re.compile(
    r"(\w+)\s*:\s*(?:<\|\"\|>(.*?)<\|\"\|>|([^,}\n]*))",
    re.DOTALL,
)
TOOL_MARKUP_RE = re.compile(
    r"<\|tool_call>.*?<tool_call\|>|<\|tool_response>.*?<tool_response\|>",
    re.DOTALL,
)


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


def user_body(question, with_image):
    q = question.strip("\r\n")
    if with_image and MEDIA not in q:
        return MEDIA + "\n" + q
    return q


def tool_header():
    return "<|turn>system\nYou are a helpful assistant." + HELLO_DECL + "<turn|>\n"


def gemma_prompt(question, with_image):
    user = user_body(question, with_image)
    # Image turns stay on the old prompt. Text turns declare hello and still
    # close an empty thought channel, which is Gemma 4's thinking-off prefill.
    head = "<bos>"
    if not with_image:
        head += tool_header()
    return (
        head
        + "<|turn>user\n"
        + user
        + "<turn|>\n"
        "<|turn>model\n"
        "<|channel>thought\n"
        "<channel|>\n"
    )


def follow_prompt(question, call_markup, line):
    response = (
        "<|tool_response>response:hello{path:"
        + Q
        + "tool_hello.txt"
        + Q
        + ",text:"
        + Q
        + line
        + Q
        + "}<tool_response|>"
    )
    # Same empty thought close as the first turn, then the call the model just
    # emitted, then the tool result. Gemma stops on <|tool_response> and continues
    # the answer after <tool_response|>.
    return (
        "<bos>"
        + tool_header()
        + "<|turn>user\n"
        + question.strip("\r\n")
        + "<turn|>\n"
        "<|turn>model\n"
        "<|channel>thought\n"
        "<channel|>\n"
        + call_markup
        + response
    )


def parse_tool_call(text):
    match = CALL_RE.search(text)
    if not match:
        return None
    args = {}
    for key, quoted, bare in ARG_RE.findall(match.group(2)):
        value = quoted if quoted else bare
        args[key] = value.strip()
    return match.group(1), args, match.group(0)


def clean_line(raw):
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    text = " ".join(text.split())
    text = text.replace(Q, "'")
    if len(text) > 200:
        text = text[:200].rstrip()
    if not text:
        text = "Hello World"
    return text


def write_hello(line):
    try:
        HELLO_PATH.write_text(line + "\n", encoding="utf-8")
    except OSError as exc:
        die("cannot write tool_hello.txt: " + str(exc))
    print("gemma: tool hello wrote tool_hello.txt (" + line + ")", file=sys.stderr, flush=True)


def strip_tool_markup(text):
    return TOOL_MARKUP_RE.sub("", text)


def answer_text(text):
    cleaned = strip_tool_markup(text).replace("\r\n", "\n").replace("\r", "\n")
    if "<channel|>" in cleaned:
        cleaned = cleaned.split("<channel|>")[-1]
    for token in ("<turn|>", "<|turn>", "<bos>", "<eos>", "`"):
        cleaned = cleaned.replace(token, "")
    return " ".join(cleaned.split())


def settings_text(prompt, image_b64):
    source = ROOT / "gemma.txt"
    if not source.is_file():
        die("missing gemma.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body:
        body += "\n"
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


def run_brain(prompt, image_b64, verbose):
    exe = ROOT / "gemma-brain.exe"
    if not exe.is_file():
        die("missing gemma-brain.exe")
    payload = settings_text(prompt, image_b64)
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
            stderr=None if verbose else subprocess.DEVNULL,
        )
    except OSError as exc:
        die("cannot run gemma-brain.exe: " + str(exc))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    out_txt = newest_out(before)
    return out_txt.read_text(encoding="utf-8")


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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    image_b64 = image_to_b64(args.image) if args.image else ""
    text = run_brain(gemma_prompt(args.question, bool(image_b64)), image_b64, args.verbose)
    if not image_b64:
        call = parse_tool_call(text)
        if call and call[0] == "hello":
            line = clean_line(call[1].get("line"))
            write_hello(line)
            follow = follow_prompt(args.question, call[2], line)
            spoken = ""
            for _ in range(3):
                spoken = answer_text(run_brain(follow, "", args.verbose))
                if spoken:
                    break
            if not spoken:
                print("gemma: tool follow-up empty", file=sys.stderr, flush=True)
                spoken = "Wrote " + line + " to tool_hello.txt."
            text = spoken
        elif call:
            print("gemma: tool skip " + call[0], file=sys.stderr, flush=True)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    raise SystemExit(0)


if __name__ == "__main__":
    main()