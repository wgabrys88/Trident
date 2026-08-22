from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import time
from pathlib import Path

from config import (
    ASR_RUNTIME, TTS_RATE, REFERENCE_MIN_SECONDS, BRAIN_MODEL, BRAIN_RUNTIME,
    BRAIN_GENERATION, BRAIN_THINKING, BRAIN_SYSTEM, FAMILIES, SHARED_MODELS, LANGUAGES,
    ASR_LANGUAGES, HARDWARE_PROFILE, Paths, default_family, resolve_voice,
)
from installer import (
    install, runtime_server, runtime_tts_server, models_for, require_model,
    validate_wav, write_text_atomic, note, set_log,
)
from local_api import chatterbox_synthesize, gemma_chat, parakeet_transcribe
from media import chatterbox_wav, compatible_mp4, parakeet_wav, publish_outputs
from resident import (
    ensure_chatterbox, ensure_gemma, ensure_parakeet,
    load_pipeline_profile, save_pipeline_profile,
    status as resident_status, stop_all as resident_stop_all,
)


PIPELINE_PROFILE_VERSION = 4


def validate_asr_language(language: str) -> str:
    code = language.lower()
    if code != "auto" and code not in ASR_LANGUAGES:
        raise RuntimeError(
            f"Parakeet v3 expected language {code!r} is unsupported; choose auto or "
            + ", ".join(ASR_LANGUAGES)
        )
    return code


def transcribe(server: Path, model: Path, input_wav: Path, paths: Paths, expected_language: str = "auto", runtime: dict | None = None) -> str:
    expected_language = validate_asr_language(expected_language)
    runtime = runtime or ASR_RUNTIME
    base_url = ensure_parakeet(server, model, runtime, note)
    # NVIDIA Parakeet TDT 0.6B v3 performs language identification internally.
    # The v0.5 parakeet-server exposes no language selector for this checkpoint.
    note(f"component=asr event=request endpoint={base_url}/v1/audio/transcriptions input={input_wav} language_mode=auto expected={expected_language}")
    started = time.perf_counter()
    payload = parakeet_transcribe(base_url, input_wav)
    note(f"component=asr event=done request_ms={(time.perf_counter() - started) * 1000.0:.3f}")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise RuntimeError("Parakeet returned an empty transcript")
    write_text_atomic(paths.transcript, text + "\n")
    return text


def spoken_reply(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\nAssistant:\n" in text:
        text = text.rsplit("\nAssistant:\n", 1)[1].strip()
    elif text.startswith("Assistant:\n"):
        text = text[len("Assistant:\n"):].strip()
    start, end = "[Start thinking]", "[End thinking]"
    if start in text and end in text:
        text = text.split(end, 1)[1].strip()
    return text


def _language_name(code: str, mapping: dict[str, str], auto_name: str = "Auto-detected input language") -> str:
    return auto_name if code == "auto" else mapping.get(code, code)


def render_system_prompt(template: str | None, asr_language: str, tts_language: str, tts_language_name: str) -> str:
    source = template if template is not None else BRAIN_SYSTEM
    values = {
        "{asr_language}": asr_language,
        "{asr_language_name}": _language_name(asr_language, ASR_LANGUAGES),
        "{tts_language}": tts_language,
        "{tts_language_name}": tts_language_name,
        # Backward-compatible placeholders from the earlier repository.
        "{language}": tts_language,
        "{language_name}": tts_language_name,
    }
    for key, value in values.items():
        source = source.replace(key, value)
    return source.strip()


def brain(
    server: Path,
    model: Path,
    asr_language: str,
    tts_language: str,
    tts_language_name: str,
    prompt: Path,
    paths: Paths,
    system_prompt: str | None = None,
    runtime: dict | None = None,
) -> str:
    asr_language = validate_asr_language(asr_language)
    system = render_system_prompt(system_prompt, asr_language, tts_language, tts_language_name)
    if not system:
        raise RuntimeError("Gemma system prompt is empty")
    write_text_atomic(paths.system, system + "\n")
    prompt_text = prompt.read_text(encoding="utf-8").strip()
    if not prompt_text:
        raise RuntimeError("Gemma prompt is empty")
    g = BRAIN_GENERATION
    runtime = runtime or BRAIN_RUNTIME
    base_url = ensure_gemma(server, model, runtime, note)
    request = {
        "model": "gemma",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt_text},
        ],
        "stream": False,
        # Gemma 4 shared-KV/SWA currently blocks the useful cross-request prefix
        # reuse path. Keep the model/KV resident but avoid host prompt-cache work.
        "cache_prompt": False,
        "temperature": g["temperature"],
        "top_p": g["top_p"],
        "top_k": g["top_k"],
        "min_p": g["min_p"],
        "repeat_penalty": g["repeat_penalty"],
        "seed": g["seed"],
        "max_tokens": g["max_tokens"],
        "chat_template_kwargs": {"enable_thinking": bool(BRAIN_THINKING)},
    }
    note(f"component=gemma event=request endpoint={base_url}/v1/chat/completions cache_prompt=0 asr_language={asr_language} output_language={tts_language}")
    started = time.perf_counter()
    payload = gemma_chat(base_url, request)
    note(f"component=gemma event=done request_ms={(time.perf_counter() - started) * 1000.0:.3f} response_id={payload.get('id', '-')}")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("llama-server returned no choices")
    message = choices[0].get("message")
    raw = message.get("content") if isinstance(message, dict) else None
    text = spoken_reply(str(raw or ""))
    if not text:
        raise RuntimeError("llama-server returned an empty answer")
    write_text_atomic(paths.answer, text + "\n")
    timings = payload.get("timings")
    if isinstance(timings, dict):
        note("component=gemma event=timings payload=" + json.dumps(timings, separators=(",", ":"), sort_keys=True))
    return text


