"""Gemma brain. Text question, or question + image file → stdout generation (thinking included).

The default keeps one gemma-brain.exe for this checkout. --once is the old single card run.
--stop unloads it. --stream writes each sampled piece as it arrives.

Text turns replay gemma.memory.txt and declare remember, devices, and cursor. Past model
turns are the speakable text only. The current turn starts at <|turn>model with thinking
left off. A Gemma 4 <|tool_call> is run here, then the brain is asked once more with the
tool result. The speakable answer is appended to that file. remember adds one fact.
devices reports the CUDA and Vulkan adapters on this computer. Trim drops the oldest
turns first. A fact drops only after every turn is gone and the prompt still does not fit.

cursor launches the Cursor CLI once and writes grok_bot_spawn.txt. If the CLI is missing
the tool writes BLOCKED and does not start a follow-up turn.

A question that starts with <<trident-inbox>> is one stateless inbox turn: the marker
is removed, tools are not declared, the thought channel stays open, and gemma.memory.txt
is not read or written. Image turns skip tools and memory replay, then append the reply.
"""

import argparse
import base64
import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_KEYS = ("gemma.text", "gemma.image")
SIDECAR = ROOT / "gemma_run.txt"
MEMORY_PATH = ROOT / "gemma.memory.txt"
LAST_PROMPT = ROOT / "gemma.lastprompt.txt"
SPAWN_PATH = ROOT / "grok_bot_spawn.txt"
MEDIA = "<__media__>"
Q = '<|"|>'
INBOX_MARK = "<<trident-inbox>>"
PROMPT_CHARS = 80_000
TURN_WORDS = 200
FACT_CHARS = 200
SYSTEM = (
    "You are Jarvis, the voice of Trident. Wojciech is the owner. "
    "Speak one or two short sentences in the owner's language. "
    "The user and model turns after this are what was already said. Use them. "
    "remember stores one fact that stays after old turns are dropped. "
    "devices reports the CUDA and Vulkan adapters on this computer. "
    "Call a tool only by its tool call. The spoken sentence has no channels or file names."
)
REMEMBER_DECL = (
    "<|tool>declaration:remember{description:"
    + Q
    + "Store one fact that stays after old conversation turns are dropped. Call this when the owner states something that must be remembered."
    + Q
    + ",parameters:{properties:{line:{description:"
    + Q
    + "The fact, one short line."
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
DEVICES_DECL = (
    "<|tool>declaration:devices{description:"
    + Q
    + "Report the CUDA device and the Vulkan device on this computer, and whether they are the same adapter. Call this when asked which GPU is here or whether speaking would share the brain GPU."
    + Q
    + ",parameters:{properties:{},required:[],type:"
    + Q
    + "OBJECT"
    + Q
    + "}}<tool|>"
)
CURSOR_DECL = (
    "<|tool>declaration:cursor{description:"
    + Q
    + "Launch the Cursor CLI once and write grok_bot_spawn.txt. Call this when the user asks to run Cursor or the cursor tool."
    + Q
    + ",parameters:{properties:{job:{description:"
    + Q
    + "Short job label. Use extensions to list installed Cursor extensions."
    + Q
    + ",type:"
    + Q
    + "STRING"
    + Q
    + "}},required:["
    + Q
    + "job"
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
    print(message, file=sys.stderr, flush=True)
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


def tool_header(facts):
    body = SYSTEM
    if facts:
        body += "\nRemembered:\n" + "\n".join(facts)
    return "<|turn>system\n" + body + REMEMBER_DECL + DEVICES_DECL + CURSOR_DECL + "<turn|>\n"


def prepare_question(question):
    text = question.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith(INBOX_MARK):
        rest = text[len(INBOX_MARK):]
        if rest.startswith("\n"):
            rest = rest[1:]
        return True, rest
    return False, question


def gemma_prompt(question, with_image, reason=False):
    # Image turns and inbox turns. Text memory turns use memory_prompt.
    user = user_body(question, with_image)
    tail = "<|channel>thought\n"
    if not reason:
        tail += "<channel|>\n"
    return (
        "<bos>"
        + "<|turn>user\n"
        + user
        + "<turn|>\n"
        "<|turn>model\n"
        + tail
    )


def memory_prompt(facts, pairs, question, suffix):
    parts = ["<bos>", tool_header(facts)]
    for user, model in pairs:
        if not user and not model:
            continue
        parts.append("<|turn>user\n" + user + "<turn|>\n")
        if model:
            parts.append("<|turn>model\n" + model + "<turn|>\n")
    parts.append("<|turn>user\n" + question.strip("\r\n") + "<turn|>\n")
    parts.append("<|turn>model\n" + suffix)
    return "".join(parts)


def tool_response(name, fields):
    parts = [key + ":" + Q + value + Q for key, value in fields]
    return "<|tool_response>response:" + name + "{" + ",".join(parts) + "}<tool_response|>"


def parse_tool_call(text):
    match = CALL_RE.search(text)
    if not match:
        return None
    args = {}
    for key, quoted, bare in ARG_RE.findall(match.group(2)):
        value = quoted if quoted else bare
        args[key] = value.strip()
    return match.group(1), args, match.group(0)


def plain(raw):
    text = " ".join((raw or "").replace(Q, "'").split())
    return text


def clip_words(raw, limit):
    words = plain(raw).split()
    if len(words) > limit:
        words = words[:limit]
    return " ".join(words)


def clip_fact(raw):
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    text = plain(text)
    if len(text) > FACT_CHARS:
        text = text[:FACT_CHARS].rstrip()
    return text


def parse_store(raw):
    facts = []
    pairs = []
    pending = None
    lines = physical_lines(raw)
    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip(" \t")
        if stripped.endswith("<<") and stripped[:-2].strip() in ("fact", "user", "model"):
            key = stripped[:-2].strip()
            index += 1
            buf = []
            while index < len(lines) and lines[index] != "<<":
                buf.append(lines[index])
                index += 1
            if index < len(lines) and lines[index] == "<<":
                index += 1
            body = "\n".join(buf).strip()
            if key == "fact":
                if body:
                    facts.append(body)
            elif key == "user":
                if pending is not None:
                    pairs.append((pending, ""))
                pending = body
            else:
                if pending is None:
                    pending = ""
                pairs.append((pending, body))
                pending = None
            continue
        index += 1
    if pending is not None:
        pairs.append((pending, ""))
    return facts, pairs


def render_store(facts, pairs):
    parts = []
    for fact in facts:
        parts.append("fact <<\n" + fact + "\n<<\n")
    for user, model in pairs:
        parts.append("user <<\n" + user + "\n<<\n")
        parts.append("model <<\n" + model + "\n<<\n")
    return "".join(parts)


def read_memory(path):
    if not path.is_file():
        return [], []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        die("cannot read " + path.name + ": " + str(exc))
    return parse_store(raw)


def write_memory(path, facts, pairs):
    body = render_store(facts, pairs)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(body.encode("utf-8"))
        tmp.replace(path)
    except OSError as exc:
        die("cannot write " + path.name + ": " + str(exc))


def fit_memory(question, suffix, path=None, limit=None):
    path = MEMORY_PATH if path is None else path
    limit = PROMPT_CHARS if limit is None else limit
    facts, pairs = read_memory(path)
    kept = list(pairs)
    prompt = memory_prompt(facts, kept, question, suffix)
    dropped = False
    while len(prompt) > limit and kept:
        kept = kept[1:]
        dropped = True
        prompt = memory_prompt(facts, kept, question, suffix)
    while len(prompt) > limit and facts:
        facts = facts[1:]
        dropped = True
        prompt = memory_prompt(facts, kept, question, suffix)
    if len(prompt) > limit:
        die("prompt too long")
    if dropped:
        write_memory(path, facts, kept)
    return prompt, facts, kept


def store_fact(path, line):
    text = clip_fact(line)
    if not text:
        print("gemma: tool remember empty", file=sys.stderr, flush=True)
        return ""
    facts, pairs = read_memory(path)
    if text in facts:
        print("gemma: tool remember kept " + path.name + " (" + text + ")", file=sys.stderr, flush=True)
        return text
    facts.append(text)
    write_memory(path, facts, pairs)
    print("gemma: tool remember wrote " + path.name + " (" + text + ")", file=sys.stderr, flush=True)
    return text


def append_memory(path, question, reply):
    user = clip_words(question, TURN_WORDS)
    model = clip_words(reply, TURN_WORDS)
    if not user or not model:
        return
    facts, pairs = read_memory(path)
    pairs.append((user, model))
    write_memory(path, facts, pairs)


def clean_job(raw):
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    text = " ".join(text.split())
    text = text.replace(Q, "'")
    if len(text) > 200:
        text = text[:200].rstrip()
    if not text:
        text = "extensions"
    return text


def log_block(title, body):
    text = body if body.endswith("\n") or body == "" else body + "\n"
    return title + " <<\n" + text + "<<\n"


def write_spawn(body):
    try:
        SPAWN_PATH.write_text(body, encoding="utf-8")
    except OSError as exc:
        die("cannot write grok_bot_spawn.txt: " + str(exc))


def find_cursor():
    found = shutil.which("cursor")
    if not found:
        return None
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".exe", ".bat"):
        return str(path)
    cmd = Path(str(path) + ".cmd")
    if cmd.is_file():
        return str(cmd)
    if path.is_file():
        return str(path)
    return None


def cursor_argv(cursor, job):
    # cursor 3.22.7 lists `agent` in help, but this build's cli.js does not
    # dispatch it and passes unknown flags to Electron. One exiting CLI job:
    # list extensions, or --version when the job asks for the version.
    folded = job.lower()
    if "version" in folded and "extension" not in folded:
        return [cursor, "--version"]
    return [cursor, "--list-extensions", "--show-versions"]


def clip_lines(text, limit):
    lines = (text or "").splitlines()
    if len(lines) > limit:
        lines = lines[-limit:]
    return "\n".join(lines)


def run_cursor_job(job):
    label = clean_job(job)
    cursor = find_cursor()
    if not cursor:
        write_spawn("BLOCKED\ncursor cli missing\n")
        print("gemma: tool cursor BLOCKED", file=sys.stderr, flush=True)
        return "BLOCKED"
    argv = cursor_argv(cursor, label)
    command = subprocess.list2cmdline(argv)
    print("gemma: tool cursor " + command, file=sys.stderr, flush=True)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        body = "job " + label + "\n"
        body += log_block("command", command + "\n")
        body += "fail cannot start: " + str(exc) + "\n"
        write_spawn(body)
        print("gemma: tool cursor fail", file=sys.stderr, flush=True)
        return "fail"
    pid = proc.pid
    try:
        out_b, err_b = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        body = "job " + label + "\n"
        body += log_block("command", command + "\n")
        body += "pid " + str(pid) + "\n"
        body += "fail timed out\n"
        write_spawn(body)
        print("gemma: tool cursor timed out pid " + str(pid), file=sys.stderr, flush=True)
        return "fail timed out"
    code = proc.returncode
    if code is None:
        code = 1
    out = (out_b or b"").decode("utf-8", errors="replace")
    err = (err_b or b"").decode("utf-8", errors="replace")
    body = "job " + label + "\n"
    body += log_block("command", command + "\n")
    body += "pid " + str(pid) + "\n"
    body += "exit " + str(code) + "\n"
    shown = clip_lines(out, 40)
    body += log_block("stdout", (shown + "\n") if shown else "")
    if code != 0 and err.strip():
        body += log_block("stderr", clip_lines(err.strip(), 20) + "\n")
    write_spawn(body)
    print(
        "gemma: tool cursor pid " + str(pid) + " exit " + str(code),
        file=sys.stderr,
        flush=True,
    )
    return "pid " + str(pid) + " exit " + str(code)


def strip_tool_markup(text):
    return TOOL_MARKUP_RE.sub("", text)


def answer_text(text):
    cleaned = strip_tool_markup(text).replace("\r\n", "\n").replace("\r", "\n")
    cut = cleaned.find("<|tool_call>")
    if cut >= 0:
        cleaned = cleaned[:cut]
    if "<channel|>" in cleaned:
        cleaned = cleaned.split("<channel|>")[-1]
    for token in ("<|channel>thought", "<|channel>", "<turn|>", "<|turn>", "<bos>", "<eos>", "<|think|>", "`"):
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


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
ASK_TIMEOUT = 600
PROMPT_CAP = 16_000_000
SLOTS = (
    "gemma.pid",
    "gemma.pid.tmp",
    "gemma.stop",
    "gemma.prompt.txt",
    "gemma.response.txt",
    "gemma.prompt.txt.tmp",
    "gemma.response.txt.tmp",
    "gemma.busy",
)
LOCK_NAME = "gemma.lock"
Resident = namedtuple("Resident", ("pid", "fingerprint", "state"))

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_int
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_wchar_p,
    ctypes.POINTER(ctypes.c_uint32),
]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
_kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_kernel32.TerminateProcess.restype = ctypes.c_int


