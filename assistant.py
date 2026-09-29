import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import hear
import nvidia_client

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
QUIT_WORDS = {"quit", "exit", "stop"}
CABLE_PHRASE = "Trident cable loopback"
EN_LIMIT = 65
PL_LIMIT = 55
LISTEN_MODEL = "v3"
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
VAD_PROC = None
OWN = False
TRACK = {"mouth": "off", "qwen": "off"}


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
    text = generation.replace("\r\n", "\n").replace("\r", "\n")
    if "</think>" in text:
        text = text.split("</think>")[-1]
    elif "<channel|>" in text:
        text = text.split("<channel|>")[-1]
    elif "<think>" in text:
        return ""
    return _markup(text).strip()


def _flush(parts, buf):
    text = "".join(buf).strip()
    buf.clear()
    if text:
        parts.append(" ".join(text.split()))


def _closing_quote(opener):
    return {'"': '"', "“": "”", "«": "»"}[opener]


def word_windows(words, limit):
    chunks = []
    start = 0
    n = len(words)
    while start < n:
        chunks.append(" ".join(words[start : start + limit]))
        start += limit
    return chunks


def pack_atoms(parts, limit):
    chunks = []
    buf = []
    count = 0

    def flush():
        nonlocal buf, count
        if buf:
            chunks.append(" ".join(buf))
            buf = []
            count = 0

    for part in parts:
        words = part.split()
        n = len(words)
        if not n:
            continue
        if n > limit:
            flush()
            chunks.extend(word_windows(words, limit))
            continue
        if count and count + n > limit:
            flush()
        buf.extend(words)
        count += n
    flush()
    return chunks


def chunks_for_mouth(text, lang):
    limit = EN_LIMIT if lang == "en" else PL_LIMIT
    return pack_atoms(atoms(text), limit)


class AtomScan:
    def __init__(self):
        self.buf = []
        self.quote = None
        self.pending = ""

    def feed(self, text, final=False):
        data = self.pending + (text or "")
        self.pending = ""
        parts = []
        i = 0
        n = len(data)
        while i < n:
            ch = data[i]
            if self.quote is not None:
                self.buf.append(ch)
                if ch == _closing_quote(self.quote):
                    self.quote = None
                i += 1
                continue
            if ch in "\"“«":
                self.quote = ch
                self.buf.append(ch)
                i += 1
                continue
            if ch == "\n":
                j = i + 1
                while j < n and data[j] in " \t":
                    j += 1
                if j >= n and not final:
                    self.pending = data[i:]
                    return parts
                if j < n and data[j] == "\n":
                    _flush(parts, self.buf)
                    i = j + 1
                    continue
                self.buf.append(" ")
                i += 1
                continue
            if ch in ".!?":
                if i + 1 >= n and not final:
                    self.pending = data[i:]
                    return parts
                self.buf.append(ch)
                if i + 1 < n and data[i + 1].isspace():
                    _flush(parts, self.buf)
                i += 1
                continue
            self.buf.append(ch)
            i += 1
        if final:
            _flush(parts, self.buf)
        return parts


def atoms(text):
    return AtomScan().feed(text, final=True)


def _held_tail(text):
    markers = TOKENS + ("**", "__", "`")
    cap = min(len(text), max(len(marker) for marker in markers))
    best = 0
    for size in range(1, cap + 1):
        suffix = text[-size:]
        if any(marker.startswith(suffix) and len(suffix) < len(marker) for marker in markers):
            best = size
    return best


def _lead_hold(text):
    lead = text.lstrip()
    if not lead:
        return False
    for marker in ("<think>", "</think>", "<channel|>", "<|channel>thought", "<channel>thought"):
        if marker.startswith(lead) and lead != marker:
            return True
    return False


def _visible(raw, locked, final):
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not locked:
        if "</think>" in text:
            text = text.split("</think>")[-1]
        elif "<channel|>" in text:
            text = text.split("<channel|>")[-1]
        elif "<think>" in text or "<|channel>thought" in text or "<channel>thought" in text:
            return "", False
        elif not final and _lead_hold(text):
            return "", False
    if not final:
        held = _held_tail(text)
        if held:
            text = text[:-held]
    return _markup(text).lstrip(), True


