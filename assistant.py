import argparse
import json
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import hear
import nvidia_client

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
BRAINS = ("qwen", "gemma")
QUIT_WORDS = {"quit", "exit", "stop"}
ACT_LINE = re.compile(r"^act:\s*(.*)$", re.IGNORECASE)
NOTE_PATH = ROOT / "assistant.note.txt"
NOTE_LIMIT = 200
CABLE_PHRASE = "Trident cable loopback"
EN_LIMIT = 65
PL_LIMIT = 55
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
CUE_WAV = ROOT / "proof" / "g429-live-cue.wav"
CUE_HZ = 1000
CUE_MS = 500
CUE_GAP_S = 0.28
CUE_WAIT_S = 10.0
CUE_LISTEN_S = 60.0


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


def chunks_for_mouth(text, fast="nano"):
    if fast not in ("nano", "turbo"):
        fast = "nano"
    return _plan_chunks(text, fast, pack=True)


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


LID_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz"
LID_NAME = "lid.176.ftz"
# Tags stored on v3-t3.gguf, minus the ipa marker. English spans do not use this set.
V3_LANGS = frozenset(
    "ar bg cy cs da de el es fi fr he hi hu it ja ko ms nl no pl pt ro ru si sk sv sw ta tr vi zh".split()
)
_LID = None
_LID_CACHE = {}


def fast_model(name):
    return "turbo" if name == "turbo" else "nano"


def _lid_model():
    global _LID
    if _LID is not None:
        return _LID
    import fasttext
    import urllib.request

    path = ROOT / LID_NAME
    if not path.is_file():
        tmp = ROOT / (LID_NAME + ".part")
        try:
            with urllib.request.urlopen(LID_URL, timeout=60) as response:
                tmp.write_bytes(response.read())
            tmp.replace(path)
        except Exception as exc:
            if tmp.is_file():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            die("cannot fetch " + LID_NAME + ": " + str(exc))
    fasttext.FastText.eprint = lambda _msg: None
    _LID = fasttext.load_model(str(path))
    return _LID


def _lid(text):
    text = " ".join(text.split()).strip(".,!?;:…\"'“”«»()[]")
    text = text.strip(".").strip()
    if not text:
        return "en", 0.0
    hit = _LID_CACHE.get(text)
    if hit is None:
        labels, probs = _lid_model().predict(text, k=1)
        hit = (labels[0].replace("__label__", ""), float(probs[0]))
        _LID_CACHE[text] = hit
    return hit


def _solo(token):
    return _lid(token)


def _script(ch):
    if not ch.isalpha():
        return None
    o = ord(ch)
    if o <= 0x024F or 0x1E00 <= o <= 0x1EFF:
        return "Latn"
    if 0x0400 <= o <= 0x052F:
        return "Cyrl"
    if 0x0590 <= o <= 0x05FF:
        return "Hebr"
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F:
        return "Arab"
    if 0x0370 <= o <= 0x03FF:
        return "Grek"
    if 0x0900 <= o <= 0x097F:
        return "Deva"
    if 0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF:
        return "Cjk"
    return "Other"


def _anchored(matches, lang):
    for match in matches:
        code, prob = _solo(match.group())
        if code == lang and prob >= 0.60:
            return True
    return False


