from __future__ import annotations

import io
import queue
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
import sounddevice as sd
from silero_vad_notorch import VADIterator, load_silero_vad

from config import ASR_RATE, GEMMA_CONTEXT, GEMMA_GEN, SMART_TURN_FILE, TTS_RATE, VAD_FRAME, Paths, load_settings, system_prompt, wasapi_device
from runtime import (
    Chatterbox, CancelableHTTP, Residents, RESP_CANCELLED, RESP_CLOSED, RESP_DONE,
    RESP_ERROR, RESP_PCM, gemma_stream, transcribe,
)

STOP_PHRASES = {
    "stop", "stop speaking", "please stop", "that's enough", "thats enough", "quiet", "silence",
    "przestan", "przestań", "przestan mowic", "przestań mówić", "dosyc", "dość", "cicho", "milcz",
}
BACKCHANNELS = {"yeah", "yes", "yep", "yup", "ok", "okay", "mhm", "mm", "uh", "um", "aha", "uh huh", "huh", "right", "sure", "tak", "no", "nie", "okej"}
BLOCK_SECONDS = VAD_FRAME / TTS_RATE
_NATIVE_RATE = 48000
_EOF = object()


def _join_or_fail(thread: threading.Thread | None, role: str, timeout: float = 5.0) -> None:
    if thread is None or not thread.is_alive(): return
    thread.join(timeout)
    if thread.is_alive(): raise RuntimeError(f"{role} worker survived shutdown")


def spoken(text: str) -> str:
    text = text.replace("\r", "").strip()
    marker = "Assistant:\n"
    return text.rsplit(marker, 1)[-1].strip() if marker in text else text


def folded_utterance(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.casefold().replace("\r", " ").replace("\n", " ")).split())


def classify_utterance(text: str) -> str:
    folded = folded_utterance(text)
    if folded in STOP_PHRASES:
        return "stop"
    if folded in BACKCHANNELS:
        return "backchannel"
    return "request"


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams((1, 2, ASR_RATE, 0, "NONE", "not compressed"))
        out.writeframes(pcm)
    return buf.getvalue()


def _mel_filters() -> np.ndarray:
    def hz_to_mel(hz):
        hz = np.asarray(hz, dtype=np.float64); mel = hz / (200.0 / 3.0); mask = hz >= 1000.0
        mel[mask] = 15.0 + np.log(hz[mask] / 1000.0) / (np.log(6.4) / 27.0); return mel
    def mel_to_hz(mel):
        mel = np.asarray(mel, dtype=np.float64); hz = (200.0 / 3.0) * mel; mask = mel >= 15.0
        hz[mask] = 1000.0 * np.exp((np.log(6.4) / 27.0) * (mel[mask] - 15.0)); return hz
    centers = mel_to_hz(np.linspace(hz_to_mel([0.0])[0], hz_to_mel([8000.0])[0], 82))
    bins = np.linspace(0.0, ASR_RATE / 2.0, 201)
    return np.maximum(0.0, np.minimum((bins[:, None] - centers[:-2]) / (centers[1:-1] - centers[:-2]), (centers[2:] - bins[:, None]) / (centers[2:] - centers[1:-1]))) * 2.0 / (centers[2:] - centers[:-2])


_FILTERS = _mel_filters()
_WINDOW = np.hanning(401)[:-1]


class SmartTurn:
    def __init__(self, model: Path, threshold: float, context_seconds: float) -> None:
        options = ort.SessionOptions(); options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        self.threshold, self.samples = float(threshold), int(float(context_seconds) * ASR_RATE)

    def decide(self, pcm: bytes) -> tuple[bool, float]:
        audio = np.frombuffer(pcm, dtype="<f4")[-self.samples:]
        if audio.size < self.samples:
            audio = np.pad(audio, (self.samples - audio.size, 0))
        if audio.size:
            audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
        frames = np.lib.stride_tricks.sliding_window_view(np.pad(audio, (200, 200), mode="reflect"), 400)[::160]
        power = np.abs(np.fft.rfft(frames * _WINDOW, n=400, axis=1)) ** 2
        spec = np.log10(np.maximum(power @ _FILTERS, 1e-10)).T[:, :-1]
        features = ((np.maximum(spec, spec.max() - 8.0) + 4.0) / 4.0).astype(np.float32)[None]
        probability = float(self.session.run(None, {"input_features": features})[0][0].item())
        return probability > self.threshold, probability


class Segmenter:
    def __init__(self) -> None:
        self.sent = 0

    def take(self, text: str, flush: bool = False) -> list[str]:
        out: list[str] = []
        while self.sent < len(text):
            pending, cut = text[self.sent:], 0
            for i, char in enumerate(pending):
                if char in ".?!" and (i + 1 == len(pending) or pending[i + 1].isspace()):
                    cut = i + 1; break
            if not cut:
                cut = len(pending) if flush else 0
            if not cut:
                break
            unit = pending[:cut].strip(); self.sent += cut
            while self.sent < len(text) and text[self.sent].isspace(): self.sent += 1
            if unit: out.append(unit)
        return out


