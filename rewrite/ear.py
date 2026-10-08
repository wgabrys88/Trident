import array
import asyncio
import ctypes
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


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
    if count == 0:
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
    values = array.array("f", (sample / 32768 for sample in samples))
    return values


class Ear:
    def __init__(self, root, cfg):
        self.root = Path(root)
        self.cfg = cfg
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ear")
        self.directory = None
        self.library = None
        self.recognizer = ctypes.c_void_p()
        self.path_bytes = None
        self.pending = b""
        self.lead = []
        self.parts = []
        self.quiet = 0
        self.lead_limit = max(1, cfg["padding_ms"] // 20)

    def bind(self, name, result, *arguments):
        function = getattr(self.library, "nemo_speech_asr_" + name)
        function.restype = result
        function.argtypes = arguments
        return function

    def check(self, status):
        if status:
            detail = self.last_error()
            message = detail.decode("utf-8", "replace") if detail else str(status)
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
        self.path_bytes = str(model).encode("utf-8")
        weights = Model(ctypes.sizeof(Model), self.path_bytes, None)
        config = Recognizer()
        config.size = ctypes.sizeof(Recognizer)
        config.backend = ctypes.addressof(backend)
        config.model = ctypes.addressof(weights)
        self.check(create(ctypes.byref(config), ctypes.byref(self.recognizer)))

    def decode(self, pcm):
        values = floats(pcm)
        if not values:
            return ""
        result = ctypes.c_void_p()
        samples = ctypes.cast(values.buffer_info()[0], ctypes.POINTER(ctypes.c_float))
        self.check(self.recognize(
            self.recognizer, None, samples, len(values), 16000, ctypes.byref(result),
        ))
        try:
            if not result.value or not self.result_count(result):
                return ""
            raw = self.result_text(result, 0)
            return "" if not raw else raw.decode("utf-8")
        finally:
            if result.value:
                self.result_free(result)

    async def start(self):
        await asyncio.get_running_loop().run_in_executor(self.executor, self.load)

    async def transcribe(self, pcm):
        return await asyncio.get_running_loop().run_in_executor(self.executor, self.decode, pcm)

    def cut(self, pcm):
        ready = []
        self.pending += pcm
        while len(self.pending) >= 640:
            frame, self.pending = self.pending[:640], self.pending[640:]
            voiced = rms(frame) >= self.cfg["rms_threshold"]
            if not self.parts:
                self.lead.append(frame)
                if len(self.lead) > self.lead_limit:
                    self.lead = self.lead[-self.lead_limit:]
                if voiced:
                    self.parts = self.lead
                    self.lead = []
                    self.quiet = 0
            else:
                self.parts.append(frame)
                self.quiet = 0 if voiced else self.quiet + 20
                long = len(self.parts) * 0.02 >= self.cfg["utterance_seconds"]
                if self.quiet >= self.cfg["silence_ms"] or long:
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

    def unload(self):
        if self.recognizer.value:
            self.destroy(self.recognizer)
            self.recognizer = ctypes.c_void_p()

    async def stop(self):
        if self.library is not None:
            await asyncio.get_running_loop().run_in_executor(self.executor, self.unload)
        self.executor.shutdown(wait=True)
        if self.directory is not None:
            self.directory.close()
            self.directory = None
