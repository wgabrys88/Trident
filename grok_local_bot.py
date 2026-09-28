"""File-backed Grok-bot team. One role per turn.

The coordinator is a thin handoff in the history file. The reasoner is the
NVIDIA Gemma role: one POST, or one nvidia_worker.py --drop --once. The worker
stays stateless. Role and memory stay in grok_bot_history.txt, grok_bot_request.txt,
and grok_bot_response.txt.

--proof runs coordinator then reasoner on that same history and exits 0 when
both roles land and the cursor tool's spawn log records exit 0.

--wav is the Iris voice door into this same team. hear.py transcribes the file.
Coordinator then reasoner append to the history file. The reasoner POSTs to
TRIDENT_NVIDIA_URL or --url. mouth.py --no-play writes the reply wav. The door
checks that the worker port is already open. It does not bind a port and does
not start nvidia_worker.py.

No microphone and no speaker playback. Does not bind a port and does not restart
the existing worker.
"""

import argparse
import io
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import gemma
import nvidia_client

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "grok_bot_history.txt"
REQUEST = ROOT / "grok_bot_request.txt"
RESPONSE = ROOT / "grok_bot_response.txt"
STATUS = ROOT / "grok_bot.txt"
DOOR = ROOT / "iris-door.txt"
CURSOR_JOB = "extensions"
HANDOFF = "next role reasoner\ncursor job: " + CURSOR_JOB + "\n"


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


def block(title, body):
    text = body if body.endswith("\n") or body == "" else body + "\n"
    return title + " <<\n" + text + "<<\n"


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


def append_history(parts):
    prev = ""
    if HISTORY.is_file():
        prev = read_text(HISTORY)
    if prev and not prev.endswith("\n"):
        prev += "\n"
    write_text(HISTORY, prev + "".join(parts))


def team_request(ident, role, text):
    body = "id " + ident + "\nrole " + role + "\ntext <<\n"
    body += text
    if not text.endswith("\n"):
        body += "\n"
    body += "<<\nimage <<\n<<\n"
    return body


def team_response(ident, role, text):
    body = "id " + ident + "\nrole " + role + "\nok\ntext <<\n"
    body += text
    if text and not text.endswith("\n"):
        body += "\n"
    body += "<<\n"
    return body


def team_error(ident, role, reason):
    reason = " ".join(str(reason).split()) or "failed"
    return "id " + ident + "\nrole " + role + "\nerr " + reason + "\n"


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


def parse_worker_response(raw):
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    ident = ""
    if lines and lines[0].startswith("id "):
        ident = lines[0][3:].strip()
        lines = lines[1:]
    if not lines:
        return ident, False, "empty response"
    if lines[0] == "ok":
        text = "\n".join(lines[1:])
        if text and not text.endswith("\n"):
            text += "\n"
        return ident, True, text
    if lines[0].startswith("err"):
        return ident, False, lines[0][3:].strip() or "failed"
    return ident, False, "bad response"


def listeners_8765():
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
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
        return []
    rows = []
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(":8765"):
            continue
        if parts[3].upper() != "LISTENING":
            continue
        rows.append((parts[1], parts[4]))
    return rows


def lan_pid(rows):
    for addr, pid in rows:
        if addr == "0.0.0.0:8765":
            return pid
    return ""


def gpu_line():
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
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
    lines = (completed.stdout or "").strip().splitlines()
    if not lines:
        return ""
    return lines[0].strip()


def gpu_fields(line):
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None


class GpuWatch:
    def __init__(self):
        self.samples = []
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            line = gpu_line()
            if line:
                self.samples.append(line)
            self.stop.wait(0.3)

    def finish(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3)


def gpu_peak(samples):
    util = None
    used = None
    base = None
    for line in samples:
        fields = gpu_fields(line)
        if not fields:
            continue
        if base is None:
            base = fields
        if util is None or fields[0] > util:
            util = fields[0]
        if used is None or fields[1] > used:
            used = fields[1]
    return base, util, used


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


def spawn_kind(raw):
    text = raw or ""
    if not text.strip():
        return "missing"
    if text.startswith("BLOCKED"):
        return "blocked"
    if "fail " in text or text.startswith("fail"):
        return "fail"
    if "pid " in text and "exit " in text:
        return "ran"
    return "missing"


def spawn_exit(raw):
    for line in (raw or "").splitlines():
        if line.startswith("exit "):
            try:
                return int(line[5:].strip())
            except ValueError:
                return None
    return None


