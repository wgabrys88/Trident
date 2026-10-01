"""Trident on this machine.

python run.py
    Gemma stays resident. The phone line listens. Prints nothing and exits 0.
    A failure prints one line and exits non-zero.

python run.py call [SECONDS]
    Place one voice call. Residents stay up when it drops.
    SECONDS hangs the call up after that long.

python run.py stop
    Hang up, stop Gemma and the mouth, and stop the line.

python run.py clean
    Delete call and test artifacts. Leaves models, installs, and reference.wav.
"""

import ctypes
import io
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def die(message):
    print(message, file=sys.stderr)
    err = SystemExit(2)
    err.message = message
    raise err


def venv_python():
    path = ROOT / ".venv" / "Scripts" / "python.exe"
    if not path.is_file():
        die("missing .venv python: " + str(path))
    return str(path)


def reexec():
    py = venv_python()
    if Path(sys.executable).resolve() != Path(py).resolve():
        os.environ["PYTHONUNBUFFERED"] = "1"
        raise SystemExit(subprocess.call([py, *sys.argv]))


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


def process_image(pid):
    if pid <= 0:
        return ""
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_uint32(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        return buf.value
    finally:
        kernel.CloseHandle(handle)


def python_alive(pid):
    image = process_image(pid)
    return bool(image) and Path(image).name.lower() in {"python.exe", "pythonw.exe"}


def taskkill(pid):
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        cwd=str(ROOT),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def drop_file(path):
    if not path.is_file():
        return 0
    path.unlink()
    return 1


def port_open(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def parse_args(argv):
    if not argv:
        return "rest", ""
    if argv[0] == "call":
        if len(argv) == 1:
            return "call", ""
        if len(argv) == 2 and argv[1].isdigit() and int(argv[1]) > 0:
            return "call", argv[1]
        die("usage: run.py call [SECONDS]")
    if argv == ["stop"]:
        return "stop", ""
    if argv == ["clean"]:
        return "clean", ""
    die("usage: run.py | run.py call [SECONDS] | run.py stop | run.py clean")


def cmd_clean():
    removed = 0
    for name in (
        "mouth.txt",
        "mouth.v3.txt",
        "mouth.stop",
        "mouth.lock",
        "gemma_run.txt",
        "sense_run.txt",
        "vad_run.txt",
        "call.hear.wav",
        "call.hear.txt",
        "call.ready",
        "call.blocker.txt",
        "node.pid",
        "node.run.err",
        "gemma.lastprompt.txt",
    ):
        removed += drop_file(ROOT / name)
    for pattern in ("*.pid", "*.pid.tmp", "*.stop", "*.prompt.txt", "*.response.txt", "*.response.wav", "*.run.log", "*.run.err", "*_chatterbox_out_*", "*_out_*.txt", "*_peer.wav"):
        for path in ROOT.glob(pattern):
            removed += drop_file(path)
    for path in ROOT.glob("*.wav"):
        if path.name != "reference.wav":
            removed += drop_file(path)
    for folder in ("node.queue", "node.state", "node.room"):
        base = ROOT / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            removed += drop_file(path)
    print("clean " + str(removed), flush=True)


def stop_mouth():
    py = venv_python()
    return subprocess.run([py, str(ROOT / "mouth.py"), "--stop"], cwd=str(ROOT), shell=False).returncode


def cmd_stop():
    import gemma
    import node

    if port_open(node.PORT):
        try:
            node.transact("127.0.0.1:" + str(node.PORT), node.make_card("call", "hang", "close"), 60)
        except SystemExit:
            pass
    stop_mouth()
    try:
        gemma.stop_resident()
    except SystemExit:
        pass
    pid = read_pid("node.pid")
    if python_alive(pid):
        taskkill(pid)
    drop_file(ROOT / "node.pid")
    print("trident: stopped", file=sys.stderr)


def ensure_node():
    import node

    py = venv_python()
    proc = None
    log = None
    if not port_open(node.PORT):
        argv = [py, "-u", str(ROOT / "node.py"), "--line"]
        log = open(ROOT / "node.run.err", "wb", buffering=0)
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(ROOT),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB,
            )
        except OSError as exc:
            die("line did not start " + " ".join(str(exc).split()))
        finally:
            if log is not None:
                log.close()
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and not port_open(node.PORT):
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if not port_open(node.PORT):
            if proc.poll() is None:
                taskkill(proc.pid)
            try:
                text = (ROOT / "node.run.err").read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            lines = [item.strip() for item in text.replace("\r\n", "\n").splitlines() if item.strip()]
            die(lines[-1] if lines else "line did not become idle")
    reply = node.transact("127.0.0.1:" + str(node.PORT), node.make_card("node", "hello", "hi"), 30)
    rows = reply.body.splitlines()
    for line in ("capture closed", "op call.dial", "op call.wait"):
        if line not in rows:
            die("node is not this protocol")
    if "op node.join" in rows:
        die("node is not this protocol")
    return rows


def line_state():
    import node

    waited = node.transact("127.0.0.1:" + str(node.PORT), node.make_card("call", "wait", ""), 240)
    return (waited.body or "").strip()


def show(text):
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cmd_call(seconds):
    ensure_node()
    import gemma
    import node

    state = line_state()
    if state == "up":
        node.transact("127.0.0.1:" + str(node.PORT), node.make_card("call", "hang", ""), 90)
        state = line_state()
    if state != "idle":
        die("line did not become idle")
    gemma.idle()
    up = node.transact("127.0.0.1:" + str(node.PORT), node.make_card("call", "dial", ""), 300)
    show(up.body)
    if not seconds:
        return
    time.sleep(int(seconds))
    hung = node.transact("127.0.0.1:" + str(node.PORT), node.make_card("call", "hang", ""), 90)
    show(hung.body)


def cmd_rest():
    sink = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sink
    sys.stderr = sink
    try:
        ensure_node()
        state = line_state()
        if state == "up":
            die("call up")
        if state != "idle":
            die("line did not become idle")
        import gemma

        gemma.idle()
    except KeyboardInterrupt:
        sys.stdout = old_out
        sys.stderr = old_err
        raise
    except BaseException as exc:
        sys.stdout = old_out
        sys.stderr = old_err
        text = getattr(exc, "message", "") or sink.getvalue() or str(exc)
        line = " ".join(str(text).split()) or "failed"
        print(line, file=sys.stderr)
        code = exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, int) and exc.code else 1
        raise SystemExit(code)
    sys.stdout = old_out
    sys.stderr = old_err


def main():
    os.environ["PYTHONUNBUFFERED"] = "1"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    reexec()
    command, extra = parse_args(sys.argv[1:])
    if command == "stop":
        cmd_stop()
        return
    if command == "clean":
        cmd_clean()
        return
    if command == "call":
        cmd_call(extra)
        return
    cmd_rest()


if __name__ == "__main__":
    main()
