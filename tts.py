from config import ensure_venv
ensure_venv()
if __name__ == "__main__":
    from main import main
    raise SystemExit(main("tts"))

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from config import TTS_RATE, Paths, Wasapi
from generation import Segmenter
from journal import finish_cleanup
from runtime import RESP_CANCELLED, RESP_CLOSED, RESP_DONE, RESP_ERROR, RESP_PCM, Residents

@dataclass
class PCMEntry:
    epoch: int; response: int; piece: int; chunk: int; pcm: bytes; offset: int = 0; started_dac: float | None = None


class Renderer:
    def __init__(self, paths: Paths) -> None:
        self.journal = paths.journal
        self.lock = threading.Lock()
        self.entries: deque[PCMEntry] = deque()
        self.epoch = 0
        self.preserved_epochs: set[int] = set()
        self.paused = self.force_silence = False
        self.pending: set[tuple[int, int, int]] = set()
        self._drained = True
        self._events: queue.SimpleQueue = queue.SimpleQueue()
        self.last_completed: tuple[int, int, int, int] | None = None
        self.underrun: tuple[float, tuple[int, int, int, int]] | None = None
        self.scheduled: tuple[int, int, int, int] | None = None
        self.scheduled_until = 0.0

    def _busy(self) -> None:
        self._drained = False

    def set_pending(self, pending: set[tuple[int, int, int]]) -> None:
        with self.lock:
            changed = pending != self.pending
            self.pending = set(pending)
            if changed and any(i[0] == self.epoch for i in pending): self._busy()
            if not any(i[0] == self.epoch for i in pending): self.underrun = None

    def put(self, entry: PCMEntry) -> tuple[bool, int, int]:
        with self.lock:
            if entry.epoch != self.epoch: self._events.put(("late", (entry, self.epoch))); return False, self.epoch, 0
            self.entries.append(entry); self._busy()
            buffered = sum(len(item.pcm) - item.offset for item in self.entries)
            return True, self.epoch, buffered

    def resume(self) -> None:
        with self.lock: self.paused = False

    def advance(self, epoch: int, preserve_playback: bool = False) -> int:
        with self.lock:
            buffered = sum(len(e.pcm) - e.offset for e in self.entries)
            if preserve_playback:
                self.preserved_epochs = {entry.epoch for entry in self.entries}
                dropped = 0
                self.paused = True; self.force_silence = False
            else:
                self.entries.clear(); self.preserved_epochs.clear()
                dropped = buffered
                self.paused = False; self.force_silence = True
                self.last_completed = None
            self.underrun = None
            self.epoch = epoch; self._busy()
            return dropped

    def snapshot(self, dac_time: float) -> dict:
        with self.lock:
            active = self.scheduled is not None and self.scheduled_until > dac_time
            fields = {"playback_active": active, "playback_tail_ms": round(max(0.0, self.scheduled_until - dac_time) * 1000, 3),
                "playback_scheduled_until": round(self.scheduled_until, 6) if active else None}
            if active:
                fields.update(zip(("playback_epoch", "playback_response_id", "playback_piece_id", "playback_chunk_id"), self.scheduled))
            else:
                fields.update({"playback_epoch": None, "playback_response_id": None, "playback_piece_id": None, "playback_chunk_id": None})
            return fields

    def render(self, frames: int, dac_time: float) -> tuple[bytes, bool, bool]:
        block = bytearray(frames * 2); wrote = 0; had_pcm = False
        with self.lock:
            if self.force_silence:
                self.force_silence = False; self._events.put(("silenced", {"epoch": self.epoch, "blocks": 1, "frames": frames, "dac_time": round(dac_time, 6)}))
                if not self.entries and not any(i[0] == self.epoch for i in self.pending): self._drained = True
                return bytes(block), False, True
            if self.paused: return bytes(block), False, False
            while wrote < len(block) and self.entries:
                entry = self.entries[0]
                if entry.epoch != self.epoch and entry.epoch not in self.preserved_epochs:
                    self.entries.popleft(); self._events.put(("late", (entry, self.epoch))); continue
                identity = (entry.epoch, entry.response, entry.piece, entry.chunk)
                if entry.started_dac is None:
                    entry.started_dac = dac_time + wrote / 2 / TTS_RATE
                    preserved = entry.epoch != self.epoch
                    if self.underrun is not None:
                        gap_start, previous = self.underrun; gap_end = entry.started_dac
                        self._events.put(("underrun", {"epoch": self.epoch, "start_dac_time": round(gap_start, 6),
                            "end_dac_time": round(gap_end, 6), "duration_ms": round(max(0.0, gap_end - gap_start) * 1000, 3),
                            "previous_response_id": previous[1], "previous_piece_id": previous[2], "previous_chunk_id": previous[3],
                            "next_response_id": entry.response, "next_piece_id": entry.piece, "next_chunk_id": entry.chunk}))
                        self.underrun = None
                    self._events.put(("started", {"epoch": entry.epoch, "live_epoch": self.epoch, "epoch_violation": False,
                        "preserved_playback": preserved, "response_id": entry.response, "piece_id": entry.piece,
                        "chunk_id": entry.chunk, "bytes": len(entry.pcm), "dac_time": round(entry.started_dac, 6)}))
                count = min(len(block) - wrote, len(entry.pcm) - entry.offset)
                block[wrote:wrote + count] = entry.pcm[entry.offset:entry.offset + count]
                wrote += count; entry.offset += count; had_pcm = had_pcm or count > 0
                self.scheduled, self.scheduled_until = identity, dac_time + wrote / 2 / TTS_RATE
                if entry.offset == len(entry.pcm):
                    self.entries.popleft(); self.last_completed = identity
                    self._events.put(("completed", {"epoch": entry.epoch, "live_epoch": self.epoch, "epoch_violation": False,
                        "preserved_playback": entry.epoch != self.epoch, "response_id": entry.response, "piece_id": entry.piece,
                        "chunk_id": entry.chunk, "bytes": len(entry.pcm), "start_dac_time": round(entry.started_dac, 6),
                        "end_dac_time": round(self.scheduled_until, 6)}))
            if not self.entries: self.preserved_epochs.clear()
            if wrote < len(block) and self.last_completed is not None and any(i[0] == self.epoch for i in self.pending) and self.underrun is None:
                self.underrun = (dac_time + wrote / 2 / TTS_RATE, self.last_completed)
            if not self.entries and not any(i[0] == self.epoch for i in self.pending) and not self.force_silence:
                self._drained = True
        return bytes(block), had_pcm, False

    def drained(self) -> bool:
        with self.lock: return self._drained and not self.entries and not self.force_silence

    def check(self) -> None:
        while not self._events.empty():
            event, value = self._events.get()
            if event == "late":
                e, live_epoch = value; self.journal.emit("playback", "dropped", epoch=e.epoch, live_epoch=live_epoch, epoch_violation=e.epoch != live_epoch, response_id=e.response, piece_id=e.piece, chunk_id=e.chunk, bytes=len(e.pcm) - e.offset)
            else:
                self.journal.emit("playback", event, **value)


