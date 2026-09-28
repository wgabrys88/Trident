"""Local voice turns. Chain hear.py, qwen.py or gemma.py, and mouth.py.

qwen.py keeps sense.exe loaded across turns. mouth.py keeps chatterbox.exe loaded across turns.
Hear and Gemma still exit after each turn.
--wav feeds hear.py a file instead of the mic. --nvidia hands the turn to nvidia_client.py
and skips the local brain. --url and --timeout are forwarded to that client. An empty client stdout
means the request file was left and mouth is not called.
--vb-cable is the cable entrypoint: loopback.py plays a phrase into CABLE Input and hears CABLE Output,
then that transcript is the turn. The live microphone is not opened. --mouth writes reply wavs and
does not play them.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
BRAINS = ("qwen", "gemma")
QUIT_WORDS = {"quit", "exit", "stop"}
CABLE_PHRASE = "Trident cable loopback"
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
    err = completed.stderr or ""
    if completed.returncode != 0:
        if err.strip():
            print(err.rstrip("\n"), file=sys.stderr)
        print(
            "assistant: " + stage + " failed (exit " + str(completed.returncode) + ")",
            file=sys.stderr,
        )
        raise SystemExit(completed.returncode)
    for line in err.splitlines():
        if line.startswith("gemma: tool"):
            print(line, file=sys.stderr)
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


def speak_raw(py, args, raw, play=True):
    spoken = speakable(raw)
    lang = resolved_lang(args.model, args.lang)
    parts = chunks_for_mouth(spoken, lang) if spoken else []
    if not parts:
        print("assistant: no speakable answer", file=sys.stderr)
        return []
    print("assistant: mouth " + str(len(parts)) + " chunk(s)", file=sys.stderr)
    argv = [py, str(ROOT / "mouth.py"), "--model", args.model]
    if not play:
        argv.append("--no-play")
    if args.lang is not None:
        argv.extend(["--lang", args.lang])
    argv.append("--")
    argv.extend(parts)
    if play:
        run_child("mouth", argv, keep_stdout=False, keep_stderr=False)
        return []
    out = run_child("mouth", argv, keep_stdout=True, keep_stderr=False)
    return [line.strip() for line in out.splitlines() if line.strip()]


def say(py, args, question, speak=True):
    script = "qwen.py" if args.brain == "qwen" else "gemma.py"
    argv = [py, str(ROOT / script), "--verbose"]
    if args.image:
        argv.extend(["--image", args.image])
    argv.extend(["--", question])
    print("assistant: " + args.brain, file=sys.stderr)
    raw = run_child(args.brain, argv, keep_stdout=True, keep_stderr=True)
    show(raw)
    if speak:
        speak_raw(py, args, raw)
    return raw


def offload(py, args, question, speak=True):
    argv = [py, str(ROOT / "nvidia_client.py")]
    if args.url:
        argv.extend(["--url", args.url])
    if args.timeout is not None:
        argv.extend(["--timeout", format(args.timeout, "g")])
    if args.image:
        argv.extend(["--image", args.image])
    argv.extend(["--", question])
    print("assistant: nvidia", file=sys.stderr)
    raw = run_child("nvidia", argv, keep_stdout=True, keep_stderr=False)
    if not raw.strip():
        print("assistant: nvidia request only", file=sys.stderr)
        return ""
    show(raw)
    if speak:
        speak_raw(py, args, raw)
    return raw


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


def listen_wav(py, wav):
    print("assistant: hear wav", file=sys.stderr)
    raw = run_child(
        "hear",
        [py, str(ROOT / "hear.py"), "--wav", wav],
        keep_stdout=True,
        keep_stderr=False,
    )
    show(raw)
    return raw.strip()


def one_turn(py, args, question):
    if is_quit(question):
        print("assistant: quit", file=sys.stderr)
        return True
    if args.nvidia:
        offload(py, args, question)
        return False
    say(py, args, question)
    return False


def status_value(text, key):
    prefix = key + ": "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def fresh_proof(name, started):
    path = ROOT / "loopback-proof" / name
    if not path.is_file():
        return ""
    if path.stat().st_mtime < started - 2:
        return ""
    return path.read_text(encoding="utf-8")


def git_head():
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def write_proof(name, text):
    folder = ROOT / "loopback-proof"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def cable_turn(py, args):
    phrase = CABLE_PHRASE if args.text is None else args.text.strip()
    argv = [py, str(ROOT / "loopback.py"), "--phrase", phrase]
    print("assistant: vb-cable", file=sys.stderr)
    started = time.time()
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except OSError as exc:
        print("assistant: loopback failed to start: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
    loop_out = completed.stdout or ""
    if loop_out:
        sys.stdout.write(loop_out if loop_out.endswith("\n") else loop_out + "\n")
        sys.stdout.flush()
    transcript = fresh_proof("transcript.txt", started).strip()
    loop_status = fresh_proof("status.txt", started)
    reply = ""
    nvidia_exit = "skipped"
    mouth_exit = "skipped"
    mouth_paths = []
    reasons = []
    if completed.returncode != 0:
        reasons.append("loopback exit " + str(completed.returncode))
    if not transcript:
        reasons.append("no transcript")
    else:
        try:
            if args.nvidia:
                reply = offload(py, args, transcript, speak=False)
                nvidia_exit = "0"
                if args.url and not reply.strip():
                    reasons.append("empty nvidia reply")
            else:
                reply = say(py, args, transcript, speak=False)
                nvidia_exit = "local"
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
            if args.nvidia:
                nvidia_exit = str(code)
            reasons.append("brain exit " + str(code))
    if args.mouth and not reasons:
        if not reply.strip():
            reasons.append("no reply to speak")
        else:
            try:
                mouth_paths = speak_raw(py, args, reply, play=False)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
                mouth_exit = str(code)
                reasons.append("mouth exit " + str(code))
            else:
                if mouth_paths:
                    mouth_exit = "0"
                    for path in mouth_paths:
                        show(path)
                else:
                    mouth_exit = "1"
                    reasons.append("no mouth wav")
    response_text = ""
    if args.nvidia and nvidia_exit != "skipped":
        response_path = ROOT / "nvidia_turn.response.txt"
        if response_path.is_file():
            response_text = response_path.read_text(encoding="utf-8")
    passed = not reasons
    reply_text = reply if reply.endswith("\n") or not reply else reply + "\n"
    lines = [
        "STATUS " + ("PASS" if passed else "FAIL"),
        "cmd: " + subprocess.list2cmdline(sys.argv),
        "loopback_cmd: " + subprocess.list2cmdline(argv),
        "phrase: " + phrase,
        "transcript: " + transcript,
        "ratio: " + status_value(loop_status, "ratio"),
        "loopback_exit: " + str(completed.returncode),
        "synth_exit: " + status_value(loop_status, "synth_exit"),
        "play_exit: " + status_value(loop_status, "play_exit"),
        "hear_exit: " + status_value(loop_status, "hear_exit"),
        "nvidia_exit: " + nvidia_exit,
        "mouth_exit: " + mouth_exit,
        "mouth_wavs: " + " ".join(mouth_paths),
        "play_device: " + status_value(loop_status, "play_device"),
        "capture_device: " + status_value(loop_status, "capture_device"),
        "spoken_wav: " + status_value(loop_status, "spoken_wav"),
        "hear_wav: " + status_value(loop_status, "hear_wav"),
        "url: " + (args.url or ""),
        "request: " + str(ROOT / "nvidia_turn.request.txt"),
        "response: " + str(ROOT / "nvidia_turn.response.txt"),
        "head: " + (status_value(loop_status, "head") or git_head()),
        "reasons: " + "; ".join(reasons),
        "",
        "nvidia reply:",
        reply_text.rstrip("\n"),
        "",
        "nvidia response file:",
        response_text.rstrip("\n"),
        "",
        "loopback status:",
        loop_status.rstrip("\n"),
        "",
    ]
    body = "\n".join(lines)
    write_proof("jarvis.txt", body)
    nvidia_body = "exit " + nvidia_exit + "\nurl " + (args.url or "") + "\n\n" + reply_text
    if response_text:
        nvidia_body += "\nresponse file:\n" + response_text
        if not response_text.endswith("\n"):
            nvidia_body += "\n"
    write_proof("nvidia.txt", nvidia_body)
    print(body, end="" if body.endswith("\n") else "\n")
    raise SystemExit(0 if passed else 1)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="assistant.py")
    parser.add_argument("--once", action="store_true", help="one turn, then exit")
    parser.add_argument(
        "--text",
        default=None,
        help="skip the mic; one brain then mouth round. With --vb-cable, the phrase played into CABLE Input",
    )
    parser.add_argument("--wav", default=None, help="transcribe this wav through hear.py; skip the mic; one turn")
    parser.add_argument(
        "--vb-cable",
        action="store_true",
        help="play a phrase into CABLE Input, hear CABLE Output, then one turn. Does not open the live mic",
    )
    parser.add_argument(
        "--mouth",
        action="store_true",
        help="with --vb-cable, synthesize the reply to wavs and do not play them",
    )
    parser.add_argument("--seconds", type=float, default=8, help="hear.py seconds (default 8)")
    parser.add_argument("--brain", default="qwen", choices=BRAINS, help="default qwen")
    parser.add_argument("--model", default="nano", choices=MODELS, help="mouth model, default nano")
    parser.add_argument("--lang", default=None, help="mouth language; omit for en, or pl when model is v3")
    parser.add_argument("--image", default=None, help="image file; local gemma, or a path on an --nvidia turn")
    parser.add_argument(
        "--nvidia",
        action="store_true",
        help="send the turn through nvidia_client.py instead of the local brain",
    )
    parser.add_argument("--url", default=None, help="NVIDIA worker URL; forwarded to nvidia_client.py")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="NVIDIA HTTP timeout seconds; forwarded when --nvidia",
    )
    args = parser.parse_args()

    if args.seconds <= 0:
        die("seconds must be > 0")
    if args.lang is not None and args.lang.strip() == "":
        die("empty language")
    if args.image is not None and args.image.strip() == "":
        die("empty image")
    if args.image and args.brain != "gemma" and not args.nvidia:
        die("image asks use --brain gemma")
    if args.text is not None and args.text.strip() == "":
        die("empty text")
    if args.wav is not None and args.wav.strip() == "":
        die("empty wav")
    if args.text is not None and args.wav is not None:
        die("use text or wav, not both")
    if args.vb_cable and args.wav is not None:
        die("use vb-cable or wav, not both")
    if args.mouth and not args.vb_cable:
        die("--mouth asks for --vb-cable")
    if args.url is not None and args.url.strip() == "":
        die("empty url")
    if args.url and not args.nvidia:
        die("url asks for --nvidia")
    if args.timeout is not None and not args.nvidia:
        die("timeout asks for --nvidia")
    if args.timeout is not None and args.timeout <= 0:
        die("timeout must be > 0")

    py = venv_python()
    if args.vb_cable:
        cable_turn(py, args)
        return
    if args.text is not None:
        one_turn(py, args, args.text.strip())
        return
    if args.wav is not None:
        question = listen_wav(py, args.wav.strip())
        if not question:
            print("assistant: hear returned no transcript", file=sys.stderr)
            return
        one_turn(py, args, question)
        return

    while True:
        question = listen(py, args.seconds)
        if not question:
            print("assistant: hear returned no transcript", file=sys.stderr)
            if args.once:
                return
            continue
        if one_turn(py, args, question) or args.once:
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("assistant: stopped", file=sys.stderr)
        raise SystemExit(0)