class StatefulResampler:
    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.step = in_rate / out_rate
        self.buffer = np.empty(0, dtype=np.float32)
        self.position = 0.0

    def feed(self, samples: np.ndarray) -> np.ndarray:
        if samples.size:
            self.buffer = np.concatenate((self.buffer, np.asarray(samples, dtype=np.float32)))
        out = []
        while self.position + 1 < self.buffer.size:
            i = int(self.position); frac = self.position - i
            out.append(self.buffer[i] + (self.buffer[i + 1] - self.buffer[i]) * frac)
            self.position += self.step
        drop = int(self.position)
        if drop:
            self.buffer = self.buffer[drop:]; self.position -= drop
        return np.asarray(out, dtype=np.float32)


@dataclass
class PCMEntry:
    epoch: int
    response: int
    piece: int
    chunk: int
    pcm: bytes
    offset: int = 0


class Renderer:
    def __init__(self, paths: Paths) -> None:
        self.journal = paths.journal
        self.lock = threading.Lock()
        self.entries: deque[PCMEntry] = deque()
        self.epoch = 0
        self.paused = False
        self.force_silence = False
        self.pending: set[tuple[int, int, int]] = set()
        self.starved_at_ns = 0
        self.starved_missing = 0
        self.started_audio = False
        self._drained = True
        self.generation = 0
        self._events: queue.SimpleQueue = queue.SimpleQueue()

    def _busy(self) -> None:
        self._drained = False
        self.generation += 1

    def _finish_starvation(self, reason: str) -> None:
        if not self.starved_at_ns:
            return
        self._events.put(("starved", (self.epoch, time.perf_counter_ns() - self.starved_at_ns, self.starved_missing, reason)))
        self.starved_at_ns = 0
        self.starved_missing = 0

    def set_pending(self, pending: set[tuple[int, int, int]]) -> None:
        with self.lock:
            changed = pending != self.pending
            self.pending = set(pending)
            live = any(identity[0] == self.epoch for identity in pending)
            if changed and live:
                self._busy()
            if not live:
                self._finish_starvation("terminal")

    def put(self, entry: PCMEntry) -> bool:
        with self.lock:
            if entry.epoch != self.epoch:
                self._events.put(("late", entry)); return False
            self._finish_starvation("pcm")
            self.entries.append(entry); self._busy()
            return True

    def pause(self) -> None:
        with self.lock:
            self._finish_starvation("paused")
            self.paused = True; self._busy()

    def resume(self) -> None:
        with self.lock:
            self.paused = False

    def advance(self, epoch: int) -> int:
        with self.lock:
            self._finish_starvation("epoch-cutover")
            dropped = sum(len(e.pcm) - e.offset for e in self.entries)
            self.entries.clear(); self.epoch = epoch; self.paused = False; self.force_silence = True
            self.started_audio = False; self._busy()
            return dropped

    def render(self) -> tuple[bytes, bool, bool]:
        block = bytearray(VAD_FRAME * 2); wrote = 0; had_pcm = False
        with self.lock:
            if self.force_silence:
                self.force_silence = False
                self._events.put(("silenced", self.epoch))
                if not self.entries and not any(identity[0] == self.epoch for identity in self.pending):
                    self._drained = True
                return bytes(block), False, True
            if self.paused:
                return bytes(block), False, False
            while wrote < len(block) and self.entries:
                entry = self.entries[0]
                if entry.epoch != self.epoch:
                    self.entries.popleft(); self._events.put(("late", entry)); continue
                count = min(len(block) - wrote, len(entry.pcm) - entry.offset)
                block[wrote:wrote + count] = entry.pcm[entry.offset:entry.offset + count]
                wrote += count; entry.offset += count; had_pcm = had_pcm or count > 0
                if count: self.started_audio = True
                if entry.offset == len(entry.pcm): self.entries.popleft()
            live_pending = any(identity[0] == self.epoch for identity in self.pending)
            if wrote < len(block) and live_pending and self.started_audio and not self.starved_at_ns:
                self.starved_at_ns = time.perf_counter_ns(); self.starved_missing = len(block) - wrote
            if not self.entries and not live_pending and not self.force_silence:
                self._finish_starvation("terminal"); self._drained = True
        return bytes(block), had_pcm, False

    def drain_state(self) -> tuple[bool, int]:
        with self.lock:
            return self._drained and not self.entries and not self.force_silence, self.generation

    def drained(self) -> bool:
        return self.drain_state()[0]

    def check(self) -> None:
        while not self._events.empty():
            event, value = self._events.get()
            if event == "late":
                e = value; self.journal.emit("playback", "dropped", epoch=e.epoch, live_epoch=self.epoch, response_id=e.response, piece_id=e.piece, chunk_id=e.chunk, bytes=len(e.pcm) - e.offset)
            elif event == "silenced":
                self.journal.emit("playback", "silenced", epoch=value, blocks=1)
            elif event == "starved":
                epoch, ns, missing, reason = value
                self.journal.emit("playback", "starved", epoch=epoch, starvation_ms=round(ns / 1e6, 3), missing_bytes=missing, block_ms=round(BLOCK_SECONDS * 1000, 3), ended_by=reason)


