"""Local Gemma worker for one NVIDIA turn.

POST JSON {"id","text","image","image_b64"} and return {"text": ...}.
POST JSON with "stream": true returns chunked text/plain of the token pieces,
then a blank line. The body is text. This worker does not return audio.
gemma.py keeps one resident gemma-brain.exe. image_b64 is standard base64 of
the image bytes; it is decoded to a temp file and passed as --image. If
image_b64 is absent, a readable image path on this machine is passed as --image.
--drop is a local nvidia_turn.* file inbox, not the production LAN HTTP path.
If the port is already accepting a connection, this process leaves it alone.
"""

import argparse
import base64
import binascii
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUEST = ROOT / "nvidia_turn.request.txt"
RESPONSE = ROOT / "nvidia_turn.response.txt"
RESPONSE_TMP = ROOT / "nvidia_turn.response.txt.tmp"
MAX_BODY = 16_000_000


class WorkerError(Exception):
    pass


def die(message):
    print(message, file=sys.stderr)
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
    return ".bin"


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
    fd, name = tempfile.mkstemp(prefix="trident-nvidia-", suffix=image_suffix(data))
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
        "nvidia worker: image_b64 " + str(len(data)) + " bytes -> " + str(path),
        file=sys.stderr,
        flush=True,
    )
    return path


def gemma_argv(verbose, stream):
    argv = [venv_python(), str(ROOT / "gemma.py")]
    if verbose:
        argv.append("--verbose")
    if stream:
        argv.append("--stream")
    return argv


def stderr_line(raw):
    text = (raw or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1][:400]


def run_gemma(text, image, timeout, verbose, via_b64=False):
    if not str(text).strip():
        raise WorkerError("empty text")
    argv = gemma_argv(verbose, False)
    used = False
    if image:
        path = Path(str(image))
        if path.is_file():
            argv.extend(["--image", str(path.resolve())])
            used = True
        else:
            print(
                "nvidia worker: image not readable, text only: " + str(image),
                file=sys.stderr,
                flush=True,
            )
    argv.extend(["--", text])
    if via_b64 and used:
        label = " image_b64"
    elif used:
        label = " image"
    else:
        label = ""
    print("nvidia worker: gemma" + label, file=sys.stderr, flush=True)
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
    print("nvidia worker: gemma " + str(elapsed) + " ms", file=sys.stderr, flush=True)
    return out


def iter_gemma_stream(text, image, timeout, verbose, via_b64, on_chunk):
    if not str(text).strip():
        raise WorkerError("empty text")
    argv = gemma_argv(verbose, True)
    used = False
    if image:
        path = Path(str(image))
        if path.is_file():
            argv.extend(["--image", str(path.resolve())])
            used = True
        else:
            print(
                "nvidia worker: image not readable, text only: " + str(image),
                file=sys.stderr,
                flush=True,
            )
    argv.extend(["--", text])
    if via_b64 and used:
        label = " image_b64"
    elif used:
        label = " image"
    else:
        label = ""
    print("nvidia worker: gemma stream" + label, file=sys.stderr, flush=True)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise WorkerError("cannot run gemma.py: " + str(exc))
    blocks = queue.Queue()

    def read_stdout():
        try:
            while True:
                block = proc.stdout.read(4096)
                if not block:
                    blocks.put(None)
                    return
                blocks.put(block)
        except Exception as exc:
            blocks.put(exc)

    err_box = []

    def read_stderr():
        try:
            err_box.append(proc.stderr.read())
        except Exception:
            pass

    threading.Thread(target=read_stdout, daemon=True).start()
    err_thread = threading.Thread(target=read_stderr, daemon=True)
    err_thread.start()
    started = time.perf_counter()
    sent = 0
    deadline = time.time() + timeout
    timed_out = False
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                proc.kill()
                break
            try:
                item = blocks.get(timeout=min(0.5, remaining))
            except queue.Empty:
                continue
            if isinstance(item, Exception):
                raise WorkerError("gemma stream failed: " + str(item))
            if item is None:
                break
            on_chunk(item)
            sent += len(item)
    finally:
        if proc.poll() is None and (timed_out or time.time() >= deadline):
            proc.kill()
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=5)
        err_thread.join(timeout=5)
    err = b"".join(err_box).decode("utf-8", errors="replace")
    elapsed = int((time.perf_counter() - started) * 1000)
    if err.strip():
        print(err.strip(), file=sys.stderr, flush=True)
    if timed_out:
        raise WorkerError("gemma timed out")
    if code != 0:
        raise WorkerError(stderr_line(err) or ("gemma exit " + str(code)))
    if sent <= 0:
        raise WorkerError("gemma returned empty")
    print("nvidia worker: gemma stream " + str(elapsed) + " ms", file=sys.stderr, flush=True)