def roles_in_order(history):
    found = [
        line
        for line in history.splitlines()
        if line in ("role coordinator", "role reasoner")
    ]
    return len(found) >= 2 and found[0] == "role coordinator" and found[1] == "role reasoner"


def run_coordinator(handoff=None):
    if handoff is None:
        handoff = HANDOFF
    print("grok-bot: role coordinator", file=sys.stderr, flush=True)
    started = time.perf_counter()
    prior = read_text(HISTORY) if HISTORY.is_file() else ""
    prior_n = sum(1 for line in prior.splitlines() if line.startswith("role "))
    ident = str(time.time_ns())
    request_text = "Assign the next team role from the history file.\n" + handoff
    write_text(REQUEST, team_request(ident, "coordinator", request_text))
    write_text(RESPONSE, team_response(ident, "coordinator", handoff))
    ms = int((time.perf_counter() - started) * 1000)
    append_history(
        [
            "role coordinator\n",
            "id " + ident + "\n",
            "exit 0\n",
            "ms " + str(ms) + "\n",
            "prior " + str(prior_n) + "\n",
            block("handoff", handoff),
        ]
    )
    print(
        "grok-bot: role coordinator exit 0 " + str(ms) + " ms",
        file=sys.stderr,
        flush=True,
    )
    return 0


def reasoner_text(history):
    handoff = last_block(history, "handoff").strip()
    if not handoff:
        handoff = HANDOFF.strip()
    return (
        "Coordinator handoff:\n"
        + handoff
        + "\nUse the cursor tool now. Set job to "
        + CURSOR_JOB
        + ". Then reply with one short sentence that contains the word reasoner.\n"
    )


def call_post(ident, text, url, timeout):
    nvidia_client.write_request(nvidia_client.request_body(ident, text, None))
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    code = 0
    try:
        nvidia_client.post_turn(url, ident, text, None, None, timeout)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    finally:
        sys.stdout = old
    return code, buf.getvalue()


def call_drop(ident, text, timeout):
    nvidia_client.write_request(nvidia_client.request_body(ident, text, None))
    if nvidia_client.RESPONSE.is_file():
        try:
            nvidia_client.RESPONSE.unlink()
        except OSError as exc:
            die("cannot remove nvidia_turn.response.txt: " + str(exc))
    argv = [
        venv_python(),
        str(ROOT / "nvidia_worker.py"),
        "--drop",
        "--once",
        "--timeout",
        str(timeout),
    ]
    print("grok-bot: drop once", file=sys.stderr, flush=True)
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
            timeout=timeout + 30,
        )
    except subprocess.TimeoutExpired:
        return 2, ""
    except OSError as exc:
        print("grok-bot: " + str(exc), file=sys.stderr, flush=True)
        return 2, ""
    err = (completed.stderr or "").strip()
    if err:
        print("\n".join(err.splitlines()[-20:]), file=sys.stderr, flush=True)
    return completed.returncode, completed.stdout or ""


def call_worker(ident, text, url, drop, timeout, watch):
    watch.start()
    try:
        if drop:
            return call_drop(ident, text, timeout)
        print("grok-bot: post " + url, file=sys.stderr, flush=True)
        return call_post(ident, text, url, timeout)
    finally:
        watch.finish()