class WavSink:
    def __init__(self, path: Path, renderer: Renderer, paths: Paths) -> None:
        self.path, self.renderer, self.paths = Path(path), renderer, paths
        self.stop_event = threading.Event(); self.thread: threading.Thread | None = None
        self.file = None; self.wav = None; self.virtual_ns = 0; self.drain_reported = False
        self.commit_lock = threading.Lock(); self.committed_generation = -1

    def open(self) -> None:
        self.file = self.path.open("xb")
        self.wav = wave.open(self.file, "wb"); self.wav.setparams((1, 2, TTS_RATE, 0, "NONE", "not compressed"))
        self.virtual_ns = time.perf_counter_ns()
        self.thread = self.paths.supervisor.start("wav-sink", self._loop)
        self.paths.journal.emit("playback", "sink.ready", type="wav", path=str(self.path), rate=TTS_RATE, channels=1, sample_format="pcm16", block=VAD_FRAME)

    def _loop(self) -> None:
        deadline = time.perf_counter()
        while not self.stop_event.is_set():
            block, _, _ = self.renderer.render(); self.wav.writeframesraw(block)
            drained, generation = self.renderer.drain_state()
            with self.commit_lock:
                self.committed_generation = generation if drained else -1
            self.virtual_ns += round(BLOCK_SECONDS * 1e9)
            deadline += BLOCK_SECONDS
            self.stop_event.wait(max(0.0, deadline - time.perf_counter()))

    def drained(self) -> bool:
        drained, generation = self.renderer.drain_state()
        with self.commit_lock:
            committed = drained and self.committed_generation == generation
        if committed and not self.drain_reported:
            self.paths.journal.emit("playback", "drained", type="wav", virtual_ns=self.virtual_ns); self.drain_reported = True
        elif not committed:
            self.drain_reported = False
        return committed

    def close(self) -> None:
        self.stop_event.set()
        failure = None
        try: _join_or_fail(self.thread, "wav-sink")
        except BaseException as error: failure = error
        if self.wav is not None: self.wav.close(); self.wav = None
        if self.file is not None and not self.file.closed: self.file.close()
        self.paths.journal.emit("playback", "sink.stopped", type="wav", path=str(self.path))
        if failure is not None: raise failure


class Upsample2x:
    def __init__(self) -> None:
        self.previous: float | None = None

    def feed(self, block: bytes) -> bytes:
        samples = np.frombuffer(block, dtype="<i2").astype(np.float32)
        if not samples.size:
            return b""
        previous = samples[0] if self.previous is None else self.previous
        left = np.empty_like(samples); left[0] = previous; left[1:] = samples[:-1]
        out = np.empty(samples.size * 2, dtype=np.float32)
        out[0::2] = (left + samples) * .5; out[1::2] = samples
        self.previous = float(samples[-1])
        return np.clip(np.rint(out), -32768, 32767).astype("<i2").tobytes()


class SpeakerSink:
    def __init__(self, renderer: Renderer, paths: Paths) -> None:
        self.renderer, self.paths = renderer, paths
        self.stream = None; self.native = b""; self.offset = 0; self.error = None
        self.resampler = Upsample2x(); self.drain_deadline = 0.0; self.drain_reported = False

    def _callback(self, outdata, frames, timing, status) -> None:
        if status:
            self.error = RuntimeError(f"speaker: {status}"); raise sd.CallbackAbort
        target = memoryview(outdata); written = 0
        while written < len(target):
            if self.offset >= len(self.native):
                block, had_pcm, forced_silence = self.renderer.render()
                self.native = self.resampler.feed(block); self.offset = 0
                if had_pcm or forced_silence:
                    self.drain_deadline = float(timing.outputBufferDacTime) + BLOCK_SECONDS
                    self.drain_reported = False
            count = min(len(target) - written, len(self.native) - self.offset)
            target[written:written + count] = self.native[self.offset:self.offset + count]
            written += count; self.offset += count

    def open(self) -> None:
        index, device, host = wasapi_device("output")
        extra = sd.WasapiSettings(exclusive=True, auto_convert=False, explicit_sample_format=True)
        sd.check_output_settings(device=index, channels=1, dtype="int16", samplerate=_NATIVE_RATE, extra_settings=extra)
        self.stream = sd.RawOutputStream(samplerate=_NATIVE_RATE, blocksize=128, device=index, channels=1, dtype="int16", latency="low", extra_settings=extra, callback=self._callback)
        self.stream.start()
        self.paths.journal.emit("playback", "sink.ready", type="speaker", device=device["name"], host_api=host["name"], native_rate=_NATIVE_RATE, render_rate=TTS_RATE, advertised_low_latency=device.get("default_low_output_latency"), negotiated_latency=self.stream.latency, native_block=128, render_block=VAD_FRAME)

    def drained(self) -> bool:
        if not self.renderer.drained(): return False
        if not self.drain_deadline: return True
        now = float(getattr(self.stream, "time", 0.0)) if self.stream is not None else time.monotonic()
        if now >= self.drain_deadline:
            if not self.drain_reported:
                self.paths.journal.emit("playback", "drained", type="speaker", dac_time=self.drain_deadline); self.drain_reported = True
            return True
        return False

    def check(self) -> None:
        if self.error is not None: raise self.error

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop(); self.stream.close(); self.stream = None
        self.paths.journal.emit("playback", "sink.stopped", type="speaker")


