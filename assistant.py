import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


def configure_stdio_utf8():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


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


def remove_file(name):
    path = name if isinstance(name, Path) else ROOT / name
    if not path.is_file():
        return
    try:
        path.unlink()
    except OSError as exc:
        die("cannot remove " + path.name + ": " + str(exc))


def _markup(text):
    for token in TOKENS:
        text = text.replace(token, "")
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"[ \t]+\n", "\n", text)


def speakable(generation):
    import node

    text = node.strip_tool_markup(generation or "")
    cut = text.find("<|tool_call>")
    if cut >= 0:
        text = text[:cut]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "</think>" in text:
        text = text.split("</think>")[-1]
    elif "<channel|>" in text:
        text = text.split("<channel|>")[-1]
    elif "<think>" in text:
        return ""
    return _markup(text).strip()


def say_text(text, hold, flip):
    del hold, flip
    import node

    spoken = " ".join((text or "").split())
    if not spoken:
        die("empty text")
    if not node.reachable("127.0.0.1:8765"):
        die("node down")
    import mouth

    lang = mouth.resolve_lang(spoken)
    reply = node.transact("127.0.0.1:8765", node.make_card("mouth", "say", spoken, agent=lang), 600)
    if (reply.body or "").strip() != "spoken":
        die("mouth missed")


def release_mouth():
    import mouth

    if mouth.stop_resident():
        print("assistant: mouth stopped", file=sys.stderr, flush=True)


def fetch_reply(found, args, question):
    import node

    image = node.file_b64(args.image) if args.image else ""
    peer = found.url if found.brain == "post" else ""
    return node.agent_turn("voice", question, image, peer=peer, timeout=args.timeout)


def apply_reply(found, args, raw, hold):
    import node

    if node.is_stop(raw):
        print("assistant: tool stop", file=sys.stderr, flush=True)
        release_mouth()
        return False, ""
    spoken = speakable(raw)
    if spoken:
        say_text(spoken, hold, found.flip)
    else:
        print("assistant: no speakable answer", file=sys.stderr)
    return (not args.once), spoken


def run_voice_turn(found, args, words, code, hold):
    del code
    text = (words or "").strip()
    if not text:
        die("hear empty")
    reply = fetch_reply(found, args, text)
    show(reply)
    return apply_reply(found, args, reply, hold)[0]


def turn_after_transcript(args, words, code, hold):
    text = (words or "").strip()
    if not text:
        die("hear empty")
    print("hear: " + text, file=sys.stderr, flush=True)
    show(text)
    found = turn_place(args)
    return run_voice_turn(found, args, text, code, hold)


def read_pid(name):
    path = ROOT / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    lines = text.splitlines()
    if not lines:
        return 0
    try:
        pid = int(lines[0].strip())
    except ValueError:
        return 0
    return pid if pid > 0 else 0


def stop_tree():
    pid = read_pid("assistant.pid")
    if pid and pid != os.getpid():
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    subprocess.run(
        [venv_python(), str(ROOT / "mouth.py"), "--stop"],
        cwd=ROOT,
        shell=False,
        stdin=subprocess.DEVNULL,
    )
    for name in ("assistant.pid", "assistant.session.txt"):
        remove_file(name)
    print("assistant: stopped", file=sys.stderr)


def words_from_wav(wav):
    import hear

    return hear.transcribe(wav)


