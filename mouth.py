"""Mouth speak entry. Synthesize each TEXT with chatterbox.exe; play on Speakers with one-chunk overlap.

The first call starts chatterbox.exe --resident and leaves it loaded. Later calls reuse that process.
mouth.py --once keeps the old one-shot process. mouth.py --stop shuts the resident down.
"""

import argparse
import concurrent.futures
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ("nano", "turbo", "v3")
DROP_KEYS = ("chatterbox.variant", "chatterbox.language", "chatterbox.play")
SND_FILENAME = 0x00020000
SND_NODEFAULT = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SLOTS = (
    "mouth.pid",
    "mouth.stop",
    "mouth.prompt.txt",
    "mouth.response.txt",
    "mouth.prompt.txt.tmp",
    "mouth.response.txt.tmp",
)

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


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


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
            if key == "chatterbox.text":
                index += 1
                while index < len(lines) and lines[index] != "<<":
                    index += 1
                if index < len(lines):
                    index += 1
                continue
        key = stripped.split(" ", 1)[0]
        if key in DROP_KEYS or key == "chatterbox.text":
            index += 1
            continue
        kept.append(line)
        index += 1
    return kept


def without_spoken_text(raw):
    lines = physical_lines(raw)
    if lines and lines[-1] == "":
        lines = lines[:-1]
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip(" \t")
        if len(stripped) >= 2 and stripped.endswith("<<"):
            key = stripped[:-2].rstrip(" \t")
            if key == "chatterbox.text":
                index += 1
                while index < len(lines) and lines[index] != "<<":
                    index += 1
                if index < len(lines):
                    index += 1
                continue
        if stripped.split(" ", 1)[0] == "chatterbox.text":
            index += 1
            continue
        kept.append(line)
        index += 1
    return "\n".join(kept)


def settings_text(model, lang, sentence, play):
    source = ROOT / "chatterbox.txt"
    if not source.is_file():
        die("missing chatterbox.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body:
        body += "\n"
    block = "\n".join(physical_lines(sentence))
    body += (
        "chatterbox.variant " + model + "\n"
        "chatterbox.language " + lang + "\n"
        "chatterbox.play " + play + "\n"
        "chatterbox.text <<\n"
        + block
        + "\n<<\n"
    )
    return body


def play_wav(path):
    path = Path(path).resolve()
    if not path.is_file():
        die("missing wav: " + str(path))
    ok = ctypes.windll.winmm.PlaySoundW(str(path), None, SND_FILENAME | SND_NODEFAULT)
    if not ok:
        die("PlaySoundW failed: " + str(path))


def synthesize(model, lang, sentence):
    mouth = ROOT / "mouth.txt"
    payload = settings_text(model, lang, sentence, "off")
    try:
        mouth.write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write mouth.txt: " + str(exc))
    before = set(ROOT.glob("*_chatterbox_out_*.txt"))
    try:
        completed = subprocess.run(
            [".\\chatterbox.exe", "mouth.txt"],
            cwd=ROOT,
            shell=False,
        )
    except OSError as exc:
        die("cannot run chatterbox.exe: " + str(exc))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    after = set(ROOT.glob("*_chatterbox_out_*.txt"))
    new_files = sorted(after - before)
    if not new_files:
        die("chatterbox wrote no output text")
    out_txt = new_files[-1]
    name = out_txt.read_text(encoding="utf-8").strip()
    if not name:
        die("empty chatterbox output text")
    wav = ROOT / name
    if not wav.is_file():
        die("missing wav named by chatterbox: " + name)
    return wav


def remove_file(name):
    path = ROOT / name
    try:
        if path.is_file():
            path.unlink()
    except OSError as exc:
        die("cannot remove " + name + ": " + str(exc))


def resident_record():
    path = ROOT / "mouth.pid"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) < 3:
        return None
    try:
        pid = int(lines[0].strip())
    except ValueError:
        return None
    if pid <= 0:
        return None
    return pid, lines[1].strip(), lines[2].strip()


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


def chatterbox_running(pid):
    image = process_image(pid)
    return bool(image) and Path(image).name.lower() == "chatterbox.exe"


def terminate_pid(pid):
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE, 0, pid)
    if not handle:
        return
    try:
        _kernel32.TerminateProcess(handle, 1)
    finally:
        _kernel32.CloseHandle(handle)


def log_tail():
    path = ROOT / "mouth.run.err"
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(data) > 2000:
        data = data[-2000:]
    return data


