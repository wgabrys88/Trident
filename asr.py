from config import ensure_venv
ensure_venv()
if __name__ == "__main__":
    from main import main
    raise SystemExit(main("asr"))

import ctypes
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import sounddevice as sd
from silero_vad_notorch import VADIterator, load_silero_vad

from config import ASR_LOCALES, ASR_RATE, PARAKEET_FILE, RUNTIMES, SMART_TURN_FILE, VAD_FRAME, Paths, Wasapi, find_exe, load_settings
from journal import finish_cleanup, join_or_fail

STOP_PHRASES = {
    "stop", "stop speaking", "please stop", "that's enough", "thats enough", "quiet", "silence",
    "przestan", "przestań", "przestan mowic", "przestań mówić", "dosyc", "dość", "cicho", "milcz",
}
BACKCHANNELS = {"yeah", "yes", "yep", "yup", "ok", "okay", "mhm", "mm", "uh", "um", "aha", "uh huh", "huh", "right", "sure", "tak", "no", "nie", "okej"}
PARAKEET_EVENT_EOU, PARAKEET_EVENT_EOB = 1, 2
_EOF = object()


def folded_utterance(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.casefold().replace("\r", " ").replace("\n", " ")).split())


def classify_utterance(text: str) -> str:
    folded = folded_utterance(text)
    if folded in STOP_PHRASES or any(folded.startswith(phrase + " ") for phrase in STOP_PHRASES): return "stop"
    return "backchannel" if folded in BACKCHANNELS else "request"


class StreamingASR:
    """One load-once parakeet.cpp C-API context and one cache-aware stream per acoustic turn."""
    def __init__(self, paths: Paths, language: str) -> None:
        root = RUNTIMES / "parakeet"
        dll = find_exe(root, "parakeet.dll")
        if dll is None: raise RuntimeError("parakeet.dll missing; run python main.py install")
        if paths.asr_device: os.environ["PARAKEET_DEVICE"] = paths.asr_device
        self._dll_dirs = [os.add_dll_directory(str(p)) for p in {dll.parent, root} if p.is_dir() and hasattr(os, "add_dll_directory")]
        os.environ["PATH"] = str(dll.parent) + os.pathsep + os.environ.get("PATH", "")
        self.lib = ctypes.CDLL(str(dll))
        self._bind()
        abi = int(self.lib.parakeet_capi_abi_version())
        if abi < 5: raise RuntimeError(f"parakeet C API ABI {abi} lacks streaming event semantics")
        self.ctx = self.lib.parakeet_capi_load(os.fsencode(paths.models_dir / PARAKEET_FILE))
        if not self.ctx: raise RuntimeError("parakeet model load failed: " + self._error())
        self.locale = ASR_LOCALES[language].encode("ascii")
        self.stream = None; self.text = ""; self.feed_calls = 0
        paths.journal.emit("asr", "resident.ready", mode="capi-stream", abi=abi, model=PARAKEET_FILE,
                           locale=self.locale.decode(), device=paths.asr_device or "auto", library=str(dll))

    def _bind(self) -> None:
        L = self.lib
        L.parakeet_capi_abi_version.restype = ctypes.c_int
        L.parakeet_capi_load.argtypes = [ctypes.c_char_p]; L.parakeet_capi_load.restype = ctypes.c_void_p
        L.parakeet_capi_free.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_stream_begin_lang.argtypes = [ctypes.c_void_p, ctypes.c_char_p]; L.parakeet_capi_stream_begin_lang.restype = ctypes.c_void_p
        L.parakeet_capi_stream_feed.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        L.parakeet_capi_stream_feed.restype = ctypes.c_void_p
        L.parakeet_capi_stream_finalize.argtypes = [ctypes.c_void_p]; L.parakeet_capi_stream_finalize.restype = ctypes.c_void_p
        L.parakeet_capi_stream_free.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_free_string.argtypes = [ctypes.c_void_p]
        L.parakeet_capi_last_error.argtypes = [ctypes.c_void_p]; L.parakeet_capi_last_error.restype = ctypes.c_char_p

    def _error(self) -> str:
        raw = self.lib.parakeet_capi_last_error(getattr(self, "ctx", None))
        return raw.decode("utf-8", "replace") if raw else "unknown error"

    def _take(self, ptr) -> str:
        if not ptr: return ""
        try: return ctypes.string_at(ptr).decode("utf-8", "replace")
        finally: self.lib.parakeet_capi_free_string(ptr)

    def begin(self) -> None:
        if self.stream: raise RuntimeError("parakeet stream already active")
        self.stream = self.lib.parakeet_capi_stream_begin_lang(self.ctx, self.locale)
        if not self.stream: raise RuntimeError("parakeet stream begin failed: " + self._error())
        self.text = ""; self.feed_calls = 0

    def feed(self, pcm: bytes) -> tuple[str, int, float]:
        if not self.stream: raise RuntimeError("parakeet stream is not active")
        samples = np.frombuffer(pcm, dtype="<f4")
        event, started = ctypes.c_int(), time.perf_counter()
        ptr = self.lib.parakeet_capi_stream_feed(self.stream, samples.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), samples.size, ctypes.byref(event))
        delta = self._take(ptr)
        if delta: self.text += delta
        self.feed_calls += 1
        return self.text.strip(), int(event.value), (time.perf_counter() - started) * 1000

    def finalize(self) -> str:
        if not self.stream: return ""
        try:
            tail = self._take(self.lib.parakeet_capi_stream_finalize(self.stream))
            if tail: self.text += tail
            return self.text.strip()
        finally:
            self.lib.parakeet_capi_stream_free(self.stream); self.stream = None

    def abort(self) -> None:
        if self.stream:
            self.lib.parakeet_capi_stream_free(self.stream); self.stream = None
        self.text = ""

    def close(self) -> None:
        self.abort()
        if self.ctx: self.lib.parakeet_capi_free(self.ctx); self.ctx = None
        self._dll_dirs.clear()


