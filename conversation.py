from __future__ import annotations

import queue
import threading
import time
import wave
from pathlib import Path


from config import ASR_CHUNK_OVERLAP_SECONDS, ASR_CHUNK_SECONDS, ASR_RATE, BRAIN_MODEL, BRAIN_RUNTIME, LIVE_AUDIO, REFERENCE_MIN_SECONDS, SHARED_MODELS, SMART_TURN_SECONDS, TTS_RATE, Paths, effective_family, gemma_payload, render_system_prompt, resolve_voice, resolved_tts, spoken_reply
from installer import models_for, require_model, runtime_server, runtime_tts_server, validate_wav
from local_api import chatterbox_stream, gemma_chat_stream, parakeet_transcribe
from log import clear_run_log, finish, note, set_run_log, start_run, write_meta, write_text
from media import chatterbox_wav, float_pcm16, parakeet_chunks, write_pcm_wav
from resident import ensure_chatterbox, ensure_gemma, ensure_parakeet, status as resident_status
from vad import SileroEndpoint, SmartTurnEndpoint



def prepared_reference(reference: Path, data_dir: Path) -> Path:
    wav = chatterbox_wav(reference, data_dir / "prepared")
    validate_wav(wav, TTS_RATE, minimum_seconds=REFERENCE_MIN_SECONDS, channels=1)
    with wave.open(str(wav), "rb") as audio:
        seconds = audio.getnframes() / audio.getframerate()
    note(f"component=tts event=reference_ready duration_s={seconds:.3f}")
    return wav


def transcribe_wav(wav: Path, base: str, chunk_dir: Path) -> str:
    with wave.open(str(wav), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    started = time.perf_counter()
    words, chunks = [], 0
    for chunk, offset, chunk_seconds, final in parakeet_chunks(wav, chunk_dir, ASR_CHUNK_SECONDS, ASR_CHUNK_OVERLAP_SECONDS):
        payload = parakeet_transcribe(base, chunk)
        chunks += 1
        rows = payload.get("words")
        if duration <= ASR_CHUNK_SECONDS and not rows:
            text = str(payload.get("text") or "").strip()
            words = [text] if text else []
            break
        if not isinstance(rows, list):
            raise RuntimeError("Parakeet verbose transcript did not include word timestamps")
        left = 0.0 if offset == 0 else ASR_CHUNK_OVERLAP_SECONDS / 2
        right = chunk_seconds if final else chunk_seconds - ASR_CHUNK_OVERLAP_SECONDS / 2
        for row in rows:
            midpoint = (float(row["start"]) + float(row["end"])) / 2
            if left <= midpoint < right or final and midpoint == right:
                word = str(row.get("word", row.get("w", ""))).strip()
                if word:
                    words.append(word)
    text = " ".join(words).strip()
    elapsed = time.perf_counter() - started
    rtf = elapsed / duration if duration else 0.0
    note(f"component=asr event=done duration_s={duration:.3f} chunks={chunks} request_ms={elapsed * 1000:.3f} rtf={rtf:.4f} x_realtime={1.0 / rtf if rtf else 0.0:.2f}")
    return text



def transcribe_pcm(pcm_f32: bytes, paths: Paths) -> str:
    if not pcm_f32:
        raise RuntimeError("audio is empty")
    write_pcm_wav(paths.input, pcm_f32)
    base = ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir))
    text = transcribe_wav(paths.input, base, paths.run_dir / ".asr-chunks")
    if not text:
        raise RuntimeError("Parakeet returned an empty transcript")
    write_text(paths.transcript, text + "\n")
    return text

def tts_endpoint(reference: Path, language: str, family: dict, paths: Paths) -> str:
    models = models_for(family["name"])
    return ensure_chatterbox(runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir), require_model(models["chatterbox-codec"], paths.models_dir), reference, family, language)


def tts_metrics(result: str) -> dict[str, float | str]:
    fields = {key: value for item in result.split() if "=" in item for key, value in [item.split("=", 1)]}
    samples = int(fields["samples"])
    rtf = float(fields["wall_rtf"])
    return {**fields, "audio_s": samples / TTS_RATE, "rtf": rtf, "x_realtime": 1.0 / rtf if rtf else 0.0}


