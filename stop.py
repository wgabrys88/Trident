"""Iris closed-mic stop.

Stops the inject process started by start.py and runs mouth.py --stop.
Does not bind, probe, or restart port 8765.
"""

import ctypes
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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


def main():
    reexec()
    if len(sys.argv) != 1:
        die("usage: stop.py")
    for name in ("assistant.pid", "iris.pid"):
        pid = read_pid(name)
        if pid and process_image(pid) and not python_alive(pid):
            die("refusing to stop " + name + " pid " + str(pid))
    iris_pid = read_pid("iris.pid")
    if python_alive(iris_pid):
        taskkill(iris_pid)
    py = venv_python()
    assistant_code = subprocess.run(
        [py, str(ROOT / "assistant.py"), "--stop"],
        cwd=str(ROOT),
        shell=False,
    ).returncode
    mouth_code = subprocess.run(
        [py, str(ROOT / "mouth.py"), "--stop"],
        cwd=str(ROOT),
        shell=False,
    ).returncode
    try:
        (ROOT / "iris.pid").unlink()
    except OSError:
        pass
    print("iris: stopped", file=sys.stderr)
    if assistant_code or mouth_code:
        raise SystemExit(assistant_code or mouth_code)


if __name__ == "__main__":
    main()
