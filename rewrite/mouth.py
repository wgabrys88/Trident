import array
import asyncio
import io
import json
import os
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path


def command(parts):
    return [os.path.expandvars(str(part)) for part in parts]


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


def pcm48(payload):
    with wave.open(io.BytesIO(payload), "rb") as source:
        if source.getsampwidth() != 2:
            raise RuntimeError("Voice output must be PCM16 WAV")
        rate = source.getframerate()
        channels = source.getnchannels()
        frames = source.readframes(source.getnframes())
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if channels > 1:
        mixed = array.array("h")
        for index in range(0, len(samples), channels):
            mixed.append(int(sum(samples[index:index + channels]) / channels))
        samples = mixed
    samples = resample(samples, rate, 48000)
    if not samples:
        raise RuntimeError("Voice output is empty")
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def healthy(url):
    try:
        with urllib.request.urlopen(url + "/health", timeout=2) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 503:
            return False
        raise
    except urllib.error.URLError:
        return False


def synthesize(url, text):
    body = json.dumps({"input": text, "response_format": "wav"}).encode("utf-8")
    request = urllib.request.Request(
        url + "/v1/audio/speech", data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        payload = response.read()
    if not payload:
        raise RuntimeError("Voice output is empty")
    return payload


class Mouth:
    def __init__(self, root, cfg):
        self.root = Path(root)
        self.cfg = cfg
        self.proc = None
        self.err = bytearray()
        self.reader = None

    async def drain(self):
        while True:
            block = await self.proc.stderr.read(4096)
            if not block:
                return
            self.err += block
            del self.err[:-4000]

    async def start(self):
        parts = command(self.cfg["command"])
        binary = self.root / parts[0] if not Path(parts[0]).is_absolute() else Path(parts[0])
        if not binary.is_file():
            raise RuntimeError("Chatterbox server is missing")
        for part in parts[1:]:
            if str(part).startswith("artifacts/") and not (self.root / part).is_file():
                raise RuntimeError(f"Chatterbox file is missing: {part}")
        self.proc = await asyncio.create_subprocess_exec(
            *parts, cwd=str(self.root),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        self.reader = asyncio.create_task(self.drain())
        while self.proc.returncode is None:
            if await asyncio.to_thread(healthy, self.cfg["url"]):
                return
            await asyncio.sleep(0.2)
            if self.proc.returncode is not None:
                break
        detail = self.err.decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1] if detail else "no stderr"
        raise RuntimeError(f"Chatterbox exited {self.proc.returncode}: {tail}")

    async def wav(self, text):
        return await asyncio.to_thread(synthesize, self.cfg["url"], text)

    async def stop(self):
        if self.proc is not None and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        if self.reader is not None:
            await self.reader
            self.reader = None