def synthesize(
    server: Path,
    t3: Path,
    codec: Path,
    reference: Path,
    output: Path,
    language: str,
    family: dict,
    text_file: Path,
) -> None:
    runtime, sample, voice, chunk = (
        family["TTS_RUNTIME"], family["TTS_SAMPLE"], family["TTS_VOICE"], family["TTS_CHUNK"]
    )
    text = text_file.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("TTS text is empty")
    base_url = ensure_chatterbox(
        server, t3, codec, reference, family["name"], language,
        runtime, sample, voice, chunk, note,
    )
    note(f"component=tts event=request endpoint={base_url} family={family['name']} language={language} reference={reference} model_resident=1 voice_resident=1")
    result = chatterbox_synthesize(base_url, text, output)
    note("component=tts event=done " + result)
    validate_wav(output, TTS_RATE, channels=1)


def effective_family(family_name: str, args, profile: dict | None = None) -> dict:
    """Return a per-run family config, inheriting the warm profile then CLI overrides."""
    family = copy.deepcopy(FAMILIES[family_name])
    if profile and profile.get("version") == PIPELINE_PROFILE_VERSION and profile.get("family") == family_name:
        for profile_key, family_key in (
            ("tts_runtime", "TTS_RUNTIME"),
            ("tts_sample", "TTS_SAMPLE"),
            ("tts_voice", "TTS_VOICE"),
            ("tts_chunk", "TTS_CHUNK"),
        ):
            value = profile.get(profile_key)
            if isinstance(value, dict):
                family[family_key].update(value)

    runtime = family["TTS_RUNTIME"]
    sample = family["TTS_SAMPLE"]
    voice = family["TTS_VOICE"]
    chunk = family["TTS_CHUNK"]
    runtime_map = {"n_gpu_layers": "gpu_layers", "context": "context", "threads": "threads"}
    sample_map = {
        "seed": "seed", "max_tokens": "max_tokens", "top_k": "top_k", "top_p": "top_p",
        "min_p": "min_p", "temperature": "temperature", "repeat_penalty": "repeat_penalty",
        "cfm_steps": "cfm_steps",
    }
    for arg_name, key in runtime_map.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            runtime[key] = value
    for arg_name, key in sample_map.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            sample[key] = value
    for arg_name in ("cfg_weight", "exaggeration"):
        value = getattr(args, arg_name, None)
        if value is not None:
            voice[arg_name] = value
    if getattr(args, "chunk_chars", None) is not None:
        chunk["chars"] = args.chunk_chars
        chunk["first_chars"] = args.chunk_chars

    if family_name in {"turbo", "nano"}:
        sample["min_p"] = 0.0
        voice["cfg_weight"] = 0.0
        voice["exaggeration"] = 0.0
    return family


