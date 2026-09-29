import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import hear
import nvidia_client
import seat

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
BRAINS = ("qwen", "gemma")
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
MIN_SPEECH_MS = 400
MIN_PEAK = 800
HOT_S = 25.0
HOLD_DEPTH = 0
DEFER_KINDS = ("say", "work", "status")


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
        die("fast mouth is nano or turbo")
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


def mouth_tag(code):
    primary = code.split("-")[0].lower() if code else ""
    if primary in ("nb", "nn"):
        return "no"
    return primary or "en"


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


VOICE_MEMORY = ROOT / "voice.memory.txt"


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


def memory_preface():
    import gemma

    facts, pairs, works = gemma.read_memory(VOICE_MEMORY)
    lines = []
    if facts:
        lines.append("Remembered:")
        lines.extend(facts)
    if works:
        lines.append("Waiting work:")
        lines.extend(works)
    if pairs:
        for user, reply in pairs[-4:]:
            if user:
                lines.append("User: " + user)
            if reply:
                lines.append("Assistant: " + reply)
        return lines
    block = history_block()
    if block:
        lines.append(block)
    return lines


def voice_question(code, words):
    # The transcript is the whole question. The brain's prompt chooses the language and the tool.
    del code
    return (words or "").strip()


def signal_question(kind, text):
    parts = [
        "You are Jarvis. Nobody is speaking. A seat signal arrived.",
        "Kind: " + kind,
        "Text: " + text,
        tool_decls(),
    ]
    parts.extend(memory_preface())
    parts.append(
        "If the owner should hear this now, speak one or two short sentences and do not call a tool. "
        "If a status should only be kept, call remember. "
        "If work should wait, call next. "
        "Do not call stop for a seat signal. Do not invent other work."
    )
    return "\n".join(parts)


def voice_follow(name, result):
    return "\n".join(
        (
            "You are Jarvis. The tool " + name + " finished.",
            "Result: " + (result or ""),
            "Speak one or two short sentences. Do not call a tool.",
        )
    )


def remember_turn(user, spoken):
    import gemma

    write_history(user, spoken or "")
    if user and spoken:
        gemma.append_memory(VOICE_MEMORY, user, spoken)


def organism_stop(raw):
    import gemma

    call = gemma.parse_tool_call(raw or "")
    return bool(call) and call[0] == "stop"


def release_mouth():
    import mouth

    if mouth.stop_resident():
        print("assistant: mouth stopped", file=sys.stderr, flush=True)


def fetch_reply(py, found, args, question):
    if found.brain == "post":
        return remote_whole(found, args, question)
    script = brain_script(found, args.brain)
    if args.image and script == "qwen.py":
        die("image asks use --brain gemma")
    return local_turn(py, script, args, question)


def say_text(args, raw, hold, flip):
    if hold:
        speak_live(args, raw, flip)
    else:
        speak_raw(args, raw, flip)


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
            name, call[1], call[2], memory_path=VOICE_MEMORY
        )
        if blocked and not fallback:
            spoken = speakable(blocked)
            if spoken:
                say_text(args, spoken, hold, found.flip)
            elif blocked.strip():
                print("assistant: no speakable answer", file=sys.stderr)
            return (not args.once), spoken
        raw2 = fetch_reply(py, found, args, voice_follow(name, fallback or ""))
        show(raw2)
        return apply_reply(py, found, args, raw2, hold, False)
    spoken = speakable(raw)
    if spoken:
        say_text(args, raw, hold, found.flip)
    else:
        print("assistant: no speakable answer", file=sys.stderr)
    return (not args.once), spoken


def run_voice_turn(py, found, args, words, code, hold):
    text = (words or "").strip()
    if not text:
        print("hear: empty", file=sys.stderr, flush=True)
        return not args.once
    reply = fetch_reply(py, found, args, voice_question(code, text))
    show(reply)
    cont, spoken = apply_reply(py, found, args, reply, hold, True)
    if cont:
        remember_turn(text, spoken)
    return cont


