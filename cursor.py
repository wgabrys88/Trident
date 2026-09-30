import ctypes
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import node

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / "cursor.status.txt"
LOCK = threading.Lock()
QUEUE = []
RUN = {"handle": None, "pid": 0, "card": None}
SETTLED = set()
KERNEL = {"lib": None}


def kernel():
    lib = KERNEL["lib"]
    if lib is not None:
        return lib
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    lib.OpenProcess.restype = ctypes.c_void_p
    lib.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.WaitForSingleObject.restype = ctypes.c_uint32
    lib.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.GetExitCodeProcess.restype = ctypes.c_int
    lib.CloseHandle.argtypes = [ctypes.c_void_p]
    lib.CloseHandle.restype = ctypes.c_int
    KERNEL["lib"] = lib
    return lib


def read_status():
    if not STATUS.is_file():
        return None
    data = {}
    for line in STATUS.read_text(encoding="utf-8").splitlines():
        key, sep, rest = line.partition(" ")
        if sep and key:
            data[key] = rest.strip()
    if "pid" not in data:
        return None
    return data


def write_status(pid, task, ident):
    body = "pid " + str(pid) + "\ntask " + task + "\ncard " + ident + "\n"
    tmp = STATUS.with_name(STATUS.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(STATUS)


def track(pid, card):
    handle = kernel().OpenProcess(0x0400 | 0x00100000, 0, int(pid))
    if not handle:
        node.die("cursor exit unseen")
    RUN["handle"] = handle
    RUN["pid"] = int(pid)
    RUN["card"] = card


def finish(code):
    card = RUN["card"]
    handle = RUN["handle"]
    SETTLED.add(RUN["pid"])
    RUN["handle"] = None
    RUN["pid"] = 0
    RUN["card"] = None
    if handle:
        kernel().CloseHandle(handle)
    if card is not None:
        node.mark(node.ROOT, card, "completed" if code == 0 else "failed")


def polled_exit():
    lib = kernel()
    handle = RUN["handle"]
    if not handle:
        node.die("cursor exit unseen")
    waited = lib.WaitForSingleObject(handle, 0)
    if waited == 0x102:
        return None
    if waited != 0:
        node.die("cursor exit unseen")
    code = ctypes.c_uint32(0)
    if not lib.GetExitCodeProcess(handle, ctypes.byref(code)):
        node.die("cursor exit unseen")
    if code.value == 259:
        return None
    return int(code.value)


def open_pid(pid):
    return kernel().OpenProcess(0x0400 | 0x00100000, 0, int(pid))


def adopt_status():
    if RUN["handle"]:
        return
    data = read_status()
    if not data:
        return
    try:
        pid = int(data["pid"])
    except ValueError:
        return
    if pid in SETTLED:
        return
    handle = open_pid(pid)
    if not handle:
        return
    ident = data.get("card") or ("cursor" + str(pid))
    card = node.make_card("tool", "call", "cursor " + data.get("task", ""), resource="cursor", ident=ident)
    RUN["handle"] = handle
    RUN["pid"] = pid
    RUN["card"] = card
    node.mark(node.ROOT, card, "running")
    code = polled_exit()
    if code is None:
        return
    finish(code)


def launch(task, card):
    agent = shutil.which("agent")
    if not agent:
        node.mark(node.ROOT, card, "failed")
        node.die("cursor missing")
    argv = [
        agent,
        "-p",
        "--force",
        "--trust",
        "--workspace",
        str(ROOT),
        "--worktree",
        "--worktree-base",
        "runner-h",
        "--model",
        "composer-2.5",
        "--output-format",
        "json",
        task + " Work on branch runner-h. Open the pull request into runner-h. Do not push main. Do not force-push.",
    ]
    proc = subprocess.Popen(
        argv,
        cwd=str(ROOT),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    write_status(proc.pid, task, card.id)
    node.mark(node.ROOT, card, "running")
    track(proc.pid, card)
    return "started local pid " + str(proc.pid)


def pump():
    if RUN["handle"] or not QUEUE:
        return
    item = QUEUE.pop(0)
    launch(item["task"], item["card"])


def _tick():
    if RUN["handle"]:
        code = polled_exit()
        if code is None:
            return
        finish(code)
    else:
        adopt_status()
    pump()


def tick():
    with LOCK:
        _tick()


def start(task):
    label = " ".join((task or "").split())
    if not label or label.startswith("-"):
        node.die("empty task")
    with LOCK:
        _tick()
        card = node.make_card("tool", "call", "cursor " + label, resource="cursor")
        if RUN["handle"]:
            node.mark(node.ROOT, card, "queued")
            QUEUE.append({"task": label, "card": card})
            return "queued " + card.id
        return launch(label, card)


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "start":
        node.die("usage: cursor.py start TASK")
    sys.stdout.write(start(" ".join(sys.argv[2:])) + "\n")


if __name__ == "__main__":
    main()
