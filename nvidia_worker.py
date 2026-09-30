"""Trident node. One text card on port 8765.

POST text/plain is one card: id, from, to, module, op, body, and optional
lang, fast, flip, image, image_b64, timeout, state, brain. The watcher is the
only performer. It runs mouth.say, ear.listen, brain.ask, node.caps, and
node.leases. A peer that cannot reach this port gets no answer from here.
There is no JSON body and no audio stream. A busy port is an error.
"""

import argparse
import base64
import binascii
import os
import platform
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import gemma
import hear
import mouth
import seat

ROOT = Path(__file__).resolve().parent
MAX_BODY = 16_000_000
QUIET_S = 60
OPS = (
    ("mouth", "say"),
    ("ear", "listen"),
    ("brain", "ask"),
    ("node", "caps"),
    ("node", "leases"),
)


class WorkerError(Exception):
    pass


def die(message):
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(2)


def configure_stdio_utf8():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def venv_python():
    path = ROOT / ".venv" / "Scripts" / "python.exe"
    if not path.is_file():
        die("missing .venv python: " + str(path))
    return str(path)


def image_suffix(data):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise WorkerError("bad image")


def write_image_b64(image_b64):
    compact = "".join(str(image_b64).split())
    if not compact:
        raise WorkerError("empty image_b64")
    try:
        data = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error):
        raise WorkerError("bad image_b64")
    if not data:
        raise WorkerError("empty image_b64")
    suffix = image_suffix(data)
    fd, name = tempfile.mkstemp(prefix="trident-node-", suffix=suffix)
    os.close(fd)
    path = Path(name)
    try:
        path.write_bytes(data)
    except OSError as exc:
        try:
            path.unlink()
        except OSError:
            pass
        raise WorkerError("cannot write image: " + str(exc))
    print(
        "node: image_b64 " + str(len(data)) + " bytes -> " + str(path),
        file=sys.stderr,
        flush=True,
    )
    return path


def resolve_image(card):
    raw_b64 = card.get("image_b64")
    if raw_b64 is not None and str(raw_b64).strip():
        return write_image_b64(raw_b64), True
    image = str(card.get("image") or "").strip()
    if not image:
        return None, False
    path = Path(image)
    if not path.is_file() or path.stat().st_size <= 0:
        raise WorkerError("missing image")
    return path, False


def stderr_line(raw):
    text = (raw or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1][:400]


def run_gemma(text, image, timeout, verbose):
    if not str(text).strip():
        raise WorkerError("empty text")
    argv = [venv_python(), str(ROOT / "gemma.py")]
    if verbose:
        argv.append("--verbose")
    if image:
        path = Path(str(image))
        if not path.is_file() or path.stat().st_size <= 0:
            raise WorkerError("missing image")
        argv.extend(["--image", str(path.resolve())])
    argv.extend(["--", text])
    print("node: gemma" + (" image" if image else ""), file=sys.stderr, flush=True)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise WorkerError("gemma timed out")
    except OSError as exc:
        raise WorkerError("cannot run gemma.py: " + str(exc))
    elapsed = int((time.perf_counter() - started) * 1000)
    err = (completed.stderr or "").strip()
    if err:
        print(err, file=sys.stderr, flush=True)
    if completed.returncode != 0:
        raise WorkerError(stderr_line(err) or ("gemma exit " + str(completed.returncode)))
    out = completed.stdout or ""
    if not out.strip():
        raise WorkerError("gemma returned empty")
    print("node: gemma " + str(elapsed) + " ms", file=sys.stderr, flush=True)
    return out


def run_qwen(text, timeout, verbose):
    if not str(text).strip():
        raise WorkerError("empty text")
    argv = [venv_python(), str(ROOT / "qwen.py")]
    if verbose:
        argv.append("--verbose")
    argv.extend(["--", text])
    print("node: qwen", file=sys.stderr, flush=True)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise WorkerError("qwen timed out")
    except OSError as exc:
        raise WorkerError("cannot run qwen.py: " + str(exc))
    elapsed = int((time.perf_counter() - started) * 1000)
    err = (completed.stderr or "").strip()
    if err:
        print(err, file=sys.stderr, flush=True)
    if completed.returncode != 0:
        raise WorkerError(stderr_line(err) or ("qwen exit " + str(completed.returncode)))
    out = completed.stdout or ""
    if not out.strip():
        raise WorkerError("qwen returned empty")
    print("node: qwen " + str(elapsed) + " ms", file=sys.stderr, flush=True)
    return out


