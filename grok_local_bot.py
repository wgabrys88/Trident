"""Local brain turn. Resident Gemma, its tools, and gemma.memory.txt.

Placement is gemma.place: an accepting port is one POST, a CUDA device with
no listener is the local resident, and no CUDA device is qwen.py. No computer
name. No online bridge.

--text and --inbox are that turn. --proof is one short sentence on the same
path. --wav hears a file, runs that turn, and writes mouth wavs with no playback.
"""

import argparse
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import gemma
import nvidia_client

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "grok_bot_inbox.txt"
STATUS = ROOT / "grok_bot.txt"
DOOR = ROOT / "iris-door.txt"


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


def write_text(path, text):
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        tmp.replace(path)
    except OSError as exc:
        die("cannot write " + path.name + ": " + str(exc))


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        die("cannot read " + path.name + ": " + str(exc))


def clean_url(url):
    if url is None:
        return None
    text = str(url).strip()
    if not text:
        return None
    if not (text.startswith("http://") or text.startswith("https://")):
        die("nvidia url must start with http:// or https://")
    return text


def run_child_text(stage, argv, timeout):
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print("grok-bot: " + stage + " timed out", file=sys.stderr, flush=True)
        return 2, "", ""
    except OSError as exc:
        print("grok-bot: " + stage + " " + str(exc), file=sys.stderr, flush=True)
        return 2, "", ""
    err = (completed.stderr or "").strip()
    if err:
        print("\n".join(err.splitlines()[-20:]), file=sys.stderr, flush=True)
    return completed.returncode, completed.stdout or "", err


def turn_argv(brain, text, image):
    # Command for a local brain. POST is not a child of this process.
    py = venv_python()
    if brain == "resident":
        argv = [py, str(ROOT / "gemma.py")]
        if image:
            argv.extend(["--image", image])
        argv.append(text)
        return argv
    if brain == "cpu":
        if image:
            return None
        return [py, str(ROOT / "qwen.py"), text]
    return None


def post_text(url, text, timeout, image):
    ident = str(time.time_ns())
    image_b64 = None
    if image:
        image_b64, nbytes = nvidia_client.file_b64(Path(image))
        print("grok-bot: image_b64 " + str(nbytes) + " bytes", file=sys.stderr, flush=True)
    nvidia_client.write_request(nvidia_client.request_body(ident, text, image))
    buf = io.StringIO()
    old_out = sys.stdout
    sys.stdout = buf
    failed = None
    try:
        nvidia_client.post_turn(url, ident, text, image, image_b64, timeout)
    except SystemExit as exc:
        failed = exc
    finally:
        sys.stdout = old_out
    if failed is not None:
        return False, "peer missing"
    reply = buf.getvalue()
    if not reply.strip():
        return False, "empty"
    return True, reply


def script_text(brain, text, timeout, image):
    argv = turn_argv(brain, text, image)
    if argv is None:
        return False, "qwen/sense vision is N/A"
    code, out, _err = run_child_text(brain, argv, timeout)
    if code != 0 or not out.strip():
        return False, brain + " failed"
    return True, out


def brain_turn(text, timeout, url=None, image=None):
    peer = clean_url(url)
    found = gemma.place(peer)
    print("grok-bot: " + gemma.place_line(found), file=sys.stderr, flush=True)
    if found.brain == "missing":
        return False, "peer missing"
    if found.brain == "post":
        print("grok-bot: post " + found.url, file=sys.stderr, flush=True)
        return post_text(found.url, text, timeout, image)
    if found.brain == "resident":
        print("grok-bot: resident gemma", file=sys.stderr, flush=True)
        return script_text("resident", text, timeout, image)
    if image:
        return False, "qwen/sense vision is N/A"
    print("grok-bot: cpu qwen", file=sys.stderr, flush=True)
    return script_text("cpu", text, timeout, image)


def show_reply(text):
    if not text:
        return
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def run_text(args):
    ok, reply = brain_turn(args.text, args.timeout, args.url)
    if not ok:
        die(reply)
    show_reply(reply)


def inbox_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def last_block(raw, key):
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    found = ""
    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip(" \t")
        if stripped.endswith("<<") and stripped[:-2].strip() == key:
            index += 1
            buf = []
            while index < len(lines) and lines[index] != "<<":
                buf.append(lines[index])
                index += 1
            found = "\n".join(buf)
            if index < len(lines) and lines[index] == "<<":
                index += 1
            continue
        index += 1
    return found


def inbox_has_blocks(raw):
    for line in raw.split("\n"):
        stripped = line.rstrip(" \t")
        if stripped.endswith("<<") and stripped[:-2].strip() in ("text", "image"):
            return True
    return False


def load_inbox(path):
    if not path.is_file():
        die("missing inbox: " + str(path))
    raw = read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        die("empty inbox")
    if inbox_has_blocks(raw):
        text = last_block(raw, "text")
        image = last_block(raw, "image").strip()
    else:
        text = raw.strip("\n")
        image = ""
    lines = [line for line in text.split("\n") if line != "<<"]
    text = "\n".join(lines).strip()
    if image == "":
        image = None
    else:
        image_path = Path(image)
        if not image_path.is_file():
            die("missing inbox image: " + image)
        image = str(image_path.resolve())
    if not text and image:
        text = "What is in this picture?"
    if not text:
        die("inbox needs text or an image")
    return text, image


def run_inbox(args):
    path = inbox_path(args.inbox)
    user_text, image = load_inbox(path)
    ok, reply = brain_turn(user_text, args.timeout, args.url, image)
    if not ok:
        die(reply)
    show_reply(reply)


def head_sha():
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
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


def write_status(lines):
    write_text(STATUS, "".join(line + "\n" for line in lines))