def response_body(ident, ok, payload):
    if ok:
        text = payload
        if not text.endswith("\n"):
            text += "\n"
        return "id " + ident + "\nok\n" + text
    reason = " ".join(str(payload).split())
    if not reason:
        reason = "failed"
    return "id " + ident + "\nerr " + reason + "\n"


def write_response(ident, ok, payload):
    body = response_body(ident, ok, payload)
    try:
        RESPONSE_TMP.write_bytes(body.encode("utf-8"))
        RESPONSE_TMP.replace(RESPONSE)
    except OSError as exc:
        die("cannot write nvidia_turn.response.txt: " + str(exc))


def parse_request(raw):
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ident = ""
    blocks = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("id ") and "text" not in blocks:
            ident = line[3:].strip()
            index += 1
            continue
        stripped = line.rstrip(" \t")
        if stripped.endswith("<<") and stripped[:-2].strip():
            key = stripped[:-2].strip()
            index += 1
            buf = []
            while index < len(lines) and lines[index] != "<<":
                buf.append(lines[index])
                index += 1
            if index < len(lines) and lines[index] == "<<":
                index += 1
            blocks[key] = "\n".join(buf)
            continue
        index += 1
    image = blocks.get("image", "").strip()
    return ident, blocks.get("text", ""), image or None


def read_request():
    if not REQUEST.is_file():
        die("missing nvidia_turn.request.txt")
    try:
        raw = REQUEST.read_text(encoding="utf-8")
    except OSError as exc:
        die("cannot read nvidia_turn.request.txt: " + str(exc))
    ident, text, image = parse_request(raw)
    if not ident:
        ident = "0"
    return ident, text, image


def serve_drop(once, timeout, verbose):
    seen = None
    if not once and REQUEST.is_file():
        try:
            seen = REQUEST.read_bytes()
        except OSError:
            seen = None
    while True:
        if once:
            ident, text, image = read_request()
            finish_drop(ident, text, image, timeout, verbose, True)
            return
        if REQUEST.is_file():
            try:
                raw = REQUEST.read_bytes()
            except OSError:
                raw = None
            if raw is not None and raw != seen:
                seen = raw
                ident, text, image = parse_request(raw.decode("utf-8", errors="replace"))
                if not ident:
                    ident = "0"
                finish_drop(ident, text, image, timeout, verbose, False)
        time.sleep(0.25)


def finish_drop(ident, text, image, timeout, verbose, once):
    print("nvidia worker: drop id " + ident, file=sys.stderr, flush=True)
    try:
        out = run_gemma(text, image, timeout, verbose)
    except WorkerError as exc:
        write_response(ident, False, str(exc))
        print("nvidia worker: " + str(exc), file=sys.stderr, flush=True)
        if once:
            raise SystemExit(2)
        return
    write_response(ident, True, out)
    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def send_bytes(handler, code, payload, content_type):
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class TurnHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("nvidia worker: " + (fmt % args), file=sys.stderr, flush=True)

    def do_POST(self):
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
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            send_bytes(self, 400, b"bad json\n", "text/plain; charset=utf-8")
            return
        if not isinstance(data, dict):
            send_bytes(self, 400, b"json object required\n", "text/plain; charset=utf-8")
            return
        text = data.get("text")
        image = data.get("image", None)
        image_b64 = data.get("image_b64", None)
        if not isinstance(text, str) or not text.strip():
            send_bytes(self, 400, b"empty text\n", "text/plain; charset=utf-8")
            return
        if image is not None and not isinstance(image, str):
            send_bytes(self, 400, b"image must be a string or null\n", "text/plain; charset=utf-8")
            return
        if image_b64 is not None and not isinstance(image_b64, str):
            send_bytes(self, 400, b"image_b64 must be a string or null\n", "text/plain; charset=utf-8")
            return
        stream = data.get("stream", False)
        if stream is not True and stream is not False:
            send_bytes(self, 400, b"stream must be true or false\n", "text/plain; charset=utf-8")
            return
        ident = data.get("id", "")
        print("nvidia worker: post id " + str(ident), file=sys.stderr, flush=True)
        temp_path = None
        via_b64 = False
        if isinstance(image_b64, str) and "".join(image_b64.split()):
            try:
                temp_path = write_image_b64(image_b64)
            except WorkerError as exc:
                message = (str(exc) + "\n").encode("utf-8")
                code = 400 if str(exc) in ("bad image_b64", "empty image_b64") else 500
                send_bytes(self, code, message, "text/plain; charset=utf-8")
                return
            image = str(temp_path)
            via_b64 = True
        try:
            if stream:
                self.stream_turn(text, image, via_b64)
            else:
                try:
                    out = run_gemma(text, image, self.server.gemma_timeout, self.server.verbose, via_b64)
                except WorkerError as exc:
                    message = (str(exc) + "\n").encode("utf-8")
                    send_bytes(self, 500, message, "text/plain; charset=utf-8")
                    return
                except Exception as exc:
                    message = ("worker failed: " + str(exc) + "\n").encode("utf-8", errors="replace")
                    try:
                        send_bytes(self, 500, message, "text/plain; charset=utf-8")
                    except Exception:
                        print("nvidia worker: " + str(exc), file=sys.stderr, flush=True)
                    return
                payload = json.dumps({"text": out}, ensure_ascii=False).encode("utf-8")
                send_bytes(self, 200, payload, "application/json; charset=utf-8")
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def stream_turn(self, text, image, via_b64):
        started = False

        def on_chunk(block):
            nonlocal started
            if not started:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                started = True
            write_chunk(self, block)

        try:
            iter_gemma_stream(text, image, self.server.gemma_timeout, self.server.verbose, via_b64, on_chunk)
        except WorkerError as exc:
            if not started:
                message = (str(exc) + "\n").encode("utf-8")
                send_bytes(self, 500, message, "text/plain; charset=utf-8")
                return
            print("nvidia worker: " + str(exc), file=sys.stderr, flush=True)
            try:
                end_chunks(self)
            except OSError:
                pass
            return
        except Exception as exc:
            if not started:
                message = ("worker failed: " + str(exc) + "\n").encode("utf-8", errors="replace")
                send_bytes(self, 500, message, "text/plain; charset=utf-8")
                return
            print("nvidia worker: " + str(exc), file=sys.stderr, flush=True)
            try:
                end_chunks(self)
            except OSError:
                pass
            return
        if not started:
            send_bytes(self, 500, b"gemma returned empty\n", "text/plain; charset=utf-8")
            return
        try:
            write_chunk(self, b"\n\n")
            end_chunks(self)
        except OSError as exc:
            print("nvidia worker: stream closed: " + str(exc), file=sys.stderr, flush=True)