class Sink(Wasapi):
    def __init__(self, renderer: Renderer, paths: Paths) -> None:
        super().__init__(paths)
        self.renderer = renderer
        self.drain_deadline = 0.0; self.drain_reported = False

    def _callback(self, outdata, frames, timing, status) -> None:
        if status:
            self.error = RuntimeError(f"WASAPI render: {status}"); raise sd.CallbackAbort
        target = np.frombuffer(outdata, dtype="<f4", count=frames)
        dac_time = float(timing.outputBufferDacTime)
        block, had_pcm, forced_silence = self.renderer.render(frames, dac_time)
        target[:] = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768.0
        if had_pcm or forced_silence:
            self.drain_deadline = dac_time + frames / TTS_RATE; self.drain_reported = False

    def snapshot(self) -> dict:
        now = float(getattr(self.stream, "time", 0.0)) if self.stream is not None else time.monotonic()
        return self.renderer.snapshot(now)

    def drained(self) -> bool:
        if not self.renderer.drained(): return False
        if not self.drain_deadline: return True
        now = float(getattr(self.stream, "time", 0.0)) if self.stream is not None else time.monotonic()
        if now >= self.drain_deadline:
            if not self.drain_reported:
                self.paths.journal.emit("playback", "drained", type="wasapi", dac_time=self.drain_deadline); self.drain_reported = True
            return True
        return False