def remove_file(name):
    path = ROOT / name
    for _ in range(50):
        try:
            if path.is_file():
                path.unlink()
            return
        except OSError:
            time.sleep(0.05)
    if path.is_file():
        die("cannot remove " + name)


def hex_fingerprint(text):
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def resident_record():
    path = ROOT / "gemma.pid"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) not in (1, 3):
        return None
    try:
        pid = int(lines[0].strip())
    except ValueError:
        return None
    if pid <= 0:
        return None
    if len(lines) == 1:
        return Resident(pid, "", "")
    fingerprint = lines[1].strip()
    state = lines[2].strip()
    if not hex_fingerprint(fingerprint) or state not in ("loading", "ready"):
        return None
    return Resident(pid, fingerprint, state)


def process_image(pid):
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_uint32(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        return buf.value
    finally:
        _kernel32.CloseHandle(handle)


def brain_running(pid):
    image = process_image(pid)
    return bool(image) and Path(image).name.lower() == "gemma-brain.exe"


def brain_running_any():
    rec = resident_record()
    return rec is not None and brain_running(rec.pid)


def terminate_pid(pid):
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, 0, pid)
    if not handle:
        return
    try:
        _kernel32.TerminateProcess(handle, 1)
    finally:
        _kernel32.CloseHandle(handle)


