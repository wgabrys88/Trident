from __future__ import annotations

import argparse
import json
import re
import time
from contextlib import ExitStack
from pathlib import Path

from media import wav_pcm
from config import ASR_RATE, TTS_FIELDS, load_live_settings, resolve_voice
from conversation import Conversation
from log import note
from main import effective_family, finish, prepared_reference, start_run, synthesize_text, warm_resident, write_meta
from resident import status as resident_status

_TURN_IDLE_TIMEOUT_S = 120.0


def _settings_namespace(models_dir: Path, data_dir: Path):
    settings = load_live_settings(data_dir)
    values = {key: None for key, *_ in TTS_FIELDS}
    return argparse.Namespace(
        models_dir=models_dir, data_dir=data_dir, family=settings["tts_family"],
        tts_language=settings["tts_language"], reference=str(resolve_voice(data_dir, settings["tts_voice"])),
        streaming=None, stream_join=None, **values,
    ), settings


def run(says: list[str], expects: list[str] | None, models_dir: Path | None = None, data_dir: Path | None = None) -> int:
    paths = start_run("agent", models_dir, data_dir)
    namespace, settings = _settings_namespace(paths.models_dir, paths.data_dir)
    results = []
    outcome = "error"
    expects = expects or []
    with ExitStack() as cleanup:
        def finalize(exc_type, exc, tb):
            final = "error" if exc_type else outcome
            write_meta(paths, command="agent", turns=len(results), transcript=paths.transcript, outcome=final)
            finish(paths, final)
            return False
        cleanup.push(finalize)
        warm_resident(namespace)
        note(f"component=agent event=residents_ready state={[row['name'] for row in resident_status() if row['ready']]}")
        family = effective_family(settings["tts_family"], {"streaming": False})
        language = settings["tts_language"]
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family['name']}")
        reference = prepared_reference(resolve_voice(paths.data_dir, settings["tts_voice"]), paths.data_dir)
        engine = Conversation(paths.models_dir, paths.data_dir, settings, paths=paths, output_audio=False)
        cleanup.callback(engine.close)
        engine.start()
        for index, say in enumerate(says):
            expect = expects[index] if index < len(expects) else None
            prompt = paths.run_dir / f"prompt-{index:02d}.wav"
            synthesize_text(say, reference, prompt, language, family, paths)
            turn_before = engine.turn
            transcript_before = len(engine.transcript)
            started = time.perf_counter()
            engine.submit_audio(wav_pcm(prompt, ASR_RATE))
            deadline = time.monotonic() + _TURN_IDLE_TIMEOUT_S
            last_status = None
            settled = None
            while True:
                complete = engine.turn > turn_before and f"TTS {engine.turn} · complete" in engine.status
                if complete and settled is None:
                    settled = time.monotonic()
                if not complete:
                    settled = None
                if complete and time.monotonic() - settled >= 1.5:
                    break
                if engine.failure:
                    raise RuntimeError(f"conversation failed during turn {index + 1}: {engine.failure}")
                if engine.status != last_status:
                    last_status = engine.status
                    deadline = time.monotonic() + _TURN_IDLE_TIMEOUT_S
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"turn {index + 1} stalled for {_TURN_IDLE_TIMEOUT_S:g}s without progress"
                        f" (turn={engine.turn} status={engine.status!r})"
                    )
                time.sleep(0.1)
            heard = engine.transcript[transcript_before:].strip()
            answer = engine.answer.strip()
            match = bool(re.search(expect, answer)) if expect and expect != "-" else None
            elapsed = time.perf_counter() - started
            results.append({
                "say": say, "heard": heard, "answer": answer,
                "expect": expect, "match": match, "turn_s": round(elapsed, 3),
            })
            result = "match" if match is True else "mismatch" if match is False else "unchecked"
            note(f"component=agent event=turn outcome={result} turn_s={elapsed:.3f} heard_len={len(heard)}")
            if match is False:
                break
        failed = any(row["match"] is False for row in results)
        outcome = "failed" if failed else "ok"
        print(json.dumps({"run_dir": str(paths.run_dir), "turns": results}, ensure_ascii=False))
        return 1 if failed else 0
