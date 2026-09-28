import argparse
import concurrent.futures
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import namedtuple
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


def hostapi_name(sd, info):
    try:
        return sd.query_hostapis(int(info["hostapi"]))["name"]
    except Exception:
        return ""


def prefer_wasapi(matches, sd):
    ranked = []
    for index, info in matches:
        api = hostapi_name(sd, info).lower()
        if "wasapi" in api:
            rank = 0
        elif "directsound" in api:
            rank = 1
        elif "mme" in api:
            rank = 2
        else:
            rank = 3
        ranked.append((rank, index, info))
    ranked.sort(key=lambda item: (item[0], item[1]))
    index, info = ranked[0][1], ranked[0][2]
    return index, info["name"]


def cable_playback_hits(devices):
    hits = []
    for index, info in enumerate(devices):
        if info["max_output_channels"] < 1:
            continue
        name = info["name"].lower()
        if "cable input" not in name or "vb-audio" not in name or "16ch" in name:
            continue
        hits.append((index, info))
    virtual = [item for item in hits if "virtual" in item[1]["name"].lower()]
    return virtual or hits


def load_wav_mono(path):
    import wave

    import numpy as np

    path = Path(path)
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width == 2:
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        pcm = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        die("unsupported wav width: " + str(width))
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(pcm, dtype=np.float32), rate


