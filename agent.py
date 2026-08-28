from __future__ import annotations

import csv
import json
import queue
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from cable import _cable_devices, wav_pcm
from config import ASR_RATE, FAMILIES, LANGUAGES, Paths, TTS_RATE, load_live_settings, resolve_voice
from conversation import Conversation
from log import clear_run_log, note, set_run_log
from main import boot_residents, effective_family, finish, prepared_reference, start_run, synthesize_text, write_meta
from resident import status as resident_status

_TURN_IDLE_TIMEOUT_S = 120.0

_DEFAULT_ACTOR_CLAUSES = (
    "Count out loud slowly, from one to twenty, saying each number clearly and taking your time between each one, do not stop.",
    "What is the capital city of France?",
    "Spell the word intelligence letter by letter.",
    "Name three fruits that are yellow.",
    "What is twelve plus seven?",
    "What color is the ocean on a clear day?",
    "Who wrote the play Romeo and Juliet?",
    "How many days are in a leap year?",
)
_DEFAULT_CLOSING = "Stop counting and answer only this. What color is a clear daytime sky?"
_CLAUSE_GAP_S = 9.0


class Speaker:
    def __init__(self, reference) -> None:
        info = sd.query_devices(_cable_devices()["play"][0])
        self._reference = reference
        self._device = int(info["index"])
        self._name = str(info["name"])
        self._rate = int(info["default_samplerate"])
        self._channels = max(1, int(info["max_output_channels"]))
        self._assistant: queue.SimpleQueue = queue.SimpleQueue()
        self._actor: queue.SimpleQueue = queue.SimpleQueue()
        self._assistant_pending = np.zeros((0, self._channels), dtype="<i2")
        self._actor_pending = np.zeros((0, self._channels), dtype="<i2")
        self._actor_frames = 0
        self._actor_done = threading.Event()
        self._actor_done.set()
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        note(
            f"component=speaker event=init device={self._device} name={self._name}"
            f" rate={self._rate} channels={self._channels}"
        )

    def _take(self, source: queue.SimpleQueue, pending: np.ndarray, frames: int) -> tuple[np.ndarray, np.ndarray, int]:
        while len(pending) < frames:
            try:
                queued = source.get_nowait()
            except queue.Empty:
                break
            pending = np.concatenate((pending, queued))
        count = min(frames, len(pending))
        block = np.zeros((frames, self._channels), dtype="<i2")
        if count:
            block[:count] = pending[:count]
        return block, pending[count:], count

    def _cable_callback(self, outdata, frames, time_info, status) -> None:
        with self._lock:
            assistant, self._assistant_pending, _ = self._take(self._assistant, self._assistant_pending, frames)
            actor, self._actor_pending, actor_count = self._take(self._actor, self._actor_pending, frames)
            mixed = assistant.astype(np.int32) + actor.astype(np.int32)
            outdata[:] = np.clip(mixed, -32768, 32767).astype("<i2")
            self._actor_frames -= actor_count
            if self._actor_frames == 0:
                self._actor_done.set()
        self._reference(assistant[:, 0].tobytes(), self._rate)

    def _ensure(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.OutputStream(
            samplerate=self._rate, channels=self._channels, dtype="int16", device=self._device,
            callback=self._cable_callback,
        )
        self._stream.start()
        note(
            f"component=speaker event=start device={self._device} name={self._name}"
            f" rate={self._rate} channels={self._channels}"
        )

    def _frames(self, pcm16: bytes, src_rate: int) -> np.ndarray:
        samples = np.frombuffer(pcm16, dtype="<i2")
        if src_rate != self._rate and samples.size:
            count = max(1, round(samples.size * self._rate / src_rate))
            samples = np.interp(
                np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples.astype(np.float32),
            ).astype("<i2")
        frames = np.zeros((len(samples), self._channels), dtype="<i2")
        frames[:, 0] = samples
        if self._channels > 1:
            frames[:, 1] = samples
        return frames

    def write(self, pcm16: bytes, src_rate: int = TTS_RATE) -> None:
        started = time.perf_counter()
        if not pcm16:
            return
        frames = self._frames(pcm16, src_rate)
        self._ensure()
        with self._lock:
            self._assistant.put(frames)
        note(
            f"component=speaker event=write_return bytes={len(pcm16)} frames={len(frames)}"
            f" elapsed_ms={(time.perf_counter() - started) * 1000:.3f}"
        )

    def write_wav(self, path: str) -> None:
        note(f"component=speaker event=write_wav_begin path={path}")
        with wave.open(path, "rb") as audio:
            pcm16 = audio.readframes(audio.getnframes())
            rate = audio.getframerate()
            channels = audio.getnchannels()
        if channels > 1:
            pcm16 = np.frombuffer(pcm16, dtype="<i2").reshape(-1, channels)[:, 0].tobytes()
        frames = self._frames(pcm16, rate)
        self._ensure()
        with self._lock:
            self._actor_done.clear()
            self._actor_frames += len(frames)
            self._actor.put(frames)
        self._actor_done.wait()
        note(f"component=speaker event=write_wav_end path={path}")

    def reset(self) -> None:
        with self._lock:
            self._assistant = queue.SimpleQueue()
            self._assistant_pending = np.zeros((0, self._channels), dtype="<i2")
        note("component=speaker event=reset")

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
            note("component=speaker event=stopped")


def _live_settings(data_dir: Path, family: str | None, language: str | None) -> dict:
    settings = load_live_settings(data_dir)
    if family is not None:
        settings["tts_family"] = family
    if language is not None:
        settings["tts_language"] = language
    return settings


def _wait(engine: Conversation, pred, what: str) -> None:
    last_status = None
    deadline = time.monotonic() + _TURN_IDLE_TIMEOUT_S
    while True:
        if engine.failure:
            raise RuntimeError(f"conversation failed while waiting for {what}: {engine.failure}")
        if pred():
            return
        if engine.status != last_status:
            last_status = engine.status
            deadline = time.monotonic() + _TURN_IDLE_TIMEOUT_S
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"stalled {_TURN_IDLE_TIMEOUT_S:g}s waiting for {what}"
                f" (turn={engine.turn} tts_started={engine.tts_started_through}"
                f" tts_done={engine.tts_done_through} status={engine.status!r})"
            )
        time.sleep(0.1)


