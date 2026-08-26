from __future__ import annotations

import argparse
import copy
import json
import shutil
import time
import wave
from pathlib import Path

from config import ASR_CHUNK_OVERLAP_SECONDS, ASR_CHUNK_SECONDS, BRAIN_GENERATION, BRAIN_MODEL, BRAIN_RUNTIME, BRAIN_THINKING, FAMILIES, HARDWARE_PROFILE, LANGUAGES, LIVE_SETTINGS, REFERENCE_MIN_SECONDS, SHARED_MODELS, TTS_FIELDS, TTS_RATE, Paths, default_family, resolve_voice
from installer import ensure_ui, install, models_for, require_model, runtime_server, runtime_tts_server, validate_wav, write_text_atomic
from local_api import chatterbox_stream as _chatterbox_stream, gemma_chat, parakeet_transcribe
from log import clear_run_log, note, set_run_log
from media import chatterbox_wav, parakeet_chunks, parakeet_wav
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
    note(f"component=tts event=reference_ready duration_s={seconds:.3f}")
    return wav


def effective_family(name: str, overrides: dict | None = None) -> dict:
    family = copy.deepcopy(FAMILIES[name])
    src = overrides or {}
    for key, section, dest, typ, *_ in TTS_FIELDS:
        value = src.get(key)
        if value is not None:
            family[section][dest] = typ(value)
    if src.get("streaming") is not None:
        family["TTS_STREAM"]["enabled"] = bool(src["streaming"])
    if src.get("stream_join") is not None:
        family["TTS_STREAM"]["join"] = src["stream_join"]
    if name in {"turbo", "nano"} and (
        family["TTS_SAMPLE"]["min_p"] != 0.0
        or family["TTS_VOICE"]["cfg_weight"] != 0.0
        or family["TTS_VOICE"]["exaggeration"] != 0.0
    ):
        raise RuntimeError(f"{name} does not support min-p, CFG weight, or exaggeration")
    return family


def gemma_kwargs(messages: list, stream: bool) -> dict:
    g = BRAIN_GENERATION
    return {
        "model": "gemma", "messages": messages, "stream": stream, "cache_prompt": True,
        "temperature": g["temperature"], "top_p": g["top_p"], "top_k": g["top_k"], "min_p": g["min_p"],
        "repeat_penalty": g["repeat_penalty"], "seed": g["seed"], "max_tokens": g["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": bool(BRAIN_THINKING)},
    }


def resolved_tts(family: dict) -> str:
    return json.dumps({k: family[k] for k in ("name", "TTS_RUNTIME", "TTS_SAMPLE", "TTS_VOICE", "TTS_CHUNK", "TTS_STREAM")}, sort_keys=True, separators=(",", ":"))


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


def transcribe_wav(wav: Path, base: str, chunk_dir: Path) -> str:
    with wave.open(str(wav), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    started = time.perf_counter(); words = []; chunks = 0
    for chunk, offset, chunk_seconds, final in parakeet_chunks(
        wav, chunk_dir, ASR_CHUNK_SECONDS, ASR_CHUNK_OVERLAP_SECONDS
    ):
        payload = parakeet_transcribe(base, chunk); chunks += 1
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
            if left <= midpoint < right or (final and midpoint == right):
                word = str(row.get("word", row.get("w", ""))).strip()
                if word:
                    words.append(word)
    text = " ".join(words).strip()
    elapsed = time.perf_counter() - started
    rtf = elapsed / duration if duration > 0 else 0.0
    speed = 1.0 / rtf if rtf > 0 else 0.0
    note(f"component=asr event=done duration_s={duration:.3f} chunks={chunks} request_ms={elapsed * 1000:.3f} rtf={rtf:.4f} x_realtime={speed:.2f}")
    return text


def transcribe(source: Path, paths: Paths) -> str:
    wav = parakeet_wav(source, paths.input)
    base = ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir))
    text = transcribe_wav(wav, base, paths.run_dir / ".asr-chunks")
    if not text:
        raise RuntimeError("Parakeet returned an empty transcript")
    write_text_atomic(paths.transcript, text + "\n")
    return text