def effective_asr_runtime(args, profile: dict | None = None) -> dict:
    runtime = copy.deepcopy(ASR_RUNTIME)
    if profile and isinstance(profile.get("asr_runtime"), dict):
        runtime.update(profile["asr_runtime"])
    value = getattr(args, "asr_device", None)
    if value:
        runtime["device"] = value
    return runtime


def effective_brain_runtime(args, profile: dict | None = None) -> dict:
    runtime = copy.deepcopy(BRAIN_RUNTIME)
    if profile and isinstance(profile.get("brain_runtime"), dict):
        runtime.update(profile["brain_runtime"])
    value = getattr(args, "brain_device", None)
    if value:
        runtime["device"] = "none" if value.lower() in {"cpu", "none"} else value
        runtime["gpu_layers"] = 0 if runtime["device"] == "none" else "all"
    value = getattr(args, "flash_attn", None)
    if value:
        runtime["flash_attn"] = value
    return runtime


def resolve_language(family: dict, language: str | None) -> str:
    code = language or family["DEFAULT_REPLY_LANGUAGE"]
    if code not in family["TTS_LANGUAGES"]:
        raise RuntimeError(
            f"language {code!r} not supported by {family['name']}; choose from "
            + ", ".join(family["TTS_LANGUAGES"])
        )
    return code


def read_system_prompt_arg(args) -> str | None:
    inline = getattr(args, "system_prompt", None)
    file_value = getattr(args, "system_prompt_file", None)
    if inline is not None and file_value is not None:
        raise RuntimeError("choose only one of --system-prompt or --system-prompt-file")
    if file_value is not None:
        path = Path(file_value).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"missing system prompt file: {path}")
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise RuntimeError("system prompt file is empty")
        return value
    if inline is not None:
        value = str(inline).strip()
        if not value:
            raise RuntimeError("--system-prompt is empty")
        return value
    return None


def extra_copy(src: Path, dest: Path | None) -> None:
    if dest is None or src.resolve() == dest.resolve():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def prepared_reference(reference: Path, data_dir: Path) -> Path:
    if not reference.is_file():
        raise RuntimeError(f"missing {reference.name}; python main.py install")
    wav = chatterbox_wav(reference, data_dir / "prepared", note)
    validate_wav(wav, TTS_RATE, minimum_seconds=REFERENCE_MIN_SECONDS, channels=1)
    return wav


def publish_speech(paths: Paths, dest: Path | None) -> Path:
    mp4 = compatible_mp4(paths.output, paths.output_mp4, note)
    publish_outputs(paths.output, mp4, dest)
    return mp4


def write_meta(paths: Paths, rows: dict[str, str]) -> None:
    body = "".join(f"{key}={value}\n" for key, value in rows.items())
    write_text_atomic(paths.run_dir / "meta.txt", body)


def start_run(command: str, args) -> Paths:
    paths = Paths(args.models_dir, args.data_dir, command)
    set_log(paths.log)
    note(f"component=pipeline event=start run_dir={paths.run_dir}")
    return paths


def run_asr(input_wav: Path, output: Path | None, paths: Paths, asr_language: str = "auto", asr_runtime: dict | None = None) -> None:
    asr_runtime = asr_runtime or ASR_RUNTIME
    wav = parakeet_wav(input_wav, paths.run_dir / "input.wav", note)
    text = transcribe(
        runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir),
        wav, paths, asr_language, asr_runtime,
    )
    extra_copy(paths.transcript, output)
    write_meta(paths, {
        "command": "asr", "input": str(input_wav), "wav": str(wav), "transcript": str(paths.transcript),
        "asr_language": asr_language, "asr_language_mode": "auto-detect",
        "device": str(asr_runtime["device"]),
    })
    print(text)
    print(f"Run: {paths.run_dir}")


def run_brain(
    prompt: Path,
    output: Path | None,
    tts_language: str,
    asr_language: str,
    paths: Paths,
    system_prompt: str | None = None,
    brain_runtime: dict | None = None,
) -> None:
    brain_runtime = brain_runtime or BRAIN_RUNTIME
    if tts_language not in LANGUAGES:
        raise RuntimeError(f"language {tts_language!r} not supported; choose from {', '.join(LANGUAGES)}")
    shutil.copy2(prompt, paths.run_dir / "prompt.txt")
    text = brain(
        runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir),
        asr_language, tts_language, LANGUAGES[tts_language], prompt, paths, system_prompt, brain_runtime,
    )
    extra_copy(paths.answer, output)
    write_meta(paths, {
        "command": "brain", "asr_language": asr_language, "tts_language": tts_language,
        "device": str(brain_runtime["device"]), "prompt": str(prompt),
        "answer": str(paths.answer), "system": str(paths.system),
    })
    print(text)
    print(f"Run: {paths.run_dir}")


