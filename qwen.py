"""Qwen (sense) brain. Text question → stdout generation. Vision is N/A (text-only Qwen3-0.6B).

The first call starts sense.exe --resident and leaves it loaded. Later calls reuse that process
when the settings fingerprint in sense.pid still matches. qwen.py --once keeps the old one-shot process.
qwen.py --stop shuts the resident down, including a loader that is not ready yet.
"""

import argparse
import ctypes
import hashlib
import os
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_KEYS = ("sense.text",)
SIDECAR = ROOT / "sense_run.txt"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SLOTS = (
    "sense.pid",
    "sense.pid.tmp",
    "sense.stop",
    "sense.prompt.txt",
    "sense.response.txt",
    "sense.prompt.txt.tmp",
    "sense.response.txt.tmp",
)
LOCK_NAME = "sense.lock"
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


def without_spoken_text(raw):
    lines = physical_lines(raw)
    if lines and lines[-1] == "":
        lines = lines[:-1]
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip(" \t")
        if len(stripped) >= 2 and stripped.endswith("<<"):
            key = stripped[:-2].rstrip(" \t")
            if key == "sense.text":
                index += 1
                while index < len(lines) and lines[index] != "<<":
                    index += 1
                if index < len(lines):
                    index += 1
                continue
        if stripped.split(" ", 1)[0] == "sense.text":
            index += 1
            continue
        kept.append(line)
        index += 1
    return "\n".join(kept)


