import os
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "node" / "queue"
LEASE = ROOT / "node" / "lease"
_SEQ = 0
_ORDER = ("id", "from", "to", "module", "op", "state", "lang", "fast", "flip", "image", "timeout")
_TAIL = {"body", "image_b64"}


class CardError(Exception):
    pass


def die(message):
    print(message, file=sys_stderr(), flush=True)
    raise CardError(message)


def sys_stderr():
    import sys

    return sys.stderr


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%s.%s" % (os.getpid(), time.time_ns()))
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def check_id(ident):
    if not ident or ident != Path(ident).name:
        die("bad id")
    for ch in ident:
        if ch not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-._":
            die("bad id")


def make(module, op, body, **fields):
    global _SEQ
    _SEQ += 1
    card = {
        "id": fields.pop("id", "%020d-%d-%06d" % (time.time_ns(), os.getpid(), _SEQ)),
        "from": fields.pop("from", ""),
        "to": fields.pop("to", ""),
        "module": module,
        "op": op,
        "body": "" if body is None else str(body),
        "state": fields.pop("state", "queued"),
    }
    for key, val in fields.items():
        if val is None:
            continue
        card[str(key)] = str(val)
    for key in ("id", "from", "to", "module", "op"):
        if not str(card.get(key) or "").strip():
            die("card missing " + key)
    check_id(card["id"])
    if "body" not in card:
        die("card missing body")
    return card


def _emit_value(lines, key, val):
    text = "" if val is None else str(val)
    if "\n" in text:
        lines.append(key + " <<")
        lines.append(text)
        lines.append("<<")
        return
    lines.append(key + " " + text)


def render(card):
    lines = []
    seen = set()
    for key in _ORDER:
        if key in card:
            _emit_value(lines, key, card[key])
            seen.add(key)
    for key in card:
        if key in seen or key in _TAIL:
            continue
        _emit_value(lines, key, card[key])
    if "image_b64" in card:
        lines.append("image_b64 <<")
        lines.append(str(card["image_b64"]))
        lines.append("<<")
    if "body" not in card:
        die("card missing body")
    lines.append("body <<")
    lines.append(str(card.get("body") or ""))
    lines.append("<<")
    return "\n".join(lines) + "\n"


def parse(raw):
    text = raw or ""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    card = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
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
            if index >= len(lines) or lines[index] != "<<":
                die("unclosed " + key)
            index += 1
            card[key] = "\n".join(buf)
            continue
        if " " not in line.strip():
            die("bad card line")
        key, val = line.strip().split(" ", 1)
        card[key] = val.strip()
        index += 1
    for key in ("id", "from", "to", "module", "op"):
        if not str(card.get(key) or "").strip():
            die("card missing " + key)
    if "body" not in card:
        die("card missing body")
    check_id(card["id"])
    return card


def write_new(card, directory=None):
    directory = QUEUE if directory is None else Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / (card["id"] + ".card")
    if dest.exists() or (directory / (card["id"] + ".hold")).exists():
        die("duplicate id " + card["id"])
    tmp = dest.with_name(dest.name + ".tmp.%s.%s" % (os.getpid(), time.time_ns()))
    tmp.write_text(render(card), encoding="utf-8")
    try:
        os.rename(tmp, dest)
    except FileExistsError:
        try:
            tmp.unlink()
        except OSError:
            pass
        die("duplicate id " + card["id"])
    return dest


def read_card(path):
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        die("cannot read " + path.name + ": " + str(exc))
    return parse(raw)


def find_card(directory, ident):
    directory = Path(directory)
    for name in (ident + ".card", ident + ".hold"):
        path = directory / name
        if path.is_file():
            return path
    return None


def finish(hold, state, body=None, extra=None):
    card = read_card(hold)
    card["state"] = state
    if body is not None:
        card["body"] = body
    if extra:
        for key, val in extra.items():
            if val is not None:
                card[str(key)] = str(val)
    atomic_write(hold, render(card))
    if str(hold).endswith(".hold"):
        dest = hold.with_name(hold.name[:-5] + ".card")
        os.replace(hold, dest)
        return dest
    return Path(hold)


def resource_name(module):
    if module in ("mouth", "ear", "brain", "seat", "node"):
        return module
    return "node"


def claim_next(directory, modules):
    directory = Path(directory)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.card")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            die("cannot read " + path.name + ": " + str(exc))
        try:
            card = parse(raw)
        except CardError as exc:
            bad = path.with_name(path.name[:-5] + ".bad")
            os.replace(path, bad)
            print(str(exc), file=sys_stderr(), flush=True)
            continue
        if card["module"] not in modules:
            continue
        if card.get("state", "queued") != "queued":
            continue
        lock = LEASE / (resource_name(card["module"]) + ".lock")
        if lock.exists():
            continue
        hold = path.with_name(path.name[:-5] + ".hold")
        try:
            os.rename(path, hold)
        except OSError:
            continue
        return hold
    return None


def lease_write(resource, state, card):
    LEASE.mkdir(parents=True, exist_ok=True)
    body = (
        "state " + state + "\n"
        "id " + card["id"] + "\n"
        "module " + card["module"] + "\n"
        "op " + card["op"] + "\n"
    )
    atomic_write(LEASE / resource, body)


def leases_text():
    LEASE.mkdir(parents=True, exist_ok=True)
    parts = []
    for path in sorted(LEASE.iterdir()):
        if not path.is_file() or path.suffix == ".lock" or path.name.endswith(".lock"):
            continue
        try:
            parts.append(path.name + "\n" + path.read_text(encoding="utf-8").rstrip("\n"))
        except OSError as exc:
            die("cannot read " + path.name + ": " + str(exc))
    queued = []
    if QUEUE.is_dir():
        for path in sorted(QUEUE.glob("*.card")):
            card = read_card(path)
            if card.get("state") == "queued":
                queued.append(card["id"] + " " + card["module"] + " " + card["op"])
    parts.append("queued\n" + "\n".join(queued))
    return "\n".join(parts) + "\n"


def node_from():
    import gemma

    skip = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
    found = []
    for ip in gemma.local_addresses():
        if ip in skip or ip.startswith("169.254.") or ":" in ip:
            continue
        found.append(ip)
    found = sorted(set(found))
    if len(found) != 1:
        die("node address " + " ".join(found))
    return found[0] + ":8765"


def peer_of(url):
    parsed = urllib.parse.urlsplit((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        die("bad url")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.hostname + ":" + str(port)


def lock_take(resource):
    LEASE.mkdir(parents=True, exist_ok=True)
    path = LEASE / (resource + ".lock")
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode("ascii"))
    return fd


def lock_drop(resource, fd):
    os.close(fd)
    os.unlink(LEASE / (resource + ".lock"))


def unclaim(hold):
    hold = Path(hold)
    if not hold.name.endswith(".hold"):
        die("bad hold")
    dest = hold.with_name(hold.name[:-5] + ".card")
    os.rename(hold, dest)
    return dest


def begin(hold):
    card = read_card(hold)
    resource = resource_name(card["module"])
    try:
        fd = lock_take(resource)
    except FileExistsError:
        unclaim(hold)
        return None
    lease_write(resource, "running", card)
    return card, resource, fd


def end(hold, card, resource, fd, state, body=None, extra=None):
    lease_write(resource, state, card)
    try:
        return finish(hold, state, body, extra)
    finally:
        lock_drop(resource, fd)
