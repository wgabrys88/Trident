import array
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import i2c


def command(parts, root):
    argv = []
    for part in parts:
        text = os.path.expandvars(str(part))
        if text.startswith("artifacts/"):
            text = str(Path(root) / text)
        argv.append(text)
    return argv


def healthy(url, timeout):
    try:
        with urllib.request.urlopen(url + "/health", timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 503:
            return False
        raise
    except urllib.error.URLError:
        return False


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


def resample(samples, source_rate, target_rate):
    if source_rate == target_rate:
        return samples
    count = int(round(len(samples) * target_rate / source_rate))
    if count <= 0 or not samples:
        return array.array("h")
    output = array.array("h")
    last = len(samples) - 1
    for index in range(count):
        position = index * source_rate / target_rate
        left = int(position)
        if left >= last:
            output.append(samples[last])
            continue
        mix = samples[left] + (samples[left + 1] - samples[left]) * (position - left)
        output.append(max(-32768, min(32767, int(round(mix)))))
    return output


class Voice:
    def __init__(self, root, cfg, run, limits):
        self.root = Path(root)
        self.cfg = cfg
        self.limits = limits
        self.run = Path(run)
        self.proc = None
        self.err = bytearray()
        self.reader = None
        self.queue = []
        self.count = 0

    async def drain(self):
        while True:
            block = await self.proc.stderr.read(int(self.limits["stderr_keep"]))
            if not block:
                return
            self.err += block
            del self.err[:-int(self.limits["stderr_keep"])]

    async def start(self, limit):
        parts = command(self.cfg["command"], self.root)
        if not Path(parts[0]).is_file():
            raise RuntimeError("Chatterbox server is missing")
        for part in parts[1:]:
            if part.endswith(".gguf") and not Path(part).is_file():
                raise RuntimeError(f"Chatterbox file is missing: {part}")
        self.proc = await asyncio.create_subprocess_exec(
            *parts, cwd=str(self.root),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        self.reader = asyncio.create_task(self.drain())
        started = asyncio.get_running_loop().time()
        while self.proc.returncode is None:
            if await asyncio.to_thread(healthy, self.cfg["url"], float(self.limits["health_timeout"])):
                return
            if asyncio.get_running_loop().time() - started > limit:
                self.proc.kill()
                await self.proc.wait()
                raise RuntimeError("Chatterbox did not start")
            await asyncio.sleep(float(self.limits["health_poll"]))
        detail = self.err.decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1] if detail else "no stderr"
        raise RuntimeError(f"Chatterbox exited {self.proc.returncode}: {tail}")

    async def stop(self):
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        if self.reader is not None:
            await self.reader

    async def wav(self, text):
        payload = await asyncio.to_thread(
            synthesize, self.cfg["url"], text, float(self.limits["http_timeout"]),
        )
        self.count += 1
        path = self.run / "wav" / f"{self.count}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "voice"), run, cfg)
    phone = i2c.addr(cfg, "telegram")
    voice = Voice(root, cfg["voice"], run, cfg["limits"])

    async def on_frame(_src, line):
        data = i2c.write_payload(line)
        if not data or data[0] != 1:
            raise i2c.Nack()
        voice.queue.append(data[1:].decode())
        return i2c.pack_write(bus.addr, data)

    async def pump():
        if not voice.queue:
            return
        path = await voice.wav(voice.queue.pop(0))
        await bus.request(phone, i2c.pack_write(phone, bytes([0x20]) + str(path).encode()))

    async def live():
        await voice.start(float(cfg["busy"]["voice"]))
        try:
            await bus.run(on_frame, pump)
        finally:
            await voice.stop()

    i2c.entry(live)


def test():
    limits = i2c.load()[1]["limits"]
    source = int(limits["pcm_rate"])
    target = int(limits["play_rate"])
    samples = array.array("h", [0, 1000, -1000, 2000])
    same = resample(samples, source, source)
    assert list(same) == list(samples)
    up = resample(samples, source, target)
    assert len(up) == int(round(len(samples) * target / source))
    frame = i2c.pack_write(0x11, bytes([0x20]) + b"C:/a.wav")
    assert i2c.write_payload(frame)[:1] == b"\x20"


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