class StreamFeed:
    """Closed atoms for the mouth while text is still arriving."""

    def __init__(self, lang):
        self.limit = EN_LIMIT if lang == "en" else PL_LIMIT
        self.scan = AtomScan()
        self.raw = ""
        self.fed = ""
        self.locked = False
        self.spoke = False

    def push(self, piece):
        self.raw += piece or ""
        return self._pump(False)

    def finish(self):
        return self._pump(True)

    def _pump(self, final):
        text, ready = _visible(self.raw, self.locked, final)
        if not ready:
            return []
        self.locked = True
        if not text.startswith(self.fed):
            if self.spoke:
                return []
            self.scan = AtomScan()
            self.fed = ""
        extra = text[len(self.fed) :]
        self.fed = text
        chunks = []
        for part in self.scan.feed(extra, final=final):
            words = part.split()
            if not words:
                continue
            self.spoke = True
            if len(words) > self.limit:
                chunks.extend(word_windows(words, self.limit))
            else:
                chunks.append(" ".join(words))
        return chunks


def budget_lang(model, lang):
    if lang:
        return lang
    return "pl" if model == "v3" else "en"


def mouth_tag(code):
    primary = code.split("-")[0].lower() if code else ""
    if primary in ("nb", "nn"):
        return "no"
    return primary or "en"


def is_quit(text):
    word = text.strip().strip(" .!?").casefold()
    return word in QUIT_WORDS


def clip_words(text, limit=200):
    return " ".join(text.split()[:limit])


def read_history():
    path = ROOT / "assistant.history.txt"
    if not path.is_file():
        return []
    pairs = []
    user = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("User: "):
            user = line[6:]
        elif line.startswith("Assistant: ") and user is not None:
            pairs.append((user, line[11:]))
            user = None
    return pairs[-4:]


def history_block():
    lines = []
    for user, reply in read_history():
        lines.append("User: " + user)
        lines.append("Assistant: " + reply)
    return "\n".join(lines)


def write_history(user, reply):
    pairs = read_history()
    pairs.append((clip_words(user), clip_words(reply)))
    pairs = pairs[-4:]
    body = []
    for user_text, reply_text in pairs:
        body.append("User: " + user_text)
        body.append("Assistant: " + reply_text)
    (ROOT / "assistant.history.txt").write_text("\n".join(body) + "\n", encoding="utf-8")


def posted_text(code, words):
    shown = code if code else "en"
    body = "User spoke " + shown + ".\n" + words
    block = history_block()
    if block:
        return block + "\n\n" + body
    return body


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


def note_qwen():
    if not OWN:
        return
    if TRACK["qwen"] == "off":
        TRACK["qwen"] = "started"
        write_session()


def note_mouth():
    if not OWN:
        return
    if TRACK["mouth"] == "off":
        TRACK["mouth"] = "started"
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
    mouth_started = session.get("mouth") == "started"
    qwen_started = session.get("qwen") == "started"
    if not pid and not vad_pid and not mouth_started and not qwen_started:
        remove_file("assistant.session.txt")
        print("stop: nothing started here", file=sys.stderr)
        return
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
    if mouth_started:
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


