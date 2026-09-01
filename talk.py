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

from config import (
    ASR_RATE, CABLE_CHANNELS, CABLE_RATE, GEMMA_CONTEXT, GEMMA_GEN, SMART_TURN_FILE,
    TTS_RATE, VAD_FRAME, Paths, cable_device, load_settings, system_prompt,
)
from runtime import (
    CancelableHTTP, Residents, RESP_CANCELLED, RESP_CLOSED, RESP_DONE,
    RESP_ERROR, RESP_PCM, gemma_stream, transcribe,
)

STOP_PHRASES = {
    "stop", "stop speaking", "please stop", "that's enough", "thats enough", "quiet", "silence",
    "przestan", "przestań", "przestan mowic", "przestań mówić", "dosyc", "dość", "cicho", "milcz",
}
BACKCHANNELS = {"yeah", "yes", "yep", "yup", "ok", "okay", "mhm", "mm", "uh", "um", "aha", "uh huh", "huh", "right", "sure", "tak", "no", "nie", "okej"}
BLOCK_SECONDS = VAD_FRAME / TTS_RATE
SPEAKABLE_MIN_WORDS = 8
SPOKEN_TURN_WORDS = 60
SPOKEN_TURN_CHARS = 480
_EOF = object()


def _join_or_fail(thread: threading.Thread | None, role: str, timeout: float = 5.0) -> None:
    if thread is None or not thread.is_alive(): return
    thread.join(timeout)
    if thread.is_alive(): raise RuntimeError(f"{role} worker survived shutdown")


def _finish_cleanup(paths: Paths, primary, actions) -> None:
    failures: list[tuple[BaseException, object]] = []
    for role, action in actions:
        try: action()
        except BaseException as error:
            failures.append((error, error.__traceback__)); paths.journal.failure(f"cleanup.{role}", error)
    if primary is not None: raise primary[0].with_traceback(primary[1])
    if failures: raise failures[0][0].with_traceback(failures[0][1])


def spoken(text: str) -> str:
    text, marker = text.replace("\r", "").strip(), "Assistant:\n"
    return text.rsplit(marker, 1)[-1].strip() if marker in text else text


def folded_utterance(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.casefold().replace("\r", " ").replace("\n", " ")).split())


def classify_utterance(text: str) -> str:
    folded = folded_utterance(text)
    if folded in STOP_PHRASES or any(folded.startswith(phrase + " ") for phrase in STOP_PHRASES): return "stop"
    return "backchannel" if folded in BACKCHANNELS else "request"


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams((1, 2, ASR_RATE, 0, "NONE", "not compressed")); out.writeframes(pcm)
    return buf.getvalue()


def _mel_filters() -> np.ndarray:
    def hz_to_mel(hz):
        hz = np.asarray(hz, dtype=np.float64)
        return np.where(hz >= 1000.0, 15.0 + np.log(hz / 1000.0) / (np.log(6.4) / 27.0), hz / (200.0 / 3.0))
    def mel_to_hz(mel):
        mel = np.asarray(mel, dtype=np.float64)
        return np.where(mel >= 15.0, 1000.0 * np.exp((np.log(6.4) / 27.0) * (mel - 15.0)), (200.0 / 3.0) * mel)
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
        if audio.size < self.samples: audio = np.pad(audio, (self.samples - audio.size, 0))
        if audio.size: audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)
        frames = np.lib.stride_tricks.sliding_window_view(np.pad(audio, (200, 200), mode="reflect"), 400)[::160]
        spec = np.log10(np.maximum(np.abs(np.fft.rfft(frames * _WINDOW, n=400, axis=1)) ** 2 @ _FILTERS, 1e-10)).T[:, :-1]
        probability = float(self.session.run(None, {"input_features": ((np.maximum(spec, spec.max() - 8.0) + 4.0) / 4.0).astype(np.float32)[None]})[0][0].item())
        return probability > self.threshold, probability


