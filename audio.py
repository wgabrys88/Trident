import asyncio
import ctypes
import os
import sys
from pathlib import Path

import numpy as np

from i2c import QueuedDevice, Server


class Backend(ctypes.Structure):
    _fields_ = [("size", ctypes.c_size_t), ("gpu", ctypes.c_int32)]


class Model(ctypes.Structure):
    _fields_ = [("size", ctypes.c_size_t), ("path", ctypes.c_char_p), ("name", ctypes.c_char_p)]


class Recognizer(ctypes.Structure):
    _fields_ = [("size", ctypes.c_size_t)] + [(name, ctypes.c_void_p) for name in
        ("backend", "model", "streaming", "decoder", "vad", "endpointing", "postproc", "diar", "batching")]


class Ears(QueuedDevice):
    def bind(self, name, result, *arguments):
        function = getattr(self.library, "nemo_speech_asr_" + name)
        function.restype, function.argtypes = result, arguments
        return function

    def check(self, status):
        if status:
            raise RuntimeError(self.last_error().decode())

    async def start(self):
        self.recognizer = ctypes.c_void_p()
        library = self.cfg.path(self.cfg["ears"]["library"])
        self.directory = os.add_dll_directory(str(library.parent))
        self.library = ctypes.CDLL(str(library))
        pointer = ctypes.c_void_p
        self.last_error = self.bind("last_error", ctypes.c_char_p)
        self.recognize = self.bind("recognize_f32", ctypes.c_int, pointer, pointer,
                                   ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_int32,
                                   ctypes.POINTER(pointer))
        self.transcript = self.bind("result_transcript", ctypes.c_char_p, pointer, ctypes.c_size_t)
        self.free = self.bind("result_destroy", None, pointer)
        create = self.bind("create", ctypes.c_int, ctypes.POINTER(Recognizer), ctypes.POINTER(pointer))
        self.backend = Backend(ctypes.sizeof(Backend), self.cfg["ears"]["gpu"])
        self.model = Model(ctypes.sizeof(Model), str(self.cfg.path(self.cfg["ears"]["model"])).encode(), None)
        configuration = Recognizer()
        configuration.size = ctypes.sizeof(Recognizer)
        configuration.backend, configuration.model = ctypes.addressof(self.backend), ctypes.addressof(self.model)
        await asyncio.wait_for(asyncio.to_thread(self.create, create, configuration), self.cfg["ears"]["start_seconds"])

    def create(self, function, configuration):
        self.check(function(ctypes.byref(configuration), ctypes.byref(self.recognizer)))

    def decode(self, path):
        samples = np.frombuffer(Path(path).read_bytes(), dtype="<i2").astype(np.float32)
        samples /= np.iinfo(np.int16).max + 1
        result = ctypes.c_void_p()
        self.check(self.recognize(self.recognizer, None,
                                  samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                  len(samples), self.cfg["audio"]["receive_rate"], ctypes.byref(result)))
        try:
            return self.transcript(result, 0).decode().strip()
        finally:
            self.free(result)

    async def work(self, source, frame):
        if text := await asyncio.to_thread(self.decode, frame.data[1:].decode()):
            await self.send("mind", text, "01")


class Voice(QueuedDevice):
    def __init__(self, name):
        super().__init__(name)
        self.server = Server(self, self.cfg["voice"])
        self.sequence = 0

    async def work(self, source, frame):
        body = {"input": frame.data[1:].decode(), "response_format": "wav"}
        audio = await asyncio.to_thread(self.server.request, "/v1/audio/speech", body)
        self.sequence += 1
        path = self.file(f"{self.sequence}.wav")
        path.write_bytes(audio)
        await self.send("telegram", str(path), "20")


if __name__ == "__main__":
    {"ears": Ears, "voice": Voice}[sys.argv[2]](sys.argv[2]).launch()
