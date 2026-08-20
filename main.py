from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

from config import (
    ASR_RUNTIME, BRAIN_MODEL, BRAIN_RUNTIME, BRAIN_GENERATION, BRAIN_THINKING, BRAIN_SYSTEM,
    FAMILIES, SHARED_MODELS, DEFAULT_MODELS_DIR, DEFAULT_DATA_DIR,
)
from paths import (
    ROOT, RUNTIMES, TRANSCRIPT, ANSWER, SYSTEM_PROMPT,
    DEFAULT_REFERENCE, ASSETS_REFERENCE,
)
from installer import (
    install, runtime_executable, runtime_tts, models_for,
    validate_wav, write_text_atomic,
)


def model_path(spec: dict, models_dir: Path, data_dir: Path) -> Path:
    root = data_dir if spec.get("directory") == "data" else models_dir
    return root / spec["file"]


def require_model(spec: dict, models_dir: Path, data_dir: Path) -> Path:
    path = model_path(spec, models_dir, data_dir)
    if not path.is_file() or path.stat().st_size != spec["size"]:
        actual = path.stat().st_size if path.is_file() else 0
        raise RuntimeError(f"model missing or wrong size: {path} (expected {spec['size']}, got {actual})")
    return path


def transcribe(exe: Path, model: Path, input_wav: Path) -> str:
    env = os.environ.copy()
    env["PARAKEET_DEVICE"] = str(ASR_RUNTIME["device"])
    command = [str(exe), "transcribe", "--model", str(model), "--input", str(input_wav), "--decoder", "tdt", "--threads", str(ASR_RUNTIME["threads"]), "--json"]
    note("asr: " + " ".join(command))
    result = subprocess.run(command, cwd=exe.parent, env=env, stdout=subprocess.PIPE, stderr=None, text=True, encoding="utf-8", errors="strict", check=True)
    payload = json.loads(result.stdout)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise RuntimeError("Parakeet returned an empty transcript")
    write_text_atomic(TRANSCRIPT, text + "\n")
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