def turn_after_transcript(py, args, words, code, hold):
    text = (words or "").strip()
    if not text:
        print("hear: empty", file=sys.stderr, flush=True)
        return not args.once
    print("hear: " + text, file=sys.stderr, flush=True)
    show(text)
    found = place_turn(args)
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
    global VAD_PROC, HOLD_DEPTH
    HOLD_DEPTH = 0
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
    global VAD_PROC, HOLD_DEPTH
    HOLD_DEPTH = 0
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
    # The mouth also holds. Depth keeps the mic closed until the whole turn is done.
    global HOLD_DEPTH
    path = ROOT / "vad.hold"
    if on:
        HOLD_DEPTH += 1
        path.write_bytes(b"")
        return
    if HOLD_DEPTH > 0:
        HOLD_DEPTH -= 1
    if HOLD_DEPTH == 0:
        remove_file(path)


def judge_wav(path):
    import wave

    try:
        handle = wave.open(str(path), "rb")
    except (wave.Error, OSError, EOFError):
        return "unreadable", 0
    with handle:
        rate = handle.getframerate() or 0
        width = handle.getsampwidth()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if rate <= 0:
        return "unreadable", 0
    ms = int(frames * 1000 / rate)
    if ms < MIN_SPEECH_MS:
        return "short", ms
    if pcm_peak(raw, width) < MIN_PEAK:
        return "quiet", ms
    return "keep", ms


def pcm_peak(raw, width):
    if not raw or width <= 0:
        return 0
    if width == 2:
        count = len(raw) // 2
        peak = 0
        for index in range(count):
            sample = int.from_bytes(raw[index * 2 : index * 2 + 2], "little", signed=True)
            value = sample if sample >= 0 else -sample
            if value > peak:
                peak = value
        return peak
    peak = 0
    for byte in raw:
        if byte > peak:
            peak = byte
    return peak


def log_vad(kind, ms):
    if kind == "keep":
        print("vad: keep " + str(ms) + "ms", file=sys.stderr, flush=True)
        return
    if kind == "short":
        print("vad: drop short " + str(ms) + "ms", file=sys.stderr, flush=True)
        return
    if kind == "quiet":
        print("vad: drop quiet", file=sys.stderr, flush=True)
        return
    print("vad: drop " + kind, file=sys.stderr, flush=True)


def words_from_wav(py, wav, live):
    def once():
        raw = transcribe(py, wav, live)
        if live:
            return parse_hear(raw)
        return " ".join((raw or "").split()), None

    words, code = once()
    if words:
        return words, code
    print("hear: empty", file=sys.stderr, flush=True)
    words, code = once()
    if not words:
        print("hear: empty", file=sys.stderr, flush=True)
    return words, code


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


def announce_place(found):
    import gemma

    print("assistant: place " + gemma.place_line(found), file=sys.stderr, flush=True)
    if found.brain == "post":
        print("assistant: lan", file=sys.stderr, flush=True)
    else:
        print("assistant: alone", file=sys.stderr, flush=True)


def turn_place(args):
    # One route. A peer that accepts is the brain. A closed port is this PC.
    # The computer name is not a key. This does not bind port 8765.
    import gemma

    peer = peer_url(args)
    if peer:
        if gemma.split_peer(peer) is None:
            die("url must start with http:// or https://")
        found = gemma.place(peer)
        if found.brain == "post":
            announce_place(found)
            return found
        print("assistant: peer unreachable", file=sys.stderr, flush=True)
    found = gemma.place(None)
    if found.brain == "missing":
        die("peer missing")
    announce_place(found)
    return found


def place_turn(args):
    found = turn_place(args)
    if args.image and brain_script(found, args.brain) == "qwen.py":
        die("image asks use --brain gemma")
    return found


def injected_turn(py, args, text):
    if not (text or "").strip():
        die("empty text")
    own_turn()
    if not drain_seat(py, args, False):
        return
    turn_after_transcript(py, args, text.strip(), None, False)


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


