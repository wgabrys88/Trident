"""POST one text card to a Trident node.

python nvidia_client.py --url URL --module MODULE --op OP [--lang TAG]
    [--fast nano|turbo] [--flip yes|no] [--image PATH] [--timeout SEC] [BODY]

The card is text: id, from, to, module, op, body. from is this machine.
to is the host in the URL. A non-200 reply or a card that is not completed
is an error. This process does not listen and does not start a model.
"""

import argparse
import base64
import sys
import urllib.error
import urllib.request
from pathlib import Path

import seat

ROOT = Path(__file__).resolve().parent


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


def file_b64(path):
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        die("cannot read image: " + str(exc))
    if not data:
        die("empty image: " + str(path))
    return base64.b64encode(data).decode("ascii"), len(data)


def _fail_body(exc):
    detail = exc.read().decode("utf-8", errors="replace")
    try:
        card = seat.parse(detail)
    except seat.CardError:
        line = detail.strip().splitlines()
        message = "http " + str(exc.code)
        if line:
            message += " " + line[0][:200]
        die(message)
    die((card.get("body") or "failed").strip() or "failed")


def post_card(url, card, timeout):
    target = (url or "").strip()
    if not (target.startswith("http://") or target.startswith("https://")):
        die("url must start with http:// or https://")
    if timeout <= 0:
        die("timeout must be > 0")
    payload = seat.render(card).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=payload,
        headers={"Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        _fail_body(exc)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        die("peer missing " + str(reason))
    try:
        raw = resp.read().decode("utf-8")
    finally:
        resp.close()
    code = getattr(resp, "status", 200)
    if code < 200 or code >= 300:
        die("http " + str(code))
    try:
        done = seat.parse(raw)
    except seat.CardError as exc:
        die(str(exc))
    if done.get("state") != "completed":
        die((done.get("body") or "failed").strip() or "failed")
    return done


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="nvidia_client.py")
    parser.add_argument("body", nargs="*")
    parser.add_argument("--url", required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--op", required=True)
    parser.add_argument("--lang", default=None)
    parser.add_argument("--fast", default=None)
    parser.add_argument("--flip", default=None)
    parser.add_argument("--image", default=None)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    url = args.url.strip()
    if not url:
        die("empty url")
    if not args.module.strip() or not args.op.strip():
        die("empty module")
    if args.timeout <= 0:
        die("timeout must be > 0")
    fields = {"from": seat.node_from(), "to": seat.peer_of(url), "timeout": str(args.timeout)}
    if args.lang is not None:
        if not args.lang.strip():
            die("empty lang")
        fields["lang"] = args.lang.strip()
    if args.fast is not None:
        if args.fast not in ("nano", "turbo"):
            die("fast mouth is nano or turbo")
        fields["fast"] = args.fast
    if args.flip is not None:
        if args.flip not in ("yes", "no"):
            die("flip is yes or no")
        fields["flip"] = args.flip
    if args.image is not None:
        if not args.image.strip():
            die("empty image")
        path = Path(args.image)
        if not path.is_file():
            die("missing image: " + str(path))
        encoded, nbytes = file_b64(path)
        fields["image"] = str(path)
        fields["image_b64"] = encoded
        print("node: image_b64 " + str(nbytes) + " bytes", file=sys.stderr, flush=True)
    card = seat.make(args.module.strip(), args.op.strip(), " ".join(args.body), **fields)
    done = post_card(url, card, args.timeout)
    sys.stdout.write(done.get("body") or "")
    if done.get("body") and not str(done["body"]).endswith("\n"):
        sys.stdout.write("\n")
    lang = (done.get("lang") or "").strip()
    if lang:
        print("node: " + lang, file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("node: stopped", file=sys.stderr)
        raise SystemExit(0)
