import json, os, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trident_lib import MODELS, ROOT, WORK, kill_server_pid, reexec, venv_python

DONE = WORK / "done"
HOST = "127.0.0.1"
PORT = int(os.environ.get("TRIDENT_PORT", "8765"))
IDLE = int(os.environ.get("TRIDENT_IDLE_SECONDS", "1800"))
LOCK = threading.RLock()
CHANGED = threading.Condition(LOCK)
STOP = threading.Event()
VARIANT = "turbo"
LAST_HEARD = time.time()
LAST_CLOCK = LAST_HEARD
NEXT_EVENT = 1
EVENTS: list[dict] = []


def bus() -> None:
    (ROOT / "wav").mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(exist_ok=True)
    (WORK / "inbox").mkdir(exist_ok=True)
    (WORK / "ready").mkdir(exist_ok=True)
    for kind in ("inbox", "transcription", "speech", "wav", "job", "wake", "ready"):
        (DONE / kind).mkdir(exist_ok=True)
    memory = WORK / "memory.md"
    if not memory.exists():
        memory.write_text("", encoding="utf-8")


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def retire(path: Path, kind: str) -> Path:
    folder = DONE / kind
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    n = 1
    while dest.exists():
        dest = folder / f"{path.stem}-{n}{path.suffix}"
        n += 1
    path.replace(dest)
    return dest


def numbered(prefix: str, suffix: str = ".txt", folder: Path = WORK) -> Path:
    n = 1
    while True:
        path = folder / f"{prefix}-{n}{suffix}"
        if not path.exists() and not (DONE / prefix / path.name).exists():
            return path
        n += 1


def event_rows() -> list[dict]:
    with LOCK:
        return list(EVENTS)


def init_event_counter() -> None:
    global NEXT_EVENT, EVENTS
    path = WORK / "events.jsonl"
    EVENTS = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                EVENTS.append(json.loads(line))
    NEXT_EVENT = (EVENTS[-1]["id"] + 1) if EVENTS else 1