def act_signal(py, args, found, sig, hold):
    import gemma

    text = " ".join(sig.text.split())
    if not text:
        return True
    if sig.kind == "stop":
        print("assistant: stop " + text, file=sys.stderr, flush=True)
        if text == "voice":
            release_mouth()
            return False
        facts, pairs, works = gemma.read_memory(VOICE_MEMORY)
        target = gemma.clip_fact(text)
        if target and target in works:
            works = [item for item in works if item != target]
            gemma.write_memory(VOICE_MEMORY, facts, pairs, works)
            print("assistant: work dropped", file=sys.stderr, flush=True)
        return True
    if sig.kind == "say":
        print("assistant: say " + text, file=sys.stderr, flush=True)
        say_text(args, text, hold, found.flip, True)
        return True
    if sig.kind == "status":
        seat.record_status(text)
        print("assistant: status " + text, file=sys.stderr, flush=True)
        raw = fetch_reply(py, found, args, signal_question("status", text))
        show(raw)
        cont, _spoken = apply_reply(py, found, args, raw, hold, True)
        return cont
    if sig.kind == "work":
        print("assistant: work " + text, file=sys.stderr, flush=True)
        raw = fetch_reply(py, found, args, signal_question("work", text))
        show(raw)
        cont, spoken = apply_reply(py, found, args, raw, hold, True)
        if cont and not spoken and gemma.parse_tool_call(raw) is None:
            kept = gemma.store_work(VOICE_MEMORY, text)
            if kept:
                print("assistant: work kept", file=sys.stderr, flush=True)
        return cont
    print("assistant: seat " + sig.kind, file=sys.stderr, flush=True)
    return True


def play_signals(py, args, path, signals, hold):
    pending = list(signals)
    try:
        found = turn_place(args)
        while pending:
            cont = act_signal(py, args, found, pending[0], hold)
            pending = pending[1:]
            if not cont:
                seat.give_back(path, pending)
                return False
    except SystemExit:
        seat.give_back(path, pending)
        raise
    return True


def hot_now(hot_until):
    return bool(hot_until) and time.monotonic() < float(hot_until)


def peek_signals(path):
    path = Path(path)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    return seat.parse_signals(raw)


def drain_file(py, args, path, hold, hot_until=0.0):
    if hot_now(hot_until):
        peeked = peek_signals(path)
        if peeked and all(item.kind in DEFER_KINDS for item in peeked):
            print("assistant: seat deferred", file=sys.stderr, flush=True)
            return True
    signals = seat.claim(path)
    if not signals:
        return True
    if hot_now(hot_until):
        later = [item for item in signals if item.kind in DEFER_KINDS]
        signals = [item for item in signals if item.kind not in DEFER_KINDS]
        if later:
            seat.give_back(path, later)
            print("assistant: seat deferred", file=sys.stderr, flush=True)
        if not signals:
            return True
    print("assistant: seat " + str(path), file=sys.stderr, flush=True)
    return play_signals(py, args, path, signals, hold)