class Synthesis:
    def __init__(self, paths: Paths, residents: Residents) -> None:
        self.paths, self.journal, self.residents = paths, paths.journal, residents
        residents.require_alive("chatterbox")
        self.lock = threading.Lock(); self.epoch = self.response_id = 0
        self.pending: set[tuple[int, int, int]] = set(); self.terminal: set[tuple[int, int, int]] = set()
        self.tts = residents.chatterbox_client(); self.renderer = Renderer(paths); self.sink = Sink(self.renderer, paths)
        self.reader = None; self.closed = residents.chatterbox_closed; self.active = False; self.first_pcm: set[tuple[int, int]] = set()

    def start_output(self) -> None:
        self.sink.open()
        try:
            self.reader = self.paths.supervisor.start("tts-reader", self._reader)
            self.residents.register_chatterbox_reader(self.reader); self.active = True
        except BaseException:
            self.sink.close(); raise

    def _sync_pending(self) -> None: self.renderer.set_pending(self.pending)

    def _live(self, epoch: int) -> bool:
        with self.lock:
            return epoch == self.epoch

    def send_sentence(self, epoch: int, response_id: int, piece_id: int, text: str) -> bool:
        identity = (epoch, response_id, piece_id)
        with self.lock:
            if epoch != self.epoch: return False
            if identity in self.pending or identity in self.terminal: raise RuntimeError("duplicate synthesis identity")
            self.pending.add(identity); self._sync_pending()
            try: self.tts.synthesize(epoch, response_id, piece_id, text)
            except BaseException:
                self.pending.remove(identity); self._sync_pending(); raise
        self.journal.emit("synthesis", "queued", epoch=epoch, response_id=response_id, piece_id=piece_id, chars=len(text), text=text)
        return True

    def advance(self, reason: str, utterance_id: int = 0, preserve_playback: bool = False) -> int:
        with self.lock:
            old_epoch = self.epoch; self.epoch += 1; epoch = self.epoch
            dropped = self.renderer.advance(epoch, preserve_playback)
            self.tts.advance(epoch); self._sync_pending(); native_advance_sent = True
            old_pending = sum(1 for identity in self.pending if identity[0] != epoch)
        if preserve_playback:
            self.journal.emit("playback", "paused", epoch=old_epoch, utterance_id=utterance_id)
        self.journal.emit("synthesis", "epoch.advanced", epoch=epoch, old_epoch=old_epoch, new_epoch=epoch, reason=reason, utterance_id=utterance_id, preserve_playback=preserve_playback, native_advance_sent=native_advance_sent, pending_cancel_count=old_pending, dropped_bytes=dropped)
        return epoch

    def cutover(self, epoch: int, reason: str, utterance_id: int) -> bool:
        with self.lock:
            if epoch != self.epoch: return False
            dropped = self.renderer.advance(epoch); self._sync_pending()
        self.journal.emit("playback", "cutover", epoch=epoch, reason=reason, utterance_id=utterance_id, dropped_bytes=dropped)
        return True

    def resume(self, utterance_id: int) -> None:
        self.renderer.resume(); self.journal.emit("playback", "resumed_after_backchannel", epoch=self.epoch, utterance_id=utterance_id)

    def _reader(self) -> None:
        while True:
            if (frame := self.tts.recv_frame()) is None:
                if not self.closed.is_set(): raise RuntimeError("native TTS socket closed before close handshake")
                break
            kind, epoch, response_id, piece_id, chunk_id, payload = frame; identity = (epoch, response_id, piece_id)
            if kind == RESP_PCM:
                response = (epoch, response_id)
                accepted, live_epoch, buffered = self.renderer.put(PCMEntry(epoch, response_id, piece_id, chunk_id, payload))
                self.journal.emit("synthesis", "pcm", epoch=epoch, live_epoch=live_epoch, epoch_violation=not accepted,
                    response_id=response_id, piece_id=piece_id, chunk_id=chunk_id, bytes=len(payload), accepted=accepted,
                    buffered_bytes=buffered)
                if accepted and response not in self.first_pcm:
                    self.first_pcm.add(response); self.journal.emit("synthesis", "first_result", epoch=epoch, response_id=response_id, piece_id=piece_id, chunk_id=chunk_id, bytes=len(payload))
            elif kind in (RESP_DONE, RESP_CANCELLED, RESP_ERROR):
                with self.lock:
                    if identity in self.terminal: raise RuntimeError(f"duplicate terminal ACK for {identity}")
                    if identity not in self.pending: raise RuntimeError(f"terminal ACK for unknown piece {identity}")
                    self.pending.remove(identity); self.terminal.add(identity); self._sync_pending()
                event = "acknowledged" if kind == RESP_DONE else "cancelled" if kind == RESP_CANCELLED else "failed"
                terminal_kind = "done" if kind == RESP_DONE else "cancelled" if kind == RESP_CANCELLED else "error"
                self.journal.emit("synthesis", event, epoch=epoch, response_id=response_id, piece_id=piece_id, terminal_kind=terminal_kind, error=payload.decode("utf-8", errors="replace") if kind == RESP_ERROR else None)
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
        assert primary is not None
        mode = TTSMode(paths, residents, primary, replacement, interrupt_after); mode.start(); mode.run()
    except BaseException as error:
        failure = (error, error.__traceback__)
    finish_cleanup(paths, failure, ([("tts", lambda: mode.stop(cancel=failure is not None))] if mode is not None else []) + [("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))])
