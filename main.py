from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

from config import (
    ASR_RUNTIME, ASR_RATE, TTS_RATE, REFERENCE_MIN_SECONDS, BRAIN_MODEL, BRAIN_RUNTIME,
    BRAIN_GENERATION, BRAIN_THINKING, BRAIN_SYSTEM, FAMILIES, SHARED_MODELS, LANGUAGES, Paths, default_family,
)
from installer import (
    install, runtime_executable, runtime_tts, models_for, require_model,
    validate_wav, write_text_atomic, note,
)


def transcribe(exe: Path, model: Path, input_wav: Path, paths: Paths) -> str:
    env = os.environ.copy()
    env["PARAKEET_DEVICE"] = str(ASR_RUNTIME["device"])
    command = [str(exe), "transcribe", "--model", str(model), "--input", str(input_wav), "--decoder", "tdt", "--threads", str(ASR_RUNTIME["threads"]), "--json"]
    note("asr: " + " ".join(command))
    result = subprocess.run(command, cwd=exe.parent, env=env, stdout=subprocess.PIPE, stderr=None, text=True, encoding="utf-8", errors="strict", check=True)
    payload = json.loads(result.stdout)
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


def brain(exe: Path, model: Path, language: str, language_name: str, prompt: Path, paths: Paths) -> str:
    system = BRAIN_SYSTEM.format(language_name=language_name, language=language)
    write_text_atomic(paths.system, system + "\n")
    paths.answer.unlink(missing_ok=True)
    g = BRAIN_GENERATION
    r = BRAIN_RUNTIME
    thinking = "on" if BRAIN_THINKING else "off"
    template_kwargs = json.dumps({"enable_thinking": bool(BRAIN_THINKING)}, separators=(",", ":"))
    command = [
        str(exe), "-m", str(model), "--system-prompt-file", str(paths.system), "--file", str(prompt),
        "--conversation", "--single-turn", "--output-file", str(paths.answer), "--no-display-prompt",
        "--show-timings", "--perf", "--log-prefix", "--log-timestamps", "--verbosity", "0",
        "--offline", "--device", str(r["device"]), "--n-gpu-layers", str(r["gpu_layers"]), "--ctx-size", str(r["context"]),
        "--no-mmproj", "--load-mode", "auto", "--flash-attn", str(r["flash_attn"]), "--repack", "--fit", str(r["fit"]),
        "--fit-target", str(r["fit_target"]), "--fit-ctx", str(r["fit_ctx"]), "--seed", str(g["seed"]),
        "--n-predict", str(g["max_tokens"]), "--temperature", str(g["temperature"]), "--top-p", str(g["top_p"]),
        "--top-k", str(g["top_k"]), "--min-p", str(g["min_p"]), "--repeat-penalty", str(g["repeat_penalty"]),
        "--reasoning", thinking, "--chat-template-kwargs", template_kwargs,
    ]
    note("brain: " + " ".join(command))
    subprocess.run(command, cwd=exe.parent, stdout=sys.stderr, stderr=sys.stderr, check=True)
    if not paths.answer.is_file():
        raise RuntimeError("llama-cli did not create answer.txt")
    text = spoken_reply(paths.answer.read_text(encoding="utf-8"))
    if not text:
        raise RuntimeError("llama-cli returned an empty answer")
    write_text_atomic(paths.answer, text + "\n")
    return text


def synthesize(exe: Path, t3: Path, codec: Path, reference: Path, output: Path, language: str, family: dict, text_file: Path) -> None:
    runtime, sample, voice = family["TTS_RUNTIME"], family["TTS_SAMPLE"], family["TTS_VOICE"]
    command = [
        str(exe), "--model", str(t3), "--s3gen-gguf", str(codec), "--reference", str(reference), "--text-file", str(text_file),
        "--output", str(output), "--n-gpu-layers", str(runtime["gpu_layers"]), "--context", str(runtime["context"]),
        "--threads", str(runtime["threads"]), "--seed", str(sample["seed"]), "--max-tokens", str(sample["max_tokens"]),
        "--top-p", str(sample["top_p"]), "--temperature", str(sample["temperature"]), "--repeat-penalty", str(sample["repeat_penalty"]),
        "--cfm-steps", str(sample["cfm_steps"]), "--chunk-chars", str(family["TTS_CHUNK"]["chars"]),
    ]
    if "top_k" in sample:
        command += ["--top-k", str(sample["top_k"])]
    if "min_p" in sample:
        command += ["--min-p", str(sample["min_p"])]
    if family["TTS_MULTILINGUAL"]:
        command += ["--language", language, "--cfg-weight", str(voice["cfg_weight"]), "--exaggeration", str(voice["exaggeration"])]
    output.unlink(missing_ok=True)
    note("tts: " + " ".join(command))
    subprocess.run(command, cwd=exe.parent, stdout=sys.stderr, stderr=sys.stderr, check=True)
    validate_wav(output, TTS_RATE, channels=1)


def run_asr(input_wav: Path, output: Path | None, paths: Paths) -> None:
    asr_exe = runtime_executable("parakeet")
    asr_model = require_model(SHARED_MODELS["parakeet"], paths.models_dir, paths.data_dir)
    validate_wav(input_wav, ASR_RATE, channels=1)
    text = transcribe(asr_exe, asr_model, input_wav, paths)
    if output:
        write_text_atomic(output, text + "\n")
    else:
        print(text)


