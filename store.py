import asyncio
import json
import os
import tomllib
from contextlib import suppress
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(encode(value) + "\n", encoding="utf-8")


def stamp():
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def command(parts):
    return [os.path.expandvars(str(part)) for part in parts]


async def cancel(*tasks):
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task


def human(value):
    if isinstance(value, dict):
        return "\n\n".join(key.upper() + "\n" + human(item) for key, item in value.items())
    if isinstance(value, list):
        return "\n\n".join(human(item) for item in value) or "[]"
    return value if isinstance(value, str) else encode(value)


def media(value):
    found = []
    seen = set()

    def walk(item):
        if isinstance(item, str) and item.endswith((".png", ".wav")):
            path = Path(item)
            if str(path) not in seen and path.is_file():
                seen.add(str(path))
                found.append(path)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return found


class Record:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        numbered = []
        for path in (*self.folder.glob("*.png"), *self.folder.glob("*.wav")):
            numbered.append(int(path.stem))
        self.serial = max(numbered, default=0)
        self.pending = asyncio.Queue()
        self.task = None

    def artifact(self, data, extension):
        self.serial += 1
        path = self.folder / f"{self.serial:04d}.{extension}"
        path.write_bytes(data)
        return path

    def append(self, kind, value, source="TRIDENT", target="OWNER", images=()):
        files = []
        seen = set()
        for path in (*images, *media(value)):
            key = str(path)
            if key not in seen and Path(key).is_file():
                seen.add(key)
                files.append(key)
        event = {"time": stamp(), "kind": kind, "source": source, "target": target,
                 "value": value, "images": files}
        data = (encode(event) + "\n").encode("utf-8")
        with (self.folder / "trace.jsonl").open("ab") as stream:
            offset = stream.tell()
            stream.write(data)
        delivered = asyncio.get_running_loop().create_future()
        self.pending.put_nowait((offset, len(data), delivered))
        return delivered

    async def send(self, line):
        while (location := await self.pending.get()) is not None:
            try:
                with (self.folder / "trace.jsonl").open("rb") as stream:
                    stream.seek(location[0])
                    event = json.loads(stream.read(location[1]))
                header = event["source"] + " => " + event["target"] + " | " + event["kind"].upper()
                body = human(event["value"])
                messages = []
                for offset in range(0, max(1, len(body)), 1800):
                    chunk = body[offset:offset + 1800]
                    message = await self.deliver(lambda chunk=chunk: line.client.send_message(
                        line.owner, header + "\n\n" + chunk, parse_mode=None))
                    messages.append(message.id)
                for path in event["images"]:
                    await self.deliver(lambda path=path: line.client.send_file(
                        line.owner, path, caption=header, force_document=True, parse_mode=None))
                if not location[2].cancelled():
                    location[2].set_result({"telegram_messages": messages})
            except Exception as error:
                if not location[2].cancelled():
                    location[2].set_result({"error": type(error).__name__ + ": " + str(error)})
                line.emit("error", {"type": type(error).__name__, "message": str(error)})
                continue

    async def deliver(self, send):
        from telethon.errors import FloodWaitError
        while True:
            try:
                return await send()
            except FloodWaitError as error:
                self.append("flood_wait", {"seconds": error.seconds})
                await asyncio.sleep(error.seconds)

    async def close(self):
        if self.task is not None:
            self.pending.put_nowait(None)
            await self.task

    def trace(self, start):
        return (self.folder / "trace.jsonl").read_text(encoding="utf-8")[start:]

    def offset(self):
        return len((self.folder / "trace.jsonl").read_text(encoding="utf-8"))