def run_tts(
    text_file: Path,
    reference: Path,
    output: Path | None,
    family: dict,
    language: str | None,
    paths: Paths,
) -> None:
    family_name = family["name"]
    language = resolve_language(family, language)
    models = models_for(family_name)
    shutil.copy2(text_file, paths.run_dir / "text.txt")
    synthesize(
        runtime_tts_server(),
        require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir),
        reference, paths.output, language, family, text_file,
    )
    mp4 = publish_speech(paths, output)
    write_meta(paths, {
        "command": "tts", "family": family_name, "language": language, "gpu_layers": str(family["TTS_RUNTIME"]["gpu_layers"]),
        "text": str(text_file), "reference": str(reference),
        "output": str(paths.output), "output_mp4": str(mp4),
        "resident": "1", "reference_conditioning": "precomputed-once-at-resident-start",
    })
    print(f"Output: {paths.output}")
    print(f"Video: {mp4}")
    print(f"Run: {paths.run_dir}")


def run_pipeline(
    input_wav: Path,
    output: Path | None,
    family: dict,
    asr_language: str,
    tts_language: str,
    reference: Path,
    paths: Paths,
    system_prompt: str | None = None,
    asr_runtime: dict | None = None,
    brain_runtime: dict | None = None,
) -> None:
    pipeline_started = time.perf_counter()
    asr_runtime = asr_runtime or ASR_RUNTIME
    brain_runtime = brain_runtime or BRAIN_RUNTIME
    family_name = family["name"]
    asr_language = validate_asr_language(asr_language)
    tts_language = resolve_language(family, tts_language)
    models = models_for(family_name)
    wav = parakeet_wav(input_wav, paths.run_dir / "input.wav", note)

    transcript = transcribe(
        runtime_server("parakeet"), require_model(models["parakeet"], paths.models_dir),
        wav, paths, asr_language, asr_runtime,
    )
    answer = brain(
        runtime_server("gemma"), require_model(models[BRAIN_MODEL], paths.models_dir),
        asr_language, tts_language, family["TTS_LANGUAGES"][tts_language],
        paths.transcript, paths, system_prompt, brain_runtime,
    )
    synthesize(
        runtime_tts_server(),
        require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir),
        reference, paths.output, tts_language, family, paths.answer,
    )
    mp4 = publish_speech(paths, output)
    write_meta(paths, {
        "command": "run", "family": family_name,
        "asr_language": asr_language, "asr_language_mode": "auto-detect",
        "tts_language": tts_language, "input": str(input_wav), "wav": str(wav),
        "asr_device": str(asr_runtime["device"]), "brain_device": str(brain_runtime["device"]),
        "tts_gpu_layers": str(family["TTS_RUNTIME"]["gpu_layers"]),
        "reference": str(reference), "output": str(paths.output),
        "output_mp4": str(mp4), "system": str(paths.system),
        "resident_chain": "parakeet->gemma->chatterbox",
    })
    note(f"component=pipeline event=done family={family_name} total_ms={(time.perf_counter() - pipeline_started) * 1000.0:.3f}")
    print(f"Transcript: {transcript}")
    print(f"Answer: {answer}")
    print(f"Output: {paths.output}")
    print(f"Video: {mp4}")
    print(f"Run: {paths.run_dir}")


EXAMPLES = """\
# Install all five independent pipelines.
python main.py install --family all

# Preload the complete repeated voice-agent chain. Parakeet v3 auto-detects
# Polish; Gemma receives that expectation in its system prompt; V3 speaks English.
python main.py resident warm --family v3 --asr-language pl --tts-language en -r trump

# Optional translation-only behavior. Placeholders are expanded per request.
python main.py resident warm --family v3 --asr-language pl --tts-language en -r trump \\
  --system-prompt "Translate {asr_language_name} to {tts_language_name}. Return only natural spoken translation."

# Every later call reuses all three warm processes and the same precomputed voice.
python main.py run input-1.wav
python main.py run input-2.wav

# English-only low-latency Chatterbox resident profile.
python main.py resident warm --family turbo --asr-language en --tts-language en -r obama

# Direct independent pipelines still work and remain resident after first use.
# Any audio/video input is converted to the WAV each model expects.
python main.py parakeet rec.mp3 --language pl
python main.py parakeet rec.mp4 --language pl
python main.py gemma prompt.txt --language en
python main.py nano line.txt -r obama
python main.py turbo line.txt -r trump
python main.py v3 line.txt --language pl -r myvoice.mp3

# Chatterbox also writes a baseline H.264/AAC MP4 next to output.wav.
python main.py run interview.m4a -o reply.mp4

python main.py resident status
python main.py resident stop
"""