def add_event(kind: str, source: str, data=None, trigger: bool = False) -> dict:
    global NEXT_EVENT, LAST_HEARD
    with CHANGED:
        row = {
            "id": NEXT_EVENT,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "time": time.time(),
            "type": kind,
            "source": source,
            "trigger": bool(trigger),
            "data": data if data is not None else {},
        }
        NEXT_EVENT += 1
        EVENTS.append(row)
        with (WORK / "events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        if kind == "heard":
            LAST_HEARD = row["time"]
        CHANGED.notify_all()
        return row


def recent_events(limit: int) -> list[dict]:
    with LOCK:
        return EVENTS[-max(1, min(limit, 500)):]


def next_event(after: int, timeout: float) -> list[dict]:
    deadline = time.monotonic() + timeout
    with CHANGED:
        while True:
            rows = [row for row in EVENTS if row["id"] > after]
            if rows or STOP.is_set():
                return rows[:100]
            left = deadline - time.monotonic()
            if left <= 0:
                return []
            CHANGED.wait(left)


def next_speech(timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    with CHANGED:
        while True:
            files = sorted(WORK.glob("speech-*.txt"), key=lambda p: int(p.stem.split("-")[-1]))
            if files:
                path = files[0]
                language, _, text = path.read_text(encoding="utf-8-sig").partition("\n")
                return {"name": path.name, "language": language.strip(), "text": text.strip()}
            if STOP.is_set():
                return None
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            CHANGED.wait(left)


def vulkan_device() -> int:
    forced = os.environ.get("TRIDENT_VULKAN_DEVICE")
    if forced is not None:
        return int(forced)
    script = r'''
import ctypes, json
from pathlib import Path
root = Path.cwd()
dll = root / ".venv" / "Lib" / "site-packages" / "llama_cpp" / "lib" / "ggml-vulkan.dll"
lib = ctypes.CDLL(str(dll))
lib.ggml_backend_vk_get_device_count.restype = ctypes.c_int
lib.ggml_backend_vk_get_device_description.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
lib.ggml_backend_vk_get_device_memory.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
rows = []
for index in range(lib.ggml_backend_vk_get_device_count()):
    name = ctypes.create_string_buffer(256)
    free, total = ctypes.c_size_t(), ctypes.c_size_t()
    lib.ggml_backend_vk_get_device_description(index, name, 256)
    lib.ggml_backend_vk_get_device_memory(index, ctypes.byref(free), ctypes.byref(total))
    rows.append({"index": index, "name": name.value.decode("utf-8", "replace"), "total": total.value})
print(json.dumps(rows))
'''
    proc = subprocess.run([str(venv_python()), "-c", script], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "vulkan device list")
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    if not rows:
        raise RuntimeError("no vulkan device")
    chosen = max(rows, key=lambda row: row["total"])
    print("vulkan device", chosen["index"], chosen["name"], chosen["total"], flush=True)
    return int(chosen["index"])


def body(handler: BaseHTTPRequestHandler) -> dict:
    size = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(size) if size else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")


def send(handler: BaseHTTPRequestHandler, status: int, payload) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


class Handler(BaseHTTPRequestHandler):
    server_version = "Trident/1"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/health":
                send(self, 200, {"ok": True, "stop": STOP.is_set()})
            elif url.path == "/config":
                send(self, 200, {"variant": VARIANT, "vulkan_device": self.server.vulkan, "idle_seconds": IDLE})
            elif url.path == "/memory":
                send(self, 200, {"text": (WORK / "memory.md").read_text(encoding="utf-8")})
            elif url.path == "/events":
                after = int(query.get("after", ["0"])[0])
                timeout = min(60.0, max(0.0, float(query.get("timeout", ["30"])[0])))
                send(self, 200, {"events": next_event(after, timeout)})
            elif url.path == "/events/recent":
                limit = int(query.get("limit", ["80"])[0])
                send(self, 200, {"events": recent_events(limit)})
            elif url.path == "/speech/next":
                timeout = min(60.0, max(0.0, float(query.get("timeout", ["30"])[0])))
                send(self, 200, {"speech": next_speech(timeout)})
            else:
                send(self, 404, {"error": "not found"})
        except Exception as err:
            send(self, 500, {"error": str(err)})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            data = body(self)
            if url.path == "/ready":
                name = str(data["name"])
                atomic_text(WORK / "ready" / name, time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
                row = add_event("ready", name, {}, False)
                send(self, 200, row)
            elif url.path == "/heard":
                text = str(data.get("text", "")).strip()
                if not text:
                    send(self, 200, {"ignored": True})
                    return
                language = str(data.get("language", "")).strip()
                times = str(data.get("timestamps", "")).strip()
                with LOCK:
                    path = numbered("transcription")
                    first = (("<" + language + "> ") if language else "") + text
                    atomic_text(path, first + "\n" + (times + "\n" if times else ""))
                    archived = retire(path, "transcription")
                row = add_event("heard", "ear", {"text": text, "language": language, "timestamps": times, "artifact": str(archived.relative_to(ROOT))}, True)
                send(self, 200, row)
            elif url.path == "/event":
                row = add_event(str(data["type"]), str(data.get("source", "unknown")), data.get("data", {}), bool(data.get("trigger", False)))
                send(self, 200, row)
            elif url.path == "/archive":
                rel = str(data.get("path", "")).strip()
                kind = str(data.get("kind", "")).strip()
                if not rel or not kind:
                    raise RuntimeError("archive path and kind required")
                path = (ROOT / rel).resolve()
                try:
                    path.relative_to(ROOT.resolve())
                except ValueError:
                    raise RuntimeError("archive path outside root")
                if not path.is_file():
                    raise RuntimeError("missing " + rel)
                archived = retire(path, kind)
                send(self, 200, {"artifact": str(archived.relative_to(ROOT))})
            elif url.path == "/speech":
                text = str(data.get("text", "")).strip()
                if not text:
                    raise RuntimeError("empty speech")
                language = str(data.get("language", "en")).strip() or "en"
                with LOCK:
                    path = numbered("speech")
                    atomic_text(path, language + "\n" + text + "\n")
                row = add_event("speech_queued", "brain", {"text": text, "language": language, "artifact": str(path.relative_to(ROOT))}, False)
                send(self, 200, {"event": row, "name": path.name})
            elif url.path == "/speech/done":
                name = Path(str(data["name"])).name
                path = WORK / name
                archived = retire(path, "speech") if path.is_file() else DONE / "speech" / name
                wav_rel = str(data.get("wav", "")).strip()
                wav_archived = ""
                if wav_rel:
                    wav_path = (ROOT / wav_rel).resolve()
                    if wav_path.is_file():
                        wav_archived = str(retire(wav_path, "wav").relative_to(ROOT))
                    else:
                        wav_archived = wav_rel
                row = add_event("speech_done", "mouth", {"speech": str(archived.relative_to(ROOT)), "wav": wav_archived}, False)
                send(self, 200, row)
            elif url.path == "/memory":
                text = str(data.get("text", "")).strip()
                if not text:
                    raise RuntimeError("empty memory")
                path = WORK / "memory.md"
                current = path.read_text(encoding="utf-8")
                atomic_text(path, (current.rstrip() + "\n" if current.strip() else "") + text + "\n")
                row = add_event("memory", "brain", {"text": text}, False)
                send(self, 200, row)
            elif url.path == "/wake":
                seconds = float(data.get("seconds", 0))
                if seconds <= 0:
                    raise RuntimeError("wake seconds")
                payload = {"due": time.time() + seconds, "seconds": seconds, "reason": str(data.get("reason", ""))}
                with LOCK:
                    path = numbered("wake", ".json")
                    atomic_text(path, json.dumps(payload, ensure_ascii=False) + "\n")
                row = add_event("wake_scheduled", "brain", {**payload, "artifact": str(path.relative_to(ROOT))}, False)
                send(self, 200, row)
            elif url.path == "/stop":
                row = add_event("stop", str(data.get("source", "brain")), {}, False)
                STOP.set()
                with CHANGED:
                    CHANGED.notify_all()
                send(self, 200, row)
            else:
                send(self, 404, {"error": "not found"})
        except Exception as err:
            send(self, 500, {"error": str(err)})


def timer_loop() -> None:
    global LAST_CLOCK
    while not STOP.is_set():
        now = time.time()
        for path in list(WORK.glob("wake-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if now >= float(payload["due"]):
                    add_event("wake", "clock", {"reason": payload.get("reason", ""), "artifact": str(path.relative_to(ROOT))}, True)
                    retire(path, "wake")
            except Exception as err:
                print("wake skipped", path.name, err, flush=True)
        if now - LAST_HEARD >= IDLE and now - LAST_CLOCK >= IDLE:
            LAST_CLOCK = now
            add_event("clock", "clock", {"seconds_since_heard": int(now - LAST_HEARD)}, True)
        STOP.wait(0.25)


def workers(variant: str, inbox_only: bool) -> list[tuple[str, subprocess.Popen]]:
    py = str(venv_python())
    url = f"http://{HOST}:{PORT}"
    return [
        ("brain", subprocess.Popen([py, str(ROOT / "brain.py"), url])),
        ("mouth", subprocess.Popen([py, str(ROOT / "mouth.py"), variant, url])),
        ("ear", subprocess.Popen([py, str(ROOT / "ear.py"), url, "--inbox"] if inbox_only else [py, str(ROOT / "ear.py"), url])),
    ]


def drain_speech(items: list[tuple[str, subprocess.Popen]], timeout: float = 600.0) -> None:
    mouth = next((proc for name, proc in items if name == "mouth"), None)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = list(WORK.glob("speech-*.txt"))
        if not pending:
            return
        if mouth is not None and mouth.poll() is not None:
            print("mouth exited before speech drained", flush=True)
            return
        STOP.wait(0.25)
    print("speech drain timeout", flush=True)


def supervise(items: list[tuple[str, subprocess.Popen]]) -> None:
    ready_dir = WORK / "ready"
    while not all((ready_dir / name).is_file() for name, _ in items):
        for name, proc in items:
            code = proc.poll()
            if code is not None:
                raise RuntimeError(f"{name} exited {code}")
        if STOP.wait(0.05):
            break
    if not STOP.is_set():
        print("jarvis ready", flush=True)
    while not STOP.wait(0.25):
        for name, proc in items:
            code = proc.poll()
            if code is not None:
                raise RuntimeError(f"{name} exited {code}")
    drain_speech(items)


def clear_ready() -> None:
    folder = WORK / "ready"
    for path in folder.glob("*"):
        if path.is_file():
            retire(path, "ready")


def main() -> None:
    global VARIANT, LAST_HEARD, LAST_CLOCK
    reexec()
    args = sys.argv[1:]
    server_only = "--server-only" in args
    args = [arg for arg in args if arg != "--server-only"]
    if not args:
        VARIANT = "turbo"
        inbox_only = False
    elif len(args) in (1, 2) and args[0] in ("nano", "turbo", "v3"):
        VARIANT = args[0]
        inbox_only = len(args) == 2 and args[1] == "inbox"
        if len(args) == 2 and not inbox_only:
            raise SystemExit("usage: python trident.py [nano|turbo|v3] [inbox] [--server-only]")
    else:
        raise SystemExit("usage: python trident.py [nano|turbo|v3] [inbox] [--server-only]")
    bus()
    clear_ready()
    init_event_counter()
    LAST_HEARD = time.time()
    LAST_CLOCK = LAST_HEARD
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.vulkan = int(os.environ.get("TRIDENT_VULKAN_DEVICE", "0")) if server_only else vulkan_device()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    threading.Thread(target=timer_loop, daemon=True).start()
    add_event("start", "trident", {"variant": VARIANT}, True)
    print(f"trident http://{HOST}:{PORT}", flush=True)
    items = [] if server_only else workers(VARIANT, inbox_only)
    try:
        if server_only:
            while not STOP.wait(1):
                pass
        else:
            supervise(items)
    except KeyboardInterrupt:
        STOP.set()
    finally:
        STOP.set()
        for _, proc in items:
            if proc.poll() is None:
                proc.terminate()
        for _, proc in items:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        kill_server_pid()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