def brain(prompt: Path, paths: Paths, language: str, language_name: str, template: str | None = None) -> str:
    system, prompt_text = render_system_prompt(template, language, language_name), prompt.read_text(encoding="utf-8").strip()
    write_text_atomic(paths.system, system + "\n")
    base = ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir), BRAIN_RUNTIME)
    payload = gemma_chat(base, gemma_kwargs([{"role": "system", "content": system}, {"role": "user", "content": prompt_text}], stream=False))
    text = spoken_reply(str((payload.get("choices") or [{}])[0].get("message", {}).get("content", "")))
    if not text: raise RuntimeError("Gemma returned an empty answer")
    write_text_atomic(paths.answer, text + "\n")
    return text


def tts_endpoint(reference: Path, language: str, family: dict, paths: Paths) -> str:
    models = models_for(family["name"])
    return ensure_chatterbox(
        runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir), reference, family, language,
    )


def _result_fields(result: str) -> dict[str, str]:
    return {key: value for item in result.split() if "=" in item for key, value in [item.split("=", 1)]}


def tts_metrics(result: str) -> dict[str, float | str]:
    fields = _result_fields(result)
    samples = int(fields["samples"])
    rtf = float(fields["wall_rtf"])
    return {
        **fields,
        "audio_s": samples / TTS_RATE,
        "rtf": rtf,
        "x_realtime": 1.0 / rtf if rtf > 0 else 0.0,
    }


def _tts_complete(result: str, unit: int | None = None) -> None:
    fields = tts_metrics(result)
    prefix = f" unit={unit}" if unit is not None else ""
    note(
        "component=tts event=complete outcome=ok" + prefix +
        f" audio_s={fields['audio_s']:.3f} chunks={fields['chunks']}" +
        f" total_ms={fields['total_ms']} t3_ms={fields['t3_ms']}" +
        f" s3gen_ms={fields['s3gen_ms']} ttfa_ms={fields['ttfa_ms']}" +
        f" rtf={fields['rtf']:.4f} x_realtime={fields['x_realtime']:.2f}"
    )


def _tts_failure(exc: Exception, unit: int | None = None, max_tokens: int | None = None) -> None:
    message = " ".join(str(exc).split())
    reason = "missing_eos" if "without EOS" in message else "request_error"
    prefix = f" unit={unit}" if unit is not None else ""
    ceiling = f" configured_max_tokens={max_tokens}" if reason == "missing_eos" and max_tokens is not None else ""
    note(f"component=tts event=failed outcome=error reason={reason}{prefix}{ceiling} message={message}")


def stream_synthesize(text: str, reference: Path, output: Path, language: str, family: dict, paths: Paths, *, base: str | None = None, unit: int | None = None, streaming: bool | None = None):
    endpoint = base or tts_endpoint(reference, language, family, paths)
    stream = family["TTS_STREAM"]
    enabled = stream["enabled"] if streaming is None else streaming
    try:
        generator = _chatterbox_stream(endpoint, text.strip(), output, enabled, stream["join"])
        while True:
            try:
                chunk = next(generator)
            except StopIteration as done:
                result = str(done.value or "")
                if result:
                    _tts_complete(result, unit)
                return result
            yield chunk
    except Exception as exc:
        _tts_failure(exc, unit, int(family["TTS_SAMPLE"]["max_tokens"]))
        raise


def synthesize_text(text: str, reference: Path, output: Path, language: str, family: dict, paths: Paths, *, base: str | None = None, streaming: bool | None = None, unit: int | None = None) -> str:
    generator = stream_synthesize(text, reference, output, language, family, paths, base=base, unit=unit, streaming=streaming)
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return str(done.value or "")


def synthesize(text_file: Path, reference: Path, output: Path, language: str, family: dict, paths: Paths) -> None:
    text = text_file.read_text(encoding="utf-8").strip()
    synthesize_text(text, reference, output, language, family, paths)
    validate_wav(output, TTS_RATE, channels=1)


def _output(value: str | None, wav=False) -> Path | None:
    path = Path(value).expanduser().resolve() if value else None
    if path and wav and path.suffix.lower() not in {"", ".wav"}: raise RuntimeError("TTS output is WAV only")
    return path


def run_asr(args):
    paths = start_run("asr", args.models_dir, args.data_dir); text = transcribe(Path(args.input).expanduser().resolve(), paths)
    _copy(paths.transcript, _output(args.output)); write_meta(paths, command="asr", transcript=paths.transcript, language_mode="auto", hardware=HARDWARE_PROFILE)
    print(text); print(f"Run: {paths.run_dir}"); finish(paths); return text