def _span_words(text):
    spans = list(re.finditer(r"\S+", text))
    if not spans:
        return [("en", text)] if text.strip() else []

    def phrase(start, end):
        return text[spans[start].start() : spans[end - 1].end()]

    def rec(start, end):
        chunk = phrase(start, end)
        lang, prob = _lid(chunk)
        # 0.95 keeps a sure monolingual span whole. English at 0.80 stays whole so
        # "The door" is not peeled off as Dutch. Mixed lines sit well below that.
        if end - start < 2 or prob >= 0.95 or (lang == "en" and prob >= 0.80):
            return [(mouth_tag(lang), chunk)]
        best = None
        for cut in range(start + 1, end):
            left_lang, left_p = _lid(phrase(start, cut))
            right_lang, right_p = _lid(phrase(cut, end))
            if left_lang == right_lang or left_p < 0.70 or right_p < 0.70:
                continue
            if not _anchored(spans[start:cut], left_lang) or not _anchored(spans[cut:end], right_lang):
                continue
            if cut - start == 1 and _solo(spans[start].group())[1] < 0.90:
                continue
            if end - cut == 1 and _solo(spans[cut].group())[1] < 0.90:
                continue
            bound = 0
            end_lang, end_p = _solo(spans[cut - 1].group())
            open_lang, open_p = _solo(spans[cut].group())
            if end_lang == left_lang and end_p >= 0.50:
                bound += 1
            if open_lang == right_lang and open_p >= 0.50:
                bound += 2
            key = (left_p + right_p, bound, -cut)
            if best is None or key > best[0]:
                best = (key, cut)
        if best is None:
            return [(mouth_tag(lang), chunk)]
        cut = best[1]
        return rec(start, cut) + rec(cut, end)

    return rec(0, len(spans))


def _spans(text):
    if not text.strip():
        return []
    runs = []
    current = None
    for index, ch in enumerate(text):
        kind = _script(ch)
        if kind is None:
            continue
        if current is None:
            current = kind
            continue
        if kind != current:
            runs.append((current, index))
            current = kind
    if current is None:
        return [("en", text.strip())]
    runs.append((current, len(text)))
    pieces = []
    left = 0
    for kind, end in runs:
        chunk = text[left:end].strip()
        left = end
        if not chunk:
            continue
        if kind == "Latn":
            pieces.extend(_span_words(chunk))
        else:
            lang, _prob = _lid(chunk)
            pieces.append((mouth_tag(lang), chunk))
    return pieces


def _voice(lang, fast):
    tag = mouth_tag(lang)
    if tag == "en":
        return fast, "en"
    if tag not in V3_LANGS:
        die("mouth has no voice for " + tag)
    return "v3", tag


def _windows(text, lang, fast):
    model, tag = _voice(lang, fast)
    limit = EN_LIMIT if tag == "en" else PL_LIMIT
    words = text.split()
    if len(words) > limit:
        return [(window, model, tag) for window in word_windows(words, limit)]
    if words:
        return [(text, model, tag)]
    return []


def _plan_chunks(text, fast, pack):
    if not pack:
        pieces = []
        for lang, span in _spans(text):
            pieces.extend(_windows(span, lang, fast))
        return pieces
    labeled = []
    for atom in atoms(text):
        labeled.extend(_spans(atom))
    pieces = []
    index = 0
    while index < len(labeled):
        lang = labeled[index][0]
        end = index + 1
        while end < len(labeled) and labeled[end][0] == lang:
            end += 1
        model, tag = _voice(lang, fast)
        limit = EN_LIMIT if tag == "en" else PL_LIMIT
        for chunk in pack_atoms([labeled[i][1] for i in range(index, end)], limit):
            pieces.append((chunk, model, tag))
        index = end
    return pieces


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

    def __init__(self, fast="nano"):
        self.fast = fast if fast in ("nano", "turbo") else "nano"
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
            pieces = _plan_chunks(part, self.fast, pack=False)
            if not pieces:
                continue
            self.spoke = True
            chunks.extend(pieces)
        return chunks


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


