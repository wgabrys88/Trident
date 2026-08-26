from __future__ import annotations

import re
import time
from pathlib import Path

from config import ASR_RATE, LIVE_AUDIO, effective_family, load_live_settings, resolve_voice
from conversation import Conversation, prepared_reference, synthesize_text, warm_residents
from log import finish, note, start_run, write_meta
from media import wav_pcm

_TURN_IDLE_TIMEOUT_S = 120.0


def run(says: list[str], expects: list[str] | None = None, models_dir: Path | None = None, data_dir: Path | None = None) -> dict:
    if not says:
        raise RuntimeError("agent requires at least one prompt")
    paths = start_run("agent", models_dir, data_dir)
    settings = load_live_settings(paths.data_dir)
    family = effective_family(settings["tts_family"], {"streaming": False})
    language = settings["tts_language"]
    reference = prepared_reference(resolve_voice(paths.data_dir, settings["tts_voice"]), paths.data_dir)
    warm_residents(paths, settings)
    engine = Conversation(paths.models_dir, paths.data_dir, settings, paths=paths, output_audio=False)
    results = []
    outcome = "error"
    try:
        engine.start()
        for index, say in enumerate(says):
            expect = expects[index] if expects and index < len(expects) else None
            prompt = paths.run_dir / f"prompt-{index:02d}.wav"
            synthesize_text(say, reference, prompt, language, family, paths, streaming=False)
            turn_before, transcript_before = engine.turn, len(engine.transcript)
            started = time.perf_counter()
            pcm = wav_pcm(prompt, ASR_RATE)
            if settings["ingestion_mode"] == "continuous":
                frame = int(LIVE_AUDIO["vad_frame_samples"])
                silence = ((int(settings["vad_silence_ms"] * ASR_RATE / 1000) + frame - 1) // frame + 1) * frame
                pcm += bytes(silence * 4)
                chunk = max(1, int(ASR_RATE * LIVE_AUDIO["asr_feed_seconds"])) * 4
                for offset in range(0, len(pcm), chunk):
                    engine.feed_audio(pcm[offset:offset + chunk])
            else:
                engine.submit_audio(pcm)
            deadline, last_status, settled = time.monotonic() + _TURN_IDLE_TIMEOUT_S, None, None
            while True:
                complete = engine.turn > turn_before and f"TTS {engine.turn} · complete" in engine.status
                if complete and settled is None:
                    settled = time.monotonic()
                if complete and time.monotonic() - settled >= 1.5:
                    break
                if engine.failure:
                    raise RuntimeError(f"conversation failed during turn {index + 1}: {engine.failure}")
                if engine.status != last_status:
                    last_status, deadline = engine.status, time.monotonic() + _TURN_IDLE_TIMEOUT_S
                if time.monotonic() > deadline:
                    raise RuntimeError(f"turn {index + 1} stalled for {_TURN_IDLE_TIMEOUT_S:g}s without progress (turn={engine.turn} status={engine.status!r})")
                time.sleep(0.1)
            answer = engine.answer.strip()
            match = bool(re.search(expect, answer)) if expect and expect != "-" else None
            elapsed = time.perf_counter() - started
            row = {"say": say, "heard": engine.transcript[transcript_before:].strip(), "answer": answer, "expect": expect, "match": match, "turn_s": round(elapsed, 3)}
            results.append(row)
            note(f"component=agent event=turn outcome={'match' if match is True else 'mismatch' if match is False else 'unchecked'} turn_s={elapsed:.3f} heard_len={len(row['heard'])}")
            if match is False:
                break
        outcome = "failed" if any(row["match"] is False for row in results) else "ok"
        return {"run_dir": str(paths.run_dir), "outcome": outcome, "turns": results}
    finally:
        engine.close()
        write_meta(paths, command="agent", turns=len(results), transcript=paths.transcript, outcome=outcome)
        finish(paths, outcome)