class Cli(argparse.ArgumentParser):
    def format_help(self) -> str:
        return EXAMPLES

    def error(self, message: str) -> None:
        self.exit(2, f"{message}\n\n{EXAMPLES}")


def add_tts_tuning_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--n-gpu-layers", type=int)
    cmd.add_argument("--context", type=int)
    cmd.add_argument("--threads", type=int)
    cmd.add_argument("--seed", type=int)
    cmd.add_argument("--max-tokens", type=int)
    cmd.add_argument("--top-k", type=int)
    cmd.add_argument("--top-p", type=float)
    cmd.add_argument("--min-p", type=float)
    cmd.add_argument("--temperature", type=float)
    cmd.add_argument("--repeat-penalty", type=float)
    cmd.add_argument("--cfg-weight", type=float)
    cmd.add_argument("--exaggeration", type=float)
    cmd.add_argument("--cfm-steps", type=int)
    cmd.add_argument("--chunk-chars", type=int)


def add_system_prompt_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--system-prompt")
    cmd.add_argument("--system-prompt-file")


def add_tts_io_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("input")
    cmd.add_argument("-r", "--reference")
    cmd.add_argument("-o", "--output")
    cmd.add_argument("--language")
    add_tts_tuning_args(cmd)


def build_parser() -> argparse.ArgumentParser:
    families = tuple(FAMILIES)
    p = Cli(prog="python main.py")
    p.add_argument("--models-dir", type=Path)
    p.add_argument("--data-dir", type=Path)
    sub = p.add_subparsers(dest="command", parser_class=Cli)

    install_cmd = sub.add_parser("install")
    install_cmd.add_argument("--family", choices=("all", *families), default="all")

    for name in ("asr", "parakeet"):
        cmd = sub.add_parser(name)
        cmd.add_argument("input")
        cmd.add_argument("-o", "--output")
        cmd.add_argument("--language", choices=("auto", *tuple(ASR_LANGUAGES)), default="auto")
        cmd.add_argument("--device", "--asr-device", dest="asr_device", help="Parakeet primary device, e.g. Vulkan0 or cpu")

    for name in ("brain", "gemma"):
        cmd = sub.add_parser(name)
        cmd.add_argument("input")
        cmd.add_argument("-o", "--output")
        cmd.add_argument("--language", choices=tuple(LANGUAGES), default="en")
        cmd.add_argument("--asr-language", choices=("auto", *tuple(ASR_LANGUAGES)), default="auto")
        cmd.add_argument("--device", "--brain-device", dest="brain_device", help="Gemma device: Vulkan0 or cpu/none")
        cmd.add_argument("--flash-attn", choices=("on", "off", "auto"))
        add_system_prompt_args(cmd)

    tts_cmd = sub.add_parser("tts")
    add_tts_io_args(tts_cmd)
    tts_cmd.add_argument("--family", choices=families, default=default_family())
    for family_name in families:
        family_cmd = sub.add_parser(family_name)
        add_tts_io_args(family_cmd)
        family_cmd.set_defaults(family=family_name)

    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("input")
    run_cmd.add_argument("-o", "--output")
    run_cmd.add_argument("--family", choices=families)
    run_cmd.add_argument("--language", help="Legacy alias for --tts-language")
    run_cmd.add_argument("--tts-language")
    run_cmd.add_argument("--asr-language", choices=("auto", *tuple(ASR_LANGUAGES)))
    run_cmd.add_argument("--asr-device", help="Parakeet primary device, e.g. Vulkan0 or cpu")
    run_cmd.add_argument("--brain-device", help="Gemma device: Vulkan0 or cpu/none")
    run_cmd.add_argument("--flash-attn", choices=("on", "off", "auto"))
    run_cmd.add_argument("-r", "--reference")
    add_system_prompt_args(run_cmd)
    add_tts_tuning_args(run_cmd)

    resident_cmd = sub.add_parser("resident")
    resident_cmd.add_argument("action", choices=("status", "warm", "stop"))
    resident_cmd.add_argument("--family", choices=families)
    resident_cmd.add_argument("--tts-language")
    resident_cmd.add_argument("--asr-language", choices=("auto", *tuple(ASR_LANGUAGES)))
    resident_cmd.add_argument("--asr-device", help="Parakeet primary device, e.g. Vulkan0 or cpu")
    resident_cmd.add_argument("--brain-device", help="Gemma device: Vulkan0 or cpu/none")
    resident_cmd.add_argument("--flash-attn", choices=("on", "off", "auto"))
    resident_cmd.add_argument("-r", "--reference")
    add_system_prompt_args(resident_cmd)
    add_tts_tuning_args(resident_cmd)
    return p


