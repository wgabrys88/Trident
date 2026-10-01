"""Trident.

python run.py [--peer HOST:PORT]
    Residents idle, no call up. Prints nothing and exits 0.
    A failure prints one line and exits non-zero.

python run.py start [--peer HOST:PORT] [inject [PATH]]
    Start the node on port 8765 when it is down, then the assistant.
    The microphone stays closed.

python run.py stop
    Stop this organism and the mouth. Leave port 8765 listening.

python run.py call [SECONDS] [--peer HOST:PORT]
    Quit Telegram Desktop, place one voice call, then restart Desktop.
    The microphone stays closed. Resident Gemma stays loaded.
"""

import ctypes
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
READY_SECONDS = 60
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


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


def remove_pidfile(name, pid):
    if read_pid(name) != pid:
        return
    try:
        (ROOT / name).unlink()
    except OSError:
        pass


def host_port(value):
    if value.startswith("http://") or value.startswith("https://"):
        die("peer is host:port")
    host, sep, port = value.rpartition(":")
    if sep != ":" or not host or not port.isdigit() or not (1 <= int(port) <= 65535):
        die("peer is host:port")
    return value


def split_peers(argv):
    peers = []
    rest = []
    index = 0
    while index < len(argv):
        if argv[index] == "--peer":
            if index + 1 >= len(argv) or not argv[index + 1].strip():
                die("peer is host:port")
            peers.append(host_port(argv[index + 1].strip()))
            index += 2
            continue
        rest.append(argv[index])
        index += 1
    return rest, peers


def parse_args(argv):
    argv, peers = split_peers(argv)
    usage = "usage: run.py [--peer HOST:PORT] | run.py start [inject [PATH]] [--peer HOST:PORT] | run.py stop | run.py call [SECONDS] [--peer HOST:PORT]"
    if not argv:
        return "rest", peers, None
    if argv[0] == "call":
        if len(argv) == 1:
            return "call", peers, ""
        if len(argv) == 2 and argv[1].isdigit() and int(argv[1]) > 0:
            return "call", peers, argv[1]
        die("usage: run.py call [SECONDS] [--peer HOST:PORT]")
    command = argv[0]
    if command == "stop":
        if len(argv) != 1:
            die("usage: run.py stop")
        return "stop", [], None
    if command != "start":
        die(usage)
    inject = None
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "inject":
            if inject is not None:
                die(usage)
            if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                inject = argv[index + 1]
                if not inject.strip():
                    die("empty inject path")
                index += 2
            else:
                inject = "-"
                index += 1
            continue
        die(usage)
    return "start", peers, inject


def already_up():
    for name in ("assistant.pid", "iris.pid"):
        pid = read_pid(name)
        if python_alive(pid):
            print("iris: already up pid " + str(pid), file=sys.stderr)
            return True
    return False


def pid_stamp(name):
    path = ROOT / name
    try:
        text = path.read_text(encoding="utf-8")
        mtime = path.stat().st_mtime_ns
    except OSError:
        return "", 0
    return text, mtime