def stream_synthesize(text: str, reference: Path, output: Path, language: str, family: dict, paths: Paths, *, base: str | None = None, unit: int | None = None, streaming: bool | None = None, cancel=None):
    endpoint = base or tts_endpoint(reference, language, family, paths)
    stream = family["TTS_STREAM"]
    enabled = stream["enabled"] if streaming is None else streaming
    try:
        generator = chatterbox_stream(endpoint, text.strip(), output, enabled, stream["join"], cancel=cancel)
        while True:
            try:
                yield next(generator)
            except StopIteration as done:
                result = str(done.value or "")
                if result:
                    fields = tts_metrics(result)
                    prefix = f" unit={unit}" if unit is not None else ""
                    note("component=tts event=complete outcome=ok" + prefix + f" audio_s={fields['audio_s']:.3f} chunks={fields['chunks']} total_ms={fields['total_ms']} t3_ms={fields['t3_ms']} s3gen_ms={fields['s3gen_ms']} ttfa_ms={fields['ttfa_ms']} rtf={fields['rtf']:.4f} x_realtime={fields['x_realtime']:.2f}")
                return result
    except Exception as exc:
        message = " ".join(str(exc).split())
        reason = "missing_eos" if "without EOS" in message else "request_error"
        prefix = f" unit={unit}" if unit is not None else ""
        ceiling = f" configured_max_tokens={family['TTS_SAMPLE']['max_tokens']}" if reason == "missing_eos" else ""
        note(f"component=tts event=failed outcome=error reason={reason}{prefix}{ceiling} message={message}")
        raise


def synthesize_text(text: str, reference: Path, output: Path, language: str, family: dict, paths: Paths, *, base: str | None = None, streaming: bool | None = None, unit: int | None = None, cancel=None) -> str:
    generator = stream_synthesize(text, reference, output, language, family, paths, base=base, unit=unit, streaming=streaming, cancel=cancel)
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return str(done.value or "")


def warm_residents(paths: Paths, settings: dict) -> None:
    family = effective_family(settings["tts_family"])
    language = settings["tts_language"]
    reference = prepared_reference(resolve_voice(paths.data_dir, settings["tts_voice"]), paths.data_dir)
    ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir))
    ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir), BRAIN_RUNTIME)
    tts_endpoint(reference, language, family, paths)


def resident_report() -> str:
    return "\n".join(f"{row['name']}: {'ready' if row['ready'] else 'stopped'} pid={row['pid'] or '-'} url={row['url']} family={row.get('family') or '-'}" for row in resident_status())


class _SpeechSegmenter:
    def __init__(self, minimum: int, hard_limit: int) -> None:
        if minimum < 1 or hard_limit < minimum:
            raise ValueError("speech segmentation limits are invalid")
        self.minimum = minimum
        self.hard_limit = hard_limit
        self.sent = 0

    def update(self, text: str, flush: bool = False) -> list[str]:
        units = []
        while self.sent < len(text):
            pending = text[self.sent:]
            stop = min(len(pending), self.hard_limit)
            cut = next((i + 1 for i in range(self.minimum - 1, stop) if pending[i] in ".?!" and (i + 1 == len(pending) or pending[i + 1].isspace())), 0)
            if not cut and len(pending) >= self.hard_limit:
                split = max(pending.rfind(ch, self.minimum, self.hard_limit) for ch in (" ", "\n", "\t"))
                cut = split + 1 if split >= self.minimum else self.hard_limit
            if not cut and flush:
                cut = len(pending)
            if not cut:
                break
            unit = pending[:cut].strip()
            self.sent += cut
            while self.sent < len(text) and text[self.sent].isspace():
                self.sent += 1
            if unit:
                units.append(unit)
        return units


