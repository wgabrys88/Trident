import asyncio
import hashlib
import io
import json
import os
import tomllib
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("TRIDENT_ROOT", Path(__file__).resolve().parent)).resolve()
CONFIG = tomllib.loads(Path(os.environ.get("TRIDENT_CONFIG", ROOT / "config.toml")).read_text(encoding="utf-8"))
RUNTIME = {"runs", "artifacts", "__pycache__"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(encode(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def stamp():
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def command(parts):
    return [os.path.expandvars(str(part)) for part in parts]


async def cancel(*tasks):
    for task in tasks:
        task.cancel()
    return await asyncio.gather(*tasks, return_exceptions=True)


def source_entries(git=False):
    for folder, directories, files in os.walk(ROOT):
        directories[:] = [name for name in directories if name != "__pycache__"
                          and (Path(folder) != ROOT or name not in RUNTIME)
                          and (git or name != ".git")]
        for name in [*directories, *files]:
            path = Path(folder) / name
            if path.lstat().st_file_attributes & 1024 or (path.is_file() and path.stat().st_nlink != 1):
                raise RuntimeError(f"Source aliases need explicit repair: {path}")
            yield path


def source_paths(git=False):
    return (path for path in source_entries(git) if path.is_file())


def activate(state, text, source, receipt, sequence=None):
    if not text.strip():
        return
    previous = state.get("task")
    if previous and not any(item["id"] == previous["id"] for item in state["open_work"]):
        state["open_work"].append(previous)
    state["task"] = {"id": f"{state['life']}:{receipt}", "text": text,
                     "owner": {"source": source, "receipt": receipt, "text": text,
                               "record": str(ROOT / "runs" / state["life"] / "trace.jsonl")}}
    state["owner_applied"] = receipt
    state["owner_sequence"] = receipt if sequence is None else sequence
    state.update(assessment=None, history=[], receipts=[], waiting=False, shutdown=False, attention=True)


def save(folder, state):
    write(Path(folder) / "session.json", state)
    retained = {item["id"]: item for item in state["open_work"]}
    if state.get("task"):
        retained[state["task"]["id"]] = state["task"]
    write(ROOT / "runs" / "memory.json", list(retained.values()))


def media(value):
    paths = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("image", "audio", "path", "archive") and isinstance(child, str) and child.endswith((".png", ".wav", ".zip")):
                paths.append(child)
            else:
                paths.extend(media(child))
    elif isinstance(value, list):
        for child in value:
            paths.extend(media(child))
    return list(dict.fromkeys(paths))


class Record:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        if self.folder.parent != ROOT / "runs":
            raise ValueError("Canonical records must be direct children of runs")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.trace = self.folder / "trace.jsonl"
        self.trace.touch(exist_ok=True)
        recovered = None
        with self.trace.open("rb+") as stream:
            while data := stream.readline():
                start = stream.tell() - len(data)
                try:
                    json.loads(data)
                    if not data.endswith(b"\n"):
                        raise ValueError("Incomplete record append")
                except (ValueError, UnicodeDecodeError):
                    if stream.read(1):
                        raise RuntimeError("Record corruption before its tail; refuse to skip events")
                    recovered = self.folder / ("interrupted-append-" + uuid.uuid4().hex + ".bin")
                    with recovered.open("xb") as tail:
                        tail.write(data)
                        tail.flush()
                        os.fsync(tail.fileno())
                    stream.seek(start)
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                    break
        self.serial = max((int(path.stem) for path in self.folder.iterdir()
                           if path.is_file() and path.stem.isdigit()), default=0)
        self.changed = asyncio.Event()
        self.changed.set()
        self.task = None
        self.cursor = read(self.folder / "delivery.json", {"version": 1, "offset": 0, "piece": 0})
        self.cursor["version"] = 1
        if not 0 <= self.cursor["offset"] <= self.offset() or self.cursor["piece"] < 0:
            raise ValueError("Invalid delivery checkpoint; refuse to skip record data")
        write(self.folder / "delivery.json", self.cursor)
        registry = ROOT / "runs" / "outbox.json"
        pending = read(registry, [])
        if self.folder.name not in pending:
            write(registry, [*pending, self.folder.name])
        self.receipts = {}
        self.artifact_receipts = {}
        self.controls = []
        with self.trace.open("rb") as stream:
            for data in stream:
                event = json.loads(data)
                if event.get("mirror", True):
                    self.controls = []
                else:
                    self.controls.append(event)
                if event["kind"] == "delivery_receipt":
                    value = event["value"]
                    self.receipts[(value["offset"], value["piece"])] = value["receipt"]
                    artifact = value["receipt"].get("artifact")
                    if artifact:
                        self.artifact_receipts[(artifact["path"], artifact["sha256"])] = value["receipt"]
        if recovered:
            self.append("interrupted_record_append", {"bytes": recovered.stat().st_size}, images=[str(recovered)])

    def events(self):
        with self.trace.open("rb") as stream:
            while data := stream.readline():
                yield stream.tell() - len(data), json.loads(data)

    def artifact(self, data, extension):
        self.serial += 1
        path = self.folder / f"{self.serial:04d}.{extension}"
        with path.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def append(self, kind, value, source="TRIDENT", target="OWNER", images=(), mirror=True):
        event = {"time": stamp(), "kind": kind, "source": source, "target": target,
                 "value": value, "images": list(dict.fromkeys((*map(str, images),
                    *(path for path in media(value) if Path(path).is_absolute() and Path(path).is_relative_to(self.folder))))),
                 "mirror": mirror}
        if mirror and self.controls:
            event["delivery"] = self.controls
            self.controls = []
        with self.trace.open("ab") as stream:
            offset = stream.tell()
            stream.write((encode(event) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        self.changed.set()
        if not mirror:
            self.controls.append(event)
        return offset

    def event(self, offset):
        with self.trace.open("rb") as stream:
            stream.seek(offset)
            return json.loads(stream.readline())

    def offset(self):
        return self.trace.stat().st_size

    async def send(self, line):
        for name in read(ROOT / "runs" / "outbox.json", []):
            if name == self.folder.name:
                break
            folder = ROOT / "runs" / name
            if folder.parent != ROOT / "runs" or not (folder / "delivery.json").exists():
                raise ValueError("Invalid outbox entry")
            previous = Record(folder)
            # Terminal cleanup/control events after a prior close need forensic
            # coverage too. Leave only the checkpoint's own receipt local.
            if any(event["kind"] != "delivery_receipt" for event in previous.controls):
                previous.checkpoint()
            await previous.deliver(line, follow=False)
        await self.deliver(line, follow=True)

    async def deliver(self, line, follow):
        from telethon.errors import FloodWaitError
        while True:
            self.changed.clear()
            with self.trace.open("rb") as stream:
                stream.seek(self.cursor["offset"])
                data = stream.readline()
            if not data:
                if not follow:
                    return
                await self.changed.wait()
                continue
            event = json.loads(data)
            if event.get("mirror", True):
                header = f"{event['source']} â†’ {event['target']} | {event['kind']}"
                value = {"event": event["value"], "delivery": event["delivery"]} if event.get("delivery") else event["value"]
                body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
                pieces = [None, *event["images"]]
                if self.cursor["piece"] > len(pieces) or any(
                    (self.cursor["offset"], piece) not in self.receipts for piece in range(self.cursor["piece"])
                ):
                    raise ValueError("Delivery progress has no matching receipt; refuse to skip pieces")
                while self.cursor["piece"] < len(pieces):
                    piece = self.cursor["piece"]
                    identifier = hashlib.sha256(f"{self.folder}:{self.cursor['offset']}:{piece}".encode()).digest()
                    random_id = int.from_bytes(identifier[:8], "little", signed=True)
                    marker = "#trident_" + identifier[:12].hex()
                    try:
                        if (self.cursor["offset"], piece) in self.receipts:
                            self.cursor["piece"] += 1
                            write(self.folder / "delivery.json", self.cursor)
                            continue
                        if not line.client.is_connected():
                            await line.client.connect()
                            await line.client.catch_up()
                        if piece:
                            path = Path(pieces[piece])
                            if not path.is_file():
                                raise FileNotFoundError(f"Recorded artifact missing: {path}")
                            digest = hashlib.sha256(path.read_bytes()).hexdigest()
                            caption = header + "\n" + path.name + "\n" + marker + "\nsha256:" + digest
                            prior = self.artifact_receipts.get((str(path), digest))
                            if prior:
                                receipt = {**prior, "reused": True}
                            else:
                                receipt = {**await line.deliver_file(path, caption, random_id),
                                           "artifact": {"path": str(path), "sha256": digest}}
                            self.artifact_receipts[(str(path), digest)] = receipt
                        elif len((header + "\n\n" + body).encode("utf-16-le")) // 2 <= 3900:
                            receipt = await line.deliver_text(header + "\n\n" + body + "\n" + marker, random_id)
                        else:
                            document = io.BytesIO(data)
                            document.name = event["kind"] + ".jsonl"
                            receipt = await line.deliver_file(document, header + "\nComplete event.\n" + marker, random_id)
                        self.append("delivery_receipt", {"offset": self.cursor["offset"], "piece": piece,
                                                        "receipt": receipt}, mirror=False)
                        self.receipts[(self.cursor["offset"], piece)] = receipt
                        self.cursor["piece"] += 1
                        write(self.folder / "delivery.json", self.cursor)
                    except Exception as error:
                        self.append("delivery_failure", {"offset": self.cursor["offset"], "piece": piece,
                                                        "error": str(error)}, mirror=False)
                        await asyncio.sleep(error.seconds if isinstance(error, FloodWaitError) else 5)
            self.cursor = {"version": 1, "offset": self.cursor["offset"] + len(data), "piece": 0}
            write(self.folder / "delivery.json", self.cursor)

    def checkpoint(self):
        checkpoint = self.offset()
        document = self.folder / f"record-{checkpoint}-{uuid.uuid4().hex}.jsonl"
        with self.trace.open("rb") as source, document.open("xb") as target:
            remaining = checkpoint
            while remaining:
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    raise RuntimeError("Record changed during checkpoint")
                target.write(chunk)
                remaining -= len(chunk)
            target.flush()
            os.fsync(target.fileno())
        self.append("record_checkpoint", {"through_byte": checkpoint}, images=[str(document)])

    async def close(self):
        # A finite byte checkpoint includes control events. Its own final
        # receipt stays local, avoiding an infinite receipt-of-receipt chain.
        try:
            self.checkpoint()
            if self.task:
                try:
                    async with asyncio.timeout(10):
                        while self.cursor["offset"] < self.offset():
                            await asyncio.sleep(0.05)
                except TimeoutError:
                    pass
        finally:
            if self.task:
                await cancel(self.task)
        self.append("record_closed", {"pending_from": self.cursor}, mirror=False)
