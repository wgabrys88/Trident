from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
import time
import wave
from pathlib import Path

from config import (
    ASR_LANGUAGES, ASR_RUNTIME, BRAIN_GENERATION, BRAIN_MODEL, BRAIN_RUNTIME,
    BRAIN_SYSTEM, BRAIN_THINKING, FAMILIES, HARDWARE_PROFILE, LANGUAGES,
    REFERENCE_MIN_SECONDS, SHARED_MODELS, TTS_RATE, Paths, default_family,
    resolve_voice,
)
from installer import (
    install, models_for, require_model, runtime_server, runtime_tts_server,
    validate_wav, write_text_atomic,
)
from local_api import chatterbox_synthesize, gemma_chat, parakeet_transcribe
from log import end_run, fail, note, set_run_log
from media import chatterbox_wav, parakeet_wav
from resident import (
    ensure_chatterbox, ensure_gemma, ensure_parakeet, status as resident_status,
    stop_all as resident_stop_all,
)


def validate_asr_language(language: str) -> str:
    code = language.lower()
    if code != "auto" and code not in ASR_LANGUAGES:
        raise RuntimeError(f"unsupported ASR language {code!r}; choose auto or {', '.join(ASR_LANGUAGES)}")
    return code


def resolve_language(family: dict, language: str | None) -> str:
    code = language or family["DEFAULT_REPLY_LANGUAGE"]
    if code not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {code!r} is not wired in {family['name']}; choose {', '.join(family['TTS_LANGUAGES'])}")
    return code


def _language_name(code: str, mapping: dict[str, str]) -> str:
    return "Auto-detected input language" if code == "auto" else mapping.get(code, code)


def render_system_prompt(template: str | None, asr_language: str, tts_language: str, tts_language_name: str) -> str:
    text = template if template is not None else BRAIN_SYSTEM
    for key, value in {
        "{asr_language}": asr_language,
        "{asr_language_name}": _language_name(asr_language, ASR_LANGUAGES),
        "{tts_language}": tts_language,
        "{tts_language_name}": tts_language_name,
        "{language}": tts_language,
        "{language_name}": tts_language_name,
    }.items():
        text = text.replace(key, value)
    return text.strip()


def spoken_reply(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\nAssistant:\n" in text:
        text = text.rsplit("\nAssistant:\n", 1)[1].strip()
    elif text.startswith("Assistant:\n"):
        text = text[len("Assistant:\n"):].strip()
    if "[Start thinking]" in text and "[End thinking]" in text:
        text = text.split("[End thinking]", 1)[1].strip()
    return text


def _read_system_prompt(args) -> str | None:
    inline, filename = getattr(args, "system_prompt", None), getattr(args, "system_prompt_file", None)
    if inline is not None and filename is not None:
        raise RuntimeError("choose only one of --system-prompt or --system-prompt-file")
    if filename is not None:
        path = Path(filename).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"missing system prompt file: {path}")
        inline = path.read_text(encoding="utf-8")
    if inline is None:
        return None
    text = str(inline).strip()
    if not text:
        raise RuntimeError("system prompt is empty")
    return text


def _copy(src: Path, dest: Path | None) -> None:
    if dest is None or src.resolve() == dest.resolve():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _text_source(args, paths: Paths) -> Path:
    filename, literal = getattr(args, "input", None), getattr(args, "text", None)
    if bool(filename) == (literal is not None):
        raise RuntimeError("provide exactly one input text file or --text")
    if filename:
        path = Path(filename).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"missing file: {path}")
        return path
    text = literal.strip()
    if not text:
        raise RuntimeError("--text is empty")
    path = paths.run_dir / "literal.txt"
    write_text_atomic(path, text + "\n")
    return path