def taskkill(pid, tree=True):
    argv = ["taskkill", "/PID", str(pid), "/F"]
    if tree:
        argv.insert(-1, "/T")
    subprocess.run(
        argv,
        cwd=str(ROOT),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def end_process(proc, before):
    if proc.poll() is None:
        taskkill(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    if pid_stamp("assistant.pid") == before:
        return
    pid = read_pid("assistant.pid")
    if pid and pid != proc.pid and python_alive(pid):
        taskkill(pid)


def wait_ready(proc, before):
    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            code = proc.returncode
            raise SystemExit(code if code is not None else 2)
        pid = read_pid("assistant.pid")
        if pid_stamp("assistant.pid") != before and python_alive(pid):
            return pid
        time.sleep(0.05)
    end_process(proc, before)
    die("assistant did not become ready")


def stop_mouth():
    py = venv_python()
    return subprocess.run(
        [py, str(ROOT / "mouth.py"), "--stop"],
        cwd=str(ROOT),
        shell=False,
    ).returncode


def stop_assistant():
    py = venv_python()
    return subprocess.run(
        [py, str(ROOT / "assistant.py"), "--stop"],
        cwd=str(ROOT),
        shell=False,
    ).returncode


def cmd_stop():
    for name in ("assistant.pid", "iris.pid"):
        pid = read_pid(name)
        if pid and process_image(pid) and not python_alive(pid):
            die("refusing to stop " + name + " pid " + str(pid))
    iris_pid = read_pid("iris.pid")
    if python_alive(iris_pid):
        taskkill(iris_pid)
    assistant_code = stop_assistant()
    mouth_code = stop_mouth()
    try:
        (ROOT / "iris.pid").unlink()
    except OSError:
        pass
    print("iris: stopped", file=sys.stderr)
    if assistant_code or mouth_code:
        raise SystemExit(assistant_code or mouth_code)


def port_open():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect(("127.0.0.1", 8765))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def ensure_node(peers, quiet=False):
    import node

    py = venv_python()
    proc = None
    if not port_open():
        argv = [py, "-u", str(ROOT / "node.py")]
        stdio = {}
        log = None
        if quiet:
            log = open(ROOT / "node.run.err", "ab", buffering=0)
            stdio["stdout"] = subprocess.DEVNULL
            stdio["stderr"] = log
            stdio["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(argv, cwd=str(ROOT), shell=False, **stdio)
        finally:
            if log is not None:
                log.close()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not port_open():
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if not port_open():
            if proc.poll() is None:
                taskkill(proc.pid, tree=False)
            die("node did not start")
    reply = node.transact("127.0.0.1:8765", node.make_card("node", "hello", "hi"), 10)
    for line in ("capture closed", "op mouth.say", "op ear.listen", "op call.dial"):
        if line not in reply.body.splitlines():
            die("node is not this protocol")
    for addr in peers:
        node.transact("127.0.0.1:8765", node.make_card("node", "join", addr), 20)
    return proc


def telegram_here():
    desktop = Path(os.environ["APPDATA"]) / "Telegram Desktop"
    return (desktop / "Telegram.exe").is_file() and (desktop / "tdata").is_dir()


def call_addr(peers):
    import node

    if telegram_here():
        return "127.0.0.1:8765"
    for addr in peers:
        if not node.reachable(addr):
            continue
        reply = node.transact(addr, node.make_card("node", "hello", "hi"), 10)
        if "op call.dial" in reply.body.splitlines():
            return addr
    die("peer missing")


GREETING = (
    "Wojciech, this is Iris calling from the desk. "
    "The microphone on this computer stays closed. "
    "I am speaking through the Telegram call. "
    "Please say a full sentence after I finish, and I will answer you."
)


def duplex(seat, deadline):
    import node

    node.transact(seat, node.make_card("mouth", "say", GREETING, resource="call"), 240)
    while deadline is None or time.monotonic() < deadline:
        remain = None if deadline is None else deadline - time.monotonic()
        if remain is not None and remain < 0.4:
            return
        limit = "" if remain is None else format(remain, ".3f")
        heard_timeout = 220 if remain is None else remain + 30
        heard = node.transact(seat, node.make_card("ear", "listen", limit, resource="call"), heard_timeout)
        if not heard.body.strip():
            return
        print(heard.body, flush=True)
        if deadline is not None and time.monotonic() >= deadline:
            return
        brain_timeout = 600 if deadline is None else (deadline - time.monotonic()) + 45
        reply = node.transact(
            seat,
            node.make_card("agent", "turn", heard.body, profile="voice", resource="call"),
            brain_timeout,
        )
        print(reply.body, flush=True)
        if node.is_stop(reply.body):
            die("gemma stop")
        if deadline is not None and time.monotonic() >= deadline:
            return
        said = node.transact(seat, node.make_card("mouth", "say", reply.body, resource="call"), 240)
        if said.body.strip() != "spoken":
            die("mouth missed")
        if deadline is None:
            return


def cmd_call(seconds, peers):
    ensure_node(peers)
    import node

    seat = call_addr(peers)
    node.brain_place(peers)
    if seat != "127.0.0.1:8765":
        hello = node.transact(seat, node.make_card("node", "hello", "hi"), 10)
        if "op call.dial" not in hello.body.splitlines() or "capture closed" not in hello.body.splitlines():
            die("peer is not the call")
    print("call: seat " + seat, file=sys.stderr, flush=True)
    up = node.transact(seat, node.make_card("call", "dial", "", resource="call"), 300)
    print(up.body, flush=True)
    deadline = time.monotonic() + int(seconds) if seconds else None
    failed = None
    try:
        duplex(seat, deadline)
    except BaseException as exc:
        failed = None if deadline is not None and time.monotonic() >= deadline else exc
    hung = node.transact(seat, node.make_card("call", "hang", seconds, resource="call"), 90)
    print(hung.body, flush=True)
    if failed is not None:
        raise failed


def cmd_rest(peers):
    import io

    sink = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sink
    sys.stderr = sink
    try:
        ensure_node(peers, quiet=True)
        import gemma
        import node

        reply = node.transact("127.0.0.1:8765", node.make_card("node", "hello", "hi"), 10)
        if "call up" in reply.body.splitlines():
            die("call up")
        if node.brain_place(peers) == "":
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


def cmd_start(peers, inject):
    if already_up():
        return
    ensure_node(peers)
    py = venv_python()
    url = peers[0] if peers else ""
    if url:
        print("iris: brain " + url, file=sys.stderr, flush=True)
    else:
        print("iris: brain local", file=sys.stderr, flush=True)
    if inject is None:
        print("iris: capture closed", file=sys.stderr, flush=True)
    else:
        print("iris: inject", file=sys.stderr, flush=True)
    before = pid_stamp("assistant.pid")
    command = [
        py,
        "-u",
        str(ROOT / "assistant.py"),
        "--timeout",
        "180",
    ]
    if url:
        command.extend(["--url", url])
    if inject is not None:
        command.append("--inject")
        if inject != "-":
            command.append(inject)
    proc = subprocess.Popen(command, cwd=str(ROOT), shell=False)
    (ROOT / "iris.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
    code = 2
    announced = False
    try:
        ready_pid = wait_ready(proc, before)
        print("iris: up pid " + str(ready_pid), file=sys.stderr, flush=True)
        code = proc.wait()
    except KeyboardInterrupt:
        print("iris: stopped", file=sys.stderr)
        announced = True
        end_process(proc, before)
        stop_assistant()
        stop_mouth()
        code = 0
    finally:
        remove_pidfile("iris.pid", proc.pid)
    if code == 0 and not announced:
        print("iris: stopped", file=sys.stderr)
    raise SystemExit(code if code is not None else 2)


def main():
    os.environ["PYTHONUNBUFFERED"] = "1"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    reexec()
    command, peers, extra = parse_args(sys.argv[1:])
    if command == "stop":
        cmd_stop()
        return
    if command == "call":
        cmd_call(extra, peers)
        return
    if command == "rest":
        cmd_rest(peers)
        return
    cmd_start(peers, extra)


if __name__ == "__main__":
    main()
