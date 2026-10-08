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


def floats(pcm):
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    return array.array("f", (sample / 32768 for sample in samples))


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


HEAR = 1


def build(bus, cfg, root, _run):
    mind = i2c.addr(cfg, "mind")
    ear = Ear(root, cfg["ears"])
    rate = int(cfg["limits"]["pcm_rate"])
    queue = []

    async def on_frame(_src, line):
        data = i2c.accept(line, HEAR)
        queue.append(data[1:].decode())
        return i2c.pack_write(bus.addr, data)

    async def pump():
        if not queue:
            return
        pcm = Path(queue.pop(0)).read_bytes()
        text = (await asyncio.to_thread(ear.decode, pcm, rate)).strip()
        if text:
            await bus.request(mind, i2c.pack_write(mind, text.encode()))

    async def around(serve):
        try:
            await asyncio.wait_for(asyncio.to_thread(ear.load), float(cfg["start"]["ears"]))
            await serve
        finally:
            ear.close()

    return on_frame, pump, around


def main():
    i2c.main_for("ears", build)


if __name__ == "__main__":
    main()