class Segmenter:
    def __init__(self) -> None:
        self.sent = 0; self.buffer: list[str] = []

    def take(self, text: str, flush: bool = False) -> list[str]:
        out: list[str] = []
        while self.sent < len(text):
            pending, cut = text[self.sent:], 0
            for i, char in enumerate(pending):
                if char in ".?!" and (i + 1 == len(pending) or pending[i + 1].isspace()):
                    cut = i + 1; break
            if not (cut := cut or (len(pending) if flush else 0)): break
            unit = pending[:cut].strip(); self.sent += cut
            while self.sent < len(text) and text[self.sent].isspace(): self.sent += 1
            if unit: self.buffer.append(unit)
            if sum(len(part.split()) for part in self.buffer) >= SPEAKABLE_MIN_WORDS:
                out.append(" ".join(self.buffer)); self.buffer.clear()
        if flush and self.buffer:
            out.append(" ".join(self.buffer)); self.buffer.clear()
        return out


class StatefulResampler:
    def __init__(self, in_rate: int, out_rate: int) -> None:
        self.step, self.buffer, self.position = in_rate / out_rate, np.empty(0, dtype=np.float32), 0.0

    def feed(self, samples: np.ndarray) -> np.ndarray:
        if samples.size: self.buffer = np.concatenate((self.buffer, np.asarray(samples, dtype=np.float32)))
        out = []
        while self.position + 1 < self.buffer.size:
            i = int(self.position); frac = self.position - i
            out.append(self.buffer[i] + (self.buffer[i + 1] - self.buffer[i]) * frac)
            self.position += self.step
        if drop := int(self.position):
            self.buffer = self.buffer[drop:]; self.position -= drop
        return np.asarray(out, dtype=np.float32)


@dataclass
class PCMEntry:
    epoch: int; response: int; piece: int; chunk: int; pcm: bytes; offset: int = 0


class Renderer:
    def __init__(self, paths: Paths) -> None:
        self.journal = paths.journal
        self.lock = threading.Lock()
        self.entries: deque[PCMEntry] = deque()
        self.epoch = 0
        self.paused = self.force_silence = False
        self.pending: set[tuple[int, int, int]] = set()
        self._drained = True
        self._events: queue.SimpleQueue = queue.SimpleQueue()

    def _busy(self) -> None:
        self._drained = False

    def set_pending(self, pending: set[tuple[int, int, int]]) -> None:
        with self.lock:
            changed = pending != self.pending
            self.pending = set(pending)
            if changed and any(i[0] == self.epoch for i in pending): self._busy()

    def put(self, entry: PCMEntry) -> bool:
        with self.lock:
            if entry.epoch != self.epoch: self._events.put(("late", entry)); return False
            self.entries.append(entry); self._busy(); return True

    def pause(self) -> None:
        with self.lock: self.paused = True; self._busy()

    def resume(self) -> None:
        with self.lock: self.paused = False

    def advance(self, epoch: int) -> int:
        with self.lock:
            dropped = sum(len(e.pcm) - e.offset for e in self.entries)
            self.entries.clear(); self.epoch = epoch; self.paused = False; self.force_silence = True; self._busy()
            return dropped

    def render(self) -> tuple[bytes, bool, bool]:
        block = bytearray(VAD_FRAME * 2); wrote = 0; had_pcm = False
        with self.lock:
            if self.force_silence:
                self.force_silence = False; self._events.put(("silenced", self.epoch))
                if not self.entries and not any(i[0] == self.epoch for i in self.pending): self._drained = True
                return bytes(block), False, True
            if self.paused: return bytes(block), False, False
            while wrote < len(block) and self.entries:
                entry = self.entries[0]
                if entry.epoch != self.epoch:
                    self.entries.popleft(); self._events.put(("late", entry)); continue
                count = min(len(block) - wrote, len(entry.pcm) - entry.offset)
                block[wrote:wrote + count] = entry.pcm[entry.offset:entry.offset + count]
                wrote += count; entry.offset += count; had_pcm = had_pcm or count > 0
                if entry.offset == len(entry.pcm): self.entries.popleft()
            if not self.entries and not any(i[0] == self.epoch for i in self.pending) and not self.force_silence:
                self._drained = True
        return bytes(block), had_pcm, False

    def drained(self) -> bool:
        with self.lock: return self._drained and not self.entries and not self.force_silence

    def check(self) -> None:
        while not self._events.empty():
            event, value = self._events.get()
            if event == "late":
                e = value; self.journal.emit("playback", "dropped", epoch=e.epoch, live_epoch=self.epoch, response_id=e.response, piece_id=e.piece, chunk_id=e.chunk, bytes=len(e.pcm) - e.offset)
            elif event == "silenced":
                self.journal.emit("playback", "silenced", epoch=value, blocks=1)


