# Shared file-watch helpers for Trident residents. No CLI flags on the exes.

import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "trident.txt"


def read_cfg():
    out = {}
    lines = CFG.read_text(encoding="utf-8-sig").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.endswith("<<"):
            key = line[:-2].strip()
            body = []
            while i < len(lines) and lines[i] != "<<":
                body.append(lines[i])
                i += 1
            i += 1
            out[key] = "\n".join(body)
            continue
        if " " not in line:
            out[line] = ""
        else:
            key, value = line.split(" ", 1)
            out[key] = value
    return out


def closed_text(path: Path):
    if not path.exists():
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        return None
    kernel32.CloseHandle(handle)
    return path.read_text(encoding="utf-8")


def wait_pid(name: str, proc, timeout=180):
    pid = ROOT / (name + ".pid")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pid.exists() and pid.stat().st_size > 0:
            return
        if proc.poll() is not None:
            raise SystemExit(name + " exited before watching")
        time.sleep(0.2)
    raise SystemExit(name + " pid timeout")


def stop_resident(name: str, proc):
    (ROOT / (name + ".stop")).write_text("1", encoding="ascii")
    proc.wait()


def start_resident(exe: str, name: str):
    proc = subprocess.Popen(
        [str(ROOT / exe)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_pid(name, proc)
    return proc


def unload_slot(unload_key: str, exe: str):
    lines = CFG.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        if line.startswith(unload_key + " "):
            out.append(unload_key + " on")
        else:
            out.append(line)
    CFG.write_text("\n".join(out) + "\n", encoding="utf-8")
    subprocess.run([str(ROOT / exe)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    restored = []
    for line in lines:
        restored.append(line)
    CFG.write_text("\n".join(restored) + "\n", encoding="utf-8")


def unload_all():
    for name, key, exe in (
        ("gemma", "gemma.unload", "gemma-brain.exe"),
        ("chatterbox", "chatterbox.unload", "chatterbox.exe"),
        ("ear", "ear.unload", "ear.exe"),
    ):
        if (ROOT / f"{name}.pid").exists():
            unload_slot(key, exe)


def gemma_ask(proc, prompt: str) -> str:
    req = ROOT / "gemma.prompt.txt"
    resp = ROOT / "gemma.response.txt"
    try:
        resp.unlink(missing_ok=True)
    except OSError:
        pass
    started = time.time()
    req.write_text(prompt, encoding="utf-8")
    while time.time() < started + 600:
        if resp.exists() and resp.stat().st_mtime >= started - 1:
            text = closed_text(resp)
            if text is not None:
                return text
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    raise SystemExit("gemma empty reply")


def mouth_speak(proc, text: str):
    prompt = ROOT / "chatterbox.prompt.txt"
    wav = ROOT / "chatterbox.response.wav"
    reply = ROOT / "chatterbox.response.txt"
    try:
        wav.unlink(missing_ok=True)
        reply.unlink(missing_ok=True)
    except OSError:
        pass
    started = time.time()
    prompt.write_text(text, encoding="utf-8")
    while time.time() < started + 300:
        if closed_text(reply) is not None and wav.exists() and wav.stat().st_size > 44 and wav.stat().st_mtime >= started - 1:
            return wav
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    raise SystemExit("mouth missing wav")