def prepared_reference(reference: Path, data_dir: Path) -> Path:
    if not reference.is_file():
        raise RuntimeError(f"missing {reference.name}; python main.py install")
    wav = chatterbox_wav(reference, data_dir / "prepared")
    validate_wav(wav, TTS_RATE, minimum_seconds=REFERENCE_MIN_SECONDS, channels=1)
    with wave.open(str(wav), "rb") as handle:
        seconds = handle.getnframes() / float(handle.getframerate() or TTS_RATE)
    digest = hashlib.sha256(wav.read_bytes()).hexdigest()
    note(f"component=tts event=reference_prepared wav={wav} sha256={digest} duration_s={seconds:.3f}")
    return wav


def _apply(args, target: dict, mapping: dict[str, str]) -> None:
    for arg_name, key in mapping.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            target[key] = value


def effective_family(name: str, args) -> dict:
    family = copy.deepcopy(FAMILIES[name])
    _apply(args, family["TTS_RUNTIME"], {"n_gpu_layers": "gpu_layers", "context": "context", "threads": "threads"})
    _apply(args, family["TTS_SAMPLE"], {
        "seed": "seed", "max_tokens": "max_tokens", "top_k": "top_k", "top_p": "top_p",
        "min_p": "min_p", "temperature": "temperature", "repeat_penalty": "repeat_penalty",
        "cfm_steps": "cfm_steps",
    })
    _apply(args, family["TTS_VOICE"], {"cfg_weight": "cfg_weight", "exaggeration": "exaggeration"})
    _apply(args, family["TTS_CHUNK"], {"first_chunk_chars": "first_chars", "chunk_chars": "chars"})
    return family


def effective_asr_runtime(args) -> dict:
    runtime = copy.deepcopy(ASR_RUNTIME)
    if value := getattr(args, "asr_device", None):
        runtime["device"] = value
    return runtime


def effective_brain_runtime(args) -> dict:
    runtime = copy.deepcopy(BRAIN_RUNTIME)
    if value := getattr(args, "brain_device", None):
        runtime["device"] = "none" if value.lower() in {"cpu", "none"} else value
        runtime["gpu_layers"] = 0 if runtime["device"] == "none" else "all"
    if value := getattr(args, "flash_attn", None):
        runtime["flash_attn"] = value
    return runtime


def _resolved_tts(family: dict) -> str:
    return json.dumps({
        "family": family["name"], "runtime": family["TTS_RUNTIME"], "sample": family["TTS_SAMPLE"],
        "voice": family["TTS_VOICE"], "chunk": family["TTS_CHUNK"],
    }, sort_keys=True, separators=(",", ":"))


def start_run(command: str, args) -> Paths:
    paths = Paths(args.models_dir, args.data_dir, command)
    mark = set_run_log(paths.run_dir / "trident.log")
    note(f"component=pipeline event=start run_dir={paths.run_dir} log_chunk={mark[0]} log_offset={mark[1]}")
    return paths


def finish(paths: Paths) -> int:
    end_run(paths.run_dir / "server.log")
    return 0


def write_meta(paths: Paths, **rows) -> None:
    write_text_atomic(paths.run_dir / "meta.txt", "".join(f"{key}={value}\n" for key, value in rows.items()))


def transcribe(input_wav: Path, paths: Paths, expected_language: str, runtime: dict) -> str:
    expected_language = validate_asr_language(expected_language)
    wav = parakeet_wav(input_wav, paths.run_dir / "input.wav")
    base = ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir), runtime)
    note(f"component=asr event=request endpoint={base}/v1/audio/transcriptions input={wav} language_mode=auto expected={expected_language}")
    started = time.perf_counter()
    payload = parakeet_transcribe(base, wav)
    note(f"component=asr event=done request_ms={(time.perf_counter() - started) * 1000:.3f}")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise RuntimeError("Parakeet returned an empty transcript")
    write_text_atomic(paths.transcript, text + "\n")
    return text


