"""Iris-side stub that POSTs one text turn to the NVIDIA Gemma worker.

Writes nvidia_turn.request.txt. If --url or TRIDENT_NVIDIA_URL is set, POST
JSON and print the worker text. With no URL, leave the request and exit 0.
Does not start gemma-brain.exe and does not listen for a connection.

--stream sends stream true. The body is text pieces, then a blank line.
Those pieces are printed as they arrive. The blank line is not printed.

--image reads the local file and POSTs image_b64 (standard base64 of those
bytes) plus the path in image. The worker can vision the bytes without that
path existing on its disk. A path-only POST still works on the worker.
"""

import argparse
import base64
import codecs
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRAIN_URL = "http://192.168.16.31:8765/"
REQUEST = ROOT / "nvidia_turn.request.txt"
REQUEST_TMP = ROOT / "nvidia_turn.request.txt.tmp"
RESPONSE = ROOT / "nvidia_turn.response.txt"


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


def request_body(ident, text, image):
    body = "id " + ident + "\n"
    body += "text <<\n" + text
    if not text.endswith("\n"):
        body += "\n"
    body += "<<\n"
    body += "image <<\n"
    if image:
        body += image
        if not image.endswith("\n"):
            body += "\n"
    body += "<<\n"
    return body


def write_bytes(path, tmp, payload):
    try:
        tmp.write_bytes(payload)
        tmp.replace(path)
    except OSError as exc:
        die("cannot write " + path.name + ": " + str(exc))


def write_request(body):
    write_bytes(REQUEST, REQUEST_TMP, body.encode("utf-8"))


def write_response(body):
    tmp = ROOT / "nvidia_turn.response.txt.tmp"
    write_bytes(RESPONSE, tmp, body.encode("utf-8"))


def generation_from_body(raw):
    stripped = raw.strip()
    if not stripped:
        return ""
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return raw
        if isinstance(data, dict) and "text" in data and data["text"] is not None:
            return str(data["text"])
    return raw


def probe(url, timeout=2):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if not host or parsed.scheme not in ("http", "https"):
        return False
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    try:
        sock = socket.create_connection((host, port), timeout)
    except OSError:
        return False
    sock.close()
    return True


def file_b64(path):
    try:
        data = path.read_bytes()
    except OSError as exc:
        die("cannot read image: " + str(exc))
    if not data:
        die("empty image: " + str(path))
    return base64.b64encode(data).decode("ascii"), len(data)


def _payload(ident, text, image, image_b64, stream):
    body = {"id": ident, "text": text, "image": image}
    if image_b64:
        body["image_b64"] = image_b64
    if stream:
        body["stream"] = True
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _fail_http(ident, exc):
    detail = exc.read().decode("utf-8", errors="replace").strip()
    message = "http " + str(exc.code)
    if detail:
        message += " " + detail.splitlines()[0][:200]
    write_response("id " + ident + "\nerr " + message + "\n")
    die("nvidia worker: " + message)