def print_resident_status() -> None:
    profile = load_pipeline_profile()
    for row in resident_status():
        state = "ready" if row["ready"] else "stopped"
        extra = ""
        if row.get("device"):
            extra += f" device={row['device']}"
        if row.get("gpu_layers") is not None:
            extra += f" gpu_layers={row['gpu_layers']}"
        if row["name"] == "chatterbox" and row.get("family"):
            extra += f" family={row['family']} language={row.get('language') or '-'} reference={row.get('reference') or '-'}"
        print(f"{row['name']}: {state} pid={row['pid'] or '-'} url={row['url']} log={row['log']}{extra}")
    if profile:
        print(
            "profile: "
            f"family={profile.get('family', '-')} asr_language={profile.get('asr_language', 'auto')} "
            f"tts_language={profile.get('tts_language', '-')} reference={profile.get('reference', '-')} "
            f"asr_device={profile.get('asr_runtime', {}).get('device', ASR_RUNTIME['device'])} "
            f"brain_device={profile.get('brain_runtime', {}).get('device', BRAIN_RUNTIME['device'])} "
            f"system_prompt={'custom' if profile.get('system_prompt') else 'default'}"
        )


def _profile_family_settings(profile: dict) -> dict:
    if not isinstance(profile, dict) or profile.get("version") != PIPELINE_PROFILE_VERSION:
        return {}
    return profile


def _make_profile(
    family: dict,
    asr_language: str,
    tts_language: str,
    reference: Path,
    system_prompt: str | None,
    asr_runtime: dict,
    brain_runtime: dict,
) -> dict:
    return {
        "version": PIPELINE_PROFILE_VERSION,
        "family": family["name"],
        "asr_language": asr_language,
        "tts_language": tts_language,
        "reference": str(reference.resolve()),
        "system_prompt": system_prompt or "",
        "asr_runtime": copy.deepcopy(asr_runtime),
        "brain_runtime": copy.deepcopy(brain_runtime),
        "tts_runtime": copy.deepcopy(family["TTS_RUNTIME"]),
        "tts_sample": copy.deepcopy(family["TTS_SAMPLE"]),
        "tts_voice": copy.deepcopy(family["TTS_VOICE"]),
        "tts_chunk": copy.deepcopy(family["TTS_CHUNK"]),
    }


def warm_resident(args) -> None:
    paths = Paths(
        args.models_dir.resolve() if args.models_dir else None,
        args.data_dir.resolve() if args.data_dir else None,
    )
    previous = load_pipeline_profile()
    family_name = args.family or previous.get("family") or default_family()
    family = effective_family(family_name, args, _profile_family_settings(previous))
    asr_runtime = effective_asr_runtime(args, previous)
    brain_runtime = effective_brain_runtime(args, previous)
    asr_language = validate_asr_language(args.asr_language or previous.get("asr_language") or "auto")
    previous_tts_language = previous.get("tts_language") if previous.get("family") == family_name else None
    tts_language = resolve_language(family, args.tts_language or previous_tts_language)

    explicit_prompt = read_system_prompt_arg(args)
    system_prompt = explicit_prompt if explicit_prompt is not None else (previous.get("system_prompt") or None)
    reference_value = args.reference if args.reference is not None else previous.get("reference")
    reference = prepared_reference(resolve_voice(paths.data_dir, reference_value), paths.data_dir)

    ensure_parakeet(
        runtime_server("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir),
        asr_runtime, note,
    )
    ensure_gemma(
        runtime_server("gemma"), require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir),
        brain_runtime, note,
    )
    models = models_for(family_name)
    ensure_chatterbox(
        runtime_tts_server(),
        require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir),
        reference, family_name, tts_language,
        family["TTS_RUNTIME"], family["TTS_SAMPLE"], family["TTS_VOICE"], family["TTS_CHUNK"], note,
    )
    profile = _make_profile(family, asr_language, tts_language, reference, system_prompt, asr_runtime, brain_runtime)
    save_pipeline_profile(profile)
    note(
        f"resident profile saved family={family_name} asr_language={asr_language} "
        f"tts_language={tts_language} reference={reference} asr_device={asr_runtime['device']} "
        f"brain_device={brain_runtime['device']} system_prompt={'custom' if system_prompt else 'default'}"
    )


