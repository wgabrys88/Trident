from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import time
import wave
from pathlib import Path

from config import BRAIN_GENERATION, BRAIN_MODEL, BRAIN_RUNTIME, BRAIN_THINKING, FAMILIES, HARDWARE_PROFILE, LANGUAGES, LIVE_SETTINGS, REFERENCE_MIN_SECONDS, SHARED_MODELS, TTS_RATE, Paths, default_family, resolve_voice
from installer import ensure_ui, install, models_for, require_model, runtime_server, runtime_tts_server, validate_wav, write_text_atomic
from local_api import chatterbox_stream, chatterbox_synthesize, gemma_chat, parakeet_transcribe
from log import end_run, note, set_run_log
from media import chatterbox_wav, parakeet_wav
from resident import ensure_chatterbox, ensure_gemma, ensure_parakeet, status as resident_status, stop_all as resident_stop_all


def resolve_language(family: dict, language: str | None) -> str:
    code = language or family["DEFAULT_REPLY_LANGUAGE"]
    if code not in family["TTS_LANGUAGES"]: raise RuntimeError(f"language {code!r} is not wired in {family['name']}")
    return code


def render_system_prompt(template: str | None, code: str, name: str) -> str:
    text = LIVE_SETTINGS["system_prompt"] if template is None else template
    for key, value in {"{tts_language}": code, "{tts_language_name}": name, "{language}": code, "{language_name}": name}.items(): text = text.replace(key, value)
    return text.strip()


def spoken_reply(raw: str, streaming: bool = False) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\nAssistant:\n" in text: text = text.rsplit("\nAssistant:\n", 1)[1].strip()
    elif text.startswith("Assistant:\n"): text = text[11:].strip()
    if "[Start thinking]" in text:
        if "[End thinking]" in text: text = text.split("[End thinking]", 1)[1].strip()
        elif streaming: return ""
    return text


def _read_system_prompt(args) -> str | None:
    text, filename = getattr(args, "system_prompt", None), getattr(args, "system_prompt_file", None)
    if text is not None and filename is not None: raise RuntimeError("choose one system prompt source")
    if filename: text = Path(filename).expanduser().resolve().read_text(encoding="utf-8")
    return text.strip() if text is not None else None


def _copy(src: Path, dest: Path | None) -> None:
    if dest and src.resolve() != dest.resolve(): dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dest)


def _text_source(args, paths: Paths) -> Path:
    filename, literal = getattr(args, "input", None), getattr(args, "text", None)
    if bool(filename) == (literal is not None): raise RuntimeError("provide exactly one input text file or --text")
    if filename: return Path(filename).expanduser().resolve()
    text = literal.strip()
    if not text: raise RuntimeError("--text is empty")
    write_text_atomic(paths.literal, text + "\n")
    return paths.literal


def prepared_reference(reference: Path, data_dir: Path) -> Path:
    wav = chatterbox_wav(reference, data_dir / "prepared")
    validate_wav(wav, TTS_RATE, minimum_seconds=REFERENCE_MIN_SECONDS, channels=1)
    with wave.open(str(wav), "rb") as audio: seconds = audio.getnframes() / audio.getframerate()
    note(f"component=tts event=reference_prepared wav={wav} sha256={hashlib.sha256(wav.read_bytes()).hexdigest()} duration_s={seconds:.3f}")
    return wav


def _apply(args, target: dict, mapping: dict[str, str]) -> None:
    for arg, key in mapping.items():
        value = getattr(args, arg, None)
        if value is not None: target[key] = value


def effective_family(name: str, args) -> dict:
    family = copy.deepcopy(FAMILIES[name])
    _apply(args, family["TTS_RUNTIME"], {"n_gpu_layers": "gpu_layers", "context": "context", "threads": "threads"})
    _apply(args, family["TTS_SAMPLE"], {"seed": "seed", "max_tokens": "max_tokens", "top_k": "top_k", "top_p": "top_p", "min_p": "min_p", "temperature": "temperature", "repeat_penalty": "repeat_penalty", "cfm_steps": "cfm_steps"})
    _apply(args, family["TTS_VOICE"], {"cfg_weight": "cfg_weight", "exaggeration": "exaggeration"})
    _apply(args, family["TTS_CHUNK"], {"first_chunk_chars": "first_chars", "chunk_chars": "chars"})
    _apply(args, family["TTS_STREAM"], {"streaming": "enabled", "stream_join": "join", "stream_first_chunk_tokens": "first_tokens", "stream_chunk_tokens": "tokens"})
    return family


def resolved_tts(family: dict) -> str:
    return json.dumps({k: family[k] for k in ("name", "TTS_RUNTIME", "TTS_SAMPLE", "TTS_VOICE", "TTS_CHUNK", "TTS_STREAM")}, sort_keys=True, separators=(",", ":"))


def start_run(command: str, models_dir=None, data_dir=None) -> Paths:
    paths = Paths(models_dir, data_dir, command)
    mark = set_run_log(paths.log)
    note(f"component=pipeline event=start run_dir={paths.run_dir} log_chunk={mark[0]} log_offset={mark[1]}")
    return paths


