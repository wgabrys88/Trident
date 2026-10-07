import asyncio
import ctypes as c
import io
import os
import uuid
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from store import CONFIG, ROOT


class Backend(c.Structure):
    _fields_ = [("size", c.c_size_t), ("gpu", c.c_int32)]


class Model(c.Structure):
    _fields_ = [("size", c.c_size_t), ("path", c.c_char_p), ("name", c.c_char_p)]


class RecognizerConfig(c.Structure):
    _fields_ = [("size", c.c_size_t)] + [
        (name, c.c_void_p) for name in
        ("backend", "model", "streaming", "decoder", "vad", "endpointing", "postproc", "diar", "batching")
    ]


class Hearing:
    def __init__(self, record, emit):
        self.record, self.emit = record, emit
        self.queue = asyncio.Queue()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nemotron-cpu")
        self.recognizer = c.c_void_p()
        self.dll_directory = None
        self.task = None
        self.busy = False

    def bind(self, name, result, *arguments):
        function = getattr(self.library, "nemo_speech_asr_" + name)
        function.restype, function.argtypes = result, arguments
        return function

    def check(self, status):
        if status:
            raise RuntimeError("Nemotron: " + self.last_error().decode("utf-8"))

    def load(self):
        path = ROOT / CONFIG["ears"]["library"]
        model_path = ROOT / CONFIG["ears"]["model"]
        model_path.stat()
        self.dll_directory = os.add_dll_directory(str(path.parent))
        self.library = c.CDLL(str(path))
        pointer = c.c_void_p
        self.last_error = self.bind("last_error", c.c_char_p)
        self.destroy = self.bind("destroy", None, pointer)
        self.start = self.bind("streaming_recognize", c.c_int, pointer, pointer, c.POINTER(pointer))
        self.push = self.bind("stream_push_f32", c.c_int, pointer, c.POINTER(c.c_float), c.c_size_t, c.c_int32)
        self.finish = self.bind("stream_finish", c.c_int, pointer)
        self.next = self.bind("stream_next", c.c_int, pointer, c.POINTER(pointer))
        self.close_stream = self.bind("stream_close", None, pointer)
        self.destroy_result = self.bind("result_destroy", None, pointer)
        self.transcript = self.bind("result_transcript", c.c_char_p, pointer, c.c_size_t)
        self.confidence = self.bind("result_confidence", c.c_float, pointer, c.c_size_t)
        self.final = self.bind("result_is_final", c.c_bool, pointer)
        self.processed = self.bind("result_audio_processed", c.c_float, pointer)
        self.alternatives = self.bind("result_alternative_count", c.c_size_t, pointer)
        create = self.bind("create", c.c_int, c.POINTER(RecognizerConfig), c.POINTER(pointer))
        backend = Backend(c.sizeof(Backend), -1)
        model = Model(c.sizeof(Model), str(model_path).encode("utf-8"), None)
        config = RecognizerConfig()
        config.size = c.sizeof(config)
        config.backend, config.model = c.addressof(backend), c.addressof(model)
        self.check(create(c.byref(config), c.byref(self.recognizer)))

    def read_result(self, result):
        alternatives = []
        for index in range(self.alternatives(result)):
            raw = self.transcript(result, index)
            alternatives.append({
                "text": "" if not raw else raw.decode("utf-8"),
                "confidence": self.confidence(result, index),
            })
        text = alternatives[0]["text"] if alternatives else ""
        return {"text": text, "alternatives": alternatives, "final": bool(self.final(result)),
                "audio_processed": self.processed(result)}

    def pull(self, stream):
        final = None
        while True:
            result = c.c_void_p()
            self.check(self.next(stream, c.byref(result)))
            if not result.value:
                return final
            try:
                item = self.read_result(result)
            finally:
                self.destroy_result(result)
            if item["final"]:
                final = item

    def recognize(self, pcm):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        seconds = len(samples) / 16000
        stream = c.c_void_p()
        self.check(self.start(self.recognizer, None, c.byref(stream)))
        try:
            final = None
            offset, chunk = 0, 2560
            while offset < len(samples):
                piece = np.ascontiguousarray(samples[offset:offset + chunk])
                offset += chunk
                self.check(self.push(stream, piece.ctypes.data_as(c.POINTER(c.c_float)), piece.shape[0], 16000))
                found = self.pull(stream)
                if found is not None:
                    final = found
            self.check(self.finish(stream))
            found = self.pull(stream)
            if found is not None:
                final = found
            if final is None:
                final = {"text": "", "alternatives": [], "final": True, "audio_processed": 0.0}
            return {**final, "audio_seconds": seconds}
        finally:
            self.close_stream(stream)

    async def open(self, pending=()):
        saved_pending = list(pending)
        for observation in saved_pending:
            with wave.open(observation["path"], "rb") as source:
                self.queue.put_nowait((source.readframes(source.getnframes()), observation))
        loading = asyncio.get_running_loop().run_in_executor(self.executor, self.load)
        try:
            await asyncio.shield(loading)
        except asyncio.CancelledError:
            await loading
            raise
        self.record.append("hearing_ready", {"model": CONFIG["ears"]["model"], "device": "cpu"})
        self.task = asyncio.create_task(self.listen())

    def submit(self, pcm):
        identifier = uuid.uuid4().hex
        path = self.record.artifact(wav(pcm, 16000), "wav")
        observation = {"id": identifier, "path": str(path)}
        observation["source_receipt"] = self.record.append("owner_audio", observation, "OWNER", "NEMOTRON")
        self.queue.put_nowait((pcm, observation))
        self.emit("audio_pending", observation)

    async def listen(self):
        try:
            while True:
                item = await self.queue.get()
                if item is None:
                    return
                pcm, observation = item
                self.busy = True
                self.record.append("asr_started", observation)
                recognition = await asyncio.get_running_loop().run_in_executor(self.executor, self.recognize, pcm)
                self.busy = False
                observation = {**observation, "recognition": recognition}
                self.record.append("asr_result", observation, "NEMOTRON", "LUNA")
                self.emit("audio", observation)
        except Exception as error:
            self.emit("error", error)
        finally:
            self.busy = False

    async def close(self):
        failures = []
        try:
            if self.task is not None:
                self.queue.put_nowait(None)
                await self.task
            if self.recognizer.value:
                await asyncio.get_running_loop().run_in_executor(self.executor, self.destroy, self.recognizer)
                self.recognizer = c.c_void_p()
        except Exception as error:
            failures.append(str(error))
        finally:
            self.executor.shutdown(wait=True)
            if self.dll_directory is not None:
                self.dll_directory.close()
        self.record.append("hearing_closed", {"failures": failures})
        if failures:
            raise RuntimeError("; ".join(failures))