def peer_addr(args):
    raw = (args.url or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        die("peer is host:port")
    host, sep, port = raw.rpartition(":")
    if sep != ":" or not host or not port.isdigit() or not (1 <= int(port) <= 65535):
        die("peer is host:port")
    return raw


def announce_place(found):
    import node

    print("assistant: place " + node.place_line(found), file=sys.stderr, flush=True)
    if found.brain == "post":
        print("assistant: lan", file=sys.stderr, flush=True)
    else:
        print("assistant: alone", file=sys.stderr, flush=True)


def turn_place(args):
    import node

    addr = node.brain_place(peer_addr(args))
    if addr:
        found = node.remote_place(addr)
    else:
        found = node.local_place()
    if found.brain == "missing":
        die("brain missing")
    announce_place(found)
    return found


def injected_turn(args, text):
    if not (text or "").strip():
        die("empty text")
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    turn_after_transcript(args, text.strip(), None, False)


def iter_inject_turns(path):
    if path is None:
        yield from _inject_blocks_from_stream(sys.stdin)
        return
    file_path = Path(path)
    if not file_path.is_file():
        die("missing inject: " + str(file_path))
    raw = file_path.read_text(encoding="utf-8")
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    if "\n---\n" in normalized or normalized.strip() == "---":
        for part in normalized.split("\n---\n"):
            text = part.strip()
            if text:
                yield text
        return
    lines = [item.strip() for item in normalized.splitlines() if item.strip()]
    if not lines:
        die("empty inject")
    if len(lines) == 1:
        yield lines[0]
        return
    for line in lines:
        yield line


def _inject_blocks_from_stream(stream):
    buf = []
    while True:
        line = stream.readline()
        if line == "":
            block = "\n".join(buf).strip()
            if block:
                yield block
            return
        stripped = line.rstrip("\r\n")
        if stripped == "---":
            block = "\n".join(buf).strip()
            buf = []
            if block:
                yield block
        else:
            buf.append(stripped)


def inject_loop(args, path):
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    for text in iter_inject_turns(path):
        if not turn_after_transcript(args, text, None, False):
            return


def consume_iris_outbox(args, path_arg):
    import node

    if not path_arg or path_arg == "-":
        die("empty iris-outbox path")
    path = Path(path_arg)
    if not path.is_file():
        die("missing card: " + str(path))
    if not node.reachable("127.0.0.1:8765"):
        die("node down")
    card = node.parse_card(path.read_text(encoding="utf-8"))
    reply = node.transact("127.0.0.1:8765", card, args.timeout)
    show(reply.body)


def closed_loop():
    import node

    if not node.reachable("127.0.0.1:8765"):
        die("node down")
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    print("assistant: capture closed", file=sys.stderr, flush=True)
    while True:
        time.sleep(0.2)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="assistant.py")
    parser.add_argument("--once", action="store_true", help="one turn, then exit")
    parser.add_argument("--text", default=None, help="skip the mic; one brain then mouth round")
    parser.add_argument(
        "--inject",
        nargs="?",
        const="-",
        metavar="PATH",
        default=None,
        help="closed capture: PATH is one turn per non-empty line, or blocks split by a --- line; flag alone reads stdin until EOF. No microphone",
    )
    parser.add_argument(
        "--iris-outbox",
        nargs="?",
        const="-",
        metavar="PATH",
        default=None,
        help="send one card file to the local node on 127.0.0.1:8765",
    )
    parser.add_argument("--wav", default=None, help="transcribe this wav through hear.py; skip the mic; one turn")
    parser.add_argument("--stop", action="store_true", help="stop the assistant tree on this PC")
    parser.add_argument("--image", default=None, help="image file forwarded on the turn")
    parser.add_argument("--url", default=None, help="peer host:port")
    parser.add_argument("--timeout", type=float, default=180, help="brain timeout seconds (default 180)")
    args = parser.parse_args()

    if args.stop:
        if args.text is not None or args.wav is not None or args.image or args.url:
            die("usage: assistant.py --stop")
        stop_tree()
        return
    if args.image is not None and args.image.strip() == "":
        die("empty image")
    if args.text is not None and args.text.strip() == "":
        die("empty text")
    if args.wav is not None and args.wav.strip() == "":
        die("empty wav")
    if args.text is not None and args.wav is not None:
        die("use text or wav, not both")
    if args.inject is not None:
        if args.text is not None or args.wav is not None:
            die("inject asks for no text or wav")
        if str(args.inject).strip() == "":
            die("empty inject path")
    if args.iris_outbox is not None:
        if args.text is not None or args.wav is not None or args.inject is not None:
            die("iris-outbox asks for no text, wav, or inject")
        if str(args.iris_outbox).strip() == "":
            die("empty iris-outbox path")
    if args.url is not None and args.url.strip() == "":
        die("empty url")
    if args.url:
        peer_addr(args)
    if args.timeout <= 0:
        die("timeout must be > 0")

    if args.iris_outbox is not None:
        path = None if args.iris_outbox == "-" else str(args.iris_outbox).strip()
        consume_iris_outbox(args, path)
        return
    if args.inject is not None:
        path = None if args.inject == "-" else str(args.inject).strip()
        inject_loop(args, path)
        return
    if args.text is not None:
        injected_turn(args, args.text.strip())
        return
    if args.wav is not None:
        words, _lang = words_from_wav(Path(args.wav.strip()))
        injected_turn(args, words)
        return
    closed_loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("assistant: stopped", file=sys.stderr)
        raise SystemExit(0)
