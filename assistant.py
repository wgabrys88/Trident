"""Iris assistant. Seat cards, one mouth, one ear.

The node on 127.0.0.1:8765 speaks and listens. This process claims seat
cards only. A peer URL that does not accept is an error. An empty URL uses
this machine. The microphone stays closed unless ear.go is present.
listen_loop(mic, until) is the resident capture for an explicit device.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import hear
import mouth
import nvidia_client
import seat

ROOT = Path(__file__).resolve().parent
LOCAL = "http://127.0.0.1:8765/"
MODELS = ("nano", "turbo")
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
OWN = False
TRACK = {"mouth": "off", "qwen": "off"}


def die(message):
    print(message, file=sys.stderr, flush=True)
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
    import gemma

    text = gemma.strip_tool_markup(generation or "")
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


def tool_decls():
    import gemma

    return "\n".join(
        (
            gemma.REMEMBER_DECL,
            gemma.PLACE_DECL,
            gemma.CURSOR_DECL,
            gemma.NEXT_DECL,
            gemma.STOP_DECL,
        )
    )


def voice_question(code, words):
    del code
    return (words or "").strip()


def signal_question(kind, text):
    return "\n".join(
        (
            "Kind: " + kind,
            "Text: " + text,
            tool_decls(),
            "If the owner should hear this now, speak one or two short sentences and do not call a tool. "
            "If a status should only be kept, call remember. "
            "If work should wait, call next. "
            "Do not call stop for a seat signal. Do not invent other work.",
        )
    )


def voice_follow(name, result):
    return "\n".join(
        (
            "You are Jarvis. The tool " + name + " finished.",
            "Result: " + (result or ""),
            "Speak one or two short sentences. Do not call a tool.",
        )
    )


def organism_stop(raw):
    import gemma

    call = gemma.parse_tool_call(raw or "")
    return bool(call) and call[0] == "stop"


def release_mouth():
    if mouth.stop_resident():
        print("assistant: mouth stopped", file=sys.stderr, flush=True)


def post_local(module, op, body, timeout, **fields):
    who = seat.node_from()
    card = seat.make(
        module,
        op,
        body,
        **{"from": who, "to": who, "timeout": str(timeout), "state": "queued", **fields},
    )
    return nvidia_client.post_card(LOCAL, card, timeout)


def say_text(text, hold, flip, fast="nano"):
    spoken = " ".join((text or "").split())
    if not spoken:
        die("empty text")
    if fast not in MODELS:
        die("fast mouth is nano or turbo")
    if hold:
        hear.set_hold(True)
    try:
        post_local(
            "mouth",
            "say",
            spoken,
            600,
            lang=mouth.language_of(spoken),
            fast=fast,
            flip="yes" if flip else "no",
        )
    finally:
        if hold:
            hear.set_hold(False)


def route_brain(peer, place):
    text = (peer or "").strip()
    if text:
        found = place(text)
        if found.brain != "post":
            die("peer missing")
        return found
    return place(None)


def peer_url(args):
    if not args.nvidia:
        return ""
    return (args.url or "").strip()


def fetch_reply(py, found, args, question):
    if found.brain == "post":
        fields = {}
        if args.image:
            path = Path(args.image)
            if not path.is_file():
                die("missing image: " + str(path))
            encoded, nbytes = nvidia_client.file_b64(path)
            fields["image"] = str(path)
            fields["image_b64"] = encoded
            print("node: image_b64 " + str(nbytes) + " bytes", file=sys.stderr, flush=True)
        who = seat.node_from()
        card = seat.make(
            "brain",
            "ask",
            question,
            **{
                "from": who,
                "to": seat.peer_of(found.url),
                "timeout": str(args.timeout),
                "state": "queued",
                **fields,
            },
        )
        print("assistant: brain " + found.url, file=sys.stderr, flush=True)
        done = nvidia_client.post_card(found.url, card, args.timeout)
        return done.get("body") or ""
    if found.brain == "resident":
        return local_turn(py, "gemma.py", args, question)
    if found.brain == "cpu":
        if args.image:
            die("image asks for gemma")
        return local_turn(py, "qwen.py", args, question)
    die("peer missing")


def apply_reply(py, found, args, raw, hold, follow=True):
    import gemma

    if organism_stop(raw):
        print("assistant: tool stop", file=sys.stderr, flush=True)
        release_mouth()
        return False, ""
    call = gemma.parse_tool_call(raw or "")
    if call and follow:
        name = call[0]
        print("assistant: tool " + name, file=sys.stderr, flush=True)
        _suffix, blocked, fallback = gemma.tool_turn(
            name, call[1], call[2], memory_path=gemma.MEMORY_PATH
        )
        if blocked and not fallback:
            spoken = speakable(blocked)
            if spoken:
                say_text(spoken, hold, found.flip, args.model)
            elif blocked.strip():
                print("assistant: no speakable answer", file=sys.stderr)
            return (not args.once), spoken
        raw2 = fetch_reply(py, found, args, voice_follow(name, fallback or ""))
        show(raw2)
        return apply_reply(py, found, args, raw2, hold, False)
    spoken = speakable(raw)
    if spoken:
        say_text(spoken, hold, found.flip, args.model)
    else:
        print("assistant: no speakable answer", file=sys.stderr)
    return (not args.once), spoken


def words_from_wav(wav):
    return hear.transcribe(wav)


def run_voice_turn(py, found, args, words, code, hold):
    text = voice_question(code, words)
    if not text:
        die("hear empty")
    reply = fetch_reply(py, found, args, text)
    show(reply)
    return apply_reply(py, found, args, reply, hold, True)[0]


def turn_after_transcript(py, args, words, code, hold):
    text = (words or "").strip()
    if not text:
        die("hear empty")
    print("hear: " + text, file=sys.stderr, flush=True)
    show(text)
    import gemma

    found = route_brain(peer_url(args), gemma.place)
    return run_voice_turn(py, found, args, text, code, hold)


def write_session():
    body = "mouth " + TRACK["mouth"] + "\nqwen " + TRACK["qwen"] + "\n"
    tmp = ROOT / "assistant.session.txt.tmp"
    path = ROOT / "assistant.session.txt"
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def init_track():
    TRACK["mouth"] = "adopted" if (ROOT / "mouth.pid").is_file() else "off"
    TRACK["qwen"] = "adopted" if (ROOT / "sense.pid").is_file() else "off"
    write_session()


def own_turn():
    global OWN
    OWN = True
    init_track()
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")


def note_qwen():
    if not OWN:
        return
    if TRACK["qwen"] == "off":
        TRACK["qwen"] = "started"
        write_session()


def read_session():
    path = ROOT / "assistant.session.txt"
    data = {}
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            data[parts[0]] = parts[1].strip()
    return data


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
    session = read_session()
    pid = read_pid("assistant.pid")
    vad_pid = read_pid("vad.pid")
    qwen_started = session.get("qwen") == "started"
    if vad_pid or pid:
        (ROOT / "vad.stop").write_bytes(b"")
    if pid and pid != os.getpid():
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    py = venv_python()
    subprocess.run(
        [py, str(ROOT / "mouth.py"), "--stop"],
        cwd=ROOT,
        shell=False,
        stdin=subprocess.DEVNULL,
    )
    if qwen_started:
        subprocess.run(
            [py, str(ROOT / "qwen.py"), "--stop"],
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    time.sleep(0.3)
    for name in (
        "vad.stop",
        "vad.hold",
        "vad.pid",
        "assistant.pid",
        "assistant.session.txt",
    ):
        remove_file(name)
    print("assistant: stopped", file=sys.stderr)


def local_turn(py, script, args, question):
    if script == "qwen.py":
        note_qwen()
        label = "qwen"
    else:
        label = "gemma"
    argv = [py, str(ROOT / script), "--verbose"]
    if args.image:
        argv.extend(["--image", args.image])
    argv.extend(["--", question])
    print("assistant: " + label, file=sys.stderr, flush=True)
    return run_child(label, argv, keep_stdout=True, keep_stderr=True)


def act_card(py, args, found, card, hold):
    import gemma

    op = card["op"]
    text = " ".join((card.get("body") or "").split())
    if op == "say":
        die("say is mouth")
    if not text:
        return True
    if op == "stop":
        print("assistant: stop " + text, file=sys.stderr, flush=True)
        if text == "voice":
            release_mouth()
            return False
        facts, pairs, works = gemma.read_memory(gemma.MEMORY_PATH)
        target = gemma.clip_fact(text)
        if target and target in works:
            works = [item for item in works if item != target]
            gemma.write_memory(gemma.MEMORY_PATH, facts, pairs, works)
            print("assistant: work dropped", file=sys.stderr, flush=True)
        return True
    if op in ("status", "work"):
        print("assistant: " + op + " " + text, file=sys.stderr, flush=True)
        raw = fetch_reply(py, found, args, signal_question(op, text))
        show(raw)
        cont, spoken = apply_reply(py, found, args, raw, hold, True)
        if op == "work" and cont and not spoken and gemma.parse_tool_call(raw) is None:
            kept = gemma.store_work(gemma.MEMORY_PATH, text)
            if kept:
                print("assistant: work kept", file=sys.stderr, flush=True)
        return cont
    die("unknown seat " + op)


def drain_seat(py, args, hold):
    import gemma

    found = None
    while True:
        hold_path = seat.claim_next(seat.QUEUE, {"seat"})
        if hold_path is None:
            return True
        started = seat.begin(hold_path)
        if started is None:
            return True
        card, resource, fd = started
        try:
            if found is None:
                found = route_brain(peer_url(args), gemma.place)
            cont = act_card(py, args, found, card, hold)
        except (SystemExit, seat.CardError) as exc:
            seat.end(hold_path, card, resource, fd, "failed", str(exc) or "failed")
            raise
        seat.end(hold_path, card, resource, fd, "completed", card.get("body") or "")
        if not cont:
            return False


def live_node(py, args):
    done = post_local("ear", "listen", "", args.timeout)
    words = (done.get("body") or "").strip()
    lang = (done.get("lang") or "").strip()
    if not words:
        die("hear empty")
    return turn_after_transcript(py, args, words, lang, True)


def live_clip(py, args):
    hear.set_hold(True)
    try:
        wav = hear.take_utterance()
        kind, ms = hear.judge_wav(wav)
        hear.log_vad(kind, ms)
        if kind != "keep":
            return not args.once
        words, code = words_from_wav(wav)
        if not words:
            die("hear empty")
        return turn_after_transcript(py, args, words, code, True)
    finally:
        hear.set_hold(False)


def listen_loop(py, args, mic=None, until=None):
    if mic is None:
        if not hear.capture_open():
            die("ear closed")
        mic = hear.wasapi_capture_name()
    own_turn()
    hear.start_vad(mic)
    try:
        while True:
            if until is not None and until():
                return
            if hear.VAD_PROC is None or hear.VAD_PROC.poll() is not None:
                die("vad exited")
            if (ROOT / "vad.utterance.txt").is_file():
                if not live_clip(py, args):
                    return
                continue
            if not drain_seat(py, args, True):
                return
            time.sleep(0.05)
    finally:
        hear.stop_vad()


def organism_loop(py, args):
    own_turn()
    while True:
        if not drain_seat(py, args, hear.capture_open()):
            return
        if not hear.capture_open():
            if args.once:
                return
            time.sleep(0.2)
            continue
        if not live_node(py, args):
            return
        if args.once:
            return


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


def inject_loop(py, args, path):
    own_turn()
    if not drain_seat(py, args, False):
        return
    for text in iter_inject_turns(path):
        if not turn_after_transcript(py, args, text, None, False):
            return
        if not drain_seat(py, args, False):
            return


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="assistant.py")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--text", default=None)
    parser.add_argument("--inject", nargs="?", const="-", metavar="PATH", default=None)
    parser.add_argument("--wav", default=None)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--model", default="nano", choices=MODELS)
    parser.add_argument("--image", default=None)
    parser.add_argument("--nvidia", action="store_true")
    parser.add_argument("--url", default=None)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    if args.stop:
        if args.text is not None or args.wav is not None or args.image or args.nvidia or args.url:
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
    if args.url is not None and args.url.strip() == "":
        die("empty url")
    if args.url and not args.nvidia:
        die("url asks for --nvidia")
    if args.nvidia and not (args.url or "").strip():
        die("empty url")
    if args.url and not (args.url.startswith("http://") or args.url.startswith("https://")):
        die("url must start with http:// or https://")
    if args.timeout <= 0:
        die("timeout must be > 0")

    py = venv_python()
    if args.inject is not None:
        path = None if args.inject == "-" else str(args.inject).strip()
        inject_loop(py, args, path)
        return
    if args.text is not None:
        own_turn()
        if not drain_seat(py, args, False):
            return
        turn_after_transcript(py, args, args.text.strip(), None, False)
        return
    if args.wav is not None:
        words, code = words_from_wav(Path(args.wav.strip()))
        own_turn()
        if not drain_seat(py, args, False):
            return
        turn_after_transcript(py, args, words, code, False)
        return
    organism_loop(py, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("assistant: stopped", file=sys.stderr)
        raise SystemExit(0)
