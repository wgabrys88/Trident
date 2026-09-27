"""Iris-side stub that hands one text turn to a later NVIDIA Gemma worker.

Writes nvidia_turn.request.txt. If --url or TRIDENT_NVIDIA_URL is set, POST
JSON and print the worker text. With no URL, leave the request and exit 0.
Does not start gemma-brain.exe and does not listen for a connection.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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


def post_turn(url, ident, text, image, timeout):
    payload = json.dumps(
        {"id": ident, "text": text, "image": image},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        message = "http " + str(exc.code)
        if detail:
            message += " " + detail.splitlines()[0][:200]
        write_response("id " + ident + "\nerr " + message + "\n")
        die("nvidia worker: " + message)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        write_response("id " + ident + "\nerr " + str(reason) + "\n")
        die("nvidia worker: " + str(reason))
    if code < 200 or code >= 300:
        write_response("id " + ident + "\nerr http " + str(code) + "\n")
        die("nvidia worker: http " + str(code))
    text_out = generation_from_body(raw)
    if not str(text_out).strip():
        write_response("id " + ident + "\nerr empty\n")
        die("nvidia worker returned empty")
    if not text_out.endswith("\n"):
        text_out += "\n"
    write_response("id " + ident + "\nok\n" + text_out)
    sys.stdout.write(text_out)


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="nvidia_client.py")
    parser.add_argument("text", help="transcript or text turn")
    parser.add_argument("--image", default=None, help="image file path stored for a later Gemma worker")
    parser.add_argument("--url", default=None, help="NVIDIA worker POST url; else TRIDENT_NVIDIA_URL")
    parser.add_argument("--timeout", type=float, default=30, help="HTTP timeout seconds (default 30)")
    args = parser.parse_args()
    if not args.text.strip():
        die("empty text")
    image = None
    if args.image is not None:
        if args.image.strip() == "":
            die("empty image")
        path = Path(args.image)
        if not path.is_file():
            die("missing image: " + str(path))
        image = str(path)
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
    post_turn(url, ident, args.text, image, args.timeout)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("nvidia: stopped", file=sys.stderr)
        raise SystemExit(0)