class WavSource:
    finite = True
    def __init__(self, path: Path, frame_cb, eof_cb, paths: Paths, candidate_silence_ms: int) -> None:
        self.path, self.frame_cb, self.eof_cb, self.paths = Path(path), frame_cb, eof_cb, paths
        frame_ms = VAD_FRAME * 1000 / ASR_RATE
        self.tail_frames = max(2, int(np.ceil(candidate_silence_ms / frame_ms)) + 2)
        self.stop_event = threading.Event(); self.thread = None

    def open(self) -> None:
        self.thread = self.paths.supervisor.start("wav-source", self._loop)
        self.paths.journal.emit("capture", "source.ready", type="wav", path=str(self.path), native_rate=TTS_RATE, capture_rate=ASR_RATE, block=VAD_FRAME)

    def _loop(self) -> None:
        resampler = StatefulResampler(TTS_RATE, ASR_RATE); pending = np.empty(0, dtype=np.float32)
        deadline = time.perf_counter()
        with wave.open(str(self.path), "rb") as src:
            while not self.stop_event.is_set():
                raw = src.readframes(768)
                if not raw: break
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                pending = np.concatenate((pending, resampler.feed(samples)))
                while pending.size >= VAD_FRAME:
                    self.frame_cb(pending[:VAD_FRAME]); pending = pending[VAD_FRAME:]
                    deadline += VAD_FRAME / ASR_RATE
                    self.stop_event.wait(max(0.0, deadline - time.perf_counter()))
        if not self.stop_event.is_set():
            if pending.size:
                pending = np.pad(pending, (0, VAD_FRAME - pending.size)); self.frame_cb(pending[:VAD_FRAME])
            for _ in range(self.tail_frames):
                self.frame_cb(np.zeros(VAD_FRAME, dtype=np.float32)); deadline += VAD_FRAME / ASR_RATE
                self.stop_event.wait(max(0.0, deadline - time.perf_counter()))
            self.eof_cb()
            self.paths.journal.emit("capture", "source.eof", type="wav")

    def close(self) -> None:
        self.stop_event.set()
        _join_or_fail(self.thread, "wav-source")


class MicrophoneSource:
    finite = False
    def __init__(self, frame_cb, _eof_cb, paths: Paths) -> None:
        self.frame_cb, self.paths = frame_cb, paths; self.stream = None; self.error = None
        self.resampler = StatefulResampler(_NATIVE_RATE, ASR_RATE); self.pending = np.empty(0, dtype=np.float32)

    def _callback(self, indata, frames, _timing, status) -> None:
        if status:
            self.error = RuntimeError(f"microphone: {status}"); raise sd.CallbackAbort
        samples = np.frombuffer(indata, dtype="<f4", count=frames)
        self.pending = np.concatenate((self.pending, self.resampler.feed(samples)))
        while self.pending.size >= VAD_FRAME:
            self.frame_cb(self.pending[:VAD_FRAME]); self.pending = self.pending[VAD_FRAME:]

    def open(self) -> None:
        index, device, host = wasapi_device("input")
        extra = sd.WasapiSettings(exclusive=True, auto_convert=False, explicit_sample_format=True)
        sd.check_input_settings(device=index, channels=1, dtype="float32", samplerate=_NATIVE_RATE, extra_settings=extra)
        self.stream = sd.RawInputStream(samplerate=_NATIVE_RATE, blocksize=1536, device=index, channels=1, dtype="float32", latency="low", extra_settings=extra, callback=self._callback)
        self.stream.start()
        self.paths.journal.emit("capture", "source.ready", type="microphone", device=device["name"], host_api=host["name"], native_rate=_NATIVE_RATE, capture_rate=ASR_RATE, advertised_low_latency=device.get("default_low_input_latency"), negotiated_latency=self.stream.latency, native_block=1536, capture_block=VAD_FRAME)

    def check(self) -> None:
        if self.error is not None: raise self.error

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop(); self.stream.close(); self.stream = None


