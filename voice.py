import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import i2c

SPEAK = 1
PLAY = 0x20


def synthesize(url, text, timeout):
    body = json.dumps({"input": text, "response_format": "wav"}).encode()
    request = urllib.request.Request(
        url + "/v1/audio/speech", data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if not payload:
        raise RuntimeError("Voice output is empty")
    return payload


class Voice:
    def __init__(self, root, cfg, run, limits):
        self.root = Path(root)
        self.cfg = cfg
        self.limits = limits
        self.run = Path(run)
        self.proc = None
        self.reader = None
        self.queue = []
        self.count = 0

    async def start(self, limit):
        self.proc, self.reader = await i2c.serve_until(
            self.root, self.cfg["command"], self.cfg["url"], self.limits, limit, "Chatterbox server is missing",
        )

    async def stop(self):
        await i2c.serve_stop(self.proc, self.reader)

    async def wav(self, text):
        payload = await asyncio.to_thread(
            synthesize, self.cfg["url"], text, float(self.limits["http_timeout"]),
        )
        self.count += 1
        path = self.run / "wav" / f"{self.count}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path


def build(bus, cfg, root, run):
    phone = i2c.addr(cfg, "telegram")
    voice = Voice(root, cfg["voice"], run, cfg["limits"])

    async def on_frame(_src, line):
        data = i2c.accept(line, SPEAK)
        voice.queue.append(data[1:].decode())
        return i2c.pack_write(bus.addr, data)

    async def pump():
        if not voice.queue:
            return
        path = await voice.wav(voice.queue.pop(0))
        await bus.request(phone, i2c.pack_write(phone, bytes([PLAY]) + str(path).encode()))

    async def around(serve):
        await voice.start(float(cfg["start"]["voice"]))
        try:
            await serve
        finally:
            await voice.stop()

    return on_frame, pump, around


def main():
    i2c.main_for("voice", build)


if __name__ == "__main__":
    main()
