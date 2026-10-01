"""Iris voice.

python run.py start [--url HOST:PORT]
    Start the node on port 8765 when it is down, then the assistant.
    The microphone stays closed.
    HOST:PORT is one peer. TRIDENT_PEERS supplies it when --url is omitted.

python run.py start inject [PATH]
    Simulated turns. PATH is one turn per line, or blocks split by a line
    that is only ---. No PATH reads stdin. No microphone.

python run.py stop
    Stop this organism and the mouth. Leave port 8765 listening.

python run.py call [SECONDS]
    Quit Telegram Desktop, place one voice call, then restart Desktop.
    The microphone stays closed. Each heard sentence is one voice card.
    Resident Gemma answers in text. That sentence is spoken into the call.
    SECONDS keeps the duplex for that long, then hangs up and stops the
    node, Telethon, and any Cursor child this call started.
    TRIDENT_PEERS is the other seat when the call or the brain is not here.

Spoken shutdown is a Gemma stop tool call, not a keyword.
"""

import ctypes
import os
import socket
import subprocess
import sys
import threading
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


def parse_args(argv):
    usage = "usage: run.py start [--url HOST:PORT] [inject [PATH]] | run.py stop | run.py call [SECONDS]"
    if not argv:
        die(usage)
    if argv[0] == "call":
        if len(argv) == 1:
            return "call", "", None
        if len(argv) == 2 and argv[1].isdigit() and int(argv[1]) > 0:
            return "call", argv[1], None
        die("usage: run.py call [SECONDS]")
    command = argv[0]
    if command == "stop":
        if len(argv) != 1:
            die("usage: run.py stop")
        return "stop", "", None
    if command != "start":
        die(usage)
    url = ""
    inject = None
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--url":
            if index + 1 >= len(argv):
                die("empty url")
            value = argv[index + 1].strip()
            if not value:
                die("empty url")
            if url:
                die(usage)
            url = value
            index += 2
            continue
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
    if not url:
        peers = os.environ.get("TRIDENT_PEERS", "").strip()
        if peers:
            url = peers.split(",")[0].strip()
    if url.startswith("http://") or url.startswith("https://"):
        die("peer is host:port")
    if url:
        host, sep, port = url.rpartition(":")
        if sep != ":" or not host or not port.isdigit() or not (1 <= int(port) <= 65535):
            die("peer is host:port")
    return "start", url, inject


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


def ensure_node():
    import node

    py = venv_python()
    proc = None
    if not port_open():
        proc = subprocess.Popen(
            [py, "-u", str(ROOT / "node.py")],
            cwd=str(ROOT),
            shell=False,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not port_open():
            time.sleep(0.05)
        if not port_open():
            if proc.poll() is None:
                taskkill(proc.pid, tree=False)
            die("node did not start")
    reply = node.transact("127.0.0.1:8765", node.make_card("node", "hello", "hi"), 10)
    for line in ("capture closed", "op mouth.say", "op ear.listen", "op call.dial"):
        if line not in reply.body.splitlines():
            die("node is not this protocol")
    return proc


def peer_addr():
    raw = os.environ.get("TRIDENT_PEERS", "").strip()
    if not raw:
        die("peer missing")
    addr = raw.split(",")[0].strip()
    if addr.startswith("http://") or addr.startswith("https://"):
        die("peer is host:port")
    host, sep, port = addr.rpartition(":")
    if sep != ":" or not host or not port.isdigit() or not (1 <= int(port) <= 65535):
        die("peer is host:port")
    return addr


def telegram_here():
    desktop = Path(os.environ["APPDATA"]) / "Telegram Desktop"
    return (desktop / "Telegram.exe").is_file() and (desktop / "tdata").is_dir()


def call_addr():
    if telegram_here():
        return "127.0.0.1:8765"
    return peer_addr()


def brain_addr():
    import node

    if node.engine_here("gemma"):
        return "127.0.0.1:8765"
    addr = peer_addr()
    reply = node.transact(addr, node.make_card("node", "hello", "hi"), 10)
    rows = reply.body.splitlines()
    cuda = ""
    for line in rows:
        if line.startswith("cuda "):
            cuda = line[5:]
    if "brain gemma" not in rows or cuda == "" or cuda == "none":
        die("brain missing gemma")
    return addr


GREETING = (
    "Wojciech, this is Iris calling from the desk. "
    "The microphone on this computer stays closed. "
    "I am speaking through the Telegram call. "
    "Please say a full sentence after I finish, and I will answer you."
)


def cursor_pid():
    path = ROOT / "cursor.status.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        if line.startswith("pid "):
            text = line[4:].strip()
            if text.isdigit():
                return int(text)
    return 0


def cut_gemma(deadline, done):
    remain = deadline - time.monotonic()
    if remain > 0:
        done.wait(remain)
    if done.is_set() or done.wait(3):
        return
    import gemma

    if gemma.brain_running_any():
        gemma.stop_resident()


def process_rows():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class ENTRY(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_int32),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ENTRY)]
    kernel.Process32FirstW.restype = ctypes.c_int
    kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ENTRY)]
    kernel.Process32NextW.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    try:
        entry = ENTRY()
        entry.dwSize = ctypes.sizeof(ENTRY)
        rows = []
        ok = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            ok = kernel.Process32NextW(snap, ctypes.byref(entry))
        return rows
    finally:
        kernel.CloseHandle(snap)