class _Cable:
    kind, component, ready_event, stop_event, rate_key, peer_rate = "output", "playback", "sink.ready", "sink.stopped", "render_rate", TTS_RATE

    def __init__(self, paths: Paths) -> None:
        self.paths = paths; self.stream = self.error = None
        self.resampler = StatefulResampler(*((TTS_RATE, CABLE_RATE) if self.kind == "output" else (CABLE_RATE, ASR_RATE)))

    def check(self) -> None:
        if self.error is not None: raise self.error

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop(); self.stream.close(); self.stream = None
        self.paths.journal.emit(self.component, self.stop_event, type="cable")

    def open(self) -> None:
        index, device, host = cable_device(self.kind)
        extra = sd.WasapiSettings(exclusive=False, auto_convert=True, explicit_sample_format=True)
        (sd.check_output_settings if self.kind == "output" else sd.check_input_settings)(
            device=index, channels=CABLE_CHANNELS, dtype="float32", samplerate=CABLE_RATE, extra_settings=extra)
        cls = sd.RawOutputStream if self.kind == "output" else sd.RawInputStream
        self.stream = cls(samplerate=CABLE_RATE, blocksize=0, device=index, channels=CABLE_CHANNELS, dtype="float32",
                          latency="low", extra_settings=extra, callback=self._callback)
        self.stream.start()
        self.paths.journal.emit(self.component, self.ready_event, type="cable", device=device["name"], host_api=host["name"],
            channels=CABLE_CHANNELS, native_rate=CABLE_RATE, auto_convert=True, negotiated_latency=self.stream.latency,
            **{self.rate_key: self.peer_rate})


class CableSink(_Cable):
    def __init__(self, renderer: Renderer, paths: Paths) -> None:
        super().__init__(paths)
        self.renderer = renderer
        self.native = np.empty(0, dtype=np.float32); self.offset = 0
        self.drain_deadline = 0.0; self.drain_reported = False

    def _callback(self, outdata, frames, timing, status) -> None:
        if status:
            self.error = RuntimeError(f"CABLE render: {status}"); raise sd.CallbackAbort
        target = np.frombuffer(outdata, dtype="<f4", count=frames * CABLE_CHANNELS).reshape(frames, CABLE_CHANNELS); written = 0
        while written < frames:
            if self.offset >= self.native.size:
                block, had_pcm, forced_silence = self.renderer.render()
                self.native = self.resampler.feed(np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768.0); self.offset = 0
                if had_pcm or forced_silence:
                    self.drain_deadline = float(timing.outputBufferDacTime) + BLOCK_SECONDS; self.drain_reported = False
            count = min(frames - written, self.native.size - self.offset)
            if count <= 0:
                target[written:] = 0; break
            target[written:written + count] = self.native[self.offset:self.offset + count][:, None]
            written += count; self.offset += count

    def drained(self) -> bool:
        if not self.renderer.drained(): return False
        if not self.drain_deadline: return True
        now = float(getattr(self.stream, "time", 0.0)) if self.stream is not None else time.monotonic()
        if now >= self.drain_deadline:
            if not self.drain_reported:
                self.paths.journal.emit("playback", "drained", type="cable", dac_time=self.drain_deadline); self.drain_reported = True
            return True
        return False


class CableSource(_Cable):
    kind, component, ready_event, stop_event, rate_key, peer_rate = "input", "capture", "source.ready", "source.stopped", "capture_rate", ASR_RATE

    def __init__(self, frame_cb, paths: Paths) -> None:
        super().__init__(paths)
        self.frame_cb, self.pending = frame_cb, np.empty(0, dtype=np.float32)

    def _callback(self, indata, frames, _timing, status) -> None:
        if status:
            self.error = RuntimeError(f"CABLE capture: {status}"); raise sd.CallbackAbort
        samples = np.frombuffer(indata, dtype="<f4", count=frames * CABLE_CHANNELS).reshape(frames, CABLE_CHANNELS).mean(axis=1)
        self.pending = np.concatenate((self.pending, self.resampler.feed(samples)))
        while self.pending.size >= VAD_FRAME:
            self.frame_cb(self.pending[:VAD_FRAME]); self.pending = self.pending[VAD_FRAME:]


