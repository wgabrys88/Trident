import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n"


def write(data, folder):
    data = bytes(data)
    if not data.startswith(PNG):
        raise RuntimeError("Model image is not a PNG")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.png"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return path


async def send_photo(client, entity, path):
    from telethon.errors import FloodWaitError
    try:
        await client.send_file(entity, str(path), force_document=False)
    except FloodWaitError as error:
        await asyncio.sleep(error.seconds)
        await client.send_file(entity, str(path), force_document=False)


async def publish(items, sent, send, keep):
    known = set(sent)
    for path, digest in items:
        if digest in known:
            continue
        await send(path)
        sent.append(digest)
        known.add(digest)
        keep()