def prove(args):
    ok, reply = brain_turn("Say one short sentence.", args.timeout, args.url)
    lines = [
        "STATUS " + ("PASS" if ok else "FAIL"),
        "date: " + time.strftime("%Y-%m-%d"),
        "head: " + head_sha(),
        "cmd: grok_local_bot.py --proof",
        "reply: " + " ".join((reply or "").split()),
    ]
    if not ok:
        lines.append("reason: " + reply)
    write_status(lines)
    if ok:
        show_reply(reply)
    print("grok-bot: " + ("PASS" if ok else "FAIL"), file=sys.stderr, flush=True)
    raise SystemExit(0 if ok else 2)


def hear_wav(path):
    wav = Path(path)
    if not wav.is_file():
        die("missing wav: " + str(wav))
    argv = [venv_python(), str(ROOT / "hear.py"), "--wav", str(wav)]
    print("grok-bot: hear wav", file=sys.stderr, flush=True)
    code, out, _err = run_child_text("hear", argv, 180)
    return code, out.strip()


def heard_line(transcript):
    flat = " ".join(transcript.split())
    if "<<" in flat:
        flat = " ".join(flat.replace("<<", " ").split())
    return flat


def wav_ok(path):
    try:
        return Path(path).is_file() and Path(path).stat().st_size > 44
    except OSError:
        return False


def speak_door(text):
    import assistant
    import mouth

    cleaned = gemma.strip_tool_markup(text)
    spoken = assistant.speakable(cleaned)
    parts = assistant.chunks_for_mouth(spoken, "nano") if spoken else []
    if not parts:
        return spoken, []
    print("grok-bot: mouth " + str(len(parts)) + " chunk(s)", file=sys.stderr, flush=True)
    try:
        paths = mouth.speak_pieces(parts, None)
    except SystemExit:
        return spoken, []
    return spoken, [str(path) for path in paths]


def clip_line(text, limit=240):
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "..."


def write_door(lines):
    write_text(DOOR, "".join(line + "\n" for line in lines))


def door_url(args):
    if args.url is not None and str(args.url).strip():
        return clean_url(args.url)
    env = os.environ.get("TRIDENT_NVIDIA_URL", "").strip()
    if not env:
        return None
    return clean_url(env)


def door(args):
    url = door_url(args)
    reasons = []
    transcript = ""
    hear_exit = "skipped"
    reply = ""
    spoken = ""
    mouth_paths = []
    mouth_exit = "skipped"
    hear_code, transcript = hear_wav(args.wav)
    hear_exit = str(hear_code)
    if hear_code != 0:
        reasons.append("hear exit " + hear_exit)
    else:
        heard = heard_line(transcript)
        if not heard:
            reasons.append("no transcript")
        else:
            ok, reply = brain_turn(heard, args.timeout, url)
            if not ok:
                reasons.append(reply)
            else:
                spoken, mouth_paths = speak_door(reply)
                if not mouth_paths or not all(wav_ok(path) for path in mouth_paths):
                    mouth_exit = "2"
                    reasons.append("no mouth wav")
                else:
                    mouth_exit = "0"
    passed = not reasons
    lines = [
        "STATUS " + ("PASS" if passed else "FAIL"),
        "date: " + time.strftime("%Y-%m-%d"),
        "head: " + head_sha(),
        "cmd: grok_local_bot.py --wav",
        "wav: " + str(Path(args.wav)),
        "transcript: " + clip_line(transcript),
        "reply: " + clip_line(spoken or reply),
        "hear_exit: " + hear_exit,
        "mouth_exit: " + mouth_exit,
        "mouth_wav: " + " ".join(mouth_paths),
        "reasons: " + "; ".join(reasons),
    ]
    write_door(lines)
    if transcript:
        sys.stdout.write(transcript.rstrip("\n") + "\n")
    if spoken:
        sys.stdout.write(spoken.rstrip("\n") + "\n")
    for path in mouth_paths:
        sys.stdout.write(path + "\n")
    print("grok-bot: " + ("PASS" if passed else "FAIL"), file=sys.stderr, flush=True)
    raise SystemExit(0 if passed else 2)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="grok_local_bot.py")
    parser.add_argument("--text", default=None, help="one brain turn; Gemma tools and memory when the brain is Gemma")
    parser.add_argument(
        "--proof",
        action="store_true",
        help="one short sentence on the same placement; writes grok_bot.txt",
    )
    parser.add_argument(
        "--wav",
        default=None,
        help="hear this wav, one brain turn, mouth wavs with no playback",
    )
    parser.add_argument(
        "--inbox",
        nargs="?",
        const=str(INBOX),
        default=None,
        help="file turn (default grok_bot_inbox.txt). Same placement as --text",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="optional peer POST url. Omitted uses this computer: listener, else local Gemma, else qwen",
    )
    parser.add_argument("--timeout", type=float, default=600, help="brain seconds (default 600)")
    args = parser.parse_args()
    if args.text is not None and not args.text.strip():
        die("empty text")
    if args.wav is not None and args.wav.strip() == "":
        die("empty wav")
    if args.inbox is not None and str(args.inbox).strip() == "":
        die("empty inbox")
    if args.url is not None and str(args.url).strip() == "":
        die("empty url")
    if args.timeout <= 0:
        die("timeout must be > 0")
    modes = sum(
        1
        for flag in (args.proof, args.text is not None, args.wav is not None, args.inbox is not None)
        if flag
    )
    if modes != 1:
        die("pass one of --text, --proof, --wav, or --inbox")
    venv_python()
    args.url = clean_url(args.url)
    if args.wav is not None:
        door(args)
        return
    if args.inbox is not None:
        run_inbox(args)
        return
    if args.proof:
        prove(args)
        return
    run_text(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("grok-bot: stopped", file=sys.stderr)
        raise SystemExit(0)