class Capture:
    def __init__(self, paths: Paths, settings: dict, on_start, on_utterance) -> None:
        self.paths, self.journal = paths, paths.journal; self.on_start, self.on_utterance = on_start, on_utterance
        self.vad = VADIterator(load_silero_vad(onnx=True), threshold=.5, sampling_rate=ASR_RATE, min_silence_duration_ms=int(settings["candidate_silence_ms"]), speech_pad_ms=0)
        self.smart = SmartTurn(paths.models_dir / SMART_TURN_FILE, settings["completion_threshold"], settings["acoustic_context_seconds"])
        self.frames: queue.SimpleQueue = queue.SimpleQueue(); self.decisions: queue.SimpleQueue = queue.SimpleQueue()
        self.audio = bytearray(); self.state_lock = threading.Lock(); self.active = self.utterance = False
        self.utterance_id = self.generation = self.accepted_turns = 0
        self.source = CableSource(self.frame, paths)
        self.vad_thread = self.decision_thread = None

    def frame(self, samples: np.ndarray) -> None:
        if self.active: self.frames.put(np.asarray(samples, dtype="<f4").tobytes())

    def open(self) -> None:
        self.active = True
        self.decision_thread = self.paths.supervisor.start("smart-turn", self._decide)
        self.vad_thread = self.paths.supervisor.start("vad", self._loop)
        self.source.open(); self.journal.emit("capture", "ready")

    def _decide(self) -> None:
        self.journal.emit("smart-turn", "start")
        while (item := self.decisions.get()) is not _EOF:
            utterance_id, generation, audio = item
            started = time.perf_counter(); complete, probability = self.smart.decide(audio)
            self.journal.emit("smart-turn", "completed", utterance_id=utterance_id, candidate_generation=generation, complete=complete, probability=round(probability, 6), decision_ms=round((time.perf_counter() - started) * 1000, 3), input_s=round(len(audio) / (ASR_RATE * 4), 3))
            accepted = b""
            with self.state_lock:
                same = self.utterance and utterance_id == self.utterance_id and generation == self.generation
                if complete and same:
                    accepted = bytes(self.audio); self.audio.clear(); self.utterance = False; self.accepted_turns += 1; self.vad.reset_states()
                elif complete and not same:
                    self.journal.emit("smart-turn", "cancelled", utterance_id=utterance_id, candidate_generation=generation, reason="candidate-resumed-or-changed")
            if accepted:
                self.journal.emit("capture", "utterance.completed", utterance_id=utterance_id, input_s=round(len(accepted) / (ASR_RATE * 4), 3)); self.on_utterance(utterance_id, accepted)
        self.journal.emit("smart-turn", "stopped", accepted_turns=self.accepted_turns)

    def _loop(self) -> None:
        self.journal.emit("vad", "start")
        while (pcm := self.frames.get()) is not _EOF:
            with self.state_lock:
                event = self.vad(np.frombuffer(pcm, dtype="<f4")) or {}
                if "start" in event:
                    if not self.utterance:
                        self.audio.clear(); self.utterance = True; self.utterance_id += 1; self.generation += 1
                        self.on_start(self.utterance_id); self.journal.emit("vad", "speech.started", utterance_id=self.utterance_id, candidate_generation=self.generation)
                    else:
                        self.generation += 1; self.journal.emit("vad", "speech.resumed", utterance_id=self.utterance_id, candidate_generation=self.generation)
                if self.utterance: self.audio.extend(pcm)
                if "end" in event and self.utterance:
                    generation, audio = self.generation, bytes(self.audio)
                    self.decisions.put((self.utterance_id, generation, audio))
                    self.journal.emit("vad", "candidate.queued", utterance_id=self.utterance_id, candidate_generation=generation, vad_sample=int(event["end"]), input_s=round(len(audio) / (ASR_RATE * 4), 3))
        self.decisions.put(_EOF); self.journal.emit("vad", "stopped")

    def check(self) -> None:
        self.source.check(); self.paths.supervisor.check()

    def close(self) -> None:
        if not self.active: return
        self.active = False; self.source.close(); self.frames.put(_EOF)
        _join_or_fail(self.vad_thread, "vad"); _join_or_fail(self.decision_thread, "smart-turn")
        self.journal.emit("capture", "stopped")