def card_timeout(card, default):
    raw = str(card.get("timeout") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise WorkerError("bad timeout")
    if value <= 0:
        raise WorkerError("bad timeout")
    return value


def pick_brain(card):
    brain = str(card.get("brain") or "").strip()
    if brain in ("gemma", "qwen"):
        return brain
    if brain:
        raise WorkerError("bad brain")
    cuda_name, _vulkan = gemma.adapter_names()
    if cuda_name:
        return "gemma"
    return "qwen"


def do_say(card, server):
    del server
    if str(card.get("flip") or "") == "yes":
        gemma.stop_resident()
    fast = str(card.get("fast") or "nano").strip() or "nano"
    hear.set_hold(True)
    try:
        mouth.say(card.get("body") or "", str(card.get("lang") or ""), True, fast)
    finally:
        hear.set_hold(False)
    return "spoken", None


def do_listen(card, server):
    del card, server
    text, lang = hear.listen()
    return text, {"lang": lang}


def do_ask(card, server):
    text = str(card.get("body") or "")
    if not text.strip():
        raise WorkerError("empty text")
    path, temp = resolve_image(card)
    try:
        brain = pick_brain(card)
        if path is not None and brain == "qwen":
            raise WorkerError("image asks for gemma")
        timeout = card_timeout(card, server.gemma_timeout)
        if brain == "gemma":
            out = run_gemma(text, str(path) if path is not None else None, timeout, server.verbose)
        else:
            out = run_qwen(text, timeout, server.verbose)
        return out, None
    finally:
        if temp and path is not None:
            path.unlink()


def capabilities():
    cpu = platform.processor().strip()
    if not cpu:
        raise WorkerError("cpu blank")
    lines = ["cpu " + cpu]
    import sounddevice as sd

    for index, dev in enumerate(sd.query_devices()):
        name = " ".join(str(dev["name"]).split())
        if int(dev["max_input_channels"]) > 0:
            lines.append("mic " + str(index) + " " + name)
        if int(dev["max_output_channels"]) > 0:
            lines.append("play " + str(index) + " " + name)
    if (ROOT / "gemma-brain.exe").is_file() and (ROOT / "gemma.gguf").is_file():
        lines.append("brain gemma")
    if (ROOT / "chatterbox.exe").is_file():
        lines.append("mouth chatterbox")
    if (ROOT / "nemo-speech.exe").is_file():
        lines.append("ear nemo")
    lines.append("capture " + ("open" if hear.capture_open() else "closed"))
    lines.append("port 8765")
    lines.append("card id from to module op body")
    lines.append("optional lang fast flip image image_b64 timeout state brain")
    for module, op in OPS:
        lines.append("op " + module + "." + op)
    return "\n".join(lines) + "\n"


def do_caps(card, server):
    del card, server
    return capabilities(), None


def do_leases(card, server):
    del card, server
    return seat.leases_text(), None


HANDLERS = {
    ("mouth", "say"): do_say,
    ("ear", "listen"): do_listen,
    ("brain", "ask"): do_ask,
    ("node", "caps"): do_caps,
    ("node", "leases"): do_leases,
}


def dispatch(card, server):
    key = (card["module"], card["op"])
    fn = HANDLERS.get(key)
    if fn is None:
        raise WorkerError("unknown op " + card["module"] + "." + card["op"])
    return fn(card, server)


def run_quiet_idle(timeout):
    argv = [venv_python(), str(ROOT / "gemma.py"), "--idle"]
    print("node: idle", file=sys.stderr, flush=True)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise WorkerError("idle timed out")
    except OSError as exc:
        raise WorkerError("cannot run gemma.py --idle: " + str(exc))
    elapsed = int((time.perf_counter() - started) * 1000)
    err = (completed.stderr or "").strip()
    if err:
        print(err, file=sys.stderr, flush=True)
    if completed.returncode != 0:
        raise WorkerError(stderr_line(err) or ("idle exit " + str(completed.returncode)))
    print("node: idle " + str(elapsed) + " ms", file=sys.stderr, flush=True)
    return completed.stdout or ""


def show_idle(text):
    stripped = (text or "").strip()
    if not stripped or stripped == "idle":
        return
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def queued_work():
    if not seat.QUEUE.is_dir():
        return False
    for path in seat.QUEUE.glob("*.card"):
        try:
            card = seat.read_card(path)
        except seat.CardError:
            return True
        if card.get("state", "queued") == "queued":
            return True
    return False


def resource_locked():
    if not seat.LEASE.is_dir():
        return False
    return any(seat.LEASE.glob("*.lock"))


def pump_quiet(server):
    with server.gate:
        busy = server.inflight > 0
    if busy or resource_locked() or queued_work():
        return
    now = time.monotonic()
    if server.quiet_s <= 0 or (now - server.last_post) < server.quiet_s:
        return
    server.last_post = now
    try:
        text = run_quiet_idle(server.gemma_timeout)
    except WorkerError as exc:
        print("node: " + str(exc), file=sys.stderr, flush=True)
        return
    show_idle(text)


def watch(server):
    modules = {"mouth", "ear", "brain", "node"}
    while not server.stopped:
        hold = seat.claim_next(seat.QUEUE, modules)
        if hold is None:
            pump_quiet(server)
            time.sleep(0.05)
            continue
        started = seat.begin(hold)
        if started is None:
            continue
        card, resource, fd = started
        extra = None
        try:
            body, extra = dispatch(card, server)
            state = "completed"
        except (Exception, SystemExit) as exc:
            body = str(exc).strip() or "failed"
            extra = None
            state = "failed"
            print("node: " + body, file=sys.stderr, flush=True)
        seat.end(hold, card, resource, fd, state, body, extra)


def send_bytes(handler, code, payload, content_type):
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def wait_done(ident, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = seat.find_card(seat.QUEUE, ident)
        if path is not None and path.name.endswith(".card"):
            card = seat.read_card(path)
            if card.get("state") in ("completed", "failed"):
                return card
        elif path is None and (seat.QUEUE / (ident + ".bad")).is_file():
            raise WorkerError("bad card")
        time.sleep(0.05)
    raise WorkerError("timeout")


class CardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("node: " + (fmt % args), file=sys.stderr, flush=True)

    def do_POST(self):
        server = self.server
        with server.gate:
            server.inflight += 1
        server.note_activity()
        try:
            self.handle_post()
        finally:
            with server.gate:
                server.inflight -= 1
            server.note_activity()

    def handle_post(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            send_bytes(self, 400, b"bad length\n", "text/plain; charset=utf-8")
            return
        if length <= 0 or length > MAX_BODY:
            send_bytes(self, 400, b"bad length\n", "text/plain; charset=utf-8")
            return
        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            send_bytes(self, 400, b"bad utf-8\n", "text/plain; charset=utf-8")
            return
        try:
            card = seat.parse(text)
        except seat.CardError as exc:
            send_bytes(self, 400, (str(exc) + "\n").encode("utf-8"), "text/plain; charset=utf-8")
            return
        try:
            seconds = card_timeout(card, self.server.gemma_timeout)
        except WorkerError as exc:
            send_bytes(self, 400, (str(exc) + "\n").encode("utf-8"), "text/plain; charset=utf-8")
            return
        card["state"] = "queued"
        card["timeout"] = str(seconds)
        try:
            seat.write_new(card)
        except seat.CardError as exc:
            send_bytes(self, 400, (str(exc) + "\n").encode("utf-8"), "text/plain; charset=utf-8")
            return
        print("node: post " + card["id"] + " " + card["module"] + "." + card["op"], file=sys.stderr, flush=True)
        try:
            done = wait_done(card["id"], seconds)
        except WorkerError as exc:
            send_bytes(self, 500, (str(exc) + "\n").encode("utf-8"), "text/plain; charset=utf-8")
            return
        payload = seat.render(done).encode("utf-8")
        code = 200 if done.get("state") == "completed" else 500
        send_bytes(self, code, payload, "text/plain; charset=utf-8")


class NodeServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, gemma_timeout, verbose, quiet_s=QUIET_S):
        super().__init__(address, CardHandler)
        self.gemma_timeout = gemma_timeout
        self.verbose = verbose
        self.quiet_s = quiet_s
        self.last_post = time.monotonic()
        self.inflight = 0
        self.gate = threading.Lock()
        self.stopped = False

    def note_activity(self):
        self.last_post = time.monotonic()


def listener_up(host, port):
    target = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect((target, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def serve_http(host, port, timeout, verbose, quiet_s=QUIET_S):
    if listener_up(host, port):
        die(host + ":" + str(port) + " is already in use")
    try:
        server = NodeServer((host, port), timeout, verbose, quiet_s)
    except OSError as exc:
        die("cannot listen on " + host + ":" + str(port) + ": " + str(exc))
    print("node: listening http://" + host + ":" + str(port) + "/", file=sys.stderr, flush=True)
    threading.Thread(target=watch, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stopped = True
        print("node: stopped", file=sys.stderr)
        raise SystemExit(0)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="nvidia_worker.py")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.port < 1 or args.port > 65535:
        die("port out of range")
    if not args.host.strip():
        die("empty host")
    if args.timeout <= 0:
        die("timeout must be > 0")
    serve_http(args.host, args.port, args.timeout, args.verbose)


if __name__ == "__main__":
    main()