def _pump_speaker(engine: Conversation, speaker: Speaker) -> None:
    set_run_log(engine.paths.log)
    last_epoch = -1
    try:
        while True:
            event = engine.next_output()
            kind, payload, epoch = event.kind, event.payload, event.epoch
            if epoch != last_epoch and kind == "audio-pcm":
                last_epoch = epoch
            if kind == "audio-pcm":
                extra = f" bytes={len(payload)} epoch={epoch}"
            elif kind == "error":
                extra = f" type={type(payload).__name__} message={payload}"
            else:
                extra = ""
            note(f"component=speaker event=queue kind={kind}{extra}")
            if kind == "closed":
                return
            if kind == "error":
                raise RuntimeError(str(payload))
            if kind == "audio-reset":
                speaker.reset()
            elif kind == "audio-pcm":
                if epoch != engine.audio_epoch:
                    note(f"component=speaker event=stale_drop epoch={epoch} current={engine.audio_epoch}")
                    continue
                speaker.write(payload)
    except Exception as exc:
        note(f"component=speaker event=pump_exception type={type(exc).__name__} message={exc}")
        if engine.failure is None:
            engine.failure = exc
        raise
    finally:
        note("component=speaker event=close_begin")
        speaker.close()
        note("component=speaker event=close_end")
        clear_run_log(engine.paths.log)


def _build_long_actor(
    clauses: list[str], gap_seconds: float, reference: Path, family_spec: dict, language: str, paths: Paths, out_dir: Path,
) -> tuple[Path, int]:
    from main import synthesize_text
    wavs = []
    for index, clause in enumerate(clauses):
        path = out_dir / f"actor-{index:02d}.wav"
        synthesize_text(clause, reference, path, language, family_spec, paths)
        wavs.append(path)
    with wave.open(str(wavs[0]), "rb") as a:
        rate, ch, sw = a.getframerate(), a.getnchannels(), a.getsampwidth()
        if (ch, sw) != (1, 2):
            raise RuntimeError("actor clause WAV is not mono PCM16")
    frame = sw * ch
    gap_bytes = int(rate * gap_seconds) * frame
    silence = b"\x00" * gap_bytes
    combined = b""
    for i, w in enumerate(wavs):
        with wave.open(str(w), "rb") as a:
            combined += a.readframes(a.getnframes())
        if i < len(wavs) - 1:
            combined += silence
    actor = paths.run_dir / "actor.wav"
    with wave.open(str(actor), "wb") as o:
        o.setnchannels(ch); o.setsampwidth(sw); o.setframerate(rate)
        o.writeframes(combined)
    return actor, rate