def _open(url, ident, text, image, image_b64, timeout, stream):
    req = urllib.request.Request(
        url,
        data=_payload(ident, text, image, image_b64, stream),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        _fail_http(ident, exc)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        write_response("id " + ident + "\nerr " + str(reason) + "\n")
        die("nvidia worker: " + str(reason))
    code = getattr(resp, "status", 200)
    if code < 200 or code >= 300:
        resp.close()
        write_response("id " + ident + "\nerr http " + str(code) + "\n")
        die("nvidia worker: http " + str(code))
    return resp


def _store(ident, text_out):
    if not str(text_out).strip():
        write_response("id " + ident + "\nerr empty\n")
        die("nvidia worker returned empty")
    if not text_out.endswith("\n"):
        text_out += "\n"
    write_response("id " + ident + "\nok\n" + text_out)
    return text_out


def _split_hold(pending):
    """Keep a trailing newline. It may be the worker's terminating blank line."""
    if pending.endswith(b"\n\n"):
        return pending[:-2], pending[-2:]
    if pending.endswith(b"\n"):
        return pending[:-1], pending[-1:]
    return pending, b""


def _chunk_left(resp):
    left = getattr(resp, "chunk_left", None)
    if isinstance(left, int) and left > 0:
        return left
    return 0


def _read_block(resp):
    """One HTTP chunk. read(n) keeps pulling chunks until n bytes, so do not do that."""
    left = _chunk_left(resp)
    if left:
        return resp.read(left)
    block = resp.read(1)
    if not block:
        return b""
    left = _chunk_left(resp)
    if left:
        return block + resp.read(left)
    return block


def iter_stream(url, ident, text, image, image_b64, timeout):
    """Yield worker text pieces. The trailing blank line is not yielded."""
    resp = _open(url, ident, text, image, image_b64, timeout, True)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = b""
    collected = []
    try:
        while True:
            block = _read_block(resp)
            if not block:
                break
            pending += block
            emit, pending = _split_hold(pending)
            if emit:
                piece = decoder.decode(emit)
                if piece:
                    collected.append(piece)
                    yield piece
        tail = decoder.decode(b"", final=True)
        if tail:
            collected.append(tail)
            yield tail
        if pending != b"\n\n":
            preview = ("".join(collected) + pending.decode("utf-8", "replace")).lstrip()
            write_response("id " + ident + "\nerr truncated stream\n")
            if preview.startswith("{"):
                die("nvidia worker returned json; stream ends with a blank line")
            die("nvidia worker stream ended without a blank line")
        _store(ident, "".join(collected))
    finally:
        resp.close()


def post_turn(url, ident, text, image, image_b64, timeout, stream=False):
    if stream:
        for piece in iter_stream(url, ident, text, image, image_b64, timeout):
            sys.stdout.buffer.write(piece.encode("utf-8"))
            sys.stdout.buffer.flush()
        return
    resp = _open(url, ident, text, image, image_b64, timeout, False)
    try:
        raw = resp.read().decode("utf-8", errors="replace")
    finally:
        resp.close()
    text_out = _store(ident, generation_from_body(raw))
    sys.stdout.write(text_out)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="nvidia_client.py")
    parser.add_argument("text", help="transcript or text turn")
    parser.add_argument(
        "--image",
        default=None,
        help="local image file; POST sends image_b64 bytes and the path",
    )
    parser.add_argument("--url", default=None, help="NVIDIA worker POST url; else TRIDENT_NVIDIA_URL")
    parser.add_argument("--timeout", type=float, default=30, help="HTTP timeout seconds (default 30)")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="POST stream true; print text pieces as they arrive",
    )
    args = parser.parse_args()
    if not args.text.strip():
        die("empty text")
    image = None
    image_b64 = None
    image_bytes = 0
    if args.image is not None:
        if args.image.strip() == "":
            die("empty image")
        path = Path(args.image)
        if not path.is_file():
            die("missing image: " + str(path))
        image = str(path)
        image_b64, image_bytes = file_b64(path)
    if args.url is not None:
        url = args.url.strip()
    else:
        url = os.environ.get("TRIDENT_NVIDIA_URL", "").strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        die("nvidia url must start with http:// or https://")
    if args.timeout <= 0:
        die("timeout must be > 0")
    ident = str(time.time_ns())
    write_request(request_body(ident, args.text, image))
    if not url:
        if RESPONSE.is_file():
            try:
                RESPONSE.unlink()
            except OSError as exc:
                die("cannot remove nvidia_turn.response.txt: " + str(exc))
        print("nvidia: worker unset; request " + str(REQUEST), file=sys.stderr)
        return
    if image_b64:
        print("nvidia: image_b64 " + str(image_bytes) + " bytes", file=sys.stderr)
    post_turn(url, ident, args.text, image, image_b64, args.timeout, args.stream)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("nvidia: stopped", file=sys.stderr)
        raise SystemExit(0)