def run_brain(prompt: Path, output: Path | None, language: str, paths: Paths) -> None:
    brain_exe = runtime_executable("gemma")
    brain_model = require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir, paths.data_dir)
    language_name = LANGUAGES.get(language, "English")
    text = brain(brain_exe, brain_model, language, language_name, prompt, paths)
    if output:
        write_text_atomic(output, text + "\n")
    else:
        print(text)


def run_tts(text_file: Path, reference: Path, output: Path, family_name: str, language: str, paths: Paths) -> None:
    family = FAMILIES[family_name]
    if language not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {language!r} not supported by family {family_name}; choose from {', '.join(family['TTS_LANGUAGES'])}")
    models = models_for(family_name)
    tts_exe = runtime_tts(family_name)
    t3_model = require_model(models["chatterbox-t3"], paths.models_dir, paths.data_dir)
    codec_model = require_model(models["chatterbox-codec"], paths.models_dir, paths.data_dir)
    validate_wav(reference, minimum_seconds=REFERENCE_MIN_SECONDS)
    synthesize(tts_exe, t3_model, codec_model, reference, output, language, family, text_file)


def run_pipeline(input_wav: Path, output_wav: Path, family_name: str, language: str | None, reference: Path | None, paths: Paths) -> None:
    family = FAMILIES[family_name]
    language = language or family["DEFAULT_REPLY_LANGUAGE"]
    if language not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {language!r} not supported by family {family_name}; choose from {', '.join(family['TTS_LANGUAGES'])}")
    reference = reference or paths.reference
    if len({input_wav.resolve(), reference.resolve(), output_wav.resolve()}) != 3:
        raise RuntimeError("input, reference, and output must be different paths")
    validate_wav(input_wav, ASR_RATE, channels=1)
    validate_wav(reference, minimum_seconds=REFERENCE_MIN_SECONDS)
    models = models_for(family_name)
    asr_model = require_model(models["parakeet"], paths.models_dir, paths.data_dir)
    brain_model = require_model(models[BRAIN_MODEL], paths.models_dir, paths.data_dir)
    t3_model = require_model(models["chatterbox-t3"], paths.models_dir, paths.data_dir)
    codec_model = require_model(models["chatterbox-codec"], paths.models_dir, paths.data_dir)
    asr_exe = runtime_executable("parakeet")
    brain_exe = runtime_executable("gemma")
    tts_exe = runtime_tts(family_name)
    transcript = transcribe(asr_exe, asr_model, input_wav, paths)
    answer = brain(brain_exe, brain_model, language, family["TTS_LANGUAGES"][language], paths.transcript, paths)
    synthesize(tts_exe, t3_model, codec_model, reference, output_wav, language, family, paths.answer)
    print(f"Transcript: {transcript}")
    print(f"Answer: {answer}")
    print(f"Output: {output_wav}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python main.py")
    p.add_argument("--models-dir", type=Path, help="Override models directory")
    p.add_argument("--data-dir", type=Path, help="Override data directory")
    sub = p.add_subparsers(dest="command", required=True)

    install_cmd = sub.add_parser("install")
    install_cmd.add_argument("--family", choices=tuple(FAMILIES), default=default_family())

    asr_cmd = sub.add_parser("asr")
    asr_cmd.add_argument("input")
    asr_cmd.add_argument("-o", "--output")

    brain_cmd = sub.add_parser("brain")
    brain_cmd.add_argument("input")
    brain_cmd.add_argument("-o", "--output")
    brain_cmd.add_argument("--language", default="en")

    tts_cmd = sub.add_parser("tts")
    tts_cmd.add_argument("input")
    tts_cmd.add_argument("-r", "--reference", required=True)
    tts_cmd.add_argument("-o", "--output", required=True)
    tts_cmd.add_argument("--family", choices=tuple(FAMILIES), default=default_family())
    tts_cmd.add_argument("--language", default="en")

    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("input")
    run_cmd.add_argument("output")
    run_cmd.add_argument("--family", choices=tuple(FAMILIES), default=default_family())
    run_cmd.add_argument("--language")
    run_cmd.add_argument("--reference")

    return p


def main() -> int:
    args = build_parser().parse_args()
    paths = Paths(args.models_dir, args.data_dir)
    try:
        if args.command == "install":
            install(args.family, paths.models_dir, paths.data_dir)
        elif args.command == "asr":
            run_asr(Path(args.input).expanduser().resolve(), Path(args.output).expanduser().resolve() if args.output else None, paths)
        elif args.command == "brain":
            run_brain(Path(args.input).expanduser().resolve(), Path(args.output).expanduser().resolve() if args.output else None, args.language, paths)
        elif args.command == "tts":
            run_tts(
                Path(args.input).expanduser().resolve(),
                Path(args.reference).expanduser().resolve(),
                Path(args.output).expanduser().resolve(),
                args.family,
                args.language,
                paths,
            )
        else:
            run_pipeline(
                Path(args.input).expanduser().resolve(),
                Path(args.output).expanduser().resolve(),
                args.family,
                args.language,
                Path(args.reference).expanduser().resolve() if args.reference else None,
                paths,
            )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError, wave.Error, UnicodeError) as exc:
        note(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