def brain(exe: Path, model: Path, language: str, language_name: str) -> str:
    system = BRAIN_SYSTEM.format(language_name=language_name, language=language)
    write_text_atomic(SYSTEM_PROMPT, system + "\n")
    ANSWER.unlink(missing_ok=True)
    g = BRAIN_GENERATION
    r = BRAIN_RUNTIME
    thinking = "on" if BRAIN_THINKING else "off"
    template_kwargs = json.dumps({"enable_thinking": bool(BRAIN_THINKING)}, separators=(",", ":"))
    command = [
        str(exe), "-m", str(model), "--system-prompt-file", str(SYSTEM_PROMPT), "--file", str(TRANSCRIPT),
        "--conversation", "--single-turn", "--output-file", str(ANSWER), "--no-display-prompt",
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
    if not ANSWER.is_file():
        raise RuntimeError("llama-cli did not create answer.txt")
    text = spoken_reply(ANSWER.read_text(encoding="utf-8"))
    if not text:
        raise RuntimeError("llama-cli returned an empty answer")
    write_text_atomic(ANSWER, text + "\n")
    return text


def synthesize(exe: Path, t3: Path, codec: Path, reference: Path, output: Path, language: str, family: dict, text_file: Path = ANSWER) -> None:
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
    if family.get("TTS_EXE") == "trident-tts-v3.exe":
        command += ["--language", language, "--cfg-weight", str(voice["cfg_weight"]), "--exaggeration", str(voice["exaggeration"])]
    output.unlink(missing_ok=True)
    note("tts: " + " ".join(command))
    subprocess.run(command, cwd=exe.parent, stdout=sys.stderr, stderr=sys.stderr, check=True)
    validate_wav(output, 24000)


def run_asr(input_wav: Path, output: Path | None, models_dir: Path, data_dir: Path) -> None:
    asr_exe = runtime_executable("parakeet")
    asr_model = require_model(models_for("v3")["parakeet"], models_dir, data_dir)
    validate_wav(input_wav, 16000)
    text = transcribe(asr_exe, asr_model, input_wav)
    if output:
        write_text_atomic(output, text + "\n")
    else:
        print(text)


def run_brain(transcript: Path, output: Path | None, language: str, models_dir: Path, data_dir: Path) -> None:
    brain_exe = runtime_executable("gemma")
    brain_model = require_model(models_for("v3")[BRAIN_MODEL], models_dir, data_dir)
    family = FAMILIES["v3"]
    language_name = family["TTS_LANGUAGES"].get(language, "English")
    text = brain(brain_exe, brain_model, language, language_name)
    if output:
        write_text_atomic(output, text + "\n")
    else:
        print(text)


def run_tts(text_file: Path, reference: Path, output: Path, family_name: str, language: str, models_dir: Path, data_dir: Path) -> None:
    family = FAMILIES[family_name]
    if language not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {language!r} not supported by family {family_name}; choose from {', '.join(family['TTS_LANGUAGES'])}")
    models = models_for(family_name)
    tts_exe = runtime_tts(family_name)
    t3_model = require_model(models["chatterbox-t3"], models_dir, data_dir)
    codec_model = require_model(models["chatterbox-codec"], models_dir, data_dir)
    validate_wav(reference, None, 5.0)
    synthesize(tts_exe, t3_model, codec_model, reference, output, language, family, text_file)


def run_pipeline(input_wav: Path, output_wav: Path, family_name: str, language: str | None, reference: Path | None, models_dir: Path, data_dir: Path) -> None:
    family = FAMILIES[family_name]
    language = language or family["DEFAULT_REPLY_LANGUAGE"]
    if language not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {language!r} not supported by family {family_name}; choose from {', '.join(family['TTS_LANGUAGES'])}")
    default_ref = DEFAULT_REFERENCE if DEFAULT_REFERENCE.is_file() else ASSETS_REFERENCE
    reference = reference or default_ref
    if len({input_wav.resolve(), reference.resolve(), output_wav.resolve()}) != 3:
        raise RuntimeError("input, reference, and output must be different paths")
    validate_wav(input_wav, 16000)
    validate_wav(reference, None, 5.0)
    models = models_for(family_name)
    asr_model = require_model(models["parakeet"], models_dir, data_dir)
    brain_model = require_model(models[BRAIN_MODEL], models_dir, data_dir)
    t3_model = require_model(models["chatterbox-t3"], models_dir, data_dir)
    codec_model = require_model(models["chatterbox-codec"], models_dir, data_dir)
    asr_exe = runtime_executable("parakeet")
    brain_exe = runtime_executable("gemma")
    tts_exe = runtime_tts(family_name)
    transcript = transcribe(asr_exe, asr_model, input_wav)
    answer = brain(brain_exe, brain_model, language, family["TTS_LANGUAGES"][language])
    synthesize(tts_exe, t3_model, codec_model, reference, output_wav, language, family)
    print(f"Transcript: {transcript}")
    print(f"Answer: {answer}")
    print(f"Output: {output_wav}")


def note(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python main.py")
    p.add_argument("--models-dir", type=Path, help="Override models directory")
    p.add_argument("--data-dir", type=Path, help="Override data directory")
    sub = p.add_subparsers(dest="command", required=True)

    install_cmd = sub.add_parser("install")
    install_cmd.add_argument("--family", choices=tuple(FAMILIES), default="v3")

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
    tts_cmd.add_argument("--family", choices=tuple(FAMILIES), default="v3")
    tts_cmd.add_argument("--language", default="en")

    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("input")
    run_cmd.add_argument("output")
    run_cmd.add_argument("--family", choices=tuple(FAMILIES), default="v3")
    run_cmd.add_argument("--language")
    run_cmd.add_argument("--reference")

    return p


def main() -> int:
    args = build_parser().parse_args()
    models_dir = (args.models_dir or DEFAULT_MODELS_DIR).resolve()
    data_dir = (args.data_dir or DEFAULT_DATA_DIR).resolve()
    try:
        if args.command == "install":
            install(args.family, models_dir, data_dir)
        elif args.command == "asr":
            run_asr(Path(args.input).expanduser().resolve(), Path(args.output).expanduser().resolve() if args.output else None, models_dir, data_dir)
        elif args.command == "brain":
            run_brain(Path(args.input).expanduser().resolve(), Path(args.output).expanduser().resolve() if args.output else None, args.language, models_dir, data_dir)
        elif args.command == "tts":
            run_tts(
                Path(args.input).expanduser().resolve(),
                Path(args.reference).expanduser().resolve(),
                Path(args.output).expanduser().resolve(),
                args.family,
                args.language,
                models_dir,
                data_dir,
            )
        else:
            run_pipeline(
                Path(args.input).expanduser().resolve(),
                Path(args.output).expanduser().resolve(),
                args.family,
                args.language,
                Path(args.reference).expanduser().resolve() if args.reference else None,
                models_dir,
                data_dir,
            )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError, wave.Error, UnicodeError) as exc:
        note(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())