def _mel_filters() -> np.ndarray:
    def hz_to_mel(hz):
        hz = np.asarray(hz, dtype=np.float64); mel, high = hz / (200.0 / 3.0), hz >= 1000.0
        mel[high] = 15.0 + np.log(hz[high] / 1000.0) / (np.log(6.4) / 27.0); return mel
    def mel_to_hz(mel):
        mel = np.asarray(mel, dtype=np.float64)
        return np.where(mel >= 15.0, 1000.0 * np.exp((np.log(6.4) / 27.0) * (mel - 15.0)), (200.0 / 3.0) * mel)
    centers = mel_to_hz(np.linspace(hz_to_mel([0.0])[0], hz_to_mel([8000.0])[0], 82)); bins = np.linspace(0.0, ASR_RATE / 2.0, 201)
    return np.maximum(0.0, np.minimum((bins[:, None] - centers[:-2]) / (centers[1:-1] - centers[:-2]), (centers[2:] - bins[:, None]) / (centers[2:] - centers[1:-1]))) * 2.0 / (centers[2:] - centers[:-2])


_FILTERS, _WINDOW = _mel_filters(), np.hanning(401)[:-1]


class SmartTurn:
    def __init__(self, model: Path, threshold: float, context_seconds: float) -> None:
        options = ort.SessionOptions(); options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = options.intra_op_num_threads = 1; options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        self.threshold, self.samples = float(threshold), int(float(context_seconds) * ASR_RATE)

    def decide(self, pcm: bytes) -> tuple[bool, float]:
        audio = np.frombuffer(pcm, dtype="<f4")[-self.samples:]
        if audio.size < self.samples: audio = np.pad(audio, (self.samples - audio.size, 0))
        if audio.size: audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
        frames = np.lib.stride_tricks.sliding_window_view(np.pad(audio, (200, 200), mode="reflect"), 400)[::160]
        spec = np.log10(np.maximum(np.abs(np.fft.rfft(frames * _WINDOW, n=400, axis=1)) ** 2 @ _FILTERS, 1e-10)).T[:, :-1]
        p = float(self.session.run(None, {"input_features": ((np.maximum(spec, spec.max() - 8.0) + 4.0) / 4.0).astype(np.float32)[None]})[0][0].item())
        return p > self.threshold, p


