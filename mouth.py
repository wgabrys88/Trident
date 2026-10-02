import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_KEYS = ("chatterbox.variant", "chatterbox.language", "chatterbox.play", "chatterbox.cfm-steps")
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SLOTS = (
    "mouth.pid",
    "mouth.pid.tmp",
    "mouth.stop",
    "mouth.prompt.txt",
    "mouth.response.txt",
    "mouth.prompt.txt.tmp",
    "mouth.response.txt.tmp",
)
LOCK_NAME = "mouth.lock"
Resident = namedtuple("Resident", ("pid", "variant", "lang", "fingerprint", "state"))

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


_PL = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"


def die(message):
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


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


def check_voice(model, lang):
    if model in ("nano", "turbo"):
        if lang != "en":
            die(model + " speaks en")
        return
    if model == "v3":
        if lang == "en":
            die("english uses nano or turbo")
        if not lang or any(ch.isspace() for ch in lang):
            die("v3 asks for a language tag")
        return
    die("unknown model")


def cfm_steps(model):
    return "5" if model == "v3" else "2"


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
        "chatterbox.cfm-steps " + cfm_steps(model) + "\n"
        "chatterbox.play " + play + "\n"
        "chatterbox.text <<\n"
        + block
        + "\n<<\n"
    )
    return body


def write_mouth(payload):
    try:
        (ROOT / "mouth.txt").write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write mouth.txt: " + str(exc))


def vacant(path):
    if not path.exists():
        return path
    for n in range(1000):
        cand = path.with_name(path.stem + "-" + str(n) + path.suffix)
        if not cand.exists():
            return cand
    die("output names exhausted")


def adopt_output(tmp, out_txt):
    name = out_txt.read_text(encoding="utf-8").strip()
    if not name or name != Path(name).name:
        die("bad wav name from chatterbox")
    src = tmp / name
    if not src.is_file():
        die("missing wav named by chatterbox: " + name)
    dest = vacant(ROOT / name)
    shutil.move(str(src), str(dest))
    try:
        vacant(ROOT / out_txt.name).write_text(dest.name + "\n", encoding="utf-8")
    except OSError as exc:
        die("cannot write chatterbox output text: " + str(exc))
    return dest


def model_text(text, lang):
    spoken = " ".join((text or "").split())
    tag = (lang or "").split("-")[0].lower()
    if tag == "en" or not tag:
        return spoken
    return unicodedata.normalize("NFKD", spoken.lower())


def synthesize(model, lang, sentence):
    check_voice(model, lang)
    sentence = model_text(sentence, lang)
    write_mouth(settings_text(model, lang, sentence, "off"))
    path = ROOT / "mouth.txt"
    tmp = Path(tempfile.mkdtemp(prefix="trident-mouth-once-"))
    try:
        try:
            completed = subprocess.run(
                [str(ROOT / "chatterbox.exe"), str(path)],
                cwd=str(tmp),
                shell=False,
            )
        except OSError as exc:
            die("cannot run chatterbox.exe: " + str(exc))
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        new_files = sorted(tmp.glob("*_chatterbox_out_*.txt"))
        if not new_files:
            die("chatterbox wrote no output text")
        if len(new_files) != 1:
            die("chatterbox wrote more than one output text")
        return adopt_output(tmp, new_files[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove_file(name):
    path = ROOT / name
    for _ in range(50):
        try:
            if path.is_file():
                path.unlink()
            return
        except OSError:
            time.sleep(0.05)
    if path.is_file():
        die("cannot remove " + name)


def hex_fingerprint(text):
    if len(text) != 64:
        return False
    for ch in text:
        if ch not in "0123456789abcdef":
            return False
    return True


def resident_record():
    path = ROOT / "mouth.pid"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) not in (3, 5):
        return None
    try:
        pid = int(lines[0].strip())
    except ValueError:
        return None
    if pid <= 0:
        return None
    variant = lines[1].strip()
    lang = lines[2].strip()
    if not variant or not lang:
        return None
    if any(ch.isspace() for ch in variant) or any(ch.isspace() for ch in lang):
        return None
    if len(lines) == 3:
        return Resident(pid, variant, lang, "", "")
    fingerprint = lines[3].strip()
    state = lines[4].strip()
    if not hex_fingerprint(fingerprint) or state not in ("loading", "ready"):
        return None
    return Resident(pid, variant, lang, fingerprint, state)


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


def wait_dead(pid, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not chatterbox_running(pid):
            return True
        time.sleep(0.05)
    return not chatterbox_running(pid)


def stop_resident():
    try:
        (ROOT / "mouth.stop").write_bytes(b"stop\n")
    except OSError as exc:
        die("cannot write mouth.stop: " + str(exc))
    stopped = False
    rec = resident_record()
    if rec is not None and chatterbox_running(rec.pid):
        stopped = True
        terminate_pid(rec.pid)
        if not wait_dead(rec.pid, 5):
            die("cannot stop chatterbox pid " + str(rec.pid))
    for name in SLOTS:
        remove_file(name)
    remove_file(LOCK_NAME)
    return stopped


def chatterbox_running_any():
    rec = resident_record()
    return rec is not None and chatterbox_running(rec.pid)


def language_of(text):
    for ch in text or "":
        if ch in _PL:
            return "pl"
    return "en"


def main():
    parser = argparse.ArgumentParser(prog="mouth.py")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if not args.stop:
        die("usage: mouth.py --stop")
    os.chdir(ROOT)
    if stop_resident():
        print("mouth: chatterbox stopped", file=sys.stderr)
    else:
        print("mouth: chatterbox not running", file=sys.stderr)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
