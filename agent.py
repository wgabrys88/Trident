from __future__ import annotations

import json
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from cable import Microphone, play_wav, wav_pcm
from config import ASR_RATE, FAMILIES, LANGUAGES, TTS_RATE, load_live_settings, resolve_voice
from conversation import Conversation
from log import clear_run_log, note, set_run_log
from main import boot_residents, effective_family, finish, prepared_reference, start_run, synthesize_text, write_meta
from resident import status as resident_status

_TURN_IDLE_TIMEOUT_S = 120.0


class Speaker:
    def __init__(self) -> None:
        info = sd.query_devices(kind="output")
        self._rate = int(info["default_samplerate"])
        self._channels = max(1, int(info["max_output_channels"]))
        self._device = int(info["index"])
        self._name = str(info["name"])
        self._stream: sd.OutputStream | None = None
        note(
            f"component=speaker event=init device={self._device} name={self._name}"
            f" rate={self._rate} channels={self._channels}"
        )

    def _ensure(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.OutputStream(samplerate=self._rate, channels=self._channels, dtype="int16")
        self._stream.start()
        note(
            f"component=speaker event=start device={self._device} name={self._name}"
            f" rate={self._rate} channels={self._channels}"
        )

    def write(self, pcm16: bytes, src_rate: int = TTS_RATE) -> None:
        note(
            f"component=speaker event=write_begin bytes={len(pcm16)} src_rate={src_rate}"
            f" dst_rate={self._rate} channels={self._channels}"
        )
        started = time.perf_counter()
        if not pcm16:
            note(f"component=speaker event=write_return bytes=0 elapsed_ms={(time.perf_counter() - started) * 1000:.3f}")
            return
        samples = np.frombuffer(pcm16, dtype="<i2")
        if src_rate != self._rate and samples.size:
            count = max(1, round(samples.size * self._rate / src_rate))
            samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples.astype(np.float32)).astype("<i2")
        self._ensure()
        if self._channels == 1:
            frames = samples
        else:
            frames = np.zeros((len(samples), self._channels), dtype="<i2")
            frames[:, 0] = samples
            if self._channels > 1:
                frames[:, 1] = samples
        self._stream.write(frames)
        note(
            f"component=speaker event=write_return bytes={len(pcm16)} frames={int(samples.size)}"
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
        self.write(pcm16, rate)

    def reset(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.abort()
            stream.close()
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


def _pump_speaker(engine: Conversation) -> None:
    set_run_log(engine.paths.log)
    speaker = Speaker()
    try:
        while True:
            event = engine.next_output()
            kind, payload = event.kind, event.payload
            if kind == "audio-pcm":
                extra = f" bytes={len(payload)}"
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


def run(
    says: list[str],
    expects: list[str] | None = None,
    models_dir: Path | None = None,
    data_dir: Path | None = None,
    family: str | None = None,
    language: str | None = None,
) -> int:
    paths = start_run("agent", models_dir, data_dir)
    settings = _live_settings(paths.data_dir, family, language)
    if settings["tts_family"] not in FAMILIES:
        raise RuntimeError(f"unknown family {settings['tts_family']!r}")
    if settings["tts_language"] not in LANGUAGES:
        raise RuntimeError(f"unknown language {settings['tts_language']!r}")
    continuous = settings["ingestion_mode"] == "continuous"
    engine: Conversation | None = None
    mic: Microphone | None = None
    pump: threading.Thread | None = None
    starter: threading.Thread | None = None
    results = []
    outcome = "error"
    try:
        boot_residents(paths.models_dir, paths.data_dir, settings["tts_family"], settings["tts_language"], settings["tts_voice"])
        note(f"component=agent event=residents_ready state={[row['name'] for row in resident_status() if row['ready']]}")
        family_spec = effective_family(settings["tts_family"])
        language_code = settings["tts_language"]
        if language_code not in family_spec["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language_code!r} is not wired in {family_spec['name']}")
        reference = prepared_reference(resolve_voice(paths.data_dir, settings["tts_voice"]), paths.data_dir)
        engine = Conversation(paths.models_dir, paths.data_dir, settings, paths=paths, output_audio=True)
        start_error: list[BaseException] = []

        def start_engine() -> None:
            try:
                engine.start()
            except BaseException as exc:
                start_error.append(exc)

        starter = threading.Thread(target=start_engine, name="trident-engine-start")
        starter.start()
        prompts = []
        try:
            for index, say in enumerate(says):
                prompt = paths.run_dir / f"prompt-{index:02d}.wav"
                synthesize_text(say, reference, prompt, language_code, family_spec, paths)
                prompts.append(prompt)
        finally:
            starter.join()
        if start_error:
            raise start_error[0]
        pump = threading.Thread(target=_pump_speaker, args=(engine,), name="trident-speaker")
        pump.start()
        if continuous:
            mic = Microphone(engine.feed_audio)
            mic.start()
        for index, (say, prompt) in enumerate(zip(says, prompts)):
            expect = expects[index] if expects and index < len(expects) else None
            turn_before = engine.turn
            started_before = engine.tts_started_through
            transcript_before = len(engine.transcript)
            started = time.perf_counter()
            if continuous:
                play_wav(prompt)
            else:
                engine.submit_audio(wav_pcm(prompt, ASR_RATE))
            last = index + 1 == len(says)
            if last:
                _wait(engine, lambda: engine.turn > turn_before and engine.tts_done_through >= engine.turn, f"spoken reply {index + 1}")
            else:
                _wait(engine, lambda: engine.tts_started_through > started_before, f"system speech {index + 1}")
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
        print(json.dumps({"run_dir": str(paths.run_dir), "turns": results}, ensure_ascii=False))
        failed = any(row["match"] is False for row in results)
        outcome = "failed" if failed else "ok"
        return 1 if failed else 0
    except Exception as exc:
        note(f"component=agent event=run_exception type={type(exc).__name__} message={exc}")
        raise
    finally:
        try:
            note(
                f"component=agent event=teardown_begin outcome={outcome}"
                f" pump_alive={int(pump is not None and pump.is_alive())}"
            )
            if mic is not None:
                mic.stop()
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