def stop_spawned(root_pid):
    kids = {}
    names = {}
    for pid, parent, name in process_rows():
        names[pid] = name.lower()
        kids.setdefault(parent, []).append(pid)
    doomed = []
    seen = set()
    stack = list(kids.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if names.get(pid) == "telegram.exe":
            continue
        stack.extend(kids.get(pid, []))
        doomed.append(pid)
    for pid in reversed(doomed):
        if process_image(pid):
            taskkill(pid, tree=False)
    if process_image(root_pid):
        taskkill(root_pid, tree=False)


def session_end(before, node_proc):
    gemma_before, mouth_before, cursor_before = before
    cursor_now = cursor_pid()
    if cursor_now and cursor_now != cursor_before:
        if process_image(cursor_now):
            taskkill(cursor_now)
        try:
            (ROOT / "cursor.status.txt").unlink()
        except OSError:
            pass
    if read_pid("gemma.pid") not in (0, gemma_before):
        import gemma

        gemma.stop_resident()
    if read_pid("mouth.pid") not in (0, mouth_before):
        import mouth

        mouth.stop_resident()
    if node_proc is not None:
        stop_spawned(node_proc.pid)
        try:
            node_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def duplex(seat, brain, deadline):
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
            brain,
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


def cmd_call(seconds):
    before = (read_pid("gemma.pid"), read_pid("mouth.pid"), cursor_pid())
    node_proc = ensure_node()
    done = threading.Event()
    watch = None
    try:
        import node

        seat = call_addr()
        brain = brain_addr()
        if seat != "127.0.0.1:8765":
            hello = node.transact(seat, node.make_card("node", "hello", "hi"), 10)
            if "op call.dial" not in hello.body.splitlines() or "capture closed" not in hello.body.splitlines():
                die("peer is not the call")
        print("call: seat " + seat + " brain " + brain, file=sys.stderr, flush=True)
        up = node.transact(seat, node.make_card("call", "dial", "", resource="call"), 300)
        print(up.body, flush=True)
        deadline = None
        if seconds:
            deadline = time.monotonic() + int(seconds)
            watch = threading.Thread(target=cut_gemma, args=(deadline, done), daemon=True)
            watch.start()
        failed = None
        try:
            duplex(seat, brain, deadline)
        except BaseException as exc:
            failed = None if deadline is not None and time.monotonic() >= deadline else exc
        finally:
            done.set()
            if watch is not None:
                watch.join()
        hung = node.transact(seat, node.make_card("call", "hang", seconds, resource="call"), 90)
        print(hung.body, flush=True)
        if failed is not None:
            raise failed
    finally:
        if seconds:
            session_end(before, node_proc)


def cmd_start(url, inject):
    if already_up():
        return
    ensure_node()
    py = venv_python()
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
        command.extend(["--nvidia", "--url", url])
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
    command, url, inject = parse_args(sys.argv[1:])
    if command == "stop":
        cmd_stop()
        return
    if command == "call":
        cmd_call(url)
        return
    cmd_start(url, inject)


if __name__ == "__main__":
    main()