def run(
    says: list[str],
    expects: list[str] | None = None,
    models_dir: Path | None = None,
    data_dir: Path | None = None,
    family: str | None = None,
    language: str | None = None,
    duration_seconds: float | None = None,
    actor_text: str | None = None,
    assert_blue: bool = False,
) -> int:
    paths = start_run("agent", models_dir, data_dir)
    settings = _live_settings(paths.data_dir, family, language)
    if settings["tts_family"] not in FAMILIES:
        raise RuntimeError(f"unknown family {settings['tts_family']!r}")
    if settings["tts_language"] not in LANGUAGES:
        raise RuntimeError(f"unknown language {settings['tts_language']!r}")
    continuous = settings["ingestion_mode"] == "continuous"
    longform = duration_seconds is not None and duration_seconds > 0
    engine: Conversation | None = None
    pump: threading.Thread | None = None
    speaker: Speaker | None = None
    results: list[dict] = []
    outcome = "error"
    turns_csv: Path | None = None
    actor_path: Path | None = None
    try:
        boot_residents(paths.models_dir, paths.data_dir, settings["tts_family"], settings["tts_language"], settings["tts_voice"])
        note(f"component=agent event=residents_ready state={[row['name'] for row in resident_status() if row['ready']]}")
        family_spec = effective_family(settings["tts_family"])
        language_code = settings["tts_language"]
        if language_code not in family_spec["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language_code!r} is not wired in {family_spec['name']}")
        reference = prepared_reference(resolve_voice(paths.data_dir, settings["tts_voice"]), paths.data_dir)
        if longform:
            closing = (actor_text or _DEFAULT_CLOSING).strip()
            clauses = [*_DEFAULT_ACTOR_CLAUSES, closing]
            actor_path, _actor_rate = _build_long_actor(clauses, _CLAUSE_GAP_S, reference, family_spec, language_code, paths, paths.run_dir / ".actor")
            says = clauses
            expects = ["-"] * (len(clauses) - 1) + ["blue"]
            actor_duration = wave.open(str(actor_path), "rb").getnframes() / _actor_rate
            note(f"component=agent event=longform_built clauses={len(clauses)} actor_duration_s={actor_duration:.3f}")
            turns_csv = paths.run_dir / "turns.csv"
            with turns_csv.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerow(["turn_index", "t_vad_end_s", "t_llm_begin_s", "t_first_pcm_s", "t_tts_done_s", "interrupted", "transcript_chars", "answer_chars", "mic_overflow_count"])
        prompts = []
        for index, say in enumerate(says):
            prompt = paths.run_dir / f"prompt-{index:02d}.wav"
            synthesize_text(say, reference, prompt, language_code, family_spec, paths)
            prompts.append(prompt)
        engine = Conversation(paths.models_dir, paths.data_dir, settings, paths=paths, output_audio=True)
        engine.start()
        speaker = Speaker(engine.reference_audio)
        pump = threading.Thread(target=_pump_speaker, args=(engine, speaker), name="trident-speaker")
        pump.start()
        log_offset = paths.log.stat().st_size
        t_run_start = time.monotonic()
        for index, (say, prompt) in enumerate(zip(says, prompts)):
            expect = expects[index] if expects and index < len(expects) else None
            turn_before = engine.turn
            started_before = engine.tts_started_through
            epoch_before = engine.audio_epoch
            transcript_before = len(engine.transcript)
            overflow_before = sum(1 for line in paths.log.read_text(encoding="utf-8", errors="replace").splitlines() if "mic_overflow" in line)
            started = time.perf_counter()
            if longform:
                speaker.write_wav(str(actor_path))
                break
            if continuous:
                speaker.write_wav(str(prompt))
            else:
                engine.submit_audio(wav_pcm(prompt, ASR_RATE))
            last = index + 1 == len(says)
            if last:
                _wait(engine, lambda: engine.turn > turn_before and (engine.tts_done_through >= engine.turn or engine.audio_epoch > epoch_before), f"spoken reply {index + 1}")
            else:
                _wait(engine, lambda: engine.tts_started_through > started_before or engine.audio_epoch > epoch_before, f"system speech {index + 1}")
            heard = engine.transcript[transcript_before:].strip()
            answer = engine.answer.strip()
            match = bool(re.search(expect, answer)) if expect and expect != "-" else None
            elapsed = time.perf_counter() - started
            results.append({
                "say": say, "heard": heard, "answer": answer,
                "expect": expect, "match": match,
                "turn_s": round(elapsed, 3),
            })
            note(f"component=agent event=turn outcome={'match' if match is True else 'mismatch' if match is False else 'unchecked'} turn_s={elapsed:.3f} heard_len={len(heard)}")
            if match is False:
                break
        if longform:
            assert actor_path is not None and turns_csv is not None
            expected_turns = len(says)
            deadline = t_run_start + (duration_seconds or 0) + 60
            while time.monotonic() < deadline:
                if engine.turn >= expected_turns and engine.tts_done_through >= engine.turn:
                    break
                if engine.failure:
                    break
                time.sleep(0.2)
            with paths.log.open("rb") as handle:
                handle.seek(log_offset)
                new_log = handle.read().decode("utf-8", errors="replace")
            log_lines = new_log.splitlines()
            overflow_after = sum(1 for line in log_lines if "mic_overflow" in line)
            vad_end_ts: list[str] = []
            for line in log_lines:
                if "vad_end" in line and "ts=" in line:
                    vad_end_ts.append(line.split("ts=", 1)[1].split(" ", 1)[0])
            rows: list[list] = []
            for turn_index in range(1, expected_turns + 1):
                turn_lines = [line for line in log_lines if f"turn={turn_index}" in line]
                vad_end = vad_end_ts[turn_index - 1] if turn_index - 1 < len(vad_end_ts) else ""
                llm_begin = next((line.split("ts=", 1)[1].split(" ", 1)[0] for line in turn_lines if "llm event=begin" in line), "")
                first_pcm = next((line.split("ts=", 1)[1].split(" ", 1)[0] for line in turn_lines if "started_through" in line and f"turn={turn_index}" in line), "")
                done = next((line.split("ts=", 1)[1].split(" ", 1)[0] for line in turn_lines if "done_through" in line and f"turn={turn_index} " in line), "")
                interrupted = any("epoch_bump" in line and f"turn={turn_index}" in line and "reason=vad-start" not in line for line in turn_lines[1:])
                rows.append([turn_index, vad_end, llm_begin, first_pcm, done, int(interrupted), 0, len(engine.history[turn_index * 2 - 1]["content"]) if engine.history and turn_index * 2 - 1 < len(engine.history) else 0, overflow_after])
            with turns_csv.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows(rows)
            results = [{
                "turns_completed": engine.turn,
                "tts_done_through": engine.tts_done_through,
                "audio_epoch": engine.audio_epoch,
                "answer": engine.answer,
                "actor_duration_s": round(actor_duration, 3),
                "mic_overflow_count": overflow_after,
                "transcript_chars": len(engine.transcript),
            }]
            note(f"component=agent event=longform_complete turns={engine.turn} done={engine.tts_done_through} overflow={overflow_after} csv={turns_csv}")
            assert_msg = ""
            if not expected_turns <= engine.turn <= expected_turns + 1 or engine.tts_done_through < engine.turn:
                assert_msg = f"longform produced {engine.turn} turns for {expected_turns} clauses and completed TTS through {engine.tts_done_through}"
                outcome = "failed"
            elif "blue" not in engine.answer.lower():
                assert_msg = f"closing answer does not contain 'blue': {engine.answer!r}"
                outcome = "failed"
            elif engine.failure:
                outcome = "error"
                raise engine.failure
            else:
                outcome = "ok"
        else:
            failed = any(row["match"] is False for row in results)
            outcome = "failed" if failed else "ok"
        print(json.dumps({"run_dir": str(paths.run_dir), "turns": results, "turns_csv": str(turns_csv) if turns_csv else None}, ensure_ascii=False, indent=2))
        if longform and outcome == "failed":
            raise SystemExit(assert_msg)
        return 1 if outcome == "failed" else 0
    except SystemExit:
        raise
    except Exception as exc:
        note(f"component=agent event=run_exception type={type(exc).__name__} message={exc}")
        raise
    finally:
        try:
            note(
                f"component=agent event=teardown_begin outcome={outcome}"
                f" pump_alive={int(pump is not None and pump.is_alive())}"
            )
            if engine is not None:
                engine.close()
            if pump is not None:
                pump.join()
                note(f"component=agent event=pump_join alive={int(pump.is_alive())}")
            if outcome == "ok" and engine is not None and engine.failure is not None:
                outcome = "error"
                raise engine.failure
            note("component=agent event=teardown_end")
        except Exception as exc:
            note(f"component=agent event=teardown_exception type={type(exc).__name__} message={exc}")
            raise
        finally:
            write_meta(
                paths, command="agent", turns=len(results), transcript=paths.transcript, outcome=outcome,
                family=settings["tts_family"], language=settings["tts_language"],
            )
            finish(paths, outcome)