def brain(prompt: Path, paths: Paths, asr_language: str, tts_language: str, tts_language_name: str,
          system_prompt: str | None, runtime: dict) -> str:
    system = render_system_prompt(system_prompt, validate_asr_language(asr_language), tts_language, tts_language_name)
    prompt_text = prompt.read_text(encoding="utf-8").strip()
    if not system or not prompt_text:
        raise RuntimeError("Gemma system prompt and input must be non-empty")
    write_text_atomic(paths.system, system + "\n")
    g = BRAIN_GENERATION
    base = ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir), runtime)
    request = {
        "model": "gemma", "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt_text}],
        "stream": False, "cache_prompt": True, "temperature": g["temperature"], "top_p": g["top_p"],
        "top_k": g["top_k"], "min_p": g["min_p"], "repeat_penalty": g["repeat_penalty"],
        "seed": g["seed"], "max_tokens": g["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": bool(BRAIN_THINKING)},
    }
    note(f"component=gemma event=request endpoint={base}/v1/chat/completions cache_prompt=1 asr_language={asr_language} output_language={tts_language}")
    started = time.perf_counter()
    payload = gemma_chat(base, request)
    note(f"component=gemma event=done request_ms={(time.perf_counter() - started) * 1000:.3f} response_id={payload.get('id', '-')}")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("llama-server returned no choices")
    message = choices[0].get("message")
    text = spoken_reply(str(message.get("content") if isinstance(message, dict) else ""))
    if not text:
        raise RuntimeError("llama-server returned an empty answer")
    write_text_atomic(paths.answer, text + "\n")
    if isinstance(payload.get("timings"), dict):
        note("component=gemma event=timings payload=" + json.dumps(payload["timings"], separators=(",", ":"), sort_keys=True))
    return text


def synthesize(text_file: Path, reference: Path, output: Path, language: str, family: dict, paths: Paths) -> None:
    text = text_file.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("TTS text is empty")
    models = models_for(family["name"])
    runtime, sample, voice, chunk = family["TTS_RUNTIME"], family["TTS_SAMPLE"], family["TTS_VOICE"], family["TTS_CHUNK"]
    note("component=config event=tts_resolved payload=" + _resolved_tts(family))
    base = ensure_chatterbox(
        runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir), reference, family["name"], language,
        runtime, sample, voice, chunk,
    )
    note(f"component=tts event=request endpoint={base} family={family['name']} language={language} reference={reference} model_resident=1 voice_resident=1")
    result = chatterbox_synthesize(base, text, output)
    note("component=tts event=done " + result)
    validate_wav(output, TTS_RATE, channels=1)


def _output_path(value: str | None, *, wav: bool = False) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser().resolve()
    if wav and path.suffix.lower() not in {"", ".wav"}:
        raise RuntimeError("baremetal TTS output is WAV only; use -o FILE.wav")
    return path


def run_asr(args) -> int:
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"missing file: {source}")
    paths, runtime = start_run("asr", args), effective_asr_runtime(args)
    text = transcribe(source, paths, args.language, runtime)
    _copy(paths.transcript, _output_path(args.output))
    write_meta(paths, command="asr", input=source, transcript=paths.transcript, asr_language=args.language, device=runtime["device"])
    print(text); print(f"Run: {paths.run_dir}")
    return finish(paths)


def run_brain(args) -> int:
    paths = start_run("brain", args)
    source = _text_source(args, paths)
    runtime = effective_brain_runtime(args)
    text = brain(source, paths, args.asr_language, args.language, LANGUAGES[args.language], _read_system_prompt(args), runtime)
    _copy(paths.answer, _output_path(args.output))
    write_meta(paths, command="brain", asr_language=args.asr_language, tts_language=args.language,
               device=runtime["device"], prompt=source, answer=paths.answer, system=paths.system, cache_prompt=1)
    print(text); print(f"Run: {paths.run_dir}")
    return finish(paths)