def _resolve_pipeline_settings(args, data_dir: Path) -> tuple[dict, str, str, Path, str | None, dict, dict]:
    profile = load_pipeline_profile()
    family_name = args.family or profile.get("family") or default_family()
    family = effective_family(family_name, args, profile)
    tts_language = resolve_language(
        family,
        getattr(args, "tts_language", None) or getattr(args, "language", None) or
        (profile.get("tts_language") if profile.get("family") == family_name else None),
    )
    asr_language = validate_asr_language(
        getattr(args, "asr_language", None) or
        (profile.get("asr_language") if profile.get("family") == family_name else None) or "auto"
    )
    explicit_prompt = read_system_prompt_arg(args)
    if explicit_prompt is not None:
        system_prompt = explicit_prompt
    elif profile.get("family") == family_name:
        system_prompt = profile.get("system_prompt") or None
    else:
        system_prompt = None
    ref_value = getattr(args, "reference", None)
    if ref_value is None and profile.get("family") == family_name:
        ref_value = profile.get("reference")
    reference = prepared_reference(resolve_voice(data_dir, ref_value), data_dir)
    return family, asr_language, tts_language, reference, system_prompt, effective_asr_runtime(args, profile), effective_brain_runtime(args, profile)


def main() -> int:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        return 2
    args = parser.parse_args()
    note(f"component=runtime event=profile hardware={HARDWARE_PROFILE}")
    if not args.command:
        parser.print_help()
        return 2
    try:
        if args.command == "install":
            install(
                args.family,
                (args.models_dir.resolve() if args.models_dir else None),
                (args.data_dir.resolve() if args.data_dir else None),
            )
            return 0
        if args.command == "resident":
            if args.action == "stop":
                resident_stop_all(note)
            elif args.action == "warm":
                warm_resident(args)
            print_resident_status()
            return 0

        operation = args.command
        if operation == "parakeet":
            operation = "asr"
        elif operation == "gemma":
            operation = "brain"
        elif operation in FAMILIES:
            operation = "tts"

        source = Path(args.input).expanduser().resolve()
        if not source.is_file():
            raise RuntimeError(f"missing file: {source}")
        output = Path(args.output).expanduser().resolve() if getattr(args, "output", None) else None
        data_dir = args.data_dir.resolve() if args.data_dir else Paths().data_dir

        if operation == "run":
            family, asr_language, tts_language, reference, system_prompt, asr_runtime, brain_runtime = _resolve_pipeline_settings(args, data_dir)
            if not reference.is_file():
                raise RuntimeError(f"missing {reference.name}; python main.py install --family {family['name']}")
            paths = start_run(args.command, args)
            run_pipeline(source, output, family, asr_language, tts_language, reference, paths, system_prompt, asr_runtime, brain_runtime)
            return 0

        paths = start_run(args.command, args)
        if operation == "asr":
            run_asr(source, output, paths, args.language, effective_asr_runtime(args))
        elif operation == "brain":
            run_brain(source, output, args.language, args.asr_language, paths, read_system_prompt_arg(args), effective_brain_runtime(args))
        elif operation == "tts":
            family = effective_family(args.family, args)
            reference = prepared_reference(resolve_voice(data_dir, args.reference), data_dir)
            run_tts(source, reference, output, family, args.language, paths)
        else:
            raise RuntimeError(f"unknown operation: {operation}")
        return 0
    except Exception as exc:
        note(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