def wait_dead(pid, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not brain_running(pid):
            return True
        time.sleep(0.05)
    return not brain_running(pid)


def log_tail():
    path = ROOT / "gemma.run.err"
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(data) > 2000:
        data = data[-2000:]
    return data


def cuda_log_line():
    path = ROOT / "gemma.run.err"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = ""
    for line in text.splitlines():
        if line.startswith("cuda[") or line.startswith("vulkan["):
            found = line
    return found


def announce(pid):
    print("gemma: resident pid " + str(pid), file=sys.stderr, flush=True)
    line = cuda_log_line()
    if line:
        print("gemma: " + line, file=sys.stderr, flush=True)


def acquire_lock():
    path = ROOT / LOCK_NAME
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        return None
    except OSError as exc:
        die("cannot create gemma.lock: " + str(exc))
    try:
        os.write(fd, (str(os.getpid()) + "\n" + str(time.time()) + "\n").encode("ascii"))
    except OSError as exc:
        os.close(fd)
        die("cannot write gemma.lock: " + str(exc))
    return fd


def release_lock(fd):
    if fd is None:
        return
    os.close(fd)
    try:
        (ROOT / LOCK_NAME).unlink()
    except OSError:
        pass


def lock_owner():
    path = ROOT / LOCK_NAME
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    try:
        return int(lines[0].strip())
    except ValueError:
        return None


def lock_busy():
    if not (ROOT / LOCK_NAME).is_file():
        return False
    owner = lock_owner()
    if owner is None:
        return True
    return bool(process_image(owner))


def clear_stale_lock():
    if lock_busy():
        return
    try:
        if (ROOT / LOCK_NAME).is_file():
            (ROOT / LOCK_NAME).unlink()
    except OSError:
        pass


def stop_resident():
    fd = None
    deadline = time.time() + 20
    while time.time() < deadline:
        fd = acquire_lock()
        if fd is not None:
            break
        clear_stale_lock()
        time.sleep(0.05)
    if fd is None:
        die("gemma busy")
    try:
        try:
            (ROOT / "gemma.stop").write_bytes(b"stop\n")
        except OSError as exc:
            die("cannot write gemma.stop: " + str(exc))
        stopped = False
        rec = resident_record()
        if rec is not None and brain_running(rec.pid):
            stopped = True
            if rec.state == "loading":
                terminate_pid(rec.pid)
                if not wait_dead(rec.pid, 5):
                    die("cannot stop gemma pid " + str(rec.pid))
            elif not wait_dead(rec.pid, 120):
                terminate_pid(rec.pid)
                if not wait_dead(rec.pid, 5):
                    die("cannot stop gemma pid " + str(rec.pid))
        for name in SLOTS:
            remove_file(name)
        return stopped
    finally:
        release_lock(fd)


def publish_pid(pid, fingerprint, state):
    body = str(pid) + "\n" + fingerprint + "\n" + state + "\n"
    tmp = ROOT / "gemma.pid.tmp"
    try:
        tmp.write_bytes(body.encode("utf-8"))
        tmp.replace(ROOT / "gemma.pid")
    except OSError as exc:
        die("cannot write gemma.pid: " + str(exc))


def note_spawn(proc, fingerprint):
    rec = resident_record()
    if rec is not None and rec.pid == proc.pid:
        if rec.state == "ready" and rec.fingerprint == fingerprint:
            return
        if rec.fingerprint == "" and rec.state == "":
            publish_pid(proc.pid, fingerprint, "ready")
            return
    publish_pid(proc.pid, fingerprint, "loading")


def launch_resident(fingerprint):
    if (ROOT / "gemma.stop").is_file():
        return None
    exe = ROOT / "gemma-brain.exe"
    if not exe.is_file():
        die("missing gemma-brain.exe")
    log_path = ROOT / "gemma.run.err"
    try:
        log = open(log_path, "wb", buffering=0)
    except OSError as exc:
        die("cannot write gemma.run.err: " + str(exc))
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        try:
            proc = subprocess.Popen(
                [str(exe), "--resident", "gemma_run.txt"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags | breakaway,
                close_fds=True,
            )
        except OSError:
            proc = subprocess.Popen(
                [str(exe), "--resident", "gemma_run.txt"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
    except OSError as exc:
        log.close()
        die("cannot run gemma-brain.exe: " + str(exc))
    log.close()
    try:
        note_spawn(proc, fingerprint)
    except SystemExit:
        if proc.poll() is None:
            terminate_pid(proc.pid)
        raise
    if (ROOT / "gemma.stop").is_file() or proc.poll() is not None:
        if proc.poll() is None:
            terminate_pid(proc.pid)
            wait_dead(proc.pid, 5)
        remove_file("gemma.pid")
        if proc.poll() is not None and not (ROOT / "gemma.stop").is_file():
            die("gemma exited " + str(proc.returncode) + "\n" + log_tail())
        return None
    print("gemma: starting", file=sys.stderr, flush=True)
    return proc


def fail_loader(pid, message):
    if brain_running(pid):
        terminate_pid(pid)
        if not wait_dead(pid, 5):
            die(message + "\nloader pid " + str(pid) + " did not exit")
    remove_file("gemma.pid")
    die(message)


def wait_until_ready(pid, fingerprint, proc=None):
    deadline = time.time() + ASK_TIMEOUT
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            die("gemma exited " + str(proc.returncode) + "\n" + log_tail())
        if not brain_running(pid):
            die("gemma exited\n" + log_tail())
        rec = resident_record()
        if rec is not None and rec.pid == pid:
            if rec.state == "ready" and rec.fingerprint == fingerprint:
                announce(pid)
                return pid
            if rec.fingerprint == fingerprint and rec.state == "loading":
                pass
            elif rec.fingerprint == "" and rec.state == "":
                publish_pid(pid, fingerprint, "ready")
                if not brain_running(pid):
                    die("gemma exited\n" + log_tail())
                announce(pid)
                return pid
        time.sleep(0.05)
    fail_loader(pid, "gemma did not become ready\n" + log_tail())


def wait_for_fingerprint(pid, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        rec = resident_record()
        if rec is None or rec.pid != pid or not brain_running(pid):
            return None
        if rec.fingerprint:
            return rec
        time.sleep(0.02)
    rec = resident_record()
    if rec is not None and rec.pid == pid and rec.fingerprint and brain_running(pid):
        return rec
    return None


def same_settings(rec, fingerprint):
    return (
        rec is not None
        and brain_running(rec.pid)
        and rec.fingerprint == fingerprint
        and rec.state in ("loading", "ready")
    )


def resident_settings():
    source = ROOT / "gemma.txt"
    if not source.is_file():
        die("missing gemma.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body and not body.endswith("\n"):
        body += "\n"
    body += "gemma.text <<\n<<\ngemma.image <<\n<<\n"
    return body


def write_sidecar(payload):
    try:
        SIDECAR.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write gemma_run.txt: " + str(exc))


def ensure_resident():
    desired = resident_settings()
    fingerprint = hashlib.sha256(desired.encode("utf-8")).hexdigest()
    deadline = time.time() + ASK_TIMEOUT
    while time.time() < deadline:
        rec = resident_record()
        if rec is not None and brain_running(rec.pid) and not rec.fingerprint:
            rec = wait_for_fingerprint(rec.pid, 2) or rec
        if same_settings(rec, fingerprint):
            if rec.state == "ready":
                announce(rec.pid)
                return rec.pid
            return wait_until_ready(rec.pid, fingerprint)
        fd = acquire_lock()
        if fd is None:
            clear_stale_lock()
            time.sleep(0.05)
            continue
        proc = None
        try:
            rec = resident_record()
            if same_settings(rec, fingerprint):
                if rec.state == "ready":
                    announce(rec.pid)
                    return rec.pid
                held = rec.pid
                release_lock(fd)
                fd = None
                return wait_until_ready(held, fingerprint)
            if rec is not None and brain_running(rec.pid):
                print("gemma: settings changed", file=sys.stderr, flush=True)
                release_lock(fd)
                fd = None
                stop_resident()
                continue
            stop_path = ROOT / "gemma.stop"
            if stop_path.is_file():
                if brain_running_any():
                    die("gemma stop requested")
                remove_file("gemma.stop")
            write_sidecar(desired)
            for name in (
                "gemma.prompt.txt",
                "gemma.response.txt",
                "gemma.prompt.txt.tmp",
                "gemma.response.txt.tmp",
                "gemma.busy",
            ):
                remove_file(name)
            proc = launch_resident(fingerprint)
            if proc is None:
                die("gemma stop requested")
        finally:
            release_lock(fd)
        return wait_until_ready(proc.pid, fingerprint, proc)
    die("gemma did not become ready\n" + log_tail())


class FrameParser:
    def __init__(self, ident, on_piece=None):
        self.ident = ident
        self.on_piece = on_piece
        self.buf = b""
        self.pos = 0
        self.mode = "id"
        self.need = 0
        self.blob = bytearray()
        self.done = ""
        self.error = ""
        self.stale = False

    def text(self):
        return self.blob.decode("utf-8", errors="replace")

    def _reset(self):
        self.pos = 0
        self.mode = "id"
        self.need = 0
        self.blob = bytearray()
        self.stale = False

    def feed(self, data):
        if self.done or self.error:
            return
        if self.stale or (self.pos and not data.startswith(self.buf[: self.pos])):
            if data == self.buf and self.stale:
                return
            self._reset()
        self.buf = data
        self._pump()

    def _pump(self):
        while not self.done and not self.error and not self.stale:
            if self.mode == "body":
                if len(self.buf) - self.pos < self.need:
                    return
                piece = self.buf[self.pos : self.pos + self.need]
                self.pos += self.need
                self.blob += piece
                self.mode = "tag"
                if self.on_piece is not None:
                    self.on_piece(bytes(piece))
                continue
            nl = self.buf.find(b"\n", self.pos)
            if nl < 0:
                return
            line = self.buf[self.pos : nl].decode("utf-8", errors="replace").rstrip("\r")
            self.pos = nl + 1
            if self.mode == "id":
                if line.strip() != self.ident:
                    self.stale = True
                    return
                self.mode = "tag"
                continue
            if line == "ok":
                self.done = "ok"
                return
            if line.startswith("err"):
                self.error = line[3:].strip() or "failed"
                return
            if len(line) > 1 and line[0] == "." and line[1:].isdigit():
                self.need = int(line[1:])
                if self.need > 65536:
                    self.error = "bad piece"
                    return
                self.mode = "body"
                continue
            self.error = "bad response"
            return


def prompt_payload(ident, image_b64, text):
    image = image_b64.encode("ascii") if image_b64 else b""
    payload = (ident + "\n" + str(len(image)) + "\n").encode("ascii") + image + text.encode("utf-8")
    if len(payload) > PROMPT_CAP:
        die("prompt too long")
    return payload


def resident_ask(pid, prompt, image_b64, on_piece, timeout):
    deadline = time.time() + timeout
    ident = str(time.time_ns())
    submitted = False
    while time.time() < deadline and not submitted:
        if (ROOT / "gemma.busy").is_file() or (ROOT / "gemma.prompt.txt").is_file():
            if not brain_running(pid):
                remove_file("gemma.busy")
            else:
                time.sleep(0.02)
                continue
        fd = acquire_lock()
        if fd is None:
            clear_stale_lock()
            time.sleep(0.05)
            continue
        try:
            if (ROOT / "gemma.busy").is_file() or (ROOT / "gemma.prompt.txt").is_file():
                continue
            response = ROOT / "gemma.response.txt"
            if response.is_file():
                try:
                    response.unlink()
                except OSError as exc:
                    die("cannot remove gemma.response.txt: " + str(exc))
            tmp = ROOT / "gemma.prompt.txt.tmp"
            try:
                tmp.write_bytes(prompt_payload(ident, image_b64, prompt))
                tmp.replace(ROOT / "gemma.prompt.txt")
            except OSError as exc:
                die("cannot write gemma.prompt.txt: " + str(exc))
            submitted = True
        finally:
            release_lock(fd)
    if not submitted:
        die("gemma busy")
    parser = FrameParser(ident, on_piece)
    while time.time() < deadline:
        path = ROOT / "gemma.response.txt"
        if path.is_file():
            try:
                data = path.read_bytes()
            except OSError:
                time.sleep(0.02)
                continue
            parser.feed(data)
            if parser.done == "ok":
                return parser.text()
            if parser.error:
                die("gemma: " + parser.error)
        if not brain_running(pid):
            if path.is_file():
                try:
                    parser.feed(path.read_bytes())
                except OSError:
                    pass
                if parser.done == "ok":
                    return parser.text()
                if parser.error:
                    die("gemma: " + parser.error)
            die("gemma exited\n" + log_tail())
        time.sleep(0.02)
    die("gemma timed out")


def resident_generate(prompt, image_b64, stream, timeout=ASK_TIMEOUT):
    pid = ensure_resident()

    def on_piece(piece):
        if stream:
            sys.stdout.buffer.write(piece)
            sys.stdout.buffer.flush()

    return resident_ask(pid, prompt, image_b64, on_piece, timeout)


def _norm_device(name):
    return " ".join((name or "").casefold().split())


def cuda_device_name():
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[0]


def vulkan_device_name():
    try:
        vk = ctypes.WinDLL("vulkan-1")
    except OSError:
        return ""
    app_type = 0
    inst_type = 1
    api = (1 << 22) | (2 << 12)

    class VkApplicationInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_uint32),
            ("pNext", ctypes.c_void_p),
            ("pApplicationName", ctypes.c_char_p),
            ("applicationVersion", ctypes.c_uint32),
            ("pEngineName", ctypes.c_char_p),
            ("engineVersion", ctypes.c_uint32),
            ("apiVersion", ctypes.c_uint32),
        ]

    class VkInstanceCreateInfo(ctypes.Structure):
        _fields_ = [
            ("sType", ctypes.c_uint32),
            ("pNext", ctypes.c_void_p),
            ("flags", ctypes.c_uint32),
            ("pApplicationInfo", ctypes.POINTER(VkApplicationInfo)),
            ("enabledLayerCount", ctypes.c_uint32),
            ("ppEnabledLayerNames", ctypes.c_void_p),
            ("enabledExtensionCount", ctypes.c_uint32),
            ("ppEnabledExtensionNames", ctypes.c_void_p),
        ]

    try:
        vk.vkCreateInstance.argtypes = [
            ctypes.POINTER(VkInstanceCreateInfo),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        vk.vkCreateInstance.restype = ctypes.c_int
        vk.vkEnumeratePhysicalDevices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        vk.vkEnumeratePhysicalDevices.restype = ctypes.c_int
        vk.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        vk.vkGetPhysicalDeviceProperties.restype = None
        vk.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        vk.vkDestroyInstance.restype = None
        app = VkApplicationInfo()
        app.sType = app_type
        app.apiVersion = api
        info = VkInstanceCreateInfo()
        info.sType = inst_type
        info.pApplicationInfo = ctypes.pointer(app)
        inst = ctypes.c_void_p()
        if vk.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(inst)) != 0 or not inst.value:
            return ""
        try:
            count = ctypes.c_uint32(0)
            if vk.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), None) != 0 or count.value < 1:
                return ""
            arr = (ctypes.c_void_p * count.value)()
            if vk.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), ctypes.cast(arr, ctypes.c_void_p)) != 0:
                return ""
            props = (ctypes.c_ubyte * 4096)()
            vk.vkGetPhysicalDeviceProperties(arr[0], ctypes.cast(props, ctypes.c_void_p))
            raw = bytes(props[20:276]).split(b"\x00", 1)[0]
            return raw.decode("utf-8", errors="replace").strip()
        finally:
            vk.vkDestroyInstance(inst, None)
    except (OSError, AttributeError):
        return ""


