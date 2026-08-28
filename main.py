from __future__ import annotations

import argparse
import json
import os, sys
import threading
import time
import wave
from pathlib import Path

from config import BRAIN_GENERATION, BRAIN_MODEL, BRAIN_RUNTIME, FAMILIES, HARDWARE_PROFILE, LIVE_SETTINGS, REFERENCE_MIN_SECONDS, SHARED_MODELS, TTS_RATE, Paths, load_live_settings, resolve_voice
from installer import install, models_for, require_model, runtime_server, runtime_tts_server, validate_wav, write_text_atomic
from local_api import parakeet_transcribe
from log import clear_run_log, note, run_log, set_run_log
from media import chatterbox_wav
from resident import mark_booted, require_alive, start_gemma, start_parakeet, status as resident_status, stop_all as resident_stop_all, use_chatterbox


def render_system_prompt(template: str | None = None) -> str:
    text = LIVE_SETTINGS["system_prompt"] if template is None else template
    return text.strip()


def spoken_reply(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\nAssistant:\n" in text: text = text.rsplit("\nAssistant:\n", 1)[1].strip()
    elif text.startswith("Assistant:\n"): text = text[11:].strip()
    return text


def prepared_reference(reference: Path, data_dir: Path) -> Path:
    wav = chatterbox_wav(reference, data_dir / "prepared")
    validate_wav(wav, TTS_RATE, minimum_seconds=REFERENCE_MIN_SECONDS, channels=1)
    with wave.open(str(wav), "rb") as audio: seconds = audio.getnframes() / audio.getframerate()
    note(f"component=tts event=reference_ready duration_s={seconds:.3f}")
    return wav


def gemma_kwargs(messages: list, stream: bool) -> dict:
    g = BRAIN_GENERATION
    return {
        "model": "gemma", "messages": messages, "stream": stream, "cache_prompt": True,
        "temperature": g["temperature"], "top_p": g["top_p"], "top_k": g["top_k"], "min_p": g["min_p"],
        "repeat_penalty": g["repeat_penalty"], "seed": g["seed"], "max_tokens": g["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": False},
    }


def resolved_tts(family: dict) -> str:
    return json.dumps({k: family[k] for k in ("name", "TTS_RUNTIME", "TTS_SAMPLE", "TTS_VOICE", "TTS_CHUNK")}, sort_keys=True, separators=(",", ":"))


def start_run(command: str, models_dir=None, data_dir=None) -> Paths:
    paths = Paths(models_dir, data_dir, command)
    set_run_log(paths.log)
    note(f"component=pipeline event=start command={command} hardware={HARDWARE_PROFILE}")
    return paths


def finish(paths: Paths, outcome: str = "ok") -> int:
    note(f"component=pipeline event=finish outcome={outcome}")
    clear_run_log(paths.log)
    return 0


def write_meta(paths: Paths, **rows) -> None:
    write_text_atomic(paths.meta, "".join(f"{k}={v}\n" for k, v in rows.items()))


def transcribe_wav(wav: Path, base: str) -> str:
    with wave.open(str(wav), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    started = time.perf_counter()
    text = str(parakeet_transcribe(base, wav).get("text") or "").strip()
    elapsed = time.perf_counter() - started
    rtf = elapsed / duration if duration > 0 else 0.0
    speed = 1.0 / rtf if rtf > 0 else 0.0
    note(f"component=asr event=done duration_s={duration:.3f} request_ms={elapsed * 1000:.3f} rtf={rtf:.4f} x_realtime={speed:.2f}")
    return text


def tts_endpoint(reference: Path, language: str, family: dict, paths: Paths) -> str:
    models = models_for(family["name"])
    return use_chatterbox(
        runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir), reference, family, language,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python main.py", description="Baremetal nano conversation")
    p.add_argument("--models-dir", type=Path); p.add_argument("--data-dir", type=Path)
    sub = p.add_subparsers(dest="command")
    sub.add_parser("install")
    sub.add_parser("ui")
    c = sub.add_parser("resident")
    c.add_argument("action", choices=("status", "boot", "stop"))
    c.add_argument("-r", "--reference")
    return p


def run_install(models_dir=None, data_dir=None) -> Path:
    paths = start_run("install", models_dir, data_dir)
    try:
        python = install(paths.models_dir, paths.data_dir)
        write_meta(paths, command="install", family="nano", hardware=HARDWARE_PROFILE)
    except Exception:
        finish(paths, "error")
        raise
    finish(paths)
    return python


def boot_residents(models_dir=None, data_dir=None, voice: str | None = None) -> Path:
    paths = Paths(models_dir, data_dir)
    settings = load_live_settings(paths.data_dir)
    voice_value = voice or settings["tts_voice"]
    spec = FAMILIES["nano"]
    errors: list[BaseException] = []
    reference: list[Path] = []
    log_path = run_log()

    def run(name: str, fn) -> None:
        set_run_log(log_path)
        try:
            fn()
        except BaseException as exc:
            errors.append(exc)
            note(f"component=resident event=boot_failed name={name} type={type(exc).__name__} message={exc}")

    def prep() -> None:
        reference.append(prepared_reference(resolve_voice(paths.data_dir, voice_value), paths.data_dir))

    def parakeet() -> None:
        start_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir))

    def gemma() -> None:
        start_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir), BRAIN_RUNTIME)

    workers = [
        threading.Thread(target=run, args=("reference", prep), name="boot-reference"),
        threading.Thread(target=run, args=("parakeet", parakeet), name="boot-parakeet"),
        threading.Thread(target=run, args=("gemma", gemma), name="boot-gemma"),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    if errors:
        raise errors[0]
    models = models_for(spec["name"])
    use_chatterbox(
        runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir), reference[0], spec, "en",
    )
    require_alive("parakeet")
    require_alive("gemma")
    require_alive("chatterbox")
    mark_booted()
    note("component=resident event=residents_ready family=nano language=en")
    return reference[0]


def resident_report() -> str:
    lines = []
    for row in resident_status():
        lines.append(
            f"{row['name']}: {'ready' if row['ready'] else 'stopped'} pid={row['pid'] or '-'} "
            f"url={row['url']} family={row.get('family') or '-'} language={row.get('language') or '-'}"
        )
    return "\n".join(lines)


def launch_ui(args) -> int:
    paths = start_run("ui", args.models_dir, args.data_dir)
    try:
        boot_residents(paths.models_dir, paths.data_dir)
        print(resident_report())
        from ui import launch
        launch(args.models_dir, args.data_dir)
    except Exception:
        finish(paths, "error")
        raise
    finish(paths)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if not args.command:
        python = run_install(args.models_dir, args.data_dir)
        os.execv(str(python), [str(python), "-X", "utf8", str(Path(__file__).resolve()), *sys.argv[1:], "ui"])
    if args.command == "install": run_install(args.models_dir, args.data_dir)
    elif args.command == "ui": return launch_ui(args)
    elif args.command == "resident":
        if args.action == "stop": resident_stop_all()
        elif args.action == "boot":
            paths = start_run("resident", args.models_dir, args.data_dir)
            try:
                boot_residents(paths.models_dir, paths.data_dir, args.reference)
            except Exception:
                finish(paths, "error")
                raise
            finish(paths)
        print(resident_report())
    return 0


if __name__ == "__main__": raise SystemExit(main())