def drain_seat(py, args, hold, hot_until=0.0):
    seen = set()
    paths = (seat.seat_path(None), seat.outbox_path(None), seat.status_path(None))
    for path in paths:
        try:
            key = str(Path(path).resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if not drain_file(py, args, path, hold, hot_until):
            return False
    return True


def inject_loop(py, args, path):
    own_turn()
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    if not drain_seat(py, args, False):
        return
    for text in iter_inject_turns(path):
        if not turn_after_transcript(py, args, text, None, False):
            return
        if not drain_seat(py, args, False):
            return


def consume_iris_outbox(py, args, path_arg):
    path = seat.outbox_path(path_arg)
    if not path.is_file():
        print("assistant: iris outbox missing", file=sys.stderr, flush=True)
        raise SystemExit(0)
    signals = seat.claim(path)
    if not signals:
        print("assistant: iris outbox empty", file=sys.stderr, flush=True)
        raise SystemExit(0)
    print("assistant: iris outbox consumed " + str(path), file=sys.stderr, flush=True)
    own_turn()
    if not play_signals(py, args, path, signals, False):
        return


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


def speak_raw(args, raw, flip=False):
    import mouth

    spoken = speakable(raw)
    parts = chunks_for_mouth(spoken, fast_model(args.model)) if spoken else []
    if not parts:
        print("assistant: no speakable answer", file=sys.stderr)
        return
    release_shared_gpu(flip)
    print("assistant: mouth " + str(len(parts)) + " chunk(s)", file=sys.stderr)
    note_mouth()
    print("mouth out: default", file=sys.stderr, flush=True)
    mouth.speak_pieces(parts, mouth.play_wav)


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


def speak_live(args, raw, flip=False):
    set_hold(True)
    try:
        speak_raw(args, raw, flip)
    finally:
        set_hold(False)


def live_clip(py, args, state):
    # Hold before the utterance file is removed. That file is what mutes capture.
    set_hold(True)
    try:
        wav = take_utterance()
        kind, ms = judge_wav(wav)
        log_vad(kind, ms)
        if kind != "keep":
            return not args.once
        words, code = words_from_wav(py, wav, True)
        if not words:
            return not args.once
        state["hot_until"] = time.monotonic() + HOT_S
        return turn_after_transcript(py, args, words, code, True)
    finally:
        set_hold(False)


def listen_loop(py, args):
    mic = hear.wasapi_capture_name()
    start_vad(mic)
    (ROOT / "assistant.pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    state = {"hot_until": 0.0}
    while True:
        if VAD_PROC is None or VAD_PROC.poll() is not None:
            die("vad exited")
        if (ROOT / "vad.utterance.txt").is_file():
            if not live_clip(py, args, state):
                return
            continue
        if not drain_seat(py, args, True, state["hot_until"]):
            return
        time.sleep(0.05)


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
        help="closed-mic loop: PATH is one turn per non-empty line, or blocks split by a --- line; flag alone reads stdin until EOF. No live mic",
    )
    parser.add_argument(
        "--iris-outbox",
        nargs="?",
        const="-",
        metavar="PATH",
        default=None,
        help="claim PE iris_outbox or TRIDENT_IRIS_OUTBOX and act. say speaks. status and work ask the brain. stop voice ends this PC voice and leaves the brain up. A failed act puts the signal back. No audio from the brain",
    )
    parser.add_argument("--wav", default=None, help="transcribe this wav through hear.py; skip the mic; one turn")
    parser.add_argument("--stop", action="store_true", help="stop the assistant tree on this PC")
    parser.add_argument("--brain", default="qwen", choices=BRAINS, help="CPU row only; a CUDA device uses Gemma")
    parser.add_argument(
        "--model",
        default="nano",
        choices=MODELS,
        help="fast English mouth: turbo, otherwise nano. Other languages use v3",
    )
    parser.add_argument("--image", default=None, help="image file forwarded on the turn")
    parser.add_argument(
        "--nvidia",
        action="store_true",
        help="use this peer when its port accepts; a closed port uses this PC. Does not bind 8765",
    )
    parser.add_argument("--url", default=None, help="peer POST URL; requires --nvidia. Else TRIDENT_NVIDIA_URL")
    parser.add_argument("--timeout", type=float, default=180, help="brain HTTP timeout seconds (default 180)")
    args = parser.parse_args()

    if args.stop:
        if (
            args.text is not None
            or args.wav is not None
            or args.image
            or args.nvidia
            or args.url
            or args.brain != "qwen"
        ):
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
    if args.url and not args.nvidia:
        die("url asks for --nvidia")
    if args.timeout <= 0:
        die("timeout must be > 0")

    py = venv_python()
    if args.iris_outbox is not None:
        path = None if args.iris_outbox == "-" else str(args.iris_outbox).strip()
        consume_iris_outbox(py, args, path)
        return
    if args.inject is not None:
        path = None if args.inject == "-" else str(args.inject).strip()
        inject_loop(py, args, path)
        return
    if args.text is not None:
        injected_turn(py, args, args.text.strip())
        return
    if args.wav is not None:
        wav_path = Path(args.wav.strip())
        kind, ms = judge_wav(wav_path)
        log_vad(kind, ms)
        if kind != "keep":
            return
        words, code = words_from_wav(py, wav_path, False)
        if not words:
            return
        injected_turn(py, args, words)
        return
    place_turn(args)
    own_turn()
    listen_loop(py, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("assistant: stopped", file=sys.stderr)
        raise SystemExit(0)
    finally:
        release_vad()
