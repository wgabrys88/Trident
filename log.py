import os
import uuid
from datetime import datetime
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n"


async def image(data, send, folder):
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
    await send(path)
    return path
