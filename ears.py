import array
import asyncio
import ctypes
import os
import sys
from pathlib import Path

import i2c


class Backend(ctypes.Structure):
    _fields_ = [("size", ctypes.c_size_t), ("gpu", ctypes.c_int32)]


class Model(ctypes.Structure):
    _fields_ = [("size", ctypes.c_size_t), ("path", ctypes.c_char_p), ("name", ctypes.c_char_p)]


class Recognizer(ctypes.Structure):
    _fields_ = [("size", ctypes.c_size_t)] + [
        (name, ctypes.c_void_p)
        for name in (
            "backend", "model", "streaming", "decoder", "vad",
            "endpointing", "postproc", "diar", "batching",
        )
    ]


def rms(frame):
    count = len(frame) // 2
    if not count:
        return 0.0
    total = 0
    for index in range(0, count * 2, 2):
        sample = int.from_bytes(frame[index:index + 2], "little", signed=True)
        total += sample * sample
    return (total / count) ** 0.5 / 32768


def floats(pcm):
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    return array.array("f", (sample / 32768 for sample in samples))


class Vad:
    def __init__(self, cfg):
        self.rms_limit = float(cfg["rms_threshold"])
        self.silence = int(cfg["silence_ms"])
        self.frame_ms = int(cfg["frame_ms"])
        self.frame_bytes = int(cfg["pcm_rate"]) * 2 * self.frame_ms // 1000
        self.lead_limit = max(1, int(cfg["padding_ms"]) // self.frame_ms)
        self.utterance = float(cfg["utterance_seconds"])
        self.pending = b""
        self.lead = []
        self.parts = []
        self.quiet = 0

    def cut(self, pcm):
        ready = []
        self.pending += pcm
        while len(self.pending) >= self.frame_bytes:
            frame, self.pending = self.pending[:self.frame_bytes], self.pending[self.frame_bytes:]
            loud = rms(frame) >= self.rms_limit
            if not self.parts:
                self.lead.append(frame)
                if len(self.lead) > self.lead_limit:
                    self.lead = self.lead[-self.lead_limit:]
                if loud:
                    self.parts = self.lead
                    self.lead = []
                    self.quiet = 0
            else:
                self.parts.append(frame)
                self.quiet = 0 if loud else self.quiet + self.frame_ms
                if self.quiet >= self.silence or len(self.parts) * (self.frame_ms / 1000) >= self.utterance:
                    ready.append(b"".join(self.parts))
                    self.parts = []
                    self.quiet = 0
        return ready

    def flush(self):
        audio = b"".join(self.parts) + self.pending if self.parts else b""
        self.pending = b""
        self.lead = []
        self.parts = []
        self.quiet = 0
        return audio

    def take(self, pcm):
        ready = self.cut(pcm)
        tail = self.flush()
        if tail:
            ready.append(tail)
        return ready


class Ear:
    def __init__(self, root, cfg):
        self.root = Path(root)
        self.cfg = cfg
        self.library = None
        self.recognizer = ctypes.c_void_p()
        self.directory = None
        self.path_bytes = None

    def bind(self, name, result, *arguments):
        function = getattr(self.library, "nemo_speech_asr_" + name)
        function.restype = result
        function.argtypes = arguments
        return function

    def check(self, status):
        if status:
            detail = self.last_error()
            message = detail.decode() if detail else str(status)
            raise RuntimeError("Nemotron: " + message)

    def load(self):
        library = self.root / self.cfg["library"]
        model = self.root / self.cfg["model"]
        if not library.is_file() or not model.is_file():
            raise RuntimeError("Ears model or library is missing")
        self.directory = os.add_dll_directory(str(library.parent))
        self.library = ctypes.CDLL(str(library))
        pointer = ctypes.c_void_p
        self.last_error = self.bind("last_error", ctypes.c_char_p)
        self.destroy = self.bind("destroy", None, pointer)
        self.recognize = self.bind(
            "recognize_f32", ctypes.c_int, pointer, pointer,
            ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_int32, ctypes.POINTER(pointer),
        )
        self.result_count = self.bind("result_alternative_count", ctypes.c_size_t, pointer)
        self.result_text = self.bind("result_transcript", ctypes.c_char_p, pointer, ctypes.c_size_t)
        self.result_free = self.bind("result_destroy", None, pointer)
        create = self.bind("create", ctypes.c_int, ctypes.POINTER(Recognizer), ctypes.POINTER(pointer))
        backend = Backend(ctypes.sizeof(Backend), -1)
        self.path_bytes = str(model).encode()
        weights = Model(ctypes.sizeof(Model), self.path_bytes, None)
        config = Recognizer()
        config.size = ctypes.sizeof(Recognizer)
        config.backend = ctypes.addressof(backend)
        config.model = ctypes.addressof(weights)
        self.check(create(ctypes.byref(config), ctypes.byref(self.recognizer)))

    def decode(self, pcm, rate):
        values = floats(pcm)
        if not values:
            return ""
        result = ctypes.c_void_p()
        samples = ctypes.cast(values.buffer_info()[0], ctypes.POINTER(ctypes.c_float))
        self.check(self.recognize(
            self.recognizer, None, samples, len(values), rate, ctypes.byref(result),
        ))
        try:
            if not result.value or not self.result_count(result):
                return ""
            raw = self.result_text(result, 0)
            return "" if not raw else raw.decode()
        finally:
            if result.value:
                self.result_free(result)

    def close(self):
        if self.recognizer.value:
            self.destroy(self.recognizer)
            self.recognizer = ctypes.c_void_p()
        if self.directory is not None:
            self.directory.close()
            self.directory = None


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "ears"), run, cfg)
    luna = i2c.addr(cfg, "luna")
    ear = Ear(root, cfg["ears"])
    heard = dict(cfg["ears"])
    heard["frame_ms"] = cfg["limits"]["frame_ms"]
    heard["pcm_rate"] = cfg["limits"]["pcm_rate"]
    vad = Vad(heard)
    rate = int(cfg["limits"]["pcm_rate"])
    queue = []

    async def on_frame(_src, line):
        data = i2c.write_payload(line)
        if not data or data[0] != 1:
            raise i2c.Nack()
        queue.append(data[1:].decode())
        return i2c.pack_write(bus.addr, data)

    async def pump():
        if not queue:
            return
        path = queue.pop(0)
        for piece in vad.take(Path(path).read_bytes()):
            text = (await asyncio.to_thread(ear.decode, piece, rate)).strip()
            if text:
                await bus.request(luna, i2c.pack_write(luna, text.encode()))

    async def live():
        try:
            await asyncio.wait_for(asyncio.to_thread(ear.load), float(cfg["busy"]["ears"]))
            await bus.run(on_frame, pump)
        finally:
            ear.close()

    i2c.entry(live)


def test():
    full = i2c.load()[1]
    cfg = dict(full["ears"])
    cfg["frame_ms"] = full["limits"]["frame_ms"]
    cfg["pcm_rate"] = full["limits"]["pcm_rate"]
    cfg["silence_ms"] = 40
    cfg["padding_ms"] = int(cfg["frame_ms"])
    vad = Vad(cfg)
    samples = vad.frame_bytes // 2
    loud = (b"\xff\x0f" * samples) * 3 + (b"\x00\x00" * samples) * 4
    pieces = vad.take(loud)
    assert pieces and len(pieces[0]) >= vad.frame_bytes
    frame = i2c.pack_write(0x16, b"hello")
    assert frame.startswith("S 16 W A")
    assert i2c.write_payload(i2c.pack_write(0x12, bytes([1]) + b"C:/a.pcm"))[:1] == b"\x01"


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