def wait_dead(pid, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not chatterbox_running(pid):
            return True
        time.sleep(0.05)
    return not chatterbox_running(pid)


def stop_resident():
    rec = resident_record()
    if rec is None or not chatterbox_running(rec[0]):
        for name in SLOTS:
            remove_file(name)
        return False
    try:
        (ROOT / "mouth.stop").write_bytes(b"stop\n")
    except OSError as exc:
        die("cannot write mouth.stop: " + str(exc))
    if not wait_dead(rec[0], 120):
        terminate_pid(rec[0])
        if not wait_dead(rec[0], 5):
            die("cannot stop chatterbox pid " + str(rec[0]))
    for name in SLOTS:
        remove_file(name)
    return True


def spawn_resident():
    exe = ROOT / "chatterbox.exe"
    if not exe.is_file():
        die("missing chatterbox.exe")
    log_path = ROOT / "mouth.run.err"
    try:
        log = open(log_path, "wb", buffering=0)
    except OSError as exc:
        die("cannot write mouth.run.err: " + str(exc))
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        try:
            proc = subprocess.Popen(
                [str(exe), "--resident", "mouth.txt"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                creationflags=flags | breakaway,
                close_fds=True,
            )
        except OSError:
            proc = subprocess.Popen(
                [str(exe), "--resident", "mouth.txt"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                creationflags=flags,
                close_fds=True,
            )
    except OSError as exc:
        log.close()
        die("cannot run chatterbox.exe: " + str(exc))
    log.close()
    print("mouth: starting chatterbox", file=sys.stderr)
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc.poll() is not None:
            die("chatterbox exited " + str(proc.returncode) + "\n" + log_tail())
        rec = resident_record()
        if rec and rec[0] == proc.pid and chatterbox_running(rec[0]):
            print("mouth: chatterbox pid " + str(rec[0]) + " resident", file=sys.stderr)
            return rec[0]
        time.sleep(0.05)
    die("chatterbox did not become ready\n" + log_tail())


def ensure_resident(model, lang):
    if any(ch.isspace() for ch in lang):
        die("language must be a single tag")
    desired = settings_text(model, lang, "", "off")
    desired_sig = without_spoken_text(desired)
    rec = resident_record()
    alive = rec is not None and chatterbox_running(rec[0])
    disk_ok = False
    mouth = ROOT / "mouth.txt"
    if mouth.is_file():
        try:
            disk_ok = without_spoken_text(mouth.read_text(encoding="utf-8-sig")) == desired_sig
        except OSError:
            disk_ok = False
    if alive and disk_ok and rec[1] == model and rec[2] == lang:
        print("mouth: chatterbox pid " + str(rec[0]) + " resident", file=sys.stderr)
        return rec[0]
    stop_resident()
    try:
        mouth.write_bytes(desired.encode("utf-8"))
    except OSError as exc:
        die("cannot write mouth.txt: " + str(exc))
    for name in SLOTS:
        remove_file(name)
    return spawn_resident()


def resident_say(pid, text):
    prompt = ROOT / "mouth.prompt.txt"
    response = ROOT / "mouth.response.txt"
    deadline = time.time() + 180
    while prompt.is_file():
        if time.time() > deadline:
            die("mouth prompt still pending")
        if not chatterbox_running(pid):
            die("chatterbox exited\n" + log_tail())
        time.sleep(0.02)
    if response.is_file():
        try:
            response.unlink()
        except OSError as exc:
            die("cannot remove mouth.response.txt: " + str(exc))
    ident = str(time.time_ns())
    tmp = ROOT / "mouth.prompt.txt.tmp"
    try:
        tmp.write_bytes((ident + "\n" + text).encode("utf-8"))
        tmp.replace(prompt)
    except OSError as exc:
        die("cannot write mouth.prompt.txt: " + str(exc))
    deadline = time.time() + 180
    while time.time() < deadline:
        if response.is_file():
            try:
                raw = response.read_bytes()
            except OSError:
                time.sleep(0.02)
                continue
            try:
                raw_text = raw.decode("utf-8")
            except UnicodeError:
                die("chatterbox response is not utf-8")
            if "\n" not in raw_text:
                time.sleep(0.02)
                continue
            head, tail = raw_text.split("\n", 1)
            if head.strip() != ident:
                time.sleep(0.02)
                continue
            try:
                response.unlink()
            except OSError:
                pass
            line = tail.strip()
            if line.startswith("ok "):
                name = line[3:].strip()
                if not name or "/" in name or "\\" in name:
                    die("bad wav name from chatterbox")
                wav = ROOT / name
                if not wav.is_file():
                    die("missing wav named by chatterbox: " + name)
                return wav
            die("chatterbox: " + (line or "empty response"))
        if not chatterbox_running(pid):
            die("chatterbox exited\n" + log_tail())
        time.sleep(0.02)
    die("chatterbox timed out")


def speak_chunks(synthesize_one, chunks):
    ready = synthesize_one(chunks[0])
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        for index, sentence in enumerate(chunks):
            play_future = pool.submit(play_wav, ready)
            if index + 1 < len(chunks):
                ready = synthesize_one(chunks[index + 1])
            play_future.result()


def main():
    parser = argparse.ArgumentParser(prog="mouth.py")
    parser.add_argument("text", nargs="*")
    parser.add_argument("--model", default="nano")
    parser.add_argument("--lang", default=None)
    parser.add_argument("--once", action="store_true", help="one-shot chatterbox.exe, then exit")
    parser.add_argument("--stop", action="store_true", help="stop the resident chatterbox.exe")
    args = parser.parse_args()
    if args.stop:
        if args.once or args.text or args.lang is not None:
            die("usage: mouth.py --stop")
        os.chdir(ROOT)
        if stop_resident():
            print("mouth: chatterbox stopped", file=sys.stderr)
        else:
            print("mouth: chatterbox not running", file=sys.stderr)
        raise SystemExit(0)
    if not args.text:
        die("usage: mouth.py [--model nano|turbo|v3] [--lang TAG] [--once] TEXT [TEXT ...]")
    if args.model not in MODELS:
        die("unknown model")
    if args.lang is None:
        lang = "pl" if args.model == "v3" else "en"
    elif args.lang.strip() == "":
        die("empty language")
    else:
        lang = args.lang
    for sentence in args.text:
        if sentence.strip() == "":
            die("empty text")
        if any(line == "<<" for line in physical_lines(sentence)):
            die("text line is only <<")

    os.chdir(ROOT)
    chunks = list(args.text)
    if args.once:
        speak_chunks(lambda sentence: synthesize(args.model, lang, sentence), chunks)
    else:
        pid = ensure_resident(args.model, lang)
        speak_chunks(lambda sentence: resident_say(pid, sentence), chunks)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
