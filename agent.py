from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from cable import Microphone, play_wav, restore, use as cable_use
from config import TTS_FIELDS, load_live_settings, resolve_voice
from conversation import Conversation
from log import clear_run_log, note
from main import effective_family, prepared_reference, start_run, synthesize_text, warm_resident
from resident import status as resident_status

_TURN_TIMEOUT_S = 120.0


def _settings_namespace(models_dir: Path, data_dir: Path):
    settings = load_live_settings(data_dir)
    values = {key: None for key, *_ in TTS_FIELDS}
    return argparse.Namespace(
        models_dir=models_dir, data_dir=data_dir, family=settings["tts_family"],
        tts_language=settings["tts_language"], reference=str(resolve_voice(data_dir, settings["tts_voice"])),
        streaming=None, stream_join=None, **values,
    ), settings


def run(says: list[str], expects: list[str], models_dir: Path | None = None, data_dir: Path | None = None) -> int:
    paths = start_run("agent", models_dir, data_dir)
    routing = cable_use()
    engine: Conversation | None = None
    mic: Microphone | None = None
    try:
        warm_resident(_settings_namespace(paths.models_dir, paths.data_dir)[0])
        note(f"component=agent event=residents_ready state={[row['name'] for row in resident_status() if row['ready']]}")
        _, settings = _settings_namespace(paths.models_dir, paths.data_dir)
        family = effective_family(settings["tts_family"], {"streaming": False})
        language = settings["tts_language"]
        if language not in family["TTS_LANGUAGES"]:
            raise RuntimeError(f"language {language!r} is not wired in {family['name']}")
        reference = prepared_reference(resolve_voice(paths.data_dir, settings["tts_voice"]), paths.data_dir)

        engine = Conversation(paths.models_dir, paths.data_dir, settings)
        engine.start()
        mic = Microphone(engine.feed_audio)
        mic.start()
        results = []
        for index, say in enumerate(says):
            expect = expects[index] if index < len(expects) else None
            prompt = paths.run_dir / f"prompt-{index:02d}.wav"
            synthesize_text(say, reference, prompt, language, family, paths)
            turn_before = engine.turn
            transcript_before = len(engine.transcript)
            started = time.perf_counter()
            play_wav(prompt)
            deadline = time.monotonic() + _TURN_TIMEOUT_S
            while True:
                if engine.turn > turn_before and f"TTS {engine.turn} · complete" in engine.status:
                    break
                if engine.failure:
                    raise RuntimeError(f"conversation failed during turn {index + 1}: {engine.failure}")
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"turn {index + 1} did not complete within {_TURN_TIMEOUT_S:g}s"
                        f" (turn={engine.turn} status={engine.status!r})"
                    )
                time.sleep(0.1)
            heard = engine.transcript[transcript_before:].strip()
            answer = engine.answer.strip()
            match = bool(re.search(expect, heard)) if expect else None
            elapsed = time.perf_counter() - started
            results.append({
                "say": say, "heard": heard, "answer": answer,
                "expect": expect, "match": match,
                "turn_s": round(elapsed, 3),
                "conversation_run_dir": str(engine.paths.run_dir),
            })
            note(f"component=agent event=turn outcome={'match' if match else 'unchecked'} turn_s={elapsed:.3f} heard_len={len(heard)}")
            if match is False:
                break
        print(json.dumps({"run_dir": str(paths.run_dir), "turns": results}, ensure_ascii=False))
        failed = any(row["match"] is False for row in results)
        return 1 if failed else 0
    finally:
        if mic is not None:
            mic.stop()
        if engine is not None:
            engine.close()
        restore(routing["previous"])
        clear_run_log(paths.log)