class Synthesis:
    def __init__(self, paths: Paths, residents: Residents) -> None:
        self.paths, self.journal, self.residents = paths, paths.journal, residents
        residents.require_alive("chatterbox")
        self.lock = threading.Lock(); self.epoch = self.response_id = 0
        self.pending: set[tuple[int, int, int]] = set(); self.terminal: set[tuple[int, int, int]] = set()
        self.tts = residents.chatterbox_client(); self.renderer = Renderer(paths); self.sink = CableSink(self.renderer, paths)
        self.reader = None; self.closed = residents.chatterbox_closed; self.active = False; self.first_pcm: set[int] = set()

    def start_output(self) -> None:
        self.sink.open()
        try:
            self.reader = self.paths.supervisor.start("tts-reader", self._reader)
            self.residents.register_chatterbox_reader(self.reader); self.active = True
        except BaseException:
            self.sink.close(); raise

    def _sync_pending(self) -> None: self.renderer.set_pending(self.pending)

    def send_sentence(self, epoch: int, response_id: int, piece_id: int, text: str) -> bool:
        identity = (epoch, response_id, piece_id)
        with self.lock:
            if epoch != self.epoch: return False
            if identity in self.pending or identity in self.terminal: raise RuntimeError("duplicate synthesis identity")
            self.pending.add(identity); self._sync_pending()
            try: self.tts.synthesize(epoch, response_id, piece_id, text)
            except BaseException:
                self.pending.remove(identity); self._sync_pending(); raise
        self.journal.emit("synthesis", "queued", epoch=epoch, response_id=response_id, piece_id=piece_id, chars=len(text))
        return True

    def advance(self, reason: str, utterance_id: int = 0, preserve_playback: bool = False) -> int:
        with self.lock:
            self.epoch += 1; epoch = self.epoch
            dropped = 0 if preserve_playback else self.renderer.advance(epoch)
            self.tts.advance(epoch); self._sync_pending()
            old_pending = sum(1 for identity in self.pending if identity[0] != epoch)
        self.journal.emit("synthesis", "epoch.advanced", epoch=epoch, reason=reason, utterance_id=utterance_id, pending_cancel_count=old_pending, dropped_bytes=dropped)
        return epoch

    def cutover(self, epoch: int, reason: str, utterance_id: int) -> bool:
        with self.lock:
            if epoch != self.epoch: return False
            dropped = self.renderer.advance(epoch); self._sync_pending()
        self.journal.emit("playback", "cutover", epoch=epoch, reason=reason, utterance_id=utterance_id, dropped_bytes=dropped)
        return True

    def pause(self, utterance_id: int) -> None:
        self.renderer.pause(); self.journal.emit("playback", "paused", epoch=self.epoch, utterance_id=utterance_id)

    def resume(self, utterance_id: int) -> None:
        self.renderer.resume(); self.journal.emit("playback", "resumed_after_backchannel", epoch=self.epoch, utterance_id=utterance_id)

    def _reader(self) -> None:
        while True:
            if (frame := self.tts.recv_frame()) is None:
                if not self.closed.is_set(): raise RuntimeError("native TTS socket closed before close handshake")
                break
            kind, epoch, response_id, piece_id, chunk_id, payload = frame; identity = (epoch, response_id, piece_id)
            if kind == RESP_PCM:
                if self.renderer.put(PCMEntry(epoch, response_id, piece_id, chunk_id, payload)) and response_id not in self.first_pcm:
                    self.first_pcm.add(response_id); self.journal.emit("synthesis", "first_result", epoch=epoch, response_id=response_id, piece_id=piece_id, chunk_id=chunk_id, bytes=len(payload))
            elif kind in (RESP_DONE, RESP_CANCELLED, RESP_ERROR):
                with self.lock:
                    if identity in self.terminal: raise RuntimeError(f"duplicate terminal ACK for {identity}")
                    if identity not in self.pending: raise RuntimeError(f"terminal ACK for unknown piece {identity}")
                    self.pending.remove(identity); self.terminal.add(identity); self._sync_pending()
                event = "acknowledged" if kind == RESP_DONE else "cancelled" if kind == RESP_CANCELLED else "failed"
                self.journal.emit("synthesis", event, epoch=epoch, response_id=response_id, piece_id=piece_id, error=payload.decode("utf-8", errors="replace") if kind == RESP_ERROR else None)
                if kind == RESP_ERROR: raise RuntimeError(payload.decode("utf-8", errors="replace"))
            elif kind == RESP_CLOSED:
                self.closed.set(); self.journal.emit("synthesis", "closed"); break
            else:
                raise RuntimeError(f"unknown TTS response kind {kind}")

    def live_complete(self) -> bool:
        with self.lock: live_pending = any(identity[0] == self.epoch for identity in self.pending)
        return not live_pending and self.sink.drained()

    def all_acknowledged(self) -> bool:
        with self.lock: return not self.pending

    def check(self) -> None:
        self.renderer.check(); self.sink.check(); self.paths.supervisor.check(); self.residents.check()

    def stop_output(self, cancel: bool) -> None:
        if not self.active: return
        failure: BaseException | None = None
        try:
            if cancel:
                try: self.advance("shutdown")
                except OSError: pass
            deadline = time.monotonic() + 10
            self.paths.supervisor.spin(self.all_acknowledged, deadline, "missing terminal synthesis ACK during shutdown", interval=.01, tick=self.check)
            if cancel:
                self.paths.supervisor.spin(self.sink.drained, deadline, "playback did not render epoch-cutover silence before shutdown", interval=.005, tick=self.check)
            self.residents.close_chatterbox()
        except BaseException as error:
            failure = error
        finally:
            try: self.sink.close()
            except BaseException as error:
                self.journal.failure("cleanup.sink", error)
                if failure is None: failure = error
            self.active = False
        if failure is not None: raise failure

    stop = stop_output