def resample_linear(pcm, src_rate, dst_rate):
    import numpy as np

    pcm = np.asarray(pcm, dtype=np.float32)
    if src_rate == dst_rate or len(pcm) == 0:
        return pcm
    new_len = max(1, int(round(len(pcm) * float(dst_rate) / float(src_rate))))
    if len(pcm) == 1:
        return np.full(new_len, float(pcm[0]), dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, num=len(pcm), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
    return np.interp(x_new, x_old, pcm.astype(np.float64)).astype(np.float32)


def pick_out(prefer, vb_cable):
    try:
        import sounddevice as sd
    except ImportError:
        die("missing sounddevice; install with: .venv\\Scripts\\python.exe -m pip install sounddevice")
    devices = sd.query_devices()
    if prefer is None and not vb_cable:
        return None, "default"
    if prefer is not None and str(prefer).isdigit():
        index = int(prefer)
        if index < 0 or index >= len(devices) or devices[index]["max_output_channels"] < 1:
            die("unknown output: " + str(prefer))
        return index, devices[index]["name"]
    if prefer is not None:
        prefer_l = str(prefer).lower()
        matches = []
        for index, info in enumerate(devices):
            if info["max_output_channels"] < 1:
                continue
            if prefer_l in info["name"].lower():
                matches.append((index, info))
        if not matches:
            die("unknown output: " + str(prefer))
        if len(matches) == 1:
            return matches[0][0], matches[0][1]["name"]
        return prefer_wasapi(matches, sd)
    hits = cable_playback_hits(devices)
    if not hits:
        die("VB-Cable playback device not found")
    return prefer_wasapi(hits, sd)


def play_wav_device(path, device_index):
    import numpy as np
    import sounddevice as sd

    path = Path(path).resolve()
    if not path.is_file():
        die("missing wav: " + str(path))
    pcm, rate = load_wav_mono(path)
    info = sd.query_devices(device_index)
    target = int(round(float(info["default_samplerate"])))
    pcm = resample_linear(pcm, rate, target)
    max_ch = int(info["max_output_channels"])
    if max_ch < 1:
        die("output device has no channels: " + str(device_index))
    order = []
    if max_ch >= 2:
        order.append(2)
    if 1 not in order:
        order.append(1)
    if max_ch > 2:
        order.append(max_ch)
    last = None
    for channels in order:
        audio = pcm if channels == 1 else np.column_stack([pcm] * channels)
        try:
            sd.play(audio, samplerate=target, device=device_index)
            sd.wait()
            return
        except sd.PortAudioError as exc:
            sd.stop()
            last = exc
    die("cannot play to device " + str(device_index) + ": " + str(last))


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


def synthesize(model, lang, sentence):
    write_mouth(settings_text(model, lang, sentence, "off"))
    tmp = Path(tempfile.mkdtemp(prefix="trident-mouth-once-"))
    try:
        try:
            completed = subprocess.run(
                [str(ROOT / "chatterbox.exe"), str(ROOT / "mouth.txt")],
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


def settings_fingerprint(text):
    return hashlib.sha256(without_spoken_text(text).encode("utf-8")).hexdigest()


def publish_pid(pid, variant, lang, fingerprint, state):
    body = str(pid) + "\n" + variant + "\n" + lang + "\n" + fingerprint + "\n" + state + "\n"
    tmp = ROOT / "mouth.pid.tmp"
    try:
        tmp.write_bytes(body.encode("utf-8"))
        tmp.replace(ROOT / "mouth.pid")
    except OSError as exc:
        die("cannot write mouth.pid: " + str(exc))


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


def acquire_lock():
    path = ROOT / LOCK_NAME
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        return None
    except OSError as exc:
        die("cannot create mouth.lock: " + str(exc))
    try:
        os.write(fd, (str(os.getpid()) + "\n" + str(time.time()) + "\n").encode("ascii"))
    except OSError as exc:
        os.close(fd)
        die("cannot write mouth.lock: " + str(exc))
    return fd


def release_lock(fd):
    if fd is None:
        return
    os.close(fd)
    try:
        (ROOT / LOCK_NAME).unlink()
    except OSError:
        pass


def lock_owner():
    path = ROOT / LOCK_NAME
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    try:
        pid = int(lines[0].strip())
    except ValueError:
        return None
    try:
        started = float(lines[1].strip()) if len(lines) > 1 else 0.0
    except ValueError:
        started = 0.0
    return pid, started


def lock_busy():
    if not (ROOT / LOCK_NAME).is_file():
        return False
    owner = lock_owner()
    if owner is None:
        return True
    return bool(process_image(owner[0]))


def clear_stale_lock():
    if lock_busy():
        return
    path = ROOT / LOCK_NAME
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def stop_resident():
    try:
        (ROOT / "mouth.stop").write_bytes(b"stop\n")
    except OSError as exc:
        die("cannot write mouth.stop: " + str(exc))
    stopped = False
    deadline = time.time() + 15
    while True:
        rec = resident_record()
        if rec is not None and chatterbox_running(rec.pid):
            stopped = True
            if rec.state == "loading":
                terminate_pid(rec.pid)
                if not wait_dead(rec.pid, 5):
                    die("cannot stop chatterbox pid " + str(rec.pid))
            elif not wait_dead(rec.pid, 120):
                terminate_pid(rec.pid)
                if not wait_dead(rec.pid, 5):
                    die("cannot stop chatterbox pid " + str(rec.pid))
            break
        if not lock_busy() or time.time() > deadline:
            break
        time.sleep(0.05)
    for name in SLOTS:
        remove_file(name)
    clear_stale_lock()
    return stopped


def note_spawn(proc, model, lang, fingerprint):
    rec = resident_record()
    if rec is not None and rec.pid == proc.pid and rec.variant == model and rec.lang == lang:
        if rec.state == "ready" and rec.fingerprint == fingerprint:
            return
        if rec.fingerprint == "" and rec.state == "":
            publish_pid(proc.pid, model, lang, fingerprint, "ready")
            return
    publish_pid(proc.pid, model, lang, fingerprint, "loading")


def launch_resident(model, lang, fingerprint):
    if (ROOT / "mouth.stop").is_file():
        return None
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
    try:
        note_spawn(proc, model, lang, fingerprint)
    except SystemExit:
        if proc.poll() is None:
            terminate_pid(proc.pid)
        raise
    if (ROOT / "mouth.stop").is_file() or proc.poll() is not None:
        if proc.poll() is None:
            terminate_pid(proc.pid)
            wait_dead(proc.pid, 5)
        remove_file("mouth.pid")
        if proc.poll() is not None and not (ROOT / "mouth.stop").is_file():
            die("chatterbox exited " + str(proc.returncode) + "\n" + log_tail())
        return None
    print("mouth: starting chatterbox", file=sys.stderr)
    return proc


def fail_loader(pid, message):
    if chatterbox_running(pid):
        terminate_pid(pid)
        if not wait_dead(pid, 5):
            die(message + "\nloader pid " + str(pid) + " did not exit")
    remove_file("mouth.pid")
    die(message)


def wait_until_ready(pid, model, lang, fingerprint, proc=None):
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            die("chatterbox exited " + str(proc.returncode) + "\n" + log_tail())
        if not chatterbox_running(pid):
            die("chatterbox exited\n" + log_tail())
        rec = resident_record()
        if rec is not None and rec.pid == pid and rec.variant == model and rec.lang == lang:
            if rec.state == "ready" and rec.fingerprint == fingerprint:
                print("mouth: chatterbox pid " + str(pid) + " resident", file=sys.stderr)
                return pid
            if rec.fingerprint == fingerprint and rec.state == "loading":
                pass
            elif rec.fingerprint == "" and rec.state == "":
                publish_pid(pid, model, lang, fingerprint, "ready")
                if not chatterbox_running(pid):
                    die("chatterbox exited\n" + log_tail())
                print("mouth: chatterbox pid " + str(pid) + " resident", file=sys.stderr)
                return pid
        time.sleep(0.05)
    fail_loader(pid, "chatterbox did not become ready\n" + log_tail())


def wait_for_fingerprint(pid, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        rec = resident_record()
        if rec is None or rec.pid != pid or not chatterbox_running(pid):
            return None
        if rec.fingerprint:
            return rec
        time.sleep(0.02)
    rec = resident_record()
    if rec is not None and rec.pid == pid and rec.fingerprint and chatterbox_running(pid):
        return rec
    return None


def same_settings(rec, model, lang, fingerprint):
    return (
        rec is not None
        and chatterbox_running(rec.pid)
        and rec.variant == model
        and rec.lang == lang
        and rec.fingerprint == fingerprint
        and rec.state in ("loading", "ready")
    )


def ensure_resident(model, lang):
    if any(ch.isspace() for ch in lang):
        die("language must be a single tag")
    desired = settings_text(model, lang, "", "off")
    fingerprint = settings_fingerprint(desired)
    deadline = time.time() + 240
    while time.time() < deadline:
        rec = resident_record()
        if rec is not None and chatterbox_running(rec.pid) and not rec.fingerprint:
            rec = wait_for_fingerprint(rec.pid, 2) or rec
        if same_settings(rec, model, lang, fingerprint):
            if rec.state == "ready":
                print("mouth: chatterbox pid " + str(rec.pid) + " resident", file=sys.stderr)
                return rec.pid
            return wait_until_ready(rec.pid, model, lang, fingerprint)
        fd = acquire_lock()
        if fd is None:
            clear_stale_lock()
            time.sleep(0.05)
            continue
        proc = None
        try:
            rec = resident_record()
            if same_settings(rec, model, lang, fingerprint):
                if rec.state == "ready":
                    print("mouth: chatterbox pid " + str(rec.pid) + " resident", file=sys.stderr)
                    return rec.pid
                proc_pid = rec.pid
                release_lock(fd)
                fd = None
                return wait_until_ready(proc_pid, model, lang, fingerprint)
            if rec is not None and chatterbox_running(rec.pid):
                print("mouth: chatterbox settings changed", file=sys.stderr)
                release_lock(fd)
                fd = None
                stop_resident()
                continue
            stop_path = ROOT / "mouth.stop"
            if stop_path.is_file():
                age = time.time() - stop_path.stat().st_mtime
                if age < 30 or chatterbox_running_any():
                    die("mouth stop requested")
                remove_file("mouth.stop")
            write_mouth(desired)
            for name in ("mouth.prompt.txt", "mouth.response.txt", "mouth.prompt.txt.tmp", "mouth.response.txt.tmp"):
                remove_file(name)
            proc = launch_resident(model, lang, fingerprint)
            if proc is None:
                die("mouth stop requested")
        finally:
            release_lock(fd)
        return wait_until_ready(proc.pid, model, lang, fingerprint, proc)
    die("chatterbox did not become ready\n" + log_tail())


def chatterbox_running_any():
    rec = resident_record()
    return rec is not None and chatterbox_running(rec.pid)


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


def speak_chunks(synthesize_one, chunks, play_one):
    ready = synthesize_one(chunks[0])
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        for index, sentence in enumerate(chunks):
            play_future = pool.submit(play_one, ready)
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
    parser.add_argument("--vb-cable", action="store_true", help="play into CABLE Input (VB-Audio Virtual Cable)")
    parser.add_argument("--out", default=None, help="playback device index or name substring")
    parser.add_argument("--no-play", action="store_true", help="synthesize and print the wav path")
    parser.add_argument("--play-wav", default=None, help="play this wav and skip synthesis")
    args = parser.parse_args()
    if args.stop:
        if args.once or args.text or args.lang is not None or args.vb_cable or args.out or args.no_play or args.play_wav:
            die("usage: mouth.py --stop")
        os.chdir(ROOT)
        if stop_resident():
            print("mouth: chatterbox stopped", file=sys.stderr)
        else:
            print("mouth: chatterbox not running", file=sys.stderr)
        raise SystemExit(0)
    if args.play_wav and (args.text or args.no_play):
        die("usage: mouth.py --play-wav FILE [--vb-cable|--out DEVICE]")
    if args.no_play and not args.text:
        die("usage: mouth.py --no-play TEXT [TEXT ...]")
    if args.play_wav:
        wav = Path(args.play_wav).expanduser()
        if not wav.is_file():
            die("missing wav: " + str(wav))
        if args.vb_cable or args.out is not None:
            index, name = pick_out(args.out, args.vb_cable)
            print("mouth out: " + name, file=sys.stderr, flush=True)
            play_wav_device(wav, index)
        else:
            print("mouth out: default", file=sys.stderr, flush=True)
            play_wav(wav)
        raise SystemExit(0)
    if not args.text:
        die("usage: mouth.py [--model nano|turbo|v3] [--lang TAG] [--once] [--vb-cable] TEXT [TEXT ...]")
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
    if args.no_play:
        if args.once:
            produced = [synthesize(args.model, lang, sentence) for sentence in chunks]
        else:
            pid = ensure_resident(args.model, lang)
            produced = [resident_say(pid, sentence) for sentence in chunks]
        for wav in produced:
            print(str(Path(wav).resolve()), flush=True)
        raise SystemExit(0)
    if args.vb_cable or args.out is not None:
        index, name = pick_out(args.out, args.vb_cable)
        print("mouth out: " + name, file=sys.stderr, flush=True)

        def play_one(path, index=index):
            play_wav_device(path, index)
    else:
        print("mouth out: default", file=sys.stderr, flush=True)
        play_one = play_wav
    if args.once:
        speak_chunks(lambda sentence: synthesize(args.model, lang, sentence), chunks, play_one)
    else:
        pid = ensure_resident(args.model, lang)
        speak_chunks(lambda sentence: resident_say(pid, sentence), chunks, play_one)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