class Capture:
    def __init__(self, paths: Paths, input_file: Path | None, settings: dict, on_start, on_utterance) -> None:
        self.paths, self.journal = paths, paths.journal; self.on_start, self.on_utterance = on_start, on_utterance
        self.candidate_silence_ms = int(settings["candidate_silence_ms"])
        self.vad = VADIterator(load_silero_vad(onnx=True), threshold=.5, sampling_rate=ASR_RATE, min_silence_duration_ms=self.candidate_silence_ms, speech_pad_ms=0)
        self.smart = SmartTurn(paths.models_dir / SMART_TURN_FILE, settings["completion_threshold"], settings["acoustic_context_seconds"])
        self.context_seconds = float(settings["acoustic_context_seconds"])
        self.frames: queue.SimpleQueue = queue.SimpleQueue(); self.decisions: queue.SimpleQueue = queue.SimpleQueue()
        self.audio = bytearray(); self.state_lock = threading.Lock(); self.active = False; self.utterance = False
        self.utterance_id = 0; self.generation = 0; self.accepted_turns = 0; self.saw_speech = False
        self.input_ended = threading.Event(); self.finite_complete = threading.Event(); self.incomplete = threading.Event()
        source_cls = WavSource if input_file is not None else MicrophoneSource
        self.source = source_cls(input_file, self.frame, self.eof, paths, self.candidate_silence_ms) if input_file is not None else source_cls(self.frame, self.eof, paths)
        self.vad_thread = self.decision_thread = None

    def frame(self, samples: np.ndarray) -> None:
        if self.active: self.frames.put(np.asarray(samples, dtype="<f4").tobytes())

    def eof(self) -> None:
        self.input_ended.set(); self.frames.put(_EOF)

    def open(self) -> None:
        self.active = True
        self.decision_thread = self.paths.supervisor.start("smart-turn", self._decide)
        self.vad_thread = self.paths.supervisor.start("vad", self._loop)
        self.source.open()
        self.journal.emit("capture", "ready", finite=self.source.finite)

    def _decide(self) -> None:
        self.journal.emit("smart-turn", "start")
        while True:
            item = self.decisions.get()
            if item is _EOF: break
            utterance_id, generation, audio = item
            started = time.perf_counter(); complete, probability = self.smart.decide(audio); elapsed = (time.perf_counter() - started) * 1000
            self.journal.emit("smart-turn", "completed", utterance_id=utterance_id, candidate_generation=generation, complete=complete, probability=round(probability, 6), decision_ms=round(elapsed, 3), input_s=round(len(audio) / (ASR_RATE * 4), 3))
            accepted = b""
            with self.state_lock:
                same = self.utterance and utterance_id == self.utterance_id and generation == self.generation
                if complete and same:
                    accepted = bytes(self.audio); self.audio.clear(); self.utterance = False; self.accepted_turns += 1; self.vad.reset_states()
                elif complete and not same:
                    self.journal.emit("smart-turn", "cancelled", utterance_id=utterance_id, candidate_generation=generation, reason="candidate-resumed-or-changed")
            if accepted:
                self.journal.emit("capture", "utterance.completed", utterance_id=utterance_id, input_s=round(len(accepted) / (ASR_RATE * 4), 3)); self.on_utterance(utterance_id, accepted)
        with self.state_lock:
            unfinished = self.utterance or self.accepted_turns == 0
        if self.source.finite:
            (self.incomplete if unfinished else self.finite_complete).set()
        self.journal.emit("smart-turn", "stopped", incomplete=unfinished, accepted_turns=self.accepted_turns)

    def _loop(self) -> None:
        self.journal.emit("vad", "start")
        while True:
            pcm = self.frames.get()
            if pcm is _EOF: break
            with self.state_lock:
                event = self.vad(np.frombuffer(pcm, dtype="<f4")) or {}
                if "start" in event:
                    if not self.utterance:
                        self.audio.clear(); self.utterance = True; self.saw_speech = True; self.utterance_id += 1; self.generation += 1
                        self.on_start(self.utterance_id); self.journal.emit("vad", "speech.started", utterance_id=self.utterance_id, candidate_generation=self.generation)
                    else:
                        self.generation += 1; self.journal.emit("vad", "speech.resumed", utterance_id=self.utterance_id, candidate_generation=self.generation)
                if self.utterance: self.audio.extend(pcm)
                if "end" in event and self.utterance:
                    generation = self.generation; audio = bytes(self.audio)
                    self.decisions.put((self.utterance_id, generation, audio))
                    self.journal.emit("vad", "candidate.queued", utterance_id=self.utterance_id, candidate_generation=generation, vad_sample=int(event["end"]), input_s=round(len(audio) / (ASR_RATE * 4), 3))
        self.decisions.put(_EOF)
        self.journal.emit("vad", "stopped")

    def check(self) -> None:
        if hasattr(self.source, "check"): self.source.check()
        self.paths.supervisor.check()
        if self.incomplete.is_set(): raise RuntimeError("finite input ended before Smart Turn accepted the utterance")

    def close(self) -> None:
        if not self.active: return
        self.active = False; self.source.close()
        if not self.input_ended.is_set(): self.frames.put(_EOF)
        _join_or_fail(self.vad_thread, "vad")
        _join_or_fail(self.decision_thread, "smart-turn")
        self.journal.emit("capture", "stopped")