def qwen_prompt(question):
    q = question.strip("\r\n")
    # Qwen3 otherwise opens <think> and spends sense.n-predict before any answer.
    if "/no_think" not in q and "/think" not in q:
        q = q.rstrip() + " /no_think"
    return (
        "<|im_start|>user\n"
        + q
        + "<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def model_prompt(question):
    prompt = qwen_prompt(question)
    if prompt.endswith("\n"):
        prompt = prompt[:-1]
    return prompt


def base_settings():
    source = ROOT / "sense.txt"
    if not source.is_file():
        die("missing sense.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body:
        body += "\n"
    return body


def settings_text(question):
    body = base_settings()
    prompt = qwen_prompt(question)
    body += "sense.text <<\n" + prompt
    if not prompt.endswith("\n"):
        body += "\n"
    body += "<<\n"
    return body


def resident_settings():
    return base_settings() + "sense.text <<\n<<\n"


def newest_out(before):
    after = set(ROOT.glob("*_sense_out_*.txt"))
    new_files = sorted(after - before)
    if not new_files:
        die("sense wrote no output text")
    return new_files[-1]


def emit_stdout(text):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def once(question, verbose):
    exe = ROOT / "sense.exe"
    if not exe.is_file():
        die("missing sense.exe")
    payload = settings_text(question)
    try:
        SIDECAR.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write sense_run.txt: " + str(exc))
    before = set(ROOT.glob("*_sense_out_*.txt"))
    try:
        completed = subprocess.run(
            [".\\sense.exe", "sense_run.txt"],
            cwd=ROOT,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=None if verbose else subprocess.DEVNULL,
        )
    except OSError as exc:
        die("cannot run sense.exe: " + str(exc))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    out_txt = newest_out(before)
    emit_stdout(out_txt.read_text(encoding="utf-8"))


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
    if len(text) != 64:
        return False
    for ch in text:
        if ch not in "0123456789abcdef":
            return False
    return True


def resident_record():
    path = ROOT / "sense.pid"
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


def settings_fingerprint(text):
    return hashlib.sha256(without_spoken_text(text).encode("utf-8")).hexdigest()


def publish_pid(pid, fingerprint, state):
    body = str(pid) + "\n" + fingerprint + "\n" + state + "\n"
    tmp = ROOT / "sense.pid.tmp"
    try:
        tmp.write_bytes(body.encode("utf-8"))
        tmp.replace(ROOT / "sense.pid")
    except OSError as exc:
        die("cannot write sense.pid: " + str(exc))


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


def sense_running(pid):
    image = process_image(pid)
    return bool(image) and Path(image).name.lower() == "sense.exe"


def terminate_pid(pid):
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, 0, pid)
    if not handle:
        return
    try:
        _kernel32.TerminateProcess(handle, 1)
    finally:
        _kernel32.CloseHandle(handle)


def log_tail():
    path = ROOT / "sense.run.err"
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(data) > 2000:
        data = data[-2000:]
    return data


def wait_dead(pid, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not sense_running(pid):
            return True
        time.sleep(0.05)
    return not sense_running(pid)


def acquire_lock():
    path = ROOT / LOCK_NAME
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        return None
    except OSError as exc:
        die("cannot create sense.lock: " + str(exc))
    try:
        os.write(fd, (str(os.getpid()) + "\n" + str(time.time()) + "\n").encode("ascii"))
    except OSError as exc:
        os.close(fd)
        die("cannot write sense.lock: " + str(exc))
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
        pid = int(lines[0].strip())
    except ValueError:
        return None
    try:
        started = float(lines[1].strip()) if len(lines) > 1 else 0.0
    except ValueError:
        started = 0.0
    return pid, started


def lock_busy():
    if not (ROOT / LOCK_NAME).is_file():
        return False
    owner = lock_owner()
    if owner is None:
        return True
    return bool(process_image(owner[0]))


def clear_stale_lock():
    if lock_busy():
        return
    path = ROOT / LOCK_NAME
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def stop_resident():
    try:
        (ROOT / "sense.stop").write_bytes(b"stop\n")
    except OSError as exc:
        die("cannot write sense.stop: " + str(exc))
    stopped = False
    deadline = time.time() + 15
    while True:
        rec = resident_record()
        if rec is not None and sense_running(rec.pid):
            stopped = True
            if rec.state == "loading":
                terminate_pid(rec.pid)
                if not wait_dead(rec.pid, 5):
                    die("cannot stop sense pid " + str(rec.pid))
            elif not wait_dead(rec.pid, 120):
                terminate_pid(rec.pid)
                if not wait_dead(rec.pid, 5):
                    die("cannot stop sense pid " + str(rec.pid))
            break
        if not lock_busy() or time.time() > deadline:
            break
        time.sleep(0.05)
    for name in SLOTS:
        remove_file(name)
    clear_stale_lock()
    return stopped


def sense_running_any():
    rec = resident_record()
    return rec is not None and sense_running(rec.pid)


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
    if (ROOT / "sense.stop").is_file():
        return None
    exe = ROOT / "sense.exe"
    if not exe.is_file():
        die("missing sense.exe")
    log_path = ROOT / "sense.run.err"
    try:
        log = open(log_path, "wb", buffering=0)
    except OSError as exc:
        die("cannot write sense.run.err: " + str(exc))
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        try:
            proc = subprocess.Popen(
                [str(exe), "--resident", "sense_run.txt"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags | breakaway,
                close_fds=True,
            )
        except OSError:
            proc = subprocess.Popen(
                [str(exe), "--resident", "sense_run.txt"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
    except OSError as exc:
        log.close()
        die("cannot run sense.exe: " + str(exc))
    log.close()
    try:
        note_spawn(proc, fingerprint)
    except SystemExit:
        if proc.poll() is None:
            terminate_pid(proc.pid)
        raise
    if (ROOT / "sense.stop").is_file() or proc.poll() is not None:
        if proc.poll() is None:
            terminate_pid(proc.pid)
            wait_dead(proc.pid, 5)
        remove_file("sense.pid")
        if proc.poll() is not None and not (ROOT / "sense.stop").is_file():
            die("sense exited " + str(proc.returncode) + "\n" + log_tail())
        return None
    print("qwen: starting sense", file=sys.stderr)
    return proc


def fail_loader(pid, message):
    if sense_running(pid):
        terminate_pid(pid)
        if not wait_dead(pid, 5):
            die(message + "\nloader pid " + str(pid) + " did not exit")
    remove_file("sense.pid")
    die(message)


def wait_until_ready(pid, fingerprint, proc=None):
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            die("sense exited " + str(proc.returncode) + "\n" + log_tail())
        if not sense_running(pid):
            die("sense exited\n" + log_tail())
        rec = resident_record()
        if rec is not None and rec.pid == pid:
            if rec.state == "ready" and rec.fingerprint == fingerprint:
                print("qwen: sense pid " + str(pid) + " resident", file=sys.stderr)
                return pid
            if rec.fingerprint == fingerprint and rec.state == "loading":
                pass
            elif rec.fingerprint == "" and rec.state == "":
                publish_pid(pid, fingerprint, "ready")
                if not sense_running(pid):
                    die("sense exited\n" + log_tail())
                print("qwen: sense pid " + str(pid) + " resident", file=sys.stderr)
                return pid
        time.sleep(0.05)
    fail_loader(pid, "sense did not become ready\n" + log_tail())


def wait_for_fingerprint(pid, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        rec = resident_record()
        if rec is None or rec.pid != pid or not sense_running(pid):
            return None
        if rec.fingerprint:
            return rec
        time.sleep(0.02)
    rec = resident_record()
    if rec is not None and rec.pid == pid and rec.fingerprint and sense_running(pid):
        return rec
    return None


def same_settings(rec, fingerprint):
    return (
        rec is not None
        and sense_running(rec.pid)
        and rec.fingerprint == fingerprint
        and rec.state in ("loading", "ready")
    )


def write_sidecar(payload):
    try:
        SIDECAR.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write sense_run.txt: " + str(exc))


def ensure_resident():
    desired = resident_settings()
    fingerprint = settings_fingerprint(desired)
    deadline = time.time() + 240
    while time.time() < deadline:
        rec = resident_record()
        if rec is not None and sense_running(rec.pid) and not rec.fingerprint:
            rec = wait_for_fingerprint(rec.pid, 2) or rec
        if same_settings(rec, fingerprint):
            if rec.state == "ready":
                print("qwen: sense pid " + str(rec.pid) + " resident", file=sys.stderr)
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
                    print("qwen: sense pid " + str(rec.pid) + " resident", file=sys.stderr)
                    return rec.pid
                proc_pid = rec.pid
                release_lock(fd)
                fd = None
                return wait_until_ready(proc_pid, fingerprint)
            if rec is not None and sense_running(rec.pid):
                print("qwen: sense settings changed", file=sys.stderr)
                release_lock(fd)
                fd = None
                stop_resident()
                continue
            stop_path = ROOT / "sense.stop"
            if stop_path.is_file():
                age = time.time() - stop_path.stat().st_mtime
                if age < 30 or sense_running_any():
                    die("sense stop requested")
                remove_file("sense.stop")
            write_sidecar(desired)
            for name in ("sense.prompt.txt", "sense.response.txt", "sense.prompt.txt.tmp", "sense.response.txt.tmp"):
                remove_file(name)
            proc = launch_resident(fingerprint)
            if proc is None:
                die("sense stop requested")
        finally:
            release_lock(fd)
        return wait_until_ready(proc.pid, fingerprint, proc)
    die("sense did not become ready\n" + log_tail())


def resident_ask(pid, text):
    prompt = ROOT / "sense.prompt.txt"
    response = ROOT / "sense.response.txt"
    deadline = time.time() + 180
    while prompt.is_file():
        if time.time() > deadline:
            die("sense prompt still pending")
        if not sense_running(pid):
            die("sense exited\n" + log_tail())
        time.sleep(0.02)
    if response.is_file():
        try:
            response.unlink()
        except OSError as exc:
            die("cannot remove sense.response.txt: " + str(exc))
    ident = str(time.time_ns())
    tmp = ROOT / "sense.prompt.txt.tmp"
    try:
        tmp.write_bytes((ident + "\n" + text).encode("utf-8"))
        tmp.replace(prompt)
    except OSError as exc:
        die("cannot write sense.prompt.txt: " + str(exc))
    deadline = time.time() + 180
    while time.time() < deadline:
        if response.is_file():
            try:
                raw = response.read_bytes()
            except OSError:
                time.sleep(0.02)
                continue
            try:
                raw_text = raw.decode("utf-8")
            except UnicodeError:
                die("sense response is not utf-8")
            if "\n" not in raw_text:
                time.sleep(0.02)
                continue
            head, tail = raw_text.split("\n", 1)
            if head.strip() != ident:
                time.sleep(0.02)
                continue
            nl = tail.find("\n")
            if nl < 0:
                time.sleep(0.02)
                continue
            status = tail[:nl].strip()
            body = tail[nl + 1 :]
            try:
                response.unlink()
            except OSError:
                pass
            if status == "ok":
                return body
            if status.startswith("err"):
                message = status[3:].strip()
                die("sense: " + (message or "failed"))
            die("sense: " + (status or "empty response"))
        if not sense_running(pid):
            die("sense exited\n" + log_tail())
        time.sleep(0.02)
    die("sense timed out")


def main():
    parser = argparse.ArgumentParser(prog="qwen.py")
    parser.add_argument("question", nargs="?", default=None, help="text question")
    parser.add_argument(
        "--image",
        metavar="PATH",
        help="not supported: Qwen3-0.6B is text-only (vision N/A)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show one-shot sense.exe stderr; resident logs are sense.run.err",
    )
    parser.add_argument("--once", action="store_true", help="one-shot sense.exe, then exit")
    parser.add_argument("--stop", action="store_true", help="stop the resident sense.exe")
    args = parser.parse_args()
    if args.stop:
        if args.once or args.question or args.image or args.verbose:
            die("usage: qwen.py --stop")
        os.chdir(ROOT)
        if stop_resident():
            print("qwen: sense stopped", file=sys.stderr)
        else:
            print("qwen: sense not running", file=sys.stderr)
        raise SystemExit(0)
    if args.question is None:
        die("usage: qwen.py [--once] [--verbose] QUESTION")
    if not args.question.strip():
        die("empty question")
    if args.image:
        die("qwen/sense vision is N/A: Qwen3-0.6B is text-only. Use gemma.py --image on Nvidia.")
    os.chdir(ROOT)
    if args.once:
        once(args.question, args.verbose)
    else:
        pid = ensure_resident()
        emit_stdout(resident_ask(pid, model_prompt(args.question)))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