def run_reasoner(url, drop, timeout):
    print("grok-bot: role reasoner", file=sys.stderr, flush=True)
    started = time.perf_counter()
    if gemma.SPAWN_PATH.is_file():
        try:
            gemma.SPAWN_PATH.unlink()
        except OSError as exc:
            die("cannot remove grok_bot_spawn.txt: " + str(exc))
    history = read_text(HISTORY) if HISTORY.is_file() else ""
    question = reasoner_text(history)
    ident = str(time.time_ns())
    write_text(REQUEST, team_request(ident, "reasoner", question))
    watch = GpuWatch()
    code, _out = call_worker(ident, question, url, drop, timeout, watch)
    ms = int((time.perf_counter() - started) * 1000)
    raw = ""
    if nvidia_client.RESPONSE.is_file():
        raw = read_text(nvidia_client.RESPONSE)
    wid, ok, payload = parse_worker_response(raw)
    if not wid:
        wid = ident
    spawn_raw = read_text(gemma.SPAWN_PATH) if gemma.SPAWN_PATH.is_file() else ""
    kind = spawn_kind(spawn_raw)
    origin = "none"
    if ok and kind == "missing":
        # One POST already finished. Temp 1.0 may skip the tool. The same
        # launcher still runs one Cursor CLI job for this turn.
        print("grok-bot: cursor tool direct", file=sys.stderr, flush=True)
        gemma.run_cursor_job(CURSOR_JOB)
        spawn_raw = read_text(gemma.SPAWN_PATH) if gemma.SPAWN_PATH.is_file() else ""
        kind = spawn_kind(spawn_raw)
        origin = "direct"
    elif kind == "blocked":
        origin = "blocked"
    elif kind == "fail":
        origin = "fail"
    elif kind == "ran":
        origin = "model"
    text_out = payload if ok else ""
    role_ok = bool(ok and text_out.strip())
    if role_ok:
        write_text(RESPONSE, team_response(wid, "reasoner", text_out))
    else:
        write_text(RESPONSE, team_error(wid, "reasoner", payload if not ok else "empty"))
    parts = [
        "role reasoner\n",
        "id " + wid + "\n",
        "exit " + ("0" if role_ok else "2") + "\n",
        "ms " + str(ms) + "\n",
        "via " + ("drop" if drop else "post") + "\n",
        "spawn " + origin + "\n",
    ]
    if role_ok:
        parts.append(block("text", text_out))
    else:
        parts.append("err " + (" ".join(str(payload).split()) or "empty") + "\n")
    if code not in (0, None) and role_ok:
        parts.append("worker_exit " + str(code) + "\n")
    append_history(parts)
    print(
        "grok-bot: role reasoner exit " + ("0" if role_ok else "2") + " " + str(ms) + " ms",
        file=sys.stderr,
        flush=True,
    )
    return role_ok, ms, origin, watch


def heard_line(transcript):
    flat = " ".join(transcript.split())
    if "<<" in flat:
        flat = " ".join(flat.replace("<<", " ").split())
    return flat


def voice_handoff(heard):
    return "next role reasoner\nheard: " + heard + "\n"


def voice_reasoner_text(history):
    handoff = last_block(history, "handoff").strip()
    if not handoff:
        die("voice door missing coordinator handoff")
    return (
        "Coordinator handoff:\n"
        + handoff
        + "\nReply in one or two short spoken sentences.\n"
    )


def resolve_door_url(args):
    if args.url is not None and args.url.strip():
        url = args.url.strip()
    else:
        url = os.environ.get("TRIDENT_NVIDIA_URL", "").strip()
    if not url:
        die("set TRIDENT_NVIDIA_URL or pass --url")
    if not (url.startswith("http://") or url.startswith("https://")):
        die("nvidia url must start with http:// or https://")
    return url


def probe_once(url, timeout):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if not host or parsed.scheme not in ("http", "https"):
        return False, "bad url"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
    except OSError as exc:
        return False, str(exc)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return True, host + ":" + str(port)


def probe_worker(url, timeout=5.0):
    ok, where = probe_once(url, timeout)
    if ok:
        return True, where
    time.sleep(0.2)
    return probe_once(url, timeout)


def local_8765_line():
    rows = listeners_8765()
    if not rows:
        return "none"
    return " ".join(addr + " pid " + pid for addr, pid in rows)


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


def hear_wav(path):
    wav = Path(path)
    if not wav.is_file():
        die("missing wav: " + str(wav))
    argv = [venv_python(), str(ROOT / "hear.py"), "--wav", str(wav)]
    print("grok-bot: hear wav", file=sys.stderr, flush=True)
    code, out, _err = run_child_text("hear", argv, 180)
    return code, out.strip()


def run_voice_reasoner(url, timeout):
    print("grok-bot: role reasoner", file=sys.stderr, flush=True)
    started = time.perf_counter()
    history = read_text(HISTORY) if HISTORY.is_file() else ""
    question = voice_reasoner_text(history)
    ident = str(time.time_ns())
    write_text(REQUEST, team_request(ident, "reasoner", question))
    print("grok-bot: post " + url, file=sys.stderr, flush=True)
    code, _out = call_post(ident, question, url, timeout)
    ms = int((time.perf_counter() - started) * 1000)
    raw = ""
    if nvidia_client.RESPONSE.is_file():
        raw = read_text(nvidia_client.RESPONSE)
    wid, ok, payload = parse_worker_response(raw)
    if not wid:
        wid = ident
    text_out = payload if ok else ""
    role_ok = bool(ok and text_out.strip())
    if role_ok:
        write_text(RESPONSE, team_response(wid, "reasoner", text_out))
    else:
        write_text(RESPONSE, team_error(wid, "reasoner", payload if not ok else "empty"))
    parts = [
        "role reasoner\n",
        "id " + wid + "\n",
        "exit " + ("0" if role_ok else "2") + "\n",
        "ms " + str(ms) + "\n",
        "via post\n",
    ]
    if role_ok:
        parts.append(block("text", text_out))
    else:
        parts.append("err " + (" ".join(str(payload).split()) or "empty") + "\n")
    if code not in (0, None) and role_ok:
        parts.append("worker_exit " + str(code) + "\n")
    append_history(parts)
    print(
        "grok-bot: role reasoner exit " + ("0" if role_ok else "2") + " " + str(ms) + " ms",
        file=sys.stderr,
        flush=True,
    )
    return role_ok, ms, text_out