class Synthesis:
    def __init__(self, paths: Paths, residents: Residents, output_file: Path | None) -> None:
        self.paths, self.journal, self.residents = paths, paths.journal, residents
        residents.require_alive("chatterbox")
        self.lock = threading.Lock(); self.epoch = 0; self.response_id = 0; self.pending: set[tuple[int, int, int]] = set(); self.terminal: set[tuple[int, int, int]] = set()
        self.tts = Chatterbox(); self.renderer = Renderer(paths); self.sink = WavSink(output_file, self.renderer, paths) if output_file else SpeakerSink(self.renderer, paths)
        self.reader = None; self.closed = threading.Event(); self.active = False; self.first_pcm: set[int] = set()

    def start_output(self) -> None:
        self.sink.open()
        try:
            self.tts.open()
            self.reader = self.paths.supervisor.start("tts-reader", self._reader)
            self.active = True
        except BaseException:
            self.tts.disconnect(); self.sink.close()
            raise

    def _sync_pending(self) -> None: self.renderer.set_pending(self.pending)

    def send_sentence(self, epoch: int, response_id: int, piece_id: int, text: str) -> bool:
        identity = (epoch, response_id, piece_id)
        with self.lock:
            if epoch != self.epoch: return False
            if identity in self.pending or identity in self.terminal: raise RuntimeError("duplicate synthesis identity")
            self.pending.add(identity); self._sync_pending()
            try:
                self.tts.synthesize(epoch, response_id, piece_id, text)
            except BaseException:
                self.pending.remove(identity); self._sync_pending()
                raise
        self.journal.emit("synthesis", "queued", epoch=epoch, response_id=response_id, piece_id=piece_id, chars=len(text))
        return True

    def advance(self, reason: str, utterance_id: int = 0) -> int:
        with self.lock:
            self.epoch += 1; epoch = self.epoch; dropped = self.renderer.advance(epoch); self.tts.advance(epoch); self._sync_pending()
            old_pending = sum(1 for identity in self.pending if identity[0] != epoch)
        self.journal.emit("synthesis", "epoch.advanced", epoch=epoch, reason=reason, utterance_id=utterance_id, pending_cancel_count=old_pending, dropped_bytes=dropped)
        return epoch

    def pause(self, utterance_id: int) -> None:
        self.renderer.pause(); self.journal.emit("playback", "paused", epoch=self.epoch, utterance_id=utterance_id)

    def resume(self, utterance_id: int) -> None:
        self.renderer.resume(); self.journal.emit("playback", "resumed_after_backchannel", epoch=self.epoch, utterance_id=utterance_id)

    def _reader(self) -> None:
        while True:
            frame = self.tts.recv_frame()
            if frame is None:
                if not self.closed.is_set(): raise RuntimeError("native TTS socket closed before close handshake")
                break
            kind, epoch, response_id, piece_id, chunk_id, payload = frame; identity = (epoch, response_id, piece_id)
            if kind == RESP_PCM:
                accepted = self.renderer.put(PCMEntry(epoch, response_id, piece_id, chunk_id, payload))
                if accepted and response_id not in self.first_pcm:
                    self.first_pcm.add(response_id); self.journal.emit("synthesis", "first_result", epoch=epoch, response_id=response_id, piece_id=piece_id, chunk_id=chunk_id, bytes=len(payload))
                continue
            if kind in (RESP_DONE, RESP_CANCELLED, RESP_ERROR):
                with self.lock:
                    if identity in self.terminal: raise RuntimeError(f"duplicate terminal ACK for {identity}")
                    if identity not in self.pending: raise RuntimeError(f"terminal ACK for unknown piece {identity}")
                    self.pending.remove(identity); self.terminal.add(identity); self._sync_pending()
                event = "acknowledged" if kind == RESP_DONE else "cancelled" if kind == RESP_CANCELLED else "failed"
                self.journal.emit("synthesis", event, epoch=epoch, response_id=response_id, piece_id=piece_id, error=payload.decode("utf-8", errors="replace") if kind == RESP_ERROR else None)
                if kind == RESP_ERROR: raise RuntimeError(payload.decode("utf-8", errors="replace"))
                continue
            if kind == RESP_CLOSED:
                self.closed.set(); self.journal.emit("synthesis", "closed"); break
            raise RuntimeError(f"unknown TTS response kind {kind}")

    def live_complete(self) -> bool:
        with self.lock: live_pending = any(identity[0] == self.epoch for identity in self.pending)
        return not live_pending and self.sink.drained()

    def all_acknowledged(self) -> bool:
        with self.lock: return not self.pending

    def check(self) -> None:
        self.renderer.check()
        if hasattr(self.sink, "check"): self.sink.check()
        self.paths.supervisor.check(); self.residents.check()

    def stop_output(self, cancel: bool) -> None:
        if not self.active: return
        failure: BaseException | None = None
        try:
            if cancel:
                try: self.advance("shutdown")
                except OSError: pass
            deadline = time.monotonic() + 10
            while not self.all_acknowledged() and time.monotonic() < deadline:
                self.check(); time.sleep(.01)
            if not self.all_acknowledged(): raise RuntimeError("missing terminal synthesis ACK during shutdown")
            if cancel:
                while not self.sink.drained() and time.monotonic() < deadline:
                    self.check(); time.sleep(.005)
                if not self.sink.drained(): raise RuntimeError("playback did not render epoch-cutover silence before shutdown")
            self.tts.request_close()
            close_deadline = time.monotonic() + 10
            while not self.closed.wait(.05):
                self.paths.supervisor.check()
                if time.monotonic() >= close_deadline: raise RuntimeError("native close handshake timed out")
            if self.reader is not None:
                self.reader.join(timeout=2)
                if self.reader.is_alive(): raise RuntimeError("native TTS reader survived close handshake")
            self.residents.mark_chatterbox_closed()
        except BaseException as error:
            failure = error
        finally:
            self.tts.disconnect()
            try:
                self.sink.close()
            except BaseException as error:
                if failure is None: failure = error
            self.active = False
        if failure is not None:
            raise failure