def write_chunk(handler, payload):
    if not payload:
        return
    handler.wfile.write(("%X\r\n" % len(payload)).encode("ascii"))
    handler.wfile.write(payload)
    handler.wfile.write(b"\r\n")
    handler.wfile.flush()


def end_chunks(handler):
    handler.wfile.write(b"0\r\n\r\n")
    handler.wfile.flush()


class WorkerServer(HTTPServer):
    allow_reuse_address = False

    def __init__(self, address, gemma_timeout, verbose):
        super().__init__(address, TurnHandler)
        self.gemma_timeout = gemma_timeout
        self.verbose = verbose


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


def port_in_use(exc):
    winerror = getattr(exc, "winerror", None)
    if winerror == 10048:
        return True
    return exc.errno in (98, 48, 10048)


def serve_http(host, port, timeout, verbose):
    if listener_up(host, port):
        print(
            "nvidia worker: already listening on " + host + ":" + str(port) + "; leaving it alone",
            file=sys.stderr,
            flush=True,
        )
        return
    try:
        server = WorkerServer((host, port), timeout, verbose)
    except OSError as exc:
        if port_in_use(exc):
            print(
                "nvidia worker: " + host + ":" + str(port) + " is already in use; leaving it alone",
                file=sys.stderr,
                flush=True,
            )
            return
        die("cannot listen on " + host + ":" + str(port) + ": " + str(exc))
    print(
        "nvidia worker: listening http://" + host + ":" + str(port) + "/",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("nvidia worker: stopped", file=sys.stderr)
        raise SystemExit(0)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="nvidia_worker.py")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0 for LAN)")
    parser.add_argument("--port", type=int, default=8765, help="bind port (default 8765)")
    parser.add_argument("--drop", action="store_true", help="local nvidia_turn.* file inbox (not LAN HTTP)")
    parser.add_argument("--once", action="store_true", help="with --drop, handle the current request and exit")
    parser.add_argument("--timeout", type=float, default=600, help="gemma.py seconds before kill (default 600)")
    parser.add_argument("--verbose", action="store_true", help="pass gemma-brain.exe stderr through gemma.py")
    args = parser.parse_args()
    if args.once and not args.drop:
        die("--once asks for --drop")
    if args.port < 1 or args.port > 65535:
        die("port out of range")
    if not args.host.strip():
        die("empty host")
    if args.timeout <= 0:
        die("timeout must be > 0")
    if args.drop:
        try:
            serve_drop(args.once, args.timeout, args.verbose)
        except KeyboardInterrupt:
            print("nvidia worker: stopped", file=sys.stderr)
            raise SystemExit(0)
        return
    serve_http(args.host, args.port, args.timeout, args.verbose)


if __name__ == "__main__":
    main()