def adapter_names():
    return cuda_device_name(), vulkan_device_name()


def same_adapter():
    cuda_name, vulkan_name = adapter_names()
    if not cuda_name or not vulkan_name:
        return False
    return _norm_device(cuda_name) == _norm_device(vulkan_name)


def run_devices():
    cuda_name, vulkan_name = adapter_names()
    cuda_name = cuda_name or "none"
    vulkan_name = vulkan_name or "none"
    if cuda_name != "none" and vulkan_name != "none" and _norm_device(cuda_name) == _norm_device(vulkan_name):
        adapter = "same"
    elif cuda_name == "none" and vulkan_name == "none":
        adapter = "unknown"
    else:
        adapter = "different"
    print(
        "gemma: tool devices cuda " + cuda_name + " vulkan " + vulkan_name + " " + adapter,
        file=sys.stderr,
        flush=True,
    )
    return cuda_name, vulkan_name, adapter


def note_prompt(prompt, facts, pairs):
    print(
        "gemma: memory facts " + str(len(facts)) + " pairs " + str(len(pairs)),
        file=sys.stderr,
        flush=True,
    )
    try:
        LAST_PROMPT.write_text(prompt, encoding="utf-8")
    except OSError as exc:
        print("gemma: cannot write gemma.lastprompt.txt: " + str(exc), file=sys.stderr, flush=True)