class Conversation(Synthesis):
    def __init__(self, paths: Paths, residents: Residents, settings: dict, input_file: Path | None, output_file: Path | None, language: str) -> None:
        super().__init__(paths, residents, output_file)
        self.settings, self.language = settings, language
        self.parakeet, self.gemma = residents.require_alive("parakeet"), residents.require_alive("gemma")
        self.asr_http, self.gemma_http = CancelableHTTP(), CancelableHTTP()
        self.recognition_q: queue.SimpleQueue = queue.SimpleQueue(); self.generation_q: queue.SimpleQueue = queue.SimpleQueue()
        self.work_lock = threading.Lock(); self.recognition_outstanding = self.generation_outstanding = 0
        self.latest_utterance = 0; self.stopping = False
        self.history: list[dict[str, str]] = []; self.fragment = ""
        self.capture = Capture(paths, input_file, settings, self._speech_start, self._utterance)
        self.recognition_thread = self.generation_thread = None

    def start(self) -> None:
        self.start_output()
        self.recognition_thread = self.paths.supervisor.start("recognition", self._recognition)
        self.generation_thread = self.paths.supervisor.start("generation", self._generation)
        self.capture.open()

    def _speech_start(self, utterance_id: int) -> None:
        self.latest_utterance = utterance_id; self.asr_http.close(); self.pause(utterance_id)

    def _utterance(self, utterance_id: int, pcm: bytes) -> None:
        with self.work_lock: self.recognition_outstanding += 1
        self.recognition_q.put((utterance_id, pcm, time.perf_counter_ns()))
        self.journal.emit("asr", "queued", utterance_id=utterance_id, input_s=round(len(pcm) / (ASR_RATE * 4), 3))

    def _recognition(self) -> None:
        while True:
            item = self.recognition_q.get()
            if item is _EOF: break
            try:
                if self.stopping: continue
                utterance_id, pcm, queued_ns = item
                dequeued_ns = time.perf_counter_ns(); duration = len(pcm) / (ASR_RATE * 4); started = time.perf_counter()
                audio = np.frombuffer(pcm, dtype="<f4"); wav = wav_bytes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
                try:
                    text = transcribe(self.parakeet, wav, self.asr_http)
                except Exception:
                    if self.stopping or utterance_id != self.latest_utterance: text = ""
                    else: raise
                total = time.perf_counter() - started; live = utterance_id == self.latest_utterance
                self.journal.emit("asr", "completed", utterance_id=utterance_id, accepted=live and bool(text), input_s=round(duration, 3), total_ms=round(total * 1000, 3), rtf=round(total / duration, 3), queue_ms=round((dequeued_ns - queued_ns) / 1e6, 3), chars=len(text))
                if not live: continue
                if not text:
                    self.resume(utterance_id); continue
                self.journal.transcript("user", text); print(f"\nuser: {text}", flush=True)
                intent = classify_utterance(text); self.journal.emit("conversation", "intent", utterance_id=utterance_id, intent=intent)
                if intent == "backchannel":
                    self.resume(utterance_id)
                elif intent == "stop":
                    self.gemma_http.close(); self.advance("stop", utterance_id)
                else:
                    self.gemma_http.close(); epoch = self.advance("request", utterance_id)
                    with self.work_lock: self.generation_outstanding += 1
                    self.generation_q.put((epoch, utterance_id, text))
            finally:
                with self.work_lock: self.recognition_outstanding -= 1

    def _system(self) -> str:
        return system_prompt(self.language, str(self.settings.get("system_prompt") or ""))

    @staticmethod
    def _bytes(messages: list[dict[str, str]]) -> int:
        return sum(len(message["content"].encode("utf-8")) for message in messages)

    def _trim_history(self) -> None:
        budget = GEMMA_CONTEXT - int(GEMMA_GEN["max_tokens"]) - 256 - len(self._system().encode("utf-8"))
        while self.history and self._bytes(self.history) > max(0, budget):
            del self.history[:2]

    def _messages(self, prompt: str) -> list[dict[str, str]]:
        fixed = [{"role": "system", "content": self._system()}, {"role": "user", "content": prompt}]
        remaining = GEMMA_CONTEXT - int(GEMMA_GEN["max_tokens"]) - 256 - self._bytes(fixed)
        if remaining < 0:
            raise RuntimeError("accepted utterance exceeds conservative Gemma context budget")
        kept: list[dict[str, str]] = []
        for i in range(len(self.history) - 2, -1, -2):
            pair = self.history[i:i + 2]; cost = self._bytes(pair)
            if cost > remaining: break
            kept[0:0] = pair; remaining -= cost
        return [fixed[0], *kept, fixed[1]]

    def _generation(self) -> None:
        while True:
            item = self.generation_q.get()
            if item is _EOF: break
            try:
                if self.stopping: continue
                epoch, utterance_id, prompt = item
                merged = " ".join(part for part in (self.fragment, prompt) if part).strip()
                with self.lock:
                    self.response_id += 1; response_id = self.response_id
                segmenter = Segmenter(); raw = ""; piece_id = 0; started = time.perf_counter(); first = True
                messages = self._messages(merged)
                self.journal.emit("gemma", "start", epoch=epoch, utterance_id=utterance_id, response_id=response_id, chars=len(merged), retained_turns=len(messages) - 2)
                cancelled = False
                try:
                    stream = gemma_stream(self.gemma, messages, self.gemma_http)
                    for delta in stream:
                        with self.lock: live = epoch == self.epoch
                        if not live: cancelled = True; break
                        if first:
                            first = False; self.journal.emit("gemma", "first_result", epoch=epoch, response_id=response_id, latency_ms=round((time.perf_counter() - started) * 1000, 3))
                        raw += delta
                        for unit in segmenter.take(spoken(raw)):
                            piece_id += 1; self.send_sentence(epoch, response_id, piece_id, unit)
                except Exception:
                    with self.lock: live = epoch == self.epoch
                    if live and not self.stopping: raise
                    cancelled = True
                with self.lock: live = epoch == self.epoch
                answer = spoken(raw)
                if cancelled or not live:
                    if answer: self.journal.transcript("assistant", answer)
                    self.journal.emit("gemma", "cancelled", epoch=epoch, response_id=response_id, elapsed_ms=round((time.perf_counter() - started) * 1000, 3), chars=len(answer)); continue
                for unit in segmenter.take(answer, True):
                    piece_id += 1; self.send_sentence(epoch, response_id, piece_id, unit)
                if answer:
                    self.fragment = ""; self.history.extend(({"role": "user", "content": merged}, {"role": "assistant", "content": answer})); self._trim_history(); self.journal.transcript("assistant", answer); print(f"assistant: {answer}", flush=True)
                else:
                    self.fragment = merged
                self.journal.emit("gemma", "completed", epoch=epoch, response_id=response_id, empty=not answer, elapsed_ms=round((time.perf_counter() - started) * 1000, 3), chars=len(answer), pieces=piece_id)
            finally:
                with self.work_lock: self.generation_outstanding -= 1

    def finite_done(self) -> bool:
        if not self.capture.source.finite or not self.capture.finite_complete.is_set(): return False
        with self.work_lock: outstanding = self.recognition_outstanding + self.generation_outstanding
        return outstanding == 0 and self.all_acknowledged() and self.live_complete()

    def check(self) -> None:
        self.capture.check(); super().check()

    def stop(self, cancel: bool) -> None:
        self.stopping = True; self.asr_http.close(); self.gemma_http.close(); self.capture.close()
        self.recognition_q.put(_EOF); self.generation_q.put(_EOF)
        _join_or_fail(self.recognition_thread, "recognition")
        _join_or_fail(self.generation_thread, "generation")
        self.stop_output(cancel)