def run_tts(args) -> int:
    paths = start_run("tts", args)
    source = _text_source(args, paths)
    family = effective_family(args.family, args)
    language = resolve_language(family, args.language)
    reference = prepared_reference(resolve_voice(paths.data_dir, args.reference), paths.data_dir)
    synthesize(source, reference, paths.output, language, family, paths)
    _copy(paths.output, _output_path(args.output, wav=True))
    write_meta(paths, command="tts", family=family["name"], language=language, text=source, reference=reference,
               output=paths.output, resolved_tts=_resolved_tts(family), resident=1)
    print(f"Output: {paths.output}"); print(f"Run: {paths.run_dir}")
    return finish(paths)


def run_pipeline(args) -> int:
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        raise RuntimeError(f"missing file: {source}")
    paths = start_run("run", args)
    family = effective_family(args.family, args)
    asr_language = validate_asr_language(args.asr_language)
    tts_language = resolve_language(family, args.tts_language)
    reference = prepared_reference(resolve_voice(paths.data_dir, args.reference), paths.data_dir)
    asr_runtime, brain_runtime = effective_asr_runtime(args), effective_brain_runtime(args)
    started = time.perf_counter()
    transcript = transcribe(source, paths, asr_language, asr_runtime)
    answer = brain(paths.transcript, paths, asr_language, tts_language, family["TTS_LANGUAGES"][tts_language],
                   _read_system_prompt(args), brain_runtime)
    synthesize(paths.answer, reference, paths.output, tts_language, family, paths)
    _copy(paths.output, _output_path(args.output, wav=True))
    total_ms = (time.perf_counter() - started) * 1000
    write_meta(paths, command="run", family=family["name"], asr_language=asr_language, tts_language=tts_language,
               input=source, asr_device=asr_runtime["device"], brain_device=brain_runtime["device"],
               reference=reference, output=paths.output, system=paths.system, resolved_tts=_resolved_tts(family),
               resident_chain="parakeet->gemma->chatterbox", pipeline_ms=f"{total_ms:.3f}")
    note(f"component=pipeline event=done family={family['name']} total_ms={total_ms:.3f}")
    print(f"Transcript: {transcript}"); print(f"Answer: {answer}"); print(f"Output: {paths.output}"); print(f"Run: {paths.run_dir}")
    return finish(paths)


def add_tts_tuning(cmd: argparse.ArgumentParser) -> None:
    for flag in ("n-gpu-layers", "context", "threads", "seed", "max-tokens", "top-k", "cfm-steps", "first-chunk-chars", "chunk-chars"):
        cmd.add_argument("--" + flag, type=int)
    for flag in ("top-p", "min-p", "temperature", "repeat-penalty", "cfg-weight", "exaggeration"):
        cmd.add_argument("--" + flag, type=float)


