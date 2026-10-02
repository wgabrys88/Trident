"""Gemma text and image inference. One resident gemma-brain.exe.
"""

import argparse
import ctypes
import hashlib
import os
import subprocess
import sys
import threading
import time
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_KEYS = ("gemma.text", "gemma.image")
SIDECAR = ROOT / "gemma_run.txt"


def die(message):
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


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



def config_body():
    source = ROOT / "gemma.txt"
    if not source.is_file():
        die("missing gemma.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body and not body.endswith("\n"):
        body += "\n"
    return body


def resident_settings():
    return config_body() + "gemma.text <<\n<<\ngemma.image <<\n<<\n"


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
ASK_TIMEOUT = 600
PRELOAD_CANCEL = threading.Event()
_PRELOAD_GUARD = threading.Lock()
_PRELOAD_THREAD = None
_PRELOAD_ABORT = threading.local()
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


def preload_should_stop():
    return PRELOAD_CANCEL.is_set() and getattr(_PRELOAD_ABORT, "on", False)


def preload_abort():
    err = SystemExit(2)
    err.message = "gemma preload cancelled"
    raise err


def wait_until_ready(pid, fingerprint, proc=None):
    deadline = time.time() + ASK_TIMEOUT
    while time.time() < deadline:
        if preload_should_stop():
            preload_abort()
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
        if preload_should_stop():
            preload_abort()
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
            if preload_should_stop():
                preload_abort()
            proc = launch_resident(fingerprint)
            if proc is None:
                die("gemma stop requested")
        finally:
            release_lock(fd)
        return wait_until_ready(proc.pid, fingerprint, proc)
    die("gemma did not become ready\n" + log_tail())


def idle():
    if (ROOT / "gemma.busy").is_file() and brain_running_any():
        die("gemma busy")
    return ensure_resident()


def begin_preload():
    import mouth

    global _PRELOAD_THREAD
    if mouth.chatterbox_running_any():
        print("vram: gemma preload held", file=sys.stderr, flush=True)
        return
    with _PRELOAD_GUARD:
        if _PRELOAD_THREAD is not None and _PRELOAD_THREAD.is_alive():
            return
        desired = resident_settings()
        fingerprint = hashlib.sha256(desired.encode("utf-8")).hexdigest()
        rec = resident_record()
        if same_settings(rec, fingerprint) and rec.state == "ready":
            print("vram: gemma resident", file=sys.stderr, flush=True)
            return
        PRELOAD_CANCEL.clear()

        def worker():
            _PRELOAD_ABORT.on = True
            try:
                if PRELOAD_CANCEL.is_set():
                    print("vram: gemma preload cancelled", file=sys.stderr, flush=True)
                    return
                import mouth as mouth_mod

                if mouth_mod.chatterbox_running_any():
                    print("vram: gemma preload held", file=sys.stderr, flush=True)
                    return
                ensure_resident()
                if PRELOAD_CANCEL.is_set():
                    print("vram: gemma preload cancelled", file=sys.stderr, flush=True)
                    return
                print("vram: gemma resident", file=sys.stderr, flush=True)
            except SystemExit as exc:
                if PRELOAD_CANCEL.is_set():
                    print("vram: gemma preload cancelled", file=sys.stderr, flush=True)
                    return
                message = getattr(exc, "message", "") or "failed"
                print("vram: gemma preload failed " + message, file=sys.stderr, flush=True)
            finally:
                _PRELOAD_ABORT.on = False

        print("vram: gemma preload", file=sys.stderr, flush=True)
        _PRELOAD_THREAD = threading.Thread(target=worker, name="gemma-preload", daemon=True)
        _PRELOAD_THREAD.start()


def cancel_preload():
    PRELOAD_CANCEL.set()
    if brain_running_any():
        stop_resident()
    with _PRELOAD_GUARD:
        thread = _PRELOAD_THREAD
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(120)
    if brain_running_any():
        stop_resident()


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
    print("vram: gemma ready", file=sys.stderr, flush=True)

    def on_piece(piece):
        if stream and piece:
            sys.stdout.buffer.write(piece)
            sys.stdout.buffer.flush()

    text = resident_ask(pid, prompt, image_b64, on_piece, timeout)
    if not str(text or "").strip():
        die("gemma returned empty")
    return text


def main():
    parser = argparse.ArgumentParser(prog="gemma.py")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if not args.stop:
        die("usage: gemma.py --stop")
    os.chdir(ROOT)
    if stop_resident():
        print("gemma: stopped", file=sys.stderr, flush=True)
    else:
        print("gemma: not running", file=sys.stderr, flush=True)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