def wav_ok(path):
    try:
        return Path(path).is_file() and Path(path).stat().st_size > 44
    except OSError:
        return False


def speak_door(text):
    import assistant

    cleaned = gemma.strip_tool_markup(text)
    spoken = assistant.speakable(cleaned)
    parts = assistant.chunks_for_mouth(spoken, "en") if spoken else []
    if not parts:
        return spoken, []
    argv = [venv_python(), str(ROOT / "mouth.py"), "--model", "nano", "--no-play", "--"]
    argv.extend(parts)
    print("grok-bot: mouth " + str(len(parts)) + " chunk(s)", file=sys.stderr, flush=True)
    code, out, _err = run_child_text("mouth", argv, 300)
    if code != 0:
        return spoken, []
    paths = []
    for line in out.splitlines():
        name = line.strip()
        if name:
            paths.append(name)
    return spoken, paths


def clip_line(text, limit=240):
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "..."


def write_door(lines):
    write_text(DOOR, "".join(line + "\n" for line in lines))


def door(args):
    url = resolve_door_url(args)
    up, where = probe_worker(url)
    print(
        "grok-bot: worker " + ("up " + where if up else "down " + where),
        file=sys.stderr,
        flush=True,
    )
    reasons = []
    transcript = ""
    hear_exit = "skipped"
    team_ran = False
    role_ok = False
    ms = 0
    reply = ""
    spoken = ""
    mouth_paths = []
    mouth_exit = "skipped"
    if not up:
        reasons.append("nvidia worker not reachable")
    else:
        hear_code, transcript = hear_wav(args.wav)
        hear_exit = str(hear_code)
        if hear_code != 0:
            reasons.append("hear exit " + hear_exit)
        elif not transcript.strip():
            reasons.append("no transcript")
        else:
            heard = heard_line(transcript)
            if not heard:
                reasons.append("no transcript")
            else:
                run_coordinator(voice_handoff(heard))
                role_ok, ms, reply = run_voice_reasoner(url, args.timeout)
                team_ran = True
                if not role_ok:
                    reasons.append("reasoner failed")
                else:
                    spoken, mouth_paths = speak_door(reply)
                    if not mouth_paths or not all(wav_ok(path) for path in mouth_paths):
                        mouth_exit = "2"
                        reasons.append("no mouth wav")
                    else:
                        mouth_exit = "0"
    up_after, where_after = probe_worker(url)
    if up and not up_after:
        reasons.append("worker down after turn")
    passed = not reasons
    if team_ran:
        append_history(["door ok\n" if passed else "door fail\n"])
    lines = [
        "STATUS " + ("PASS" if passed else "FAIL"),
        "date: " + time.strftime("%Y-%m-%d"),
        "head: " + head_sha(),
        "cmd: grok_local_bot.py --wav",
        "wav: " + str(Path(args.wav)),
        "transcript: " + clip_line(transcript),
        "reply: " + clip_line(spoken or reply),
        "url: " + url,
        "probe: " + ("up " + where if up else "down " + where),
        "probe_after: " + ("up " + where_after if up_after else "down " + where_after),
        "local_8765: " + local_8765_line(),
        "hear_exit: " + hear_exit,
        "coordinator_exit: " + ("0" if team_ran else "skipped"),
        "reasoner_exit: " + ("0" if role_ok else ("2" if team_ran else "skipped")),
        "reasoner_ms: " + str(ms),
        "mouth_exit: " + mouth_exit,
        "mouth_wav: " + " ".join(mouth_paths),
        "history: grok_bot_history.txt",
        "request: grok_bot_request.txt",
        "response: grok_bot_response.txt",
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


def reset_team_files():
    write_text(HISTORY, "")
    if gemma.SPAWN_PATH.is_file():
        try:
            gemma.SPAWN_PATH.unlink()
        except OSError as exc:
            die("cannot remove grok_bot_spawn.txt: " + str(exc))


def write_status(lines):
    write_text(STATUS, "".join(line + "\n" for line in lines))


def prove(args):
    before = listeners_8765()
    pid = lan_pid(before)
    print(
        "grok-bot: listener " + ("0.0.0.0:8765 pid " + pid if pid else "missing"),
        file=sys.stderr,
        flush=True,
    )
    if not pid:
        write_status(
            [
                "STATUS FAIL",
                "reason: 0.0.0.0:8765 is not listening",
                "head: " + head_sha(),
            ]
        )
        raise SystemExit(2)
    reset_team_files()
    run_coordinator()
    role_ok, ms, origin, watch = run_reasoner(args.url, args.drop, args.timeout)
    after = listeners_8765()
    pid_after = lan_pid(after)
    history = read_text(HISTORY) if HISTORY.is_file() else ""
    spawn_raw = read_text(gemma.SPAWN_PATH) if gemma.SPAWN_PATH.is_file() else ""
    sexit = spawn_exit(spawn_raw)
    spawn_ok = spawn_kind(spawn_raw) == "ran" and sexit == 0
    listener_ok = bool(pid_after) and pid_after == pid
    passed = role_ok and roles_in_order(history) and spawn_ok and listener_ok
    append_history(["result ok\n" if passed else "result fail\n"])
    base, util, used = gpu_peak(watch.samples)
    lines = [
        "STATUS " + ("PASS" if passed else "FAIL"),
        "date: " + time.strftime("%Y-%m-%d"),
        "head: " + head_sha(),
        "coordinator_exit: 0",
        "reasoner_exit: " + ("0" if role_ok else "2"),
        "reasoner_ms: " + str(ms),
        "via: " + ("drop" if args.drop else "post"),
        "spawn: " + origin,
        "spawn_exit: " + (str(sexit) if sexit is not None else "none"),
        "listener: 0.0.0.0:8765 pid " + pid,
        "listener_after: " + ("0.0.0.0:8765 pid " + pid_after if pid_after else "missing"),
        "history: grok_bot_history.txt",
        "request: grok_bot_request.txt",
        "response: grok_bot_response.txt",
        "spawn_log: grok_bot_spawn.txt",
    ]
    if base:
        lines.append("gpu_base_util: " + str(base[0]))
        lines.append("gpu_base_mib: " + str(base[1]))
    if util is not None:
        lines.append("gpu_peak_util: " + str(util))
    if used is not None:
        lines.append("gpu_peak_mib: " + str(used))
    write_status(lines)
    print("grok-bot: " + ("PASS" if passed else "FAIL"), file=sys.stderr, flush=True)
    raise SystemExit(0 if passed else 2)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="grok_local_bot.py")
    parser.add_argument("--role", choices=("coordinator", "reasoner"))
    parser.add_argument(
        "--proof",
        action="store_true",
        help="run coordinator then reasoner on one history file",
    )
    parser.add_argument(
        "--wav",
        default=None,
        help="Iris voice door: hear this wav, team turn over LAN, mouth wav with --no-play",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="reasoner uses nvidia_worker.py --drop --once instead of POST",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="worker POST url. --wav uses this or TRIDENT_NVIDIA_URL. Other modes default to http://127.0.0.1:8765/",
    )
    parser.add_argument("--timeout", type=float, default=600, help="worker seconds (default 600)")
    args = parser.parse_args()
    if args.wav is not None and args.wav.strip() == "":
        die("empty wav")
    modes = sum(1 for flag in (args.proof, bool(args.role), args.wav is not None) if flag)
    if modes != 1:
        die("pass one of --proof, --role, or --wav")
    if args.wav is not None and args.drop:
        die("--wav posts to the NVIDIA worker")
    if args.timeout <= 0:
        die("timeout must be > 0")
    if args.url is not None and args.url.strip() == "":
        die("empty url")
    venv_python()
    if args.wav is not None:
        door(args)
        return
    if not args.drop:
        url = (args.url or "http://127.0.0.1:8765/").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            die("nvidia url must start with http:// or https://")
        args.url = url
    if args.proof:
        prove(args)
        return
    if args.role == "coordinator":
        run_coordinator()
        raise SystemExit(0)
    role_ok, _ms, origin, _watch = run_reasoner(args.url, args.drop, args.timeout)
    print("grok-bot: spawn " + origin, file=sys.stderr, flush=True)
    raise SystemExit(0 if role_ok else 2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("grok-bot: stopped", file=sys.stderr)
        raise SystemExit(0)