def split_act(text):
    """Question text, then one trailing act. No act line leaves the question unchanged."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = normalized.split("\n")
    act_at = None
    for index, line in enumerate(raw_lines):
        if ACT_LINE.match(line.strip()):
            if act_at is not None:
                die("one act")
            act_at = index
    if act_at is None:
        return text.strip(), None
    if any(line.strip() for line in raw_lines[act_at + 1 :]):
        die("act line last")
    rest = ACT_LINE.match(raw_lines[act_at].strip()).group(1).strip()
    if not rest:
        die("empty act")
    name, _, arg = rest.partition(" ")
    question = "\n".join(raw_lines[:act_at]).strip()
    return question, (name.casefold(), arg.strip())


def act_user_line(name, arg):
    if arg:
        return "act: " + name + " " + arg
    return "act: " + name


def note_lines():
    if not NOTE_PATH.is_file():
        die("no note")
    try:
        stored = NOTE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        die("cannot read assistant.note.txt: " + str(exc))
    lines = [item.strip() for item in stored.splitlines() if item.strip()]
    if not lines:
        die("no note")
    return lines


def prepare_act(name, arg):
    if name == "time":
        if arg:
            die("time takes no words")
        return "time", ""
    if name == "note":
        line = " ".join(arg.split())
        if not line:
            die("empty note")
        if len(line) > NOTE_LIMIT:
            die("note over 200 characters")
        return "note", line
    if name == "next":
        if arg:
            die("next takes no words")
        note_lines()
        return "next", ""
    die("unknown act " + name)


def commit_act(name, arg):
    print("assistant: act " + name, file=sys.stderr, flush=True)
    if name == "time":
        return time.strftime("The time is %H:%M.")
    if name == "note":
        try:
            with NOTE_PATH.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(arg + "\n")
        except OSError as exc:
            die("cannot write assistant.note.txt: " + str(exc))
        return "Noted. " + arg
    said = note_lines()[-1]
    if said[-1] not in ".!?":
        said += "."
    return "The note is " + said


def run_local_act(args, act, local):
    name, arg = prepare_act(act[0], act[1])
    if local:
        print("assistant: local act", file=sys.stderr, flush=True)
    sentence = commit_act(name, arg)
    show(sentence)
    speak_raw(args, sentence, play=True, flip=False)
    return sentence, act_user_line(name, arg)


def speak_act(args, act, local, hold):
    if not hold:
        return run_local_act(args, act, local)
    set_hold(True)
    try:
        return run_local_act(args, act, local)
    finally:
        set_hold(False)


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


def stop_vad():
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
    for name in ("vad.stop", "vad.hold"):
        try:
            remove_file(name)
        except SystemExit:
            pass


def release_vad():
    stop_vad()
    if read_pid("assistant.pid") != os.getpid():
        return
    try:
        remove_file("assistant.pid")
    except SystemExit:
        pass


def set_hold(on):
    path = ROOT / "vad.hold"
    if on:
        path.write_bytes(b"")
        return
    remove_file(path)


def take_utterance(timeout=None):
    path = ROOT / "vad.utterance.txt"
    deadline = None if timeout is None else time.monotonic() + timeout
    while not path.is_file():
        if VAD_PROC is None or VAD_PROC.poll() is not None:
            die("vad exited")
        if deadline is not None and time.monotonic() >= deadline:
            die("no utterance")
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


def release_shared_gpu(flip):
    import gemma

    cuda_name, vulkan_name = gemma.adapter_names()
    print(
        "assistant: gpu cuda " + (cuda_name or "none") + " vulkan " + (vulkan_name or "none"),
        file=sys.stderr,
    )
    if not flip:
        return
    if not cuda_name:
        return
    if not vulkan_name:
        die("cannot read Vulkan device 0")
    print("assistant: flip gemma off", file=sys.stderr)
    gemma.stop_resident()


def peer_url(args):
    if not args.nvidia:
        return None
    return (args.url or os.environ.get("TRIDENT_NVIDIA_URL", "")).strip()


def turn_place(args):
    import gemma

    found = gemma.place(peer_url(args))
    print("assistant: place " + gemma.place_line(found), file=sys.stderr, flush=True)
    if found.brain == "missing":
        die("peer missing")
    return found


def place_turn(args):
    found = turn_place(args)
    if args.image and brain_script(found, args.brain) == "qwen.py":
        die("image asks use --brain gemma")
    return found


def injected_turn(py, args, question, act):
    if not question and not act:
        die("empty text")
    if act:
        prepare_act(act[0], act[1])
    if not question:
        if args.image:
            die("image asks for a question")
        own_turn()
        run_local_act(args, act, True)
        return
    found = place_turn(args)
    own_turn()
    answer(py, found, args, question)
    if act:
        run_local_act(args, act, False)


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


def process_inject_turn(py, args, found, text):
    stripped = text.strip()
    if not stripped:
        return True
    if is_quit(stripped):
        print("assistant: quit", file=sys.stderr)
        return False
    question, act = split_act(stripped)
    if not question and not act:
        print("assistant: empty turn", file=sys.stderr)
        return True
    if act:
        prepare_act(act[0], act[1])
    if not question:
        if args.image:
            die("image asks for a question")
        sentence, line = run_local_act(args, act, True)
        write_history(line, sentence)
        return not args.once
    reply = answer(py, found, args, question)
    write_history(question, speakable(reply))
    if act:
        sentence, line = run_local_act(args, act, False)
        write_history(line, sentence)
    return not args.once


def inject_loop(py, args, path):
    found = place_turn(args)
    own_turn()
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    for text in iter_inject_turns(path):
        if not process_inject_turn(py, args, found, text):
            return


def iris_outbox_path(path_arg):
    if path_arg:
        return Path(path_arg)
    env = os.environ.get("TRIDENT_IRIS_OUTBOX", "").strip()
    if env:
        return Path(env)
    return ROOT / "iris_outbox.txt"


def consume_iris_outbox(args, path_arg):
    path = iris_outbox_path(path_arg)
    if not path.is_file():
        print("assistant: iris outbox missing", file=sys.stderr, flush=True)
        raise SystemExit(0)
    line = path.read_text(encoding="utf-8-sig").strip()
    if not line:
        print("assistant: iris outbox empty", file=sys.stderr, flush=True)
        raise SystemExit(0)
    try:
        path.write_text("", encoding="utf-8")
    except OSError as exc:
        die("iris outbox clear: " + str(exc))
    print("assistant: iris outbox consumed " + str(path), file=sys.stderr, flush=True)
    own_turn()
    speak_raw(args, line, play=True, flip=False)


def brain_script(found, brain_flag):
    if found.brain == "missing":
        die("peer missing")
    if found.brain == "post":
        return "nvidia_client.py"
    if found.brain == "resident":
        return "gemma.py"
    if brain_flag == "gemma":
        return "gemma.py"
    return "qwen.py"


def speak_raw(args, raw, play=True, flip=False):
    import mouth

    spoken = speakable(raw)
    parts = chunks_for_mouth(spoken, fast_model(args.model)) if spoken else []
    if not parts:
        print("assistant: no speakable answer", file=sys.stderr)
        return []
    release_shared_gpu(flip)
    print("assistant: mouth " + str(len(parts)) + " chunk(s)", file=sys.stderr)
    note_mouth()
    if play:
        print("mouth out: default", file=sys.stderr, flush=True)
        mouth.speak_pieces(parts, mouth.play_wav)
        return []
    return [str(Path(path).resolve()) for path in mouth.speak_pieces(parts, None)]


def prepare_remote(args, question):
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
    return ident, image, image_b64


def open_remote(args, question):
    opened = prepare_remote(args, question)
    print("assistant: nvidia stream", file=sys.stderr, flush=True)
    return opened


def iter_remote(found, args, opened, question):
    ident, image, image_b64 = opened
    return nvidia_client.iter_stream(
        found.url, ident, question, image, image_b64, args.timeout
    )


def remote_whole(found, args, question):
    import io

    ident, image, image_b64 = prepare_remote(args, question)
    print("assistant: nvidia", file=sys.stderr, flush=True)
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        nvidia_client.post_turn(found.url, ident, question, image, image_b64, args.timeout, False)
    finally:
        sys.stdout = old
    return buf.getvalue()


def remote_speak(found, args, question, hold):
    import mouth

    opened = open_remote(args, question)
    box = {"err": None, "parts": []}
    pending = queue.Queue()
    feed = StreamFeed(fast_model(args.model))

    def produce():
        try:
            for piece in iter_remote(found, args, opened, question):
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
            yield chunk
            chunk = pending.get()

    if hold:
        set_hold(True)
    try:
        mouth.speak_pieces(arriving(), mouth.play_wav)
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


def answer(py, found, args, question, speak=True):
    if found.brain == "post":
        if speak and not found.flip:
            return remote_speak(found, args, question, False)
        raw = remote_whole(found, args, question)
        show(raw)
        if speak:
            speak_raw(args, raw, play=True, flip=found.flip)
        return raw
    script = brain_script(found, args.brain)
    if args.image and script == "qwen.py":
        die("image asks use --brain gemma")
    raw = local_turn(py, script, args, question)
    show(raw)
    if speak:
        speak_raw(args, raw, play=True, flip=found.flip)
    return raw


def speak_live(args, raw, flip=False):
    set_hold(True)
    try:
        speak_raw(args, raw, play=True, flip=flip)
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


def cable_turn(py, found, args):
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
            reply = answer(py, found, args, transcript, speak=False)
            if found.brain == "post":
                nvidia_exit = "0"
                if not reply.strip():
                    reasons.append("empty nvidia reply")
            else:
                nvidia_exit = found.brain
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
            if found.brain == "post":
                nvidia_exit = str(code)
            reasons.append("brain exit " + str(code))
    if args.mouth and not reasons:
        if not reply.strip():
            reasons.append("no reply to speak")
        else:
            try:
                mouth_paths = speak_raw(args, reply, play=False, flip=found.flip)
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
    if found.brain == "post" and nvidia_exit != "skipped":
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
        "url: " + found.url,
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
    nvidia_body = "exit " + nvidia_exit + "\nurl " + found.url + "\n\n" + reply_text
    if response_text:
        nvidia_body += "\nresponse file:\n" + response_text
        if not response_text.endswith("\n"):
            nvidia_body += "\n"
    write_proof("nvidia.txt", nvidia_body)
    print(body, end="" if body.endswith("\n") else "\n")
    raise SystemExit(0 if passed else 1)


def heard_turn(py, found, args, words, code):
    if is_quit(words):
        print("assistant: quit", file=sys.stderr)
        return False
    question, act = split_act(words)
    if question and is_quit(question):
        print("assistant: quit", file=sys.stderr)
        return False
    if act:
        prepare_act(act[0], act[1])
    if not question:
        if act is None:
            print("assistant: hear returned no transcript", file=sys.stderr)
            return not args.once
        sentence, line = speak_act(args, act, True, True)
        write_history(line, sentence)
        return not args.once
    posted = posted_text(code, question)
    if found.brain == "post" and not found.flip:
        reply = remote_speak(found, args, posted, True)
    else:
        reply = answer(py, found, args, posted, speak=False)
        speak_live(args, reply, found.flip)
    write_history(question, speakable(reply))
    if act:
        sentence, line = speak_act(args, act, False, True)
        write_history(line, sentence)
    return not args.once


def cue_stamp():
    now = time.time()
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    return base + ".%03d" % int((now - int(now)) * 1000)


def cue_log(line):
    print(line, file=sys.stderr, flush=True)
    path = ROOT / "proof" / "g429-live-mic.log"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print("assistant: cue log failed: " + str(exc), file=sys.stderr)


def cue_log_reset():
    path = ROOT / "proof" / "g429-live-mic.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def ensure_cue_wav():
    path = CUE_WAV
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 44100
    count = int(rate * CUE_MS / 1000)
    frames = bytearray()
    for index in range(count):
        second = index / rate
        sign = 1.0 if (int(second * CUE_HZ) % 2 == 0) else -1.0
        sample = int(0.98 * 32767 * sign)
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


def play_cues(count):
    import mouth

    wav = ensure_cue_wav()
    cue_log("assistant: cue " + str(count) + " begin " + cue_stamp())
    for index in range(count):
        if index:
            time.sleep(CUE_GAP_S)
        mouth.play_wav(wav)
        cue_log("assistant: cue " + str(count) + " beep " + str(index + 1) + " " + cue_stamp())
    cue_log("assistant: cue " + str(count) + " end " + cue_stamp())


def snapshot_turn_files():
    proof = ROOT / "proof"
    proof.mkdir(parents=True, exist_ok=True)
    for name in ("nvidia_turn.request.txt", "nvidia_turn.response.txt"):
        src = ROOT / name
        if not src.is_file():
            continue
        dest = proof / ("g429-live-mic-" + name)
        dest.write_bytes(src.read_bytes())
        cue_log("assistant: saved " + str(dest))


def cued_listen_loop(py, found, args):
    if not (ROOT / "vad.exe").is_file():
        die("missing vad.exe")
    if not (ROOT / "vad.txt").is_file():
        die("missing vad.txt")
    model = ROOT / "silero_vad.onnx"
    try:
        model_bytes = model.read_bytes()
    except OSError as exc:
        die("missing vad model: " + str(exc))
    if not model_bytes:
        die("empty vad model")
    mic = hear.wasapi_capture_name()
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    cue_log_reset()
    cue_log("assistant: cue mic " + mic)
    cue_log("assistant: mic closed before cue " + cue_stamp())
    while True:
        play_cues(1)
        cue_log("assistant: cue wait 10 begin " + cue_stamp())
        time.sleep(CUE_WAIT_S)
        cue_log("assistant: cue wait 10 end " + cue_stamp())
        if found.brain == "post" and not nvidia_client.probe(found.url):
            die("peer missing")
        play_cues(2)
        cue_log("assistant: mic opening " + mic + " " + cue_stamp())
        start_vad(mic)
        cue_log("assistant: mic open " + cue_stamp())
        wav = take_utterance(CUE_LISTEN_S)
        cue_log("assistant: utterance " + wav.name + " " + cue_stamp())
        stop_vad()
        cue_log("assistant: mic closed " + cue_stamp())
        kept = ROOT / "proof" / "g429-live-mic-utterance.wav"
        kept.write_bytes(wav.read_bytes())
        cue_log("assistant: utterance copy " + str(kept))
        raw = transcribe(py, wav, live=True)
        words, code = parse_hear(raw)
        show(words)
        hear_path = ROOT / "proof" / "g429-live-mic-hear.json"
        hear_path.write_text(raw, encoding="utf-8")
        cue_log("assistant: hear " + words)
        if not words:
            print("assistant: hear returned no transcript", file=sys.stderr)
            if args.once:
                die("hear returned no transcript")
            continue
        try:
            keep = heard_turn(py, found, args, words, code)
        except SystemExit:
            snapshot_turn_files()
            raise
        snapshot_turn_files()
        play_cues(3)
        if not keep:
            return


def listen_loop(py, found, args):
    if args.cue:
        cued_listen_loop(py, found, args)
        return
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
        if not heard_turn(py, found, args, words, code):
            return


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="assistant.py")
    parser.add_argument("--once", action="store_true", help="one turn, then exit")
    parser.add_argument(
        "--text",
        default=None,
        help="skip the mic; one brain then mouth round. A final act: line runs on this PC. With --vb-cable, the phrase played into CABLE Input",
    )
    parser.add_argument(
        "--inject",
        nargs="?",
        const="-",
        metavar="PATH",
        default=None,
        help="closed-mic loop: PATH is one turn per non-empty line, or blocks split by a --- line; flag alone reads stdin until EOF. No live mic",
    )
    parser.add_argument(
        "--iris-outbox",
        nargs="?",
        const="-",
        metavar="PATH",
        default=None,
        help="read one PE iris_outbox line, clear the file, speak via mouth. PATH or TRIDENT_IRIS_OUTBOX; else repo iris_outbox.txt. No brain POST",
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
    parser.add_argument("--brain", default="qwen", choices=BRAINS, help="CPU row only; a CUDA device uses Gemma")
    parser.add_argument(
        "--model",
        default="nano",
        choices=MODELS,
        help="fast English mouth: turbo, otherwise nano. Other languages use v3",
    )
    parser.add_argument("--lang", default=None, help="accepted; the chunker assigns the voice. Empty tag fails")
    parser.add_argument("--image", default=None, help="image file forwarded on the turn")
    parser.add_argument(
        "--nvidia",
        action="store_true",
        help="POST to one peer URL; a closed port exits peer missing",
    )
    parser.add_argument("--url", default=None, help="peer POST URL; requires --nvidia. Else TRIDENT_NVIDIA_URL")
    parser.add_argument("--timeout", type=float, default=180, help="brain HTTP timeout seconds (default 180)")
    parser.add_argument(
        "--cue",
        action="store_true",
        help="live mic: one beep, wait 10s, two beeps, then open the default mic. Three beeps after the turn",
    )
    args = parser.parse_args()

    if args.stop:
        if (
            args.text is not None
            or args.wav is not None
            or args.vb_cable
            or args.mouth
            or args.image
            or args.nvidia
            or args.url
            or args.cue
            or args.brain != "qwen"
        ):
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
    if args.inject is not None:
        if args.text is not None or args.wav is not None or args.vb_cable:
            die("inject asks for no text, wav, or vb-cable")
        if str(args.inject).strip() == "":
            die("empty inject path")
    if args.cue and (
        args.text is not None
        or args.wav is not None
        or args.vb_cable
        or args.inject is not None
        or args.iris_outbox is not None
    ):
        die("cue asks for the live mic")
    if args.iris_outbox is not None:
        if args.text is not None or args.wav is not None or args.vb_cable or args.inject is not None:
            die("iris-outbox asks for no text, wav, vb-cable, or inject")
        if args.nvidia or args.url:
            die("iris-outbox asks for no nvidia or url")
        if str(args.iris_outbox).strip() == "":
            die("empty iris-outbox path")
    if args.vb_cable and args.wav is not None:
        die("use vb-cable or wav, not both")
    if args.mouth and not args.vb_cable:
        die("--mouth asks for --vb-cable")
    if args.url is not None and args.url.strip() == "":
        die("empty url")
    if args.url and not args.nvidia:
        die("url asks for --nvidia")
    if args.timeout <= 0:
        die("timeout must be > 0")

    py = venv_python()
    if args.vb_cable:
        found = place_turn(args)
        own_turn()
        cable_turn(py, args, found)
        return
    if args.iris_outbox is not None:
        path = None if args.iris_outbox == "-" else str(args.iris_outbox).strip()
        consume_iris_outbox(args, path)
        return
    if args.inject is not None:
        path = None if args.inject == "-" else str(args.inject).strip()
        inject_loop(py, args, path)
        return
    if args.text is not None:
        question, act = split_act(args.text.strip())
        injected_turn(py, args, question, act)
        return
    if args.wav is not None:
        heard = transcribe(py, args.wav.strip(), live=False).strip()
        show(heard)
        if not heard:
            print("assistant: hear returned no transcript", file=sys.stderr)
            return
        question, act = split_act(heard)
        injected_turn(py, args, question, act)
        return
    found = place_turn(args)
    own_turn()
    listen_loop(py, found, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("assistant: stopped", file=sys.stderr)
        raise SystemExit(0)
    finally:
        release_vad()
