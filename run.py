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

python run.py call
    Quit Telegram Desktop, place one voice call, then restart Desktop.
    The microphone stays closed. Speech goes through the call.

Spoken shutdown is a Gemma stop tool call, not a keyword.
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


def parse_args(argv):
    usage = "usage: run.py start [--url HOST:PORT] [inject [PATH]] | run.py stop | run.py call"
    if not argv:
        die(usage)
    if argv == ["call"]:
        return "call", "", None
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


def taskkill(pid):
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
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
    if not port_open():
        subprocess.Popen(
            [py, "-u", str(ROOT / "node.py")],
            cwd=str(ROOT),
            shell=False,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not port_open():
            time.sleep(0.05)
        if not port_open():
            die("node did not start")
    reply = node.transact("127.0.0.1:8765", node.make_card("node", "hello", "hi"), 10)
    for line in ("capture closed", "op mouth.say", "op ear.listen", "op call.dial"):
        if line not in reply.body.splitlines():
            die("node is not this protocol")


def cmd_call():
    ensure_node()
    import node

    greeting = (
        "Wojciech, this is Iris calling from the desk. "
        "The microphone on this computer stays closed. "
        "I am speaking through the Telegram call. "
        "Please say a full sentence after I finish, and I will write your words down."
    )
    up = node.transact("127.0.0.1:8765", node.make_card("call", "dial", "", resource="call"), 300)
    print(up.body, flush=True)
    failed = None
    try:
        node.transact("127.0.0.1:8765", node.make_card("mouth", "say", greeting, resource="call"), 240)
        heard = node.transact("127.0.0.1:8765", node.make_card("ear", "listen", "", resource="call"), 220)
        print(heard.body, flush=True)
        reply = "I heard you. " + " ".join(heard.body.split())
        node.transact("127.0.0.1:8765", node.make_card("mouth", "say", reply, resource="call"), 240)
    except SystemExit as exc:
        failed = exc
    hung = node.transact("127.0.0.1:8765", node.make_card("call", "hang", "", resource="call"), 90)
    print(hung.body, flush=True)
    if failed is not None:
        raise failed


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
    reexec()
    command, url, inject = parse_args(sys.argv[1:])
    if command == "stop":
        cmd_stop()
        return
    if command == "call":
        cmd_call()
        return
    cmd_start(url, inject)


if __name__ == "__main__":
    main()
