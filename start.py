"""Iris closed-mic start.

Runs `assistant.py --nvidia --timeout 180 --inject` and waits.
No microphone and no VAD. Does not bind, stop, or restart port 8765.

Peer URL: `--url`, else `TRIDENT_NVIDIA_URL`, else `http://192.168.16.31:8765/`.
Type a turn, then a line that is only `---`.
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


def brain_url(argv):
    url = ""
    index = 0
    while index < len(argv):
        if argv[index] != "--url":
            die("usage: start.py [--url URL]")
        if index + 1 >= len(argv):
            die("empty url")
        value = argv[index + 1].strip()
        if not value:
            die("empty url")
        if url:
            die("usage: start.py [--url URL]")
        url = value
        index += 2
    if not url:
        url = os.environ.get("TRIDENT_NVIDIA_URL", "").strip() or DEFAULT_URL
    if not (url.startswith("http://") or url.startswith("https://")):
        die("url must start with http:// or https://")
    return url


def already_up():
    for name in ("assistant.pid", "iris.pid"):
        pid = read_pid(name)
        if python_alive(pid):
            print("iris: already up pid " + str(pid), file=sys.stderr)
            return True
    return False


def wait_ready(proc):
    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            code = proc.returncode
            raise SystemExit(code if code is not None else 2)
        if read_pid("assistant.pid") == proc.pid:
            return
        time.sleep(0.05)
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    die("inject did not become ready")


def main():
    os.environ["PYTHONUNBUFFERED"] = "1"
    reexec()
    url = brain_url(sys.argv[1:])
    if already_up():
        return
    py = venv_python()
    print("iris: brain " + url, file=sys.stderr, flush=True)
    proc = subprocess.Popen(
        [
            py,
            "-u",
            str(ROOT / "assistant.py"),
            "--nvidia",
            "--url",
            url,
            "--timeout",
            "180",
            "--inject",
        ],
        cwd=str(ROOT),
        shell=False,
    )
    (ROOT / "iris.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
    code = 2
    try:
        wait_ready(proc)
        print("iris: up pid " + str(proc.pid), file=sys.stderr, flush=True)
        code = proc.wait()
    except KeyboardInterrupt:
        print("iris: stopped", file=sys.stderr)
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                cwd=str(ROOT),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
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