class Source(Wasapi):
    kind, component, ready_event, stop_event, rate_key, peer_rate = "input", "capture", "source.ready", "source.stopped", "capture_rate", ASR_RATE
    def __init__(self, frame_cb, paths: Paths) -> None:
        super().__init__(paths); self.frame_cb, self.pending = frame_cb, np.empty(0, dtype=np.float32)
    def _callback(self, indata, frames, _timing, status) -> None:
        if status: self.error = RuntimeError(f"WASAPI capture: {status}"); raise sd.CallbackAbort
        self.pending = np.concatenate((self.pending, np.frombuffer(indata, dtype="<f4", count=frames)))
        while self.pending.size >= VAD_FRAME:
            self.frame_cb(self.pending[:VAD_FRAME]); self.pending = self.pending[VAD_FRAME:]


class Capture:
    def __init__(self, paths: Paths, settings: dict, language: str, on_start, on_utterance, on_partial=None, on_resume=None) -> None:
        self.paths, self.journal = paths, paths.journal; self.on_start, self.on_utterance = on_start, on_utterance
        self.on_partial, self.on_resume = on_partial or (lambda *_: None), on_resume or (lambda *_: None)
        self.vad = VADIterator(load_silero_vad(onnx=True), threshold=.5, sampling_rate=ASR_RATE, min_silence_duration_ms=int(settings["candidate_silence_ms"]), speech_pad_ms=0)
        self.smart = SmartTurn(paths.models_dir / SMART_TURN_FILE, settings["completion_threshold"], settings["acoustic_context_seconds"])
        self.asr = StreamingASR(paths, language); self.frames: queue.SimpleQueue = queue.SimpleQueue(); self.decisions: queue.SimpleQueue = queue.SimpleQueue()
        self.audio = bytearray(); self.state_lock = threading.Lock(); self.active = self.utterance = False
        self.utterance_id = self.generation = self.accepted_turns = 0; self.last_partial = ""; self.source = Source(self.frame, paths)
        self.vad_thread = self.decision_thread = None

    def frame(self, samples: np.ndarray) -> None:
        if self.active: self.frames.put(np.asarray(samples, dtype="<f4").tobytes())

    def open(self) -> None:
        self.active = True; self.decision_thread = self.paths.supervisor.start("smart-turn", self._decide); self.vad_thread = self.paths.supervisor.start("vad-asr", self._loop)
        self.source.open(); self.journal.emit("capture", "ready")

    def _decide(self) -> None:
        self.journal.emit("smart-turn", "start")
        while (item := self.decisions.get()) is not _EOF:
            uid, generation, audio = item; started = time.perf_counter(); complete, probability = self.smart.decide(audio)
            self.journal.emit("smart-turn", "completed", utterance_id=uid, candidate_generation=generation, complete=complete, probability=round(probability, 6), decision_ms=round((time.perf_counter() - started) * 1000, 3), input_s=round(len(audio) / (ASR_RATE * 4), 3))
            accepted = None
            with self.state_lock:
                same = self.utterance and uid == self.utterance_id and generation == self.generation
                if complete and same:
                    text = self.asr.finalize(); accepted = (text, len(self.audio) / (ASR_RATE * 4)); self.audio.clear(); self.utterance = False; self.accepted_turns += 1; self.last_partial = ""; self.vad.reset_states()
                elif complete and not same:
                    self.journal.emit("smart-turn", "cancelled", utterance_id=uid, candidate_generation=generation, reason="candidate-resumed-or-changed")
            if accepted is not None:
                text, duration = accepted; self.journal.emit("capture", "utterance.completed", utterance_id=uid, input_s=round(duration, 3)); self.on_utterance(uid, generation, text, duration)
        self.journal.emit("smart-turn", "stopped", accepted_turns=self.accepted_turns)

    def _loop(self) -> None:
        self.journal.emit("vad", "start")
        while (pcm := self.frames.get()) is not _EOF:
            resumed = partial = None
            with self.state_lock:
                event = self.vad(np.frombuffer(pcm, dtype="<f4")) or {}
                if "start" in event:
                    if not self.utterance:
                        self.audio.clear(); self.utterance = True; self.utterance_id += 1; self.generation += 1; self.asr.begin()
                        fields = self.on_start(self.utterance_id, self.generation) or {}
                        self.journal.emit("vad", "speech.started", utterance_id=self.utterance_id, candidate_generation=self.generation, **fields)
                    else:
                        self.generation += 1; resumed = (self.utterance_id, self.generation); self.journal.emit("vad", "speech.resumed", utterance_id=self.utterance_id, candidate_generation=self.generation)
                if self.utterance:
                    self.audio.extend(pcm); text, flags, feed_ms = self.asr.feed(pcm)
                    if flags: self.journal.emit("asr", "event", utterance_id=self.utterance_id, candidate_generation=self.generation, eou=bool(flags & PARAKEET_EVENT_EOU), eob=bool(flags & PARAKEET_EVENT_EOB), input_s=round(len(self.audio) / (ASR_RATE * 4), 3))
                    if text and text != self.last_partial:
                        self.last_partial = text; partial = (self.utterance_id, self.generation, text, flags, feed_ms, len(self.audio) / (ASR_RATE * 4))
                if "end" in event and self.utterance:
                    self.decisions.put((self.utterance_id, self.generation, bytes(self.audio)))
                    self.journal.emit("vad", "candidate.queued", utterance_id=self.utterance_id, candidate_generation=self.generation, vad_sample=int(event["end"]), input_s=round(len(self.audio) / (ASR_RATE * 4), 3))
            if resumed is not None: self.on_resume(*resumed)
            if partial is not None:
                uid, generation, text, flags, feed_ms, duration = partial
                self.journal.emit("asr", "partial", utterance_id=uid, candidate_generation=generation, input_s=round(duration, 3), chars=len(text), feed_ms=round(feed_ms, 3), eou=bool(flags & PARAKEET_EVENT_EOU), eob=bool(flags & PARAKEET_EVENT_EOB), text=text)
                self.on_partial(uid, generation, text, flags)
        self.decisions.put(_EOF); self.journal.emit("vad", "stopped")

    def check(self) -> None: self.source.check(); self.paths.supervisor.check()

    def close(self) -> None:
        if not self.active: return
        self.active = False; self.source.close(); self.frames.put(_EOF); join_or_fail(self.vad_thread, "vad-asr"); join_or_fail(self.decision_thread, "smart-turn"); self.asr.close(); self.journal.emit("capture", "stopped")


def launch(paths: Paths, family: str = "nano", language: str = "en", primary=None, replacement=None, interrupt_after=None) -> None:
    capture, failure = None, None
    try:
        settings = load_settings(paths.data_dir)
        def start(uid, generation): return {}
        def partial(uid, generation, text, flags): print(f"\ruser~: {text}", end="", flush=True)
        def utterance(uid, generation, text, duration):
            print(f"\nuser: {text}", flush=True); paths.journal.transcript("user", text)
            paths.journal.emit("asr", "completed", utterance_id=uid, accepted=bool(text), input_s=round(duration, 3), chars=len(text), text=text)
        capture = Capture(paths, settings, language, start, utterance, partial); capture.open(); paths.journal.emit("main", "ready"); print("trident.ready", flush=True)
        while True: capture.check(); paths.supervisor.wait(.02)
    except BaseException as error: failure = (error, error.__traceback__)
    finish_cleanup(paths, failure, ([('capture', capture.close)] if capture is not None else []) + [("supervisor", lambda: paths.supervisor.join(1))])