def add_prompt(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--system-prompt")
    cmd.add_argument("--system-prompt-file")


def add_tts_options(cmd: argparse.ArgumentParser, *, language_flag: str = "--language") -> None:
    cmd.add_argument("-r", "--reference")
    cmd.add_argument(language_flag, dest=language_flag[2:].replace("-", "_"))
    add_tts_tuning(cmd)


def build_parser() -> argparse.ArgumentParser:
    families = tuple(FAMILIES)
    p = argparse.ArgumentParser(prog="python main.py", description="Baremetal local ASR -> Gemma -> Chatterbox runtime")
    p.add_argument("--models-dir", type=Path); p.add_argument("--data-dir", type=Path)
    sub = p.add_subparsers(dest="command", required=True)

    cmd = sub.add_parser("install"); cmd.add_argument("--family", choices=("all", *families), default="all")

    cmd = sub.add_parser("asr"); cmd.add_argument("input"); cmd.add_argument("-o", "--output")
    cmd.add_argument("--language", choices=("auto", *ASR_LANGUAGES), default="auto")
    cmd.add_argument("--asr-device", help="Parakeet primary device, e.g. Vulkan0 or cpu")

    cmd = sub.add_parser("brain"); cmd.add_argument("input", nargs="?"); cmd.add_argument("-t", "--text"); cmd.add_argument("-o", "--output")
    cmd.add_argument("--language", choices=tuple(LANGUAGES), default="en")
    cmd.add_argument("--asr-language", choices=("auto", *ASR_LANGUAGES), default="auto")
    cmd.add_argument("--brain-device", help="Gemma device: Vulkan0 or cpu/none")
    cmd.add_argument("--flash-attn", choices=("on", "off", "auto")); add_prompt(cmd)

    cmd = sub.add_parser("tts"); cmd.add_argument("input", nargs="?"); cmd.add_argument("-t", "--text"); cmd.add_argument("-o", "--output")
    cmd.add_argument("--family", choices=families, default=default_family()); add_tts_options(cmd)

    cmd = sub.add_parser("run"); cmd.add_argument("input"); cmd.add_argument("-o", "--output")
    cmd.add_argument("--family", choices=families, default=default_family())
    cmd.add_argument("--asr-language", choices=("auto", *ASR_LANGUAGES), default="auto")
    cmd.add_argument("--asr-device"); cmd.add_argument("--brain-device"); cmd.add_argument("--flash-attn", choices=("on", "off", "auto"))
    add_tts_options(cmd, language_flag="--tts-language"); add_prompt(cmd)

    cmd = sub.add_parser("resident"); cmd.add_argument("action", choices=("status", "warm", "stop"))
    cmd.add_argument("--family", choices=families, default=default_family())
    cmd.add_argument("--asr-language", choices=("auto", *ASR_LANGUAGES), default="auto")
    cmd.add_argument("--asr-device"); cmd.add_argument("--brain-device"); cmd.add_argument("--flash-attn", choices=("on", "off", "auto"))
    add_tts_options(cmd, language_flag="--tts-language")
    return p


def print_resident_status() -> None:
    for row in resident_status():
        state = "ready" if row["ready"] else "stopped"
        extra = f" device={row['device']}" if row.get("device") else ""
        if row.get("gpu_layers") is not None: extra += f" gpu_layers={row['gpu_layers']}"
        if row["name"] == "chatterbox" and row.get("family"):
            extra += f" family={row['family']} language={row.get('language') or '-'} reference={row.get('reference') or '-'}"
        print(f"{row['name']}: {state} pid={row['pid'] or '-'} url={row['url']} log={row['log']}{extra}")


def warm_resident(args) -> None:
    paths = Paths(args.models_dir, args.data_dir)
    family = effective_family(args.family, args)
    language = resolve_language(family, args.tts_language)
    reference = prepared_reference(resolve_voice(paths.data_dir, args.reference), paths.data_dir)
    asr_runtime, brain_runtime = effective_asr_runtime(args), effective_brain_runtime(args)
    ensure_parakeet(runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir), asr_runtime)
    ensure_gemma(runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir), brain_runtime)
    models = models_for(family["name"])
    ensure_chatterbox(runtime_tts_server(), require_model(models["chatterbox-t3"], paths.models_dir),
                      require_model(models["chatterbox-codec"], paths.models_dir), reference, family["name"], language,
                      family["TTS_RUNTIME"], family["TTS_SAMPLE"], family["TTS_VOICE"], family["TTS_CHUNK"])
    note("component=resident event=warm resolved_tts=" + _resolved_tts(family))


def main() -> int:
    args = build_parser().parse_args()
    note(f"component=runtime event=profile hardware={HARDWARE_PROFILE}")
    try:
        if args.command == "install":
            install(args.family, args.models_dir, args.data_dir); return 0
        if args.command == "resident":
            if args.action == "stop": resident_stop_all()
            elif args.action == "warm": warm_resident(args)
            print_resident_status(); return 0
        return {"asr": run_asr, "brain": run_brain, "tts": run_tts, "run": run_pipeline}[args.command](args)
    except Exception as exc:
        fail(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