def run_brain(args):
    paths = start_run("brain", args.models_dir, args.data_dir); source = _text_source(args, paths)
    text = brain(source, paths, args.language, LANGUAGES[args.language], _read_system_prompt(args)); _copy(paths.answer, _output(args.output))
    write_meta(paths, command="brain", language=args.language, answer=paths.answer, hardware=HARDWARE_PROFILE); print(text); finish(paths); return text


def _tts_context(args, command: str, language_attr: str):
    family = effective_family(args.family, vars(args))
    language = resolve_language(family, getattr(args, language_attr))
    paths = start_run(command, args.models_dir, args.data_dir)
    reference = prepared_reference(resolve_voice(paths.data_dir, args.reference), paths.data_dir)
    return paths, family, language, reference


def run_tts(args):
    paths, family, language, reference = _tts_context(args, "tts", "language"); source = _text_source(args, paths)
    synthesize(source, reference, paths.output, language, family, paths)
    dest = _output(args.output, True); _copy(paths.output, dest)
    write_meta(paths, command="tts", family=family["name"], language=language, output=paths.output, resolved_tts=resolved_tts(family)); print(f"Output: {paths.output}"); finish(paths)
    return str(dest) if dest else str(paths.output)


def run_pipeline(args):
    paths, family, language, reference = _tts_context(args, "run", "tts_language"); started = time.perf_counter()
    transcript = transcribe(Path(args.input).expanduser().resolve(), paths); answer = brain(paths.transcript, paths, language, family["TTS_LANGUAGES"][language], _read_system_prompt(args))
    synthesize(paths.answer, reference, paths.output, language, family, paths)
    dest = _output(args.output, True); _copy(paths.output, dest)
    write_meta(paths, command="run", family=family["name"], tts_language=language, transcript=paths.transcript, answer=paths.answer, output=paths.output, pipeline_ms=f"{(time.perf_counter()-started)*1000:.3f}", resolved_tts=resolved_tts(family)); print(f"Transcript: {transcript}\nAnswer: {answer}\nOutput: {paths.output}"); finish(paths)
    return transcript, answer, str(dest) if dest else str(paths.output)


def add_tts_options(cmd, language_flag="--language") -> None:
    cmd.add_argument("-r", "--reference"); cmd.add_argument(language_flag, dest=language_flag[2:].replace("-", "_"))
    for _, _, _, typ, flag, *_ in TTS_FIELDS:
        cmd.add_argument(flag, type=typ)
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


def run_install(args) -> str:
    paths = start_run("install", args.models_dir, args.data_dir)
    try:
        install(args.family, paths.models_dir, paths.data_dir)
        write_meta(paths, command="install", family=args.family, hardware=HARDWARE_PROFILE)
    except Exception:
        finish(paths, "error")
        raise
    finish(paths)
    return "Install complete"

def warm_resident(args) -> None:
    paths = Paths(args.models_dir, args.data_dir); family = effective_family(args.family, vars(args)); language = resolve_language(family, args.tts_language); reference = prepared_reference(resolve_voice(paths.data_dir, args.reference), paths.data_dir)
    ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir)); ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir), BRAIN_RUNTIME)
    models = models_for(family["name"]); ensure_chatterbox(runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir), require_model(models["chatterbox-codec"], paths.models_dir), reference, family, language)


def resident_report() -> str:
    lines = []
    for row in resident_status():
        role = " runtime=Parakeet.cpp model=Parakeet-TDT-0.6B-v3" if row["name"] == "parakeet" else ""
        lines.append(f"{row['name']}: {'ready' if row['ready'] else 'stopped'} pid={row['pid'] or '-'} url={row['url']} family={row.get('family') or '-'}{role}")
    return "\n".join(lines)


def launch_ui(args) -> int:
    ensure_ui(); from ui import launch; launch(args.models_dir, args.data_dir); return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "install": run_install(args)
    elif args.command == "resident":
        if args.action == "stop": resident_stop_all()
        elif args.action == "warm": warm_resident(args)
        print(resident_report())
    elif args.command: {"asr": run_asr, "brain": run_brain, "tts": run_tts, "run": run_pipeline}[args.command](args)
    if args.ui: return launch_ui(args)
    if not args.command: raise RuntimeError("choose a command or --ui")
    return 0


if __name__ == "__main__": raise SystemExit(main())