def tool_turn(name, args, raw):
    if name == "remember":
        line = store_fact(MEMORY_PATH, args.get("line")) or "empty"
        return raw + tool_response("remember", [("line", line)]), None, line
    if name == "devices":
        cuda_name, vulkan_name, adapter = run_devices()
        suffix = raw + tool_response(
            "devices",
            [("adapter", adapter), ("cuda", cuda_name), ("vulkan", vulkan_name)],
        )
        spoken = "cuda " + cuda_name + "; vulkan " + vulkan_name + "; " + adapter
        return suffix, None, spoken
    if name == "cursor":
        job = clean_job(args.get("job"))
        summary = run_cursor_job(job)
        if summary == "BLOCKED":
            print("gemma: tool cursor stopped", file=sys.stderr, flush=True)
            return None, "BLOCKED cursor cli missing\n", None
        line = plain(summary) or "cursor"
        suffix = raw + tool_response(
            "cursor",
            [("path", "grok_bot_spawn.txt"), ("text", line)],
        )
        return suffix, None, "Cursor job logged in grok_bot_spawn.txt."
    print("gemma: tool skip " + name, file=sys.stderr, flush=True)
    return None, None, None


def main():
    parser = argparse.ArgumentParser(prog="gemma.py")
    parser.add_argument("question", nargs="?", default=None, help="text question / analysis prompt")
    parser.add_argument(
        "--image",
        metavar="PATH",
        help="image file (png/jpeg/...); encoded as raw base64 into gemma.image",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show one-shot gemma-brain.exe stderr; resident logs are gemma.run.err",
    )
    parser.add_argument("--once", action="store_true", help="one-shot gemma-brain.exe, then exit")
    parser.add_argument("--stop", action="store_true", help="stop the resident gemma-brain.exe")
    parser.add_argument("--stream", action="store_true", help="write token pieces to stdout as they are sampled")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(ROOT)
    if args.stop:
        if args.once or args.question or args.image or args.verbose or args.stream:
            die("usage: gemma.py --stop")
        if stop_resident():
            print("gemma: stopped", file=sys.stderr, flush=True)
        else:
            print("gemma: not running", file=sys.stderr, flush=True)
        raise SystemExit(0)
    if args.question is None or not args.question.strip():
        die("usage: gemma.py [--once] [--stream] [--verbose] QUESTION")
    if args.stream and args.once:
        die("stream asks for the resident")
    reason, question = prepare_question(args.question)
    if not question.strip():
        die("empty question")
    if reason:
        print("gemma: inbox reasoning on", file=sys.stderr, flush=True)
    image_b64 = image_to_b64(args.image) if args.image else ""

    def run(prompt, image):
        if args.once:
            if brain_running_any():
                die("gemma resident is running; gemma.py --stop first")
            return run_brain(prompt, image, args.verbose)
        return resident_generate(prompt, image, args.stream)

    def emit_fallback(line):
        if args.stream and line:
            sys.stdout.buffer.write(line.encode("utf-8"))
            sys.stdout.buffer.flush()

    use_memory = not image_b64 and not reason
    if use_memory:
        prompt, facts, kept = fit_memory(question, "")
        note_prompt(prompt, facts, kept)
    else:
        prompt = gemma_prompt(question, bool(image_b64), reason)
    text = run(prompt, image_b64)
    if use_memory:
        call = parse_tool_call(text)
        if call:
            suffix, blocked, fallback = tool_turn(call[0], call[1], call[2])
            if blocked:
                text = blocked
                emit_fallback(blocked)
            elif suffix:
                follow, facts, kept = fit_memory(question, suffix)
                note_prompt(follow, facts, kept)
                reply = answer_text(run(follow, ""))
                if not reply:
                    print("gemma: tool follow-up empty", file=sys.stderr, flush=True)
                    reply = fallback or "done"
                    emit_fallback(reply)
                text = reply
        spoken = answer_text(text)
        if spoken:
            append_memory(MEMORY_PATH, question, spoken)
    elif image_b64 and not reason:
        spoken = answer_text(text)
        if spoken:
            append_memory(MEMORY_PATH, question, spoken)
    if not args.stream:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
    raise SystemExit(0)


if __name__ == "__main__":
    main()