class Conversation:
    def __init__(self, models_dir: Path, data_dir: Path, settings: dict, paths=None, output_audio: bool = True) -> None:
        self.models_dir = models_dir
        self.data_dir = data_dir
        self.settings = dict(settings)
        self.paths = paths
        self.owns_run = paths is None
        self.output_audio = output_audio
        self.parakeet = None
        self.smart_turn = None
        self.vad = None
        self.gemma_base = None
        self.tts_base = None
        self.turn_wave = None
        self.turn_path = None
        self.turn_tail = bytearray()
        self.turn_index = 0
        self.asr_queue = queue.SimpleQueue()
        self.llm_queue = queue.SimpleQueue()
        self.tts_queue = queue.SimpleQueue()
        self.output_queue = queue.SimpleQueue()
        self.asr_thread = None
        self.llm_thread = None
        self.tts_thread = None
        self.active = False
        self.failure = None
        self.turn = 0
        self.transcript = ""
        self.answer = ""
        self.status = "Stopped"
        self.history = []
        self.references: dict[str, Path] = {}
        self.cancelled_through = 0
        self.tts_done_through = 0

    def _family(self, settings: dict, streaming: bool):
        return effective_family(settings["tts_family"], {"streaming": streaming, "stream_join": settings["tts_join"]})

    def _require_engine(self) -> None:
        if self.failure:
            raise RuntimeError(str(self.failure))
        if not self.active:
            raise RuntimeError("conversation is not active")

    def _reference(self, voice: str) -> Path:
        source = resolve_voice(self.data_dir, voice).resolve()
        key = str(source)
        if key not in self.references:
            self.references[key] = prepared_reference(source, self.data_dir)
        return self.references[key]

    def _emit(self, kind: str, payload=None) -> None:
        if self.output_audio or not kind.startswith("audio-"):
            self.output_queue.put((kind, payload))

    def _state(self, status: str | None = None) -> None:
        if status is not None:
            self.status = status
        self._emit("state")

    def _worker(self, target, name: str) -> threading.Thread:
        def run():
            set_run_log(self.paths.log)
            try:
                target()
            except Exception as exc:
                self.failure = exc
                self.status = f"{name} failed · {exc}"
                self._emit("error", exc)
            finally:
                clear_run_log(self.paths.log)

        return threading.Thread(target=run, name=name, daemon=True)

    def start(self) -> None:
        if self.active:
            raise RuntimeError("conversation is already active")
        if self.paths is None:
            self.paths = start_run("conversation", self.models_dir, self.data_dir)
        else:
            set_run_log(self.paths.log)
        started = False
        try:
            self.parakeet = ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], self.models_dir))
            self.gemma_base = ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], self.models_dir), BRAIN_RUNTIME)
            settings = dict(self.settings)
            family = self._family(settings, settings["tts_mode"] == "real")
            language = settings["tts_language"]
            if language not in family["TTS_LANGUAGES"]:
                raise RuntimeError(f"language {language!r} is not wired in {family['name']}")
            self.tts_base = tts_endpoint(self._reference(settings["tts_voice"]), language, family, self.paths)
            self.smart_turn = SmartTurnEndpoint(require_model(SHARED_MODELS["smart-turn"], self.models_dir))
            self.vad = SileroEndpoint(settings["vad_threshold"], settings["vad_silence_ms"])
            self.active = True
            self.asr_thread = self._worker(self._asr_loop, "trident-asr")
            self.llm_thread = self._worker(self._llm_loop, "trident-llm")
            self.tts_thread = self._worker(self._tts_loop, "trident-tts")
            self.asr_thread.start()
            self.llm_thread.start()
            self.tts_thread.start()
            self._state("Listening · Parakeet ASR · Smart Turn CPU · Gemma · TTS resident")
            started = True
        finally:
            if not started and self.owns_run:
                finish(self.paths, "error")

    def configure(self, settings: dict) -> None:
        vad_changed = settings["vad_threshold"] != self.settings["vad_threshold"] or settings["vad_silence_ms"] != self.settings["vad_silence_ms"]
        voice_changed = any(settings[key] != self.settings[key] for key in ("tts_family", "tts_voice", "tts_language"))
        self.settings = dict(settings)
        if vad_changed:
            self.asr_queue.put(("vad-config", (self.settings["vad_threshold"], self.settings["vad_silence_ms"])))
        if voice_changed:
            self.tts_base = None
        note(
            f"component=conversation event=configure ingestion={settings['ingestion_mode']}"
            f" tts_family={settings['tts_family']} tts_mode={settings['tts_mode']}"
            f" tts_language={settings['tts_language']} vad_threshold={settings['vad_threshold']}"
            f" vad_silence_ms={settings['vad_silence_ms']}"
        )
        self._state("Configuration applied")

    def submit(self, text: str) -> None:
        self._require_engine()
        self._interrupt_tts()
        self.asr_queue.put(("manual", text.strip()))

    def submit_audio(self, pcm_f32: bytes) -> None:
        self._require_engine()
        if not pcm_f32:
            self._state("Push-to-talk · no audio")
            return
        self._interrupt_tts()
        self.asr_queue.put(("feed", pcm_f32))
        self.asr_queue.put(("cut", "PTT"))
        self._state("Push-to-talk · finalizing")

    def feed_audio(self, pcm_f32: bytes) -> None:
        self._require_engine()
        if self.settings["ingestion_mode"] != "continuous":
            return
        if pcm_f32:
            self.asr_queue.put(("feed", pcm_f32))

    def _interrupt_tts(self) -> None:
        target = self.turn
        if target < 1:
            return
        self._emit("audio-reset")
        if target <= self.tts_done_through or target <= self.cancelled_through:
            return
        self.cancelled_through = target
        note(f"component=tts event=interruption_requested through_turn={target}")
        self._state(f"TTS through {target} · interrupted")

    def _tts_cancelled(self, turn: int) -> bool:
        return turn <= self.cancelled_through

    def _append_turn(self, pcm_f32: bytes) -> None:
        if self.turn_wave is None:
            self.turn_index += 1
            self.turn_path = self.paths.run_dir / f".turn-{self.turn_index:04d}.wav"
            self.turn_wave = wave.open(str(self.turn_path), "wb")
            self.turn_wave.setnchannels(1)
            self.turn_wave.setsampwidth(2)
            self.turn_wave.setframerate(ASR_RATE)
        self.turn_wave.writeframesraw(float_pcm16(pcm_f32))
        self.turn_tail.extend(pcm_f32)
        limit = SMART_TURN_SECONDS * ASR_RATE * 4
        if len(self.turn_tail) > limit:
            del self.turn_tail[:-limit]

    def _take_turn(self) -> Path | None:
        if self.turn_wave is None:
            return None
        self.turn_wave.close()
        path = self.turn_path
        self.turn_wave = self.turn_path = None
        self.turn_tail.clear()
        return path

    def _discard_turn(self) -> None:
        path = self._take_turn()
        if path:
            path.unlink(missing_ok=True)

    def _transcribe_turn(self, reason: str) -> None:
        path = self._take_turn()
        if not path:
            return
        try:
            self._state(f"Parakeet · transcribing · {reason}")
            text = transcribe_wav(path, self.parakeet, self.paths.run_dir / ".asr-chunks")
        finally:
            path.unlink(missing_ok=True)
        if not text:
            self._state(f"Parakeet · no speech · {reason}")
            return
        self.transcript = (self.transcript.rstrip() + " " + text).strip()
        self._dispatch(reason, text)

    def _asr_loop(self) -> None:
        while True:
            op, payload = self.asr_queue.get()
            if op == "feed":
                self._append_turn(payload)
                if self.settings["ingestion_mode"] == "continuous":
                    speech_started, speech_ended = self.vad.feed(payload)
                    if speech_started:
                        self._interrupt_tts()
                    if not speech_ended:
                        continue
                    started = time.perf_counter()
                    complete, probability = self.smart_turn.complete(bytes(self.turn_tail))
                    note(
                        f"component=vad event=smart_turn complete={int(complete)}"
                        f" p={probability:.3f} elapsed_ms={(time.perf_counter() - started) * 1000:.3f}"
                    )
                    self._state(f"Smart Turn · {'complete' if complete else 'continue'} · p={probability:.3f}")
                    if complete:
                        self.vad.reset()
                        self._transcribe_turn("SMART")
            elif op == "cut":
                self.vad.reset()
                self._transcribe_turn(payload)
            elif op == "manual":
                if payload:
                    self._dispatch("MANUAL", payload)
                else:
                    self.vad.reset()
                    self._transcribe_turn("MANUAL")
            elif op == "vad-config":
                self.vad.configure(*payload)
            elif op == "finish":
                if self.vad.speech:
                    self._transcribe_turn("STOP")
                else:
                    self._discard_turn()
                return
            else:
                raise RuntimeError(f"unknown ASR queue operation: {op}")

    def _dispatch(self, reason: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.turn += 1
        note(f'component=conversation event=dispatch turn={self.turn} reason={reason} text="{text}"')
        self.llm_queue.put((self.turn, text, dict(self.settings)))
        self._state(f"Dispatch {self.turn} · {reason} · {len(text)} chars")

    def _llm_payload(self, text: str, settings: dict) -> dict:
        language = settings["tts_language"]
        system = render_system_prompt(settings["system_prompt"], language)
        limit = int(LIVE_AUDIO["llm_history_turns"]) * 2
        return gemma_payload(
            [{"role": "system", "content": system}, *self.history[-limit:], {"role": "user", "content": text}],
            stream=True,
        )

    def _llm_loop(self) -> None:
        base = self.gemma_base
        while True:
            item = self.llm_queue.get()
            if item is None:
                return
            turn, prompt, settings = item
            self.answer = ""
            self._state(f"LLM {turn} · generating")
            raw = ""
            started = time.perf_counter()
            ttfa = None
            family = self._family(settings, settings["tts_mode"] == "real")
            hard_limit = int(family["TTS_CHUNK"]["chars"])
            segmenter = _SpeechSegmenter(min(int(family["TTS_CHUNK"]["first_chars"]), hard_limit), hard_limit)
            speech_started = False

            def enqueue(units) -> None:
                nonlocal speech_started
                for unit in units:
                    if speech_started:
                        self.tts_queue.put(("unit", turn, unit))
                    else:
                        self.tts_queue.put(("start", turn, unit, settings))
                        speech_started = True

            for delta in gemma_chat_stream(base, self._llm_payload(prompt, settings)):
                if ttfa is None:
                    ttfa = time.perf_counter() - started
                raw += delta
                self.answer = raw
                units = segmenter.update(spoken_reply(raw, streaming=True))
                enqueue(units)
                if units:
                    self._state(f"LLM {turn} · speech ready")
            answer = spoken_reply(raw)
            if not answer:
                raise RuntimeError("Gemma returned an empty answer")
            enqueue(segmenter.update(answer, flush=True))
            self.answer = answer
            write_text(self.paths.answer, answer + "\n")
            self.history.extend(({"role": "user", "content": prompt}, {"role": "assistant", "content": answer}))
            self.tts_queue.put(("end", turn, answer))
            note(
                f"component=llm event=complete turn={turn} ttfa_ms={(ttfa or time.perf_counter() - started) * 1000:.3f}"
                f" total_ms={(time.perf_counter() - started) * 1000:.3f} chars={len(answer)}"
            )
            note(f'component=llm event=answer turn={turn} text="{answer}"')
            self._state(f"LLM {turn} · complete · TTS finishing")

    def _tts_loop(self) -> None:
        while True:
            item = self.tts_queue.get()
            if item is None:
                return
            op, turn, text, settings = item
            if op != "start":
                raise RuntimeError(f"unexpected TTS queue operation: {op}")
            unit, index = text, 0
            interrupted = self._tts_cancelled(turn)
            if not interrupted:
                family = self._family(settings, settings["tts_mode"] == "real")
                language = settings["tts_language"]
                reference = self._reference(settings["tts_voice"])
                streaming = settings["tts_mode"] == "real"
                base = self.tts_base
                if base is None:
                    base = self.tts_base = tts_endpoint(reference, language, family, self.paths)
                while unit is not None and not interrupted:
                    index += 1
                    self._state(f"TTS {turn} · {'streaming' if streaming else 'buffered'} · speech unit {index}")
                    output = self.paths.run_dir / f"tts-turn-{turn:04d}-{index:03d}.wav"
                    cancel = lambda turn=turn: self._tts_cancelled(turn)
                    if streaming:
                        for raw in stream_synthesize(unit, reference, output, language, family, self.paths, base=base, unit=index, cancel=cancel):
                            self._emit("audio-pcm", raw)
                    else:
                        result = synthesize_text(unit, reference, output, language, family, self.paths, base=base, streaming=False, unit=index, cancel=cancel)
                        if not cancel():
                            if not output.is_file():
                                raise RuntimeError(f"Chatterbox did not create buffered unit {index}: {result}")
                            self._emit("audio-file", str(output))
                    interrupted = cancel()
                    if not interrupted:
                        unit, _ = self._next_tts(turn)
            if interrupted:
                while unit is not None:
                    unit, _ = self._next_tts(turn)
                note(f"component=tts event=interrupted turn={turn}")
            self.tts_done_through = turn
            self._state(f"TTS {turn} · {'interrupted' if interrupted else 'complete'}")

    def _next_tts(self, turn: int) -> tuple[str | None, str | None]:
        item = self.tts_queue.get()
        if item is None:
            raise RuntimeError(f"TTS turn {turn} ended without its marker")
        op, item_turn, text = item
        if item_turn != turn:
            raise RuntimeError(f"TTS turn {item_turn} interleaved with turn {turn}")
        if op == "unit":
            return text, None
        if op == "end":
            return None, text
        raise RuntimeError(f"unexpected TTS queue operation: {op}")

    def stop(self) -> None:
        if not self.active:
            return
        self._interrupt_tts()
        self.active = False
        self.asr_queue.put(("finish", None))
        self.asr_thread.join()
        self.llm_queue.put(None)
        self.llm_thread.join()
        self.tts_queue.put(None)
        self.tts_thread.join()
        write_text(self.paths.transcript, self.transcript + ("\n" if self.transcript else ""))
        if self.owns_run:
            final_family = self._family(self.settings, self.settings["tts_mode"] == "real")
            write_meta(
                self.paths, command="conversation", transcript=self.paths.transcript, turns=self.turn,
                turn_detector="smart-turn-v3.2-cpu", vad_threshold=self.settings["vad_threshold"],
                vad_silence_ms=self.settings["vad_silence_ms"], tts_mode=self.settings["tts_mode"],
                tts_language=self.settings["tts_language"], resolved_tts=resolved_tts(final_family),
            )
            set_run_log(self.paths.log)
            finish(self.paths)
        self._state("Stopped")
        self._emit("closed")

    def close(self) -> None:
        if self.active:
            self.stop()
        else:
            self._discard_turn()

    def next_output(self):
        return self.output_queue.get()