def finish(paths: Paths) -> int:
    end_run(paths.server_log)
    return 0


def write_meta(paths: Paths, **rows) -> None:
    write_text_atomic(paths.meta, "".join(f"{k}={v}\n" for k, v in rows.items()))


def transcribe(source: Path, paths: Paths) -> str:
    wav = parakeet_wav(source, paths.input)
    base = ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir))
    started = time.perf_counter(); payload = parakeet_transcribe(base, wav)
    note(f"component=asr event=done request_ms={(time.perf_counter() - started) * 1000:.3f}")
    text = str(payload.get("text") or "").strip()
    if not text: raise RuntimeError("Parakeet returned an empty transcript")
    write_text_atomic(paths.transcript, text + "\n")
    return text


def brain(prompt: Path, paths: Paths, language: str, language_name: str, template: str | None = None) -> str:
    system, prompt_text = render_system_prompt(template, language, language_name), prompt.read_text(encoding="utf-8").strip()
    write_text_atomic(paths.system, system + "\n")
    base = ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir), BRAIN_RUNTIME)
    g = BRAIN_GENERATION
    payload = gemma_chat(base, {"model": "gemma", "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt_text}], "stream": False, "cache_prompt": True, "temperature": g["temperature"], "top_p": g["top_p"], "top_k": g["top_k"], "min_p": g["min_p"], "repeat_penalty": g["repeat_penalty"], "seed": g["seed"], "max_tokens": g["max_tokens"], "chat_template_kwargs": {"enable_thinking": bool(BRAIN_THINKING)}})
    text = spoken_reply(str((payload.get("choices") or [{}])[0].get("message", {}).get("content", "")))
    if not text: raise RuntimeError("Gemma returned an empty answer")
    write_text_atomic(paths.answer, text + "\n")
    return text


def tts_endpoint(reference: Path, language: str, family: dict, paths: Paths) -> str:
    models = models_for(family["name"]); note("component=config event=tts_resolved payload=" + resolved_tts(family))
    return ensure_chatterbox(runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir), require_model(models["chatterbox-codec"], paths.models_dir), reference, family["name"], language, family["TTS_RUNTIME"], family["TTS_SAMPLE"], family["TTS_VOICE"], family["TTS_CHUNK"], family["TTS_STREAM"])


def stream_synthesize(text: str, reference: Path, output: Path, language: str, family: dict, paths: Paths):
    base = tts_endpoint(reference, language, family, paths); stream = family["TTS_STREAM"]
    yield from chatterbox_stream(base, text.strip(), output, stream["enabled"], stream["join"])


def synthesize(text_file: Path, reference: Path, output: Path, language: str, family: dict, paths: Paths) -> None:
    text = text_file.read_text(encoding="utf-8").strip(); base = tts_endpoint(reference, language, family, paths); stream = family["TTS_STREAM"]
    result = chatterbox_synthesize(base, text, output, stream["enabled"], stream["join"]); note("component=tts event=done " + result)
    validate_wav(output, TTS_RATE, channels=1)


def _output(value: str | None, wav=False) -> Path | None:
    path = Path(value).expanduser().resolve() if value else None
    if path and wav and path.suffix.lower() not in {"", ".wav"}: raise RuntimeError("TTS output is WAV only")
    return path


def run_asr(args) -> int:
    paths = start_run("asr", args.models_dir, args.data_dir); text = transcribe(Path(args.input).expanduser().resolve(), paths)
    _copy(paths.transcript, _output(args.output)); write_meta(paths, command="asr", transcript=paths.transcript, language_mode="auto", hardware=HARDWARE_PROFILE)
    print(text); print(f"Run: {paths.run_dir}"); return finish(paths)


def run_brain(args) -> int:
    paths = start_run("brain", args.models_dir, args.data_dir); source = _text_source(args, paths)
    text = brain(source, paths, args.language, LANGUAGES[args.language], _read_system_prompt(args)); _copy(paths.answer, _output(args.output))
    write_meta(paths, command="brain", language=args.language, answer=paths.answer, hardware=HARDWARE_PROFILE); print(text); return finish(paths)


def _tts_context(args, command: str, language_attr: str):
    paths = start_run(command, args.models_dir, args.data_dir); family = effective_family(args.family, args)
    language = resolve_language(family, getattr(args, language_attr)); reference = prepared_reference(resolve_voice(paths.data_dir, args.reference), paths.data_dir)
    return paths, family, language, reference


def run_tts(args) -> int:
    paths, family, language, reference = _tts_context(args, "tts", "language"); source = _text_source(args, paths)
    synthesize(source, reference, paths.output, language, family, paths); _copy(paths.output, _output(args.output, True))
    write_meta(paths, command="tts", family=family["name"], language=language, output=paths.output, resolved_tts=resolved_tts(family)); print(f"Output: {paths.output}"); return finish(paths)


def run_pipeline(args) -> int:
    paths, family, language, reference = _tts_context(args, "run", "tts_language"); started = time.perf_counter()
    transcript = transcribe(Path(args.input).expanduser().resolve(), paths); answer = brain(paths.transcript, paths, language, family["TTS_LANGUAGES"][language], _read_system_prompt(args))
    synthesize(paths.answer, reference, paths.output, language, family, paths); _copy(paths.output, _output(args.output, True))
    write_meta(paths, command="run", family=family["name"], tts_language=language, transcript=paths.transcript, answer=paths.answer, output=paths.output, pipeline_ms=f"{(time.perf_counter()-started)*1000:.3f}", resolved_tts=resolved_tts(family)); print(f"Transcript: {transcript}\nAnswer: {answer}\nOutput: {paths.output}"); return finish(paths)


def add_tts_options(cmd, language_flag="--language") -> None:
    cmd.add_argument("-r", "--reference"); cmd.add_argument(language_flag, dest=language_flag[2:].replace("-", "_"))
    ints = "n-gpu-layers context threads seed max-tokens top-k cfm-steps first-chunk-chars chunk-chars stream-first-chunk-tokens stream-chunk-tokens".split()
    floats = "top-p min-p temperature repeat-penalty cfg-weight exaggeration".split()
    for flag in ints: cmd.add_argument("--" + flag, type=int)
    for flag in floats: cmd.add_argument("--" + flag, type=float)
    cmd.add_argument("--streaming", action=argparse.BooleanOptionalAction, default=None)
    cmd.add_argument("--stream-join", choices=("chunks", "crossfade"))


def add_prompt(cmd) -> None:
    cmd.add_argument("--system-prompt"); cmd.add_argument("--system-prompt-file")


def build_parser() -> argparse.ArgumentParser:
    families = tuple(FAMILIES); p = argparse.ArgumentParser(prog="python main.py", description="Baremetal local ASR -> Gemma -> Chatterbox")
    p.add_argument("--models-dir", type=Path); p.add_argument("--data-dir", type=Path); p.add_argument("--ui", action="store_true")
    sub = p.add_subparsers(dest="command")
    c=sub.add_parser("install"); c.add_argument("--family", choices=("all", *families), default="all"); c.add_argument("--ui", action="store_true", default=argparse.SUPPRESS)
    c=sub.add_parser("asr"); c.add_argument("input"); c.add_argument("-o", "--output")
    c=sub.add_parser("brain"); c.add_argument("input", nargs="?"); c.add_argument("-t", "--text"); c.add_argument("-o", "--output"); c.add_argument("--language", choices=tuple(LANGUAGES), default="en"); add_prompt(c)
    c=sub.add_parser("tts"); c.add_argument("input", nargs="?"); c.add_argument("-t", "--text"); c.add_argument("-o", "--output"); c.add_argument("--family", choices=families, default=default_family()); add_tts_options(c)
    c=sub.add_parser("run"); c.add_argument("input"); c.add_argument("-o", "--output"); c.add_argument("--family", choices=families, default=default_family()); add_tts_options(c, "--tts-language"); add_prompt(c)
    c=sub.add_parser("resident"); c.add_argument("action", choices=("status", "warm", "stop")); c.add_argument("--family", choices=families, default=default_family()); add_tts_options(c, "--tts-language")
    return p


def warm_resident(args) -> None:
    paths = Paths(args.models_dir, args.data_dir); family = effective_family(args.family, args); language = resolve_language(family, args.tts_language); reference = prepared_reference(resolve_voice(paths.data_dir, args.reference), paths.data_dir)
    ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir)); ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir), BRAIN_RUNTIME)
    models = models_for(family["name"]); ensure_chatterbox(runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir), require_model(models["chatterbox-codec"], paths.models_dir), reference, family["name"], language, family["TTS_RUNTIME"], family["TTS_SAMPLE"], family["TTS_VOICE"], family["TTS_CHUNK"], family["TTS_STREAM"])


def print_resident_status() -> None:
    for row in resident_status(): print(f"{row['name']}: {'ready' if row['ready'] else 'stopped'} pid={row['pid'] or '-'} url={row['url']} family={row.get('family') or '-'}")


def launch_ui(args) -> int:
    ensure_ui(); from ui import launch; launch(args.models_dir, args.data_dir); return 0


def main() -> int:
    args = build_parser().parse_args(); note(f"component=runtime event=profile hardware={HARDWARE_PROFILE}")
    if args.command == "install": install(args.family, args.models_dir, args.data_dir)
    elif args.command == "resident":
        if args.action == "stop": resident_stop_all()
        elif args.action == "warm": warm_resident(args)
        print_resident_status()
    elif args.command: return {"asr": run_asr, "brain": run_brain, "tts": run_tts, "run": run_pipeline}[args.command](args)
    if args.ui: return launch_ui(args)
    if not args.command: raise RuntimeError("choose a command or --ui")
    return 0


if __name__ == "__main__": raise SystemExit(main())
