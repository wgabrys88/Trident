"""Iris start.

Default runs `assistant.py --nvidia --timeout 180 --inject` and waits.
No microphone and no VAD.

`--live` runs `assistant.py --nvidia --timeout 180` on the default mic.
Resident VAD, free speech, brain POST, mouth. No canned phrase.

`--live --cue` holds the mic closed until the speak cue:
one beep, wait 10 seconds, two beeps, then capture.
Three beeps after the turn. `--once` and `--cue` require `--live`.

Does not bind, stop, or restart port 8765.

Peer URL: `--url`, else `TRIDENT_NVIDIA_URL`, else `http://192.168.16.31:8765/`.
Inject: type a turn, then a line that is only `---`.
A turn that is only `quit`, `exit`, or `stop` ends the loop.
`python stop.py` stops this process and the mouth. It does not contact the brain.
"""

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_URL = "http://192.168.16.31:8765/"
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


def parse_start_args(argv):
    url = ""
    live = False
    once = False
    cue = False
    index = 0
    usage = "usage: start.py [--url URL] [--live] [--cue] [--once]"
    while index < len(argv):
        arg = argv[index]
        if arg == "--live":
            if live:
                die(usage)
            live = True
            index += 1
            continue
        if arg == "--cue":
            if cue:
                die(usage)
            cue = True
            index += 1
            continue
        if arg == "--once":
            if once:
                die(usage)
            once = True
            index += 1
            continue
        if arg != "--url":
            die(usage)
        if index + 1 >= len(argv):
            die("empty url")
        value = argv[index + 1].strip()
        if not value:
            die("empty url")
        if url:
            die(usage)
        url = value
        index += 2
    if once and not live:
        die(usage)
    if cue and not live:
        die(usage)
    if not url:
        url = os.environ.get("TRIDENT_NVIDIA_URL", "").strip() or DEFAULT_URL
    if not (url.startswith("http://") or url.startswith("https://")):
        die("url must start with http:// or https://")
    return url, live, once, cue


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
    # venv python.exe is a redirector. taskkill /T stops the inject child too.
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
    # assistant.pid is the child interpreter, not proc.pid.
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


def main():
    os.environ["PYTHONUNBUFFERED"] = "1"
    reexec()
    url, live, once, cue = parse_start_args(sys.argv[1:])
    if already_up():
        return
    py = venv_python()
    print("iris: brain " + url, file=sys.stderr, flush=True)
    if live:
        print("iris: live mic", file=sys.stderr, flush=True)
    before = pid_stamp("assistant.pid")
    command = [
        py,
        "-u",
        str(ROOT / "assistant.py"),
        "--nvidia",
        "--url",
        url,
        "--timeout",
        "180",
    ]
    if not live:
        command.append("--inject")
    elif cue:
        command.append("--cue")
    if once:
        command.append("--once")
    proc = subprocess.Popen(
        command,
        cwd=str(ROOT),
        shell=False,
    )
    (ROOT / "iris.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
    code = 2
    try:
        ready_pid = wait_ready(proc, before)
        print("iris: up pid " + str(ready_pid), file=sys.stderr, flush=True)
        code = proc.wait()
    except KeyboardInterrupt:
        print("iris: stopped", file=sys.stderr)
        end_process(proc, before)
        subprocess.run(
            [py, str(ROOT / "assistant.py"), "--stop"],
            cwd=str(ROOT),
            shell=False,
        )
        subprocess.run(
            [py, str(ROOT / "mouth.py"), "--stop"],
            cwd=str(ROOT),
            shell=False,
        )
        code = 0
    finally:
        remove_pidfile("iris.pid", proc.pid)
    raise SystemExit(code if code is not None else 2)


if __name__ == "__main__":
    main()