class TTSMode(Synthesis):
    def __init__(self, paths: Paths, residents: Residents, output_file: Path | None, primary: str, replacement: str | None, interrupt_after: float | None) -> None:
        super().__init__(paths, residents, output_file); self.primary, self.replacement, self.interrupt_after = primary, replacement, interrupt_after
        self.ready_ns = self.started_ns = 0

    def _input(self, text: str, source: str, injected_ns: int) -> None:
        units = Segmenter().take(text, True)
        with self.lock: self.response_id += 1; epoch, response_id = self.epoch, self.response_id
        self.journal.emit("tts", "input", source=source, epoch=epoch, response_id=response_id, after_ready_ms=round((injected_ns - self.ready_ns) / 1e6, 3), chars=len(text), pieces=len(units))
        for piece_id, unit in enumerate(units, 1): self.send_sentence(epoch, response_id, piece_id, unit)

    def start(self) -> None:
        self.start_output(); self.ready_ns = self.started_ns = time.perf_counter_ns()
        self.journal.emit("tts", "mode.ready", epoch=self.epoch, ready_ns=self.ready_ns); print("trident.ready", flush=True)
        self._input(self.primary, "primary", self.ready_ns)

    def run(self) -> None:
        requested_ns = self.ready_ns + round(self.interrupt_after * 1e9) if self.replacement is not None else 0; interrupted = False
        while True:
            self.check(); now = time.perf_counter_ns()
            if self.replacement is not None and not interrupted and now >= requested_ns:
                epoch = self.advance("replacement"); self.journal.emit("tts", "replacement", epoch=epoch, requested_after_s=self.interrupt_after, observed_after_s=round((now - self.ready_ns) / 1e9, 6), drift_ms=round((now - requested_ns) / 1e6, 3)); self._input(self.replacement, "replacement", now); interrupted = True
            if (self.replacement is None or interrupted) and self.all_acknowledged() and self.live_complete():
                self.renderer.check(); self.journal.emit("tts", "completed", epoch=self.epoch, response_id=self.response_id, elapsed_ms=round((time.perf_counter_ns() - self.started_ns) / 1e6, 3)); return
            self.paths.supervisor.wait(.01)


def launch(paths: Paths, family: str = "nano", language: str = "en", input_file: Path | None = None, output_file: Path | None = None) -> None:
    residents = Residents(paths); conversation = None; interrupted = False
    try:
        residents.boot(family, language); conversation = Conversation(paths, residents, load_settings(paths.data_dir), input_file, output_file, language); conversation.start()
        paths.journal.emit("main", "ready", family=family, language=language, finite_input=input_file is not None); print("trident.ready", flush=True)
        while True:
            conversation.check()
            if conversation.finite_done(): break
            paths.supervisor.wait(.02)
    except BaseException:
        interrupted = True; raise
    finally:
        try:
            if conversation is not None: conversation.stop(cancel=interrupted)
        finally:
            try: residents.stop()
            finally: paths.supervisor.join(1)


def launch_tts(paths: Paths, family: str, language: str, primary: str, replacement: str | None, interrupt_after: float | None, output_file: Path | None = None) -> None:
    residents = Residents(paths); mode = None; interrupted = False
    try:
        residents.boot(family, language); mode = TTSMode(paths, residents, output_file, primary, replacement, interrupt_after); mode.start(); mode.run()
    except BaseException:
        interrupted = True; raise
    finally:
        try:
            if mode is not None: mode.stop_output(cancel=interrupted)
        finally:
            try: residents.stop()
            finally: paths.supervisor.join(1)