class Conversation(Synthesis):
    def __init__(self, paths: Paths, residents: Residents, settings: dict, language: str) -> None:
        super().__init__(paths, residents)
        self.settings, self.language = settings, language
        self.parakeet, self.gemma = residents.require_alive("parakeet"), residents.require_alive("gemma")
        self.asr_http, self.gemma_http = CancelableHTTP(), CancelableHTTP()
        self.recognition_q: queue.SimpleQueue = queue.SimpleQueue(); self.generation_q: queue.SimpleQueue = queue.SimpleQueue()
        self.latest_utterance = self.interruption_epoch = 0; self.stopping = False
        self.history: list[dict[str, str]] = []; self.fragment = ""
        self.capture = Capture(paths, settings, self._speech_start, self._utterance)
        self.recognition_thread = self.generation_thread = None

    def start(self) -> None:
        self.start_output()
        self.recognition_thread = self.paths.supervisor.start("recognition", self._recognition)
        self.generation_thread = self.paths.supervisor.start("generation", self._generation)
        self.capture.open()

    def _speech_start(self, utterance_id: int) -> None:
        self.latest_utterance = utterance_id; self.asr_http.close(); self.gemma_http.close(); self.pause(utterance_id)
        self.interruption_epoch = self.advance("request", utterance_id, preserve_playback=True)

    def _utterance(self, utterance_id: int, pcm: bytes) -> None:
        self.recognition_q.put((utterance_id, pcm, time.perf_counter_ns()))
        self.journal.emit("asr", "queued", utterance_id=utterance_id, input_s=round(len(pcm) / (ASR_RATE * 4), 3))

    def _live(self, epoch: int) -> bool:
        with self.lock: return epoch == self.epoch

    def _recognition(self) -> None:
        while (item := self.recognition_q.get()) is not _EOF:
            if self.stopping: continue
            utterance_id, pcm, queued_ns = item
            dequeued_ns, duration, started = time.perf_counter_ns(), len(pcm) / (ASR_RATE * 4), time.perf_counter()
            wav = wav_bytes((np.clip(np.frombuffer(pcm, dtype="<f4"), -1, 1) * 32767).astype("<i2").tobytes())
            try: text = transcribe(self.parakeet, wav, self.asr_http)
            except Exception:
                if self.stopping or utterance_id != self.latest_utterance: text = ""
                else: raise
            total, live = time.perf_counter() - started, utterance_id == self.latest_utterance
            self.journal.emit("asr", "completed", utterance_id=utterance_id, accepted=live and bool(text), input_s=round(duration, 3), total_ms=round(total * 1000, 3), rtf=round(total / duration, 3), queue_ms=round((dequeued_ns - queued_ns) / 1e6, 3), chars=len(text))
            if not live: continue
            if not text: self.resume(utterance_id); continue
            self.journal.transcript("user", text); print(f"\nuser: {text}", flush=True)
            intent = classify_utterance(text); self.journal.emit("conversation", "intent", utterance_id=utterance_id, intent=intent)
            if intent == "backchannel": self.resume(utterance_id)
            elif intent == "stop": self.cutover(self.interruption_epoch, "stop", utterance_id)
            else:
                epoch = self.interruption_epoch
                if self.cutover(epoch, "request", utterance_id): self.generation_q.put((epoch, utterance_id, text))

    def _system(self) -> str:
        return system_prompt(self.language, str(self.settings.get("system_prompt") or ""))

    @staticmethod
    def _bytes(messages: list[dict[str, str]]) -> int:
        return sum(len(message["content"].encode("utf-8")) for message in messages)

    def _trim_history(self) -> None:
        budget = GEMMA_CONTEXT - int(GEMMA_GEN["max_tokens"]) - 256 - len(self._system().encode("utf-8"))
        while self.history and self._bytes(self.history) > max(0, budget): del self.history[:2]

    def _messages(self, prompt: str) -> list[dict[str, str]]:
        fixed = [{"role": "system", "content": self._system()}, {"role": "user", "content": prompt}]
        remaining = GEMMA_CONTEXT - int(GEMMA_GEN["max_tokens"]) - 256 - self._bytes(fixed)
        if remaining < 0: raise RuntimeError("accepted utterance exceeds conservative Gemma context budget")
        kept: list[dict[str, str]] = []
        for i in range(len(self.history) - 2, -1, -2):
            pair = self.history[i:i + 2]; cost = self._bytes(pair)
            if cost > remaining: break
            kept[0:0] = pair; remaining -= cost
        return [fixed[0], *kept, fixed[1]]

    def _generation(self) -> None:
        while (item := self.generation_q.get()) is not _EOF:
            if self.stopping: continue
            epoch, utterance_id, prompt = item
            merged = " ".join(part for part in (self.fragment, prompt) if part).strip()
            with self.lock:
                self.response_id += 1; response_id = self.response_id
            segmenter, raw, units, piece_id, started, first, budget_reached = Segmenter(), "", [], 0, time.perf_counter(), True, False
            def queue_unit(unit: str) -> bool:
                nonlocal piece_id, budget_reached
                words, chars = sum(len(part.split()) for part in units), sum(len(part) for part in units)
                if units and (words + len(unit.split()) > SPOKEN_TURN_WORDS or chars + len(unit) > SPOKEN_TURN_CHARS):
                    budget_reached = True; return False
                piece_id += 1
                if self.send_sentence(epoch, response_id, piece_id, unit): units.append(unit)
                budget_reached = words + len(unit.split()) >= SPOKEN_TURN_WORDS or chars + len(unit) >= SPOKEN_TURN_CHARS
                return not budget_reached
            messages = self._messages(merged)
            self.journal.emit("gemma", "start", epoch=epoch, utterance_id=utterance_id, response_id=response_id, chars=len(merged), retained_turns=len(messages) - 2)
            cancelled = False
            try:
                stream = gemma_stream(self.gemma, messages, self.gemma_http)
                for delta in stream:
                    if not self._live(epoch): cancelled = True; break
                    if first:
                        first = False; self.journal.emit("gemma", "first_result", epoch=epoch, response_id=response_id, latency_ms=round((time.perf_counter() - started) * 1000, 3))
                    raw += delta
                    for unit in segmenter.take(spoken(raw)):
                        if not queue_unit(unit): break
                    if budget_reached: break
                if budget_reached: stream.close()
            except Exception:
                if self._live(epoch) and not self.stopping: raise
                cancelled = True
            live, generated = self._live(epoch), spoken(raw)
            if cancelled or not live:
                if generated: self.journal.transcript("assistant", generated)
                self.journal.emit("gemma", "cancelled", epoch=epoch, response_id=response_id, elapsed_ms=round((time.perf_counter() - started) * 1000, 3), chars=len(generated)); continue
            if not budget_reached:
                for unit in segmenter.take(generated, True):
                    if not queue_unit(unit): break
            answer = " ".join(units)
            if answer:
                self.fragment = ""; self.history.extend(({"role": "user", "content": merged}, {"role": "assistant", "content": answer})); self._trim_history(); self.journal.transcript("assistant", answer); print(f"assistant: {answer}", flush=True)
            else:
                self.fragment = merged
            self.journal.emit("gemma", "completed", epoch=epoch, response_id=response_id, empty=not answer, elapsed_ms=round((time.perf_counter() - started) * 1000, 3), chars=len(answer), generated_chars=len(generated), pieces=piece_id, budget_reached=budget_reached)

    def check(self) -> None:
        self.capture.check(); super().check()

    def stop(self, cancel: bool) -> None:
        self.stopping = True; self.asr_http.close(); self.gemma_http.close()
        primary = None
        try: self.capture.close()
        except BaseException as error:
            primary = (error, error.__traceback__); self.journal.failure("cleanup.capture", error)
        self.recognition_q.put(_EOF); self.generation_q.put(_EOF)
        _finish_cleanup(self.paths, primary, [
            ("recognition", lambda: _join_or_fail(self.recognition_thread, "recognition")),
            ("generation", lambda: _join_or_fail(self.generation_thread, "generation")),
            ("synthesis", lambda: self.stop_output(cancel)),
        ])