def write_listen_card(name):
    source = ROOT / "vad.txt"
    if not source.is_file():
        die("missing vad.txt")
    lines = []
    replaced = False
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("vad.device "):
            lines.append("vad.device " + name)
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        die("vad.txt missing vad.device")
    (ROOT / "vad_run.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def start_vad(name):
    global VAD_PROC
    exe = ROOT / "vad.exe"
    if not exe.is_file():
        die("missing vad.exe")
    write_listen_card(name)
    for name_in in ("vad.utterance.txt", "vad.hold", "vad.stop"):
        remove_file(name_in)
    VAD_PROC = subprocess.Popen(
        [str(exe), "--resident", "vad_run.txt"],
        cwd=ROOT,
        shell=False,
        stdin=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    pid_path = ROOT / "vad.pid"
    while time.time() < deadline:
        if VAD_PROC.poll() is not None:
            die("vad exited " + str(VAD_PROC.returncode))
        if pid_path.is_file():
            text = pid_path.read_text(encoding="utf-8", errors="replace")
            if "ready" in text.split():
                return
        time.sleep(0.05)
    die("vad did not become ready")


def release_vad():
    global VAD_PROC
    proc = VAD_PROC
    if proc is None:
        return
    VAD_PROC = None
    try:
        (ROOT / "vad.stop").write_bytes(b"")
    except OSError as exc:
        print("cannot write vad.stop: " + str(exc), file=sys.stderr)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    for name in ("vad.stop", "vad.hold", "assistant.pid"):
        try:
            remove_file(name)
        except SystemExit:
            pass


def set_hold(on):
    path = ROOT / "vad.hold"
    if on:
        path.write_bytes(b"")
        return
    remove_file(path)


def take_utterance():
    path = ROOT / "vad.utterance.txt"
    while not path.is_file():
        if VAD_PROC is None or VAD_PROC.poll() is not None:
            die("vad exited")
        time.sleep(0.05)
    name = path.read_text(encoding="utf-8").strip()
    remove_file(path)
    if not name or "/" in name or "\\" in name:
        die("bad vad utterance")
    wav = ROOT / name
    if not wav.is_file():
        die("missing vad wav: " + name)
    return wav


def parse_hear(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw, file=sys.stderr)
        die("hear json")
    if not isinstance(data, dict):
        die("hear json")
    text = data.get("text") or ""
    if not isinstance(text, str):
        die("hear json")
    text = re.sub(r"<[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?>", " ", text)
    text = " ".join(text.split())
    langs = data.get("languages") or []
    code = ""
    if isinstance(langs, list) and langs and isinstance(langs[0], str):
        code = langs[0].strip().strip("<>")
    return text, code


def transcribe(py, wav, live):
    argv = [py, str(ROOT / "hear.py"), "--wav", str(wav)]
    if live:
        argv.extend(["--language", "auto", "--format", "json", "--verbatim"])
    return run_child("hear", argv, keep_stdout=True, keep_stderr=False)


class Route:
    def __init__(self, kind, url):
        self.kind = kind
        self.url = url


def cuda_device():
    script = ROOT / "gemma" / "scripts" / "detect_gpu.ps1"
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", str(script)],
            cwd=ROOT,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (completed.stdout or "").strip() == "cuda"


def brain_url(args):
    if args.url:
        return args.url.strip()
    return nvidia_client.BRAIN_URL


def route_of(args):
    url = brain_url(args)
    if nvidia_client.probe(url):
        return Route("remote", url)
    if args.nvidia:
        die("brain down " + url)
    if cuda_device():
        return Route("gemma", url)
    return Route("qwen", url)


def announce(route):
    if route.kind == "remote":
        print("assistant: remote Gemma, local Vulkan " + route.url, file=sys.stderr)
    elif route.kind == "gemma":
        print("assistant: local Gemma", file=sys.stderr)
    else:
        print("assistant: Qwen", file=sys.stderr)


def speak_raw(py, args, raw, play=True, model=None, budget=None, prefix=None):
    model = args.model if model is None else model
    if budget is None:
        budget = budget_lang(model, args.lang)
    spoken = speakable(raw)
    parts = chunks_for_mouth(spoken, budget) if spoken else []
    if prefix:
        parts = ["[" + prefix + "] " + part for part in parts]
    if not parts:
        print("assistant: no speakable answer", file=sys.stderr)
        return []
    print("assistant: mouth " + str(len(parts)) + " chunk(s)", file=sys.stderr)
    note_mouth()
    argv = [py, str(ROOT / "mouth.py"), "--model", model]
    if not play:
        argv.append("--no-play")
    if prefix is None and args.lang is not None:
        argv.extend(["--lang", args.lang])
    argv.append("--")
    argv.extend(parts)
    if play:
        run_child("mouth", argv, keep_stdout=False, keep_stderr=False)
        return []
    out = run_child("mouth", argv, keep_stdout=True, keep_stderr=False)
    return [line.strip() for line in out.splitlines() if line.strip()]


def open_remote(args, question):
    image = None
    image_b64 = None
    if args.image:
        path = Path(args.image)
        if not path.is_file():
            die("missing image: " + str(path))
        image_b64, nbytes = nvidia_client.file_b64(path)
        image = str(path)
        print("nvidia: image_b64 " + str(nbytes) + " bytes", file=sys.stderr)
    ident = str(time.time_ns())
    nvidia_client.write_request(nvidia_client.request_body(ident, question, image))
    print("assistant: nvidia stream", file=sys.stderr, flush=True)
    return ident, image, image_b64


def iter_remote(route, args, opened, question):
    ident, image, image_b64 = opened
    return nvidia_client.iter_stream(
        route.url, ident, question, image, image_b64, args.timeout
    )


def remote_text(route, args, question):
    return "".join(iter_remote(route, args, open_remote(args, question), question))


def resident_lang(model, args, prefix):
    if prefix is None and args.lang:
        return args.lang
    return "pl" if model == "v3" else "en"


def prefixed(chunk, prefix):
    if prefix:
        return "[" + prefix + "] " + chunk
    return chunk


def remote_speak(route, args, question, model, budget, prefix, hold):
    import mouth

    model = args.model if model is None else model
    if budget is None:
        budget = budget_lang(model, args.lang)
    opened = open_remote(args, question)
    box = {"err": None, "parts": []}
    pending = queue.Queue()
    feed = StreamFeed(budget)

    def produce():
        try:
            for piece in iter_remote(route, args, opened, question):
                box["parts"].append(piece)
                sys.stdout.write(piece)
                sys.stdout.flush()
                for chunk in feed.push(piece):
                    pending.put(chunk)
            for chunk in feed.finish():
                pending.put(chunk)
        except SystemExit as exc:
            box["err"] = exc
        except Exception as exc:
            box["err"] = SystemExit(2)
            print("assistant: nvidia stream failed: " + str(exc), file=sys.stderr)
        finally:
            pending.put(None)

    threading.Thread(target=produce, daemon=True).start()
    print("mouth out: default", file=sys.stderr, flush=True)
    pid = mouth.ensure_resident(model, resident_lang(model, args, prefix))
    note_mouth()
    first = pending.get()

    def take_raw():
        text = "".join(box["parts"])
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
        return text

    if first is None:
        raw = take_raw()
        if box["err"] is not None:
            raise box["err"]
        print("assistant: no speakable answer", file=sys.stderr)
        return raw
    count = 0

    def arriving():
        nonlocal count
        chunk = first
        while chunk is not None:
            count += 1
            if count == 1:
                print("assistant: mouth", file=sys.stderr, flush=True)
            yield prefixed(chunk, prefix)
            chunk = pending.get()

    if hold:
        set_hold(True)
    try:
        mouth.speak_chunks(
            lambda sentence: mouth.resident_say(pid, sentence),
            arriving(),
            mouth.play_wav,
        )
    finally:
        if hold:
            set_hold(False)
    raw = take_raw()
    print("assistant: mouth " + str(count) + " chunk(s)", file=sys.stderr)
    if box["err"] is not None:
        raise box["err"]
    return raw


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
    print("assistant: " + label, file=sys.stderr)
    return run_child(label, argv, keep_stdout=True, keep_stderr=True)


def answer(py, route, args, question, speak=True, model=None, budget=None, prefix=None):
    if route.kind == "remote":
        if speak:
            return remote_speak(route, args, question, model, budget, prefix, False)
        raw = remote_text(route, args, question)
        show(raw)
        return raw
    if route.kind == "gemma":
        raw = local_turn(py, "gemma.py", args, question)
    else:
        raw = local_turn(py, "qwen.py", args, question)
    show(raw)
    if speak:
        speak_raw(py, args, raw, play=True, model=model, budget=budget, prefix=prefix)
    return raw


def speak_live(py, args, raw, budget, prefix):
    set_hold(True)
    try:
        speak_raw(py, args, raw, play=True, model=LISTEN_MODEL, budget=budget, prefix=prefix)
    finally:
        set_hold(False)


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


def cable_turn(py, route, args):
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
            reply = answer(py, route, args, transcript, speak=False)
            if route.kind == "remote":
                nvidia_exit = "0"
                if not reply.strip():
                    reasons.append("empty nvidia reply")
            else:
                nvidia_exit = "local"
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
            if route.kind == "remote":
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
    if route.kind == "remote" and nvidia_exit != "skipped":
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
        "url: " + route.url,
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
    nvidia_body = "exit " + nvidia_exit + "\nurl " + route.url + "\n\n" + reply_text
    if response_text:
        nvidia_body += "\nresponse file:\n" + response_text
        if not response_text.endswith("\n"):
            nvidia_body += "\n"
    write_proof("nvidia.txt", nvidia_body)
    print(body, end="" if body.endswith("\n") else "\n")
    raise SystemExit(0 if passed else 1)


def listen_loop(py, route, args):
    mic = hear.wasapi_capture_name()
    start_vad(mic)
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    while True:
        wav = take_utterance()
        raw = transcribe(py, wav, live=True)
        words, code = parse_hear(raw)
        show(words)
        if not words:
            print("assistant: hear returned no transcript", file=sys.stderr)
            if args.once:
                return
            continue
        if is_quit(words):
            print("assistant: quit", file=sys.stderr)
            return
        tag = mouth_tag(code)
        question = posted_text(code, words)
        if route.kind == "remote":
            reply = remote_speak(route, args, question, LISTEN_MODEL, tag, tag, True)
        else:
            reply = answer(py, route, args, question, speak=False)
            speak_live(py, args, reply, tag, tag)
        write_history(words, speakable(reply))
        if args.once:
            return


def main():
    global OWN
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
    parser.add_argument("--stop", action="store_true", help="stop the assistant tree on this PC")
    parser.add_argument("--model", default="nano", choices=MODELS, help="mouth model for --text, --wav, and --vb-cable")
    parser.add_argument("--lang", default=None, help="mouth language for --text, --wav, and --vb-cable")
    parser.add_argument("--image", default=None, help="image file forwarded on the turn")
    parser.add_argument(
        "--nvidia",
        action="store_true",
        help="brain URL must accept a connection or the process exits",
    )
    parser.add_argument("--url", default=None, help="brain URL; default is nvidia_client.BRAIN_URL")
    parser.add_argument("--timeout", type=float, default=180, help="brain HTTP timeout seconds (default 180)")
    args = parser.parse_args()

    if args.stop:
        if args.text is not None or args.wav is not None or args.vb_cable or args.mouth or args.image or args.nvidia or args.url:
            die("usage: assistant.py --stop")
        stop_tree()
        return
    if args.lang is not None and args.lang.strip() == "":
        die("empty language")
    if args.image is not None and args.image.strip() == "":
        die("empty image")
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
    if args.timeout <= 0:
        die("timeout must be > 0")

    py = venv_python()
    route = route_of(args)
    announce(route)
    OWN = True
    init_track()
    if args.vb_cable:
        cable_turn(py, route, args)
        return
    if args.text is not None:
        answer(py, route, args, args.text.strip())
        return
    if args.wav is not None:
        question = transcribe(py, args.wav.strip(), live=False).strip()
        show(question)
        if not question:
            print("assistant: hear returned no transcript", file=sys.stderr)
            return
        answer(py, route, args, question)
        return
    listen_loop(py, route, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("assistant: stopped", file=sys.stderr)
        raise SystemExit(0)
    finally:
        release_vad()