def wav(pcm, rate):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)
    return stream.getvalue()


def call_pcm(payload):
    with wave.open(io.BytesIO(payload), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("Voice output must be PCM16 WAV")
        rate = source.getframerate()
        channels = source.getnchannels()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32)
    samples = samples.reshape(-1, channels).mean(axis=1)
    positions = np.arange(round(len(samples) * 48000 / rate)) * rate / 48000
    return np.rint(np.interp(positions, np.arange(len(samples)), samples)).astype("<i2").tobytes()


class Utterances:
    def __init__(self):
        self.cfg = CONFIG["ears"]
        self.reset()

    def reset(self):
        self.pending = b""
        self.lead = deque(maxlen=self.cfg["padding_ms"] // 20)
        self.parts = []
        self.quiet = 0

    def feed(self, pcm):
        self.pending += pcm
        while len(self.pending) >= 640:
            frame, self.pending = self.pending[:640], self.pending[640:]
            samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
            voiced = np.sqrt(np.mean(samples * samples)) >= self.cfg["rms_threshold"]
            if not self.parts:
                self.lead.append(frame)
                if voiced:
                    self.parts = list(self.lead)
                    self.lead.clear()
            else:
                self.parts.append(frame)
                self.quiet = 0 if voiced else self.quiet + 20
                if self.quiet >= self.cfg["silence_ms"] or len(self.parts) * 0.02 >= self.cfg["utterance_seconds"]:
                    yield b"".join(self.parts)
                    self.parts = []
                    self.quiet = 0

    def finish(self):
        result = b"".join(self.parts) + self.pending if self.parts else b""
        self.reset()
        return result