class TTSMode(Synthesis):
    def __init__(self, paths: Paths, residents: Residents, primary: str, replacement: str | None, interrupt_after: float | None) -> None:
        super().__init__(paths, residents); self.primary, self.replacement, self.interrupt_after = primary, replacement, interrupt_after
        self.ready_ns = 0

    def _input(self, text: str, source: str, injected_ns: int) -> None:
        units = Segmenter().take(text, True)
        with self.lock: self.response_id += 1; epoch, response_id = self.epoch, self.response_id
        self.journal.emit("tts", "input", source=source, epoch=epoch, response_id=response_id, after_ready_ms=round((injected_ns - self.ready_ns) / 1e6, 3), chars=len(text), pieces=len(units))
        for piece_id, unit in enumerate(units, 1): self.send_sentence(epoch, response_id, piece_id, unit)

    def start(self) -> None:
        self.start_output(); self.ready_ns = time.perf_counter_ns()
        self.journal.emit("tts", "mode.ready", epoch=self.epoch, ready_ns=self.ready_ns); print("trident.ready", flush=True)
        self._input(self.primary, "primary", self.ready_ns)

    def run(self) -> None:
        requested_ns = self.ready_ns + round(self.interrupt_after * 1e9) if self.replacement is not None else 0; interrupted = False
        while True:
            self.check(); now = time.perf_counter_ns()
            if self.replacement is not None and not interrupted and now >= requested_ns:
                epoch = self.advance("replacement")
                self.journal.emit("tts", "replacement", epoch=epoch, requested_after_s=self.interrupt_after, observed_after_s=round((now - self.ready_ns) / 1e9, 6), drift_ms=round((now - requested_ns) / 1e6, 3))
                self._input(self.replacement, "replacement", now); interrupted = True
            if (self.replacement is None or interrupted) and self.all_acknowledged() and self.live_complete():
                self.renderer.check(); self.journal.emit("tts", "completed", epoch=self.epoch, response_id=self.response_id, elapsed_ms=round((time.perf_counter_ns() - self.ready_ns) / 1e6, 3)); return
            self.paths.supervisor.wait(.01)


def launch(paths: Paths, family: str = "nano", language: str = "en", primary: str | None = None,
           replacement: str | None = None, interrupt_after: float | None = None) -> None:
    residents, mode, failure = Residents(paths), None, None
    try:
        residents.boot(family, language)
        if paths.command == "talk":
            mode = Conversation(paths, residents, load_settings(paths.data_dir), language); mode.start()
            paths.journal.emit("main", "ready", family=family, language=language); print("trident.ready", flush=True)
            while True: mode.check(); paths.supervisor.wait(.02)
        else:
            assert primary is not None
            mode = TTSMode(paths, residents, primary, replacement, interrupt_after); mode.start(); mode.run()
    except BaseException as error:
        failure = (error, error.__traceback__)
    actions = ([(paths.command, lambda: mode.stop(cancel=failure is not None))] if mode is not None else [])
    actions.extend((("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))))
    _finish_cleanup(paths, failure, actions)
