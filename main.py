from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from config import (
    ASR_RUNTIME, ASR_RATE, TTS_RATE, REFERENCE_MIN_SECONDS, BRAIN_MODEL, BRAIN_RUNTIME,
    BRAIN_GENERATION, BRAIN_THINKING, BRAIN_SYSTEM, FAMILIES, SHARED_MODELS, LANGUAGES, Paths, default_family,
)
from installer import (
    install, runtime_executable, runtime_tts, models_for, require_model,
    validate_wav, write_text_atomic, note, set_log,
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
    note("tts: " + " ".join(command))
    subprocess.run(command, cwd=exe.parent, stdout=sys.stderr, stderr=sys.stderr, check=True)
    validate_wav(output, TTS_RATE, channels=1)


def resolve_language(family: dict, language: str | None) -> str:
    code = language or family["DEFAULT_REPLY_LANGUAGE"]
    if code not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {code!r} not supported by {family['name']}; choose from {', '.join(family['TTS_LANGUAGES'])}")
    return code


def extra_copy(src: Path, dest: Path | None) -> None:
    if dest is None or src.resolve() == dest.resolve():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def write_meta(paths: Paths, rows: dict[str, str]) -> None:
    body = "".join(f"{key}={value}\n" for key, value in rows.items())
    write_text_atomic(paths.run_dir / "meta.txt", body)


def start_run(command: str, args) -> Paths:
    paths = Paths(args.models_dir, args.data_dir, command)
    set_log(paths.log)
    note(f"run dir {paths.run_dir}")
    return paths


def run_asr(input_wav: Path, output: Path | None, paths: Paths) -> None:
    validate_wav(input_wav, ASR_RATE, channels=1)
    shutil.copy2(input_wav, paths.run_dir / "input.wav")
    text = transcribe(runtime_executable("parakeet"), require_model(SHARED_MODELS["parakeet"], paths.models_dir), input_wav, paths)
    extra_copy(paths.transcript, output)
    write_meta(paths, {"command": "asr", "input": str(input_wav), "transcript": str(paths.transcript)})
    print(text)
    print(f"Run: {paths.run_dir}")


def run_brain(prompt: Path, output: Path | None, language: str, paths: Paths) -> None:
    if language not in LANGUAGES:
        raise RuntimeError(f"language {language!r} not supported; choose from {', '.join(LANGUAGES)}")
    shutil.copy2(prompt, paths.run_dir / "prompt.txt")
    text = brain(
        runtime_executable("gemma"),
        require_model(SHARED_MODELS[BRAIN_MODEL], paths.models_dir),
        language,
        LANGUAGES[language],
        prompt,
        paths,
    )
    extra_copy(paths.answer, output)
    write_meta(paths, {"command": "brain", "language": language, "prompt": str(prompt), "answer": str(paths.answer)})
    print(text)
    print(f"Run: {paths.run_dir}")


def run_tts(text_file: Path, reference: Path, output: Path | None, family_name: str, language: str | None, paths: Paths) -> None:
    family = FAMILIES[family_name]
    language = resolve_language(family, language)
    validate_wav(reference, minimum_seconds=REFERENCE_MIN_SECONDS)
    models = models_for(family_name)
    shutil.copy2(text_file, paths.run_dir / "text.txt")
    synthesize(
        runtime_tts(family_name),
        require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir),
        reference,
        paths.output,
        language,
        family,
        text_file,
    )
    extra_copy(paths.output, output)
    write_meta(paths, {
        "command": "tts", "family": family_name, "language": language,
        "text": str(text_file), "reference": str(reference), "output": str(paths.output),
    })
    print(f"Output: {paths.output}")
    print(f"Run: {paths.run_dir}")


def run_pipeline(input_wav: Path, output: Path | None, family_name: str, language: str | None, reference: Path, paths: Paths) -> None:
    family = FAMILIES[family_name]
    language = resolve_language(family, language)
    validate_wav(input_wav, ASR_RATE, channels=1)
    validate_wav(reference, minimum_seconds=REFERENCE_MIN_SECONDS)
    models = models_for(family_name)
    shutil.copy2(input_wav, paths.run_dir / "input.wav")
    transcript = transcribe(runtime_executable("parakeet"), require_model(models["parakeet"], paths.models_dir), input_wav, paths)
    answer = brain(
        runtime_executable("gemma"),
        require_model(models[BRAIN_MODEL], paths.models_dir),
        language,
        family["TTS_LANGUAGES"][language],
        paths.transcript,
        paths,
    )
    synthesize(
        runtime_tts(family_name),
        require_model(models["chatterbox-t3"], paths.models_dir),
        require_model(models["chatterbox-codec"], paths.models_dir),
        reference,
        paths.output,
        language,
        family,
        paths.answer,
    )
    extra_copy(paths.output, output)
    write_meta(paths, {
        "command": "run", "family": family_name, "language": language,
        "input": str(input_wav), "reference": str(reference), "output": str(paths.output),
    })
    print(f"Transcript: {transcript}")
    print(f"Answer: {answer}")
    print(f"Output: {paths.output}")
    print(f"Run: {paths.run_dir}")


def build_parser() -> argparse.ArgumentParser:
    families = tuple(FAMILIES)
    p = argparse.ArgumentParser(
        prog="python main.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Windows listen-think-speak box. Parakeet hears, Gemma answers, Chatterbox speaks.",
        epilog=(
            "commands:\n"
            "  install [--family v3|turbo|nano]   tools, one family, models, celebrity voices\n"
            "  asr IN.wav [-o text]               16 kHz mono PCM16 -> transcript\n"
            "  brain IN.txt [--language] [-o text]  spoken reply\n"
            "  tts IN.txt [--family] [--language] [-r wav] [-o wav]  24 kHz speech\n"
            "  run IN.wav [--family] [--language] [-r wav] [-o wav]  listen, think, speak\n"
            "\n"
            "asr/brain/tts/run write a new timestamped folder and never overwrite an older run.\n"
            "-o is an extra copy. -r defaults to the Trump celebrity voice.\n"
            "\n"
            "  python main.py install --family v3\n"
            "  python main.py run rec.wav --language pl\n"
            "  python main.py tts line.txt --family turbo\n"
        ),
    )
    p.add_argument("--models-dir", type=Path, help="models directory")
    p.add_argument("--data-dir", type=Path, help="data directory")
    sub = p.add_subparsers(dest="command")

    install_cmd = sub.add_parser("install", help="tools, one family, models, celebrity voices")
    install_cmd.add_argument("--family", choices=families, default=default_family())

    asr_cmd = sub.add_parser("asr", help="transcribe 16 kHz mono WAV")
    asr_cmd.add_argument("input")
    asr_cmd.add_argument("-o", "--output")

    brain_cmd = sub.add_parser("brain", help="spoken reply to a text file")
    brain_cmd.add_argument("input")
    brain_cmd.add_argument("-o", "--output")
    brain_cmd.add_argument("--language", default="en")

    tts_cmd = sub.add_parser("tts", help="speak a text file")
    tts_cmd.add_argument("input")
    tts_cmd.add_argument("-r", "--reference")
    tts_cmd.add_argument("-o", "--output")
    tts_cmd.add_argument("--family", choices=families, default=default_family())
    tts_cmd.add_argument("--language")

    run_cmd = sub.add_parser("run", help="listen, think, speak")
    run_cmd.add_argument("input")
    run_cmd.add_argument("-o", "--output")
    run_cmd.add_argument("--family", choices=families, default=default_family())
    run_cmd.add_argument("--language")
    run_cmd.add_argument("-r", "--reference")
    return p


def main() -> int:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        return 2
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 2
    try:
        if args.command == "install":
            install(args.family, (args.models_dir.resolve() if args.models_dir else None), (args.data_dir.resolve() if args.data_dir else None))
            return 0
        paths = start_run(args.command, args)
        if args.command == "asr":
            run_asr(Path(args.input).expanduser().resolve(), Path(args.output).expanduser().resolve() if args.output else None, paths)
        elif args.command == "brain":
            run_brain(Path(args.input).expanduser().resolve(), Path(args.output).expanduser().resolve() if args.output else None, args.language, paths)
        elif args.command == "tts":
            run_tts(
                Path(args.input).expanduser().resolve(),
                Path(args.reference).expanduser().resolve() if args.reference else paths.reference,
                Path(args.output).expanduser().resolve() if args.output else None,
                args.family,
                args.language,
                paths,
            )
        else:
            run_pipeline(
                Path(args.input).expanduser().resolve(),
                Path(args.output).expanduser().resolve() if args.output else None,
                args.family,
                args.language,
                Path(args.reference).expanduser().resolve() if args.reference else paths.reference,
                paths,
            )
        return 0
    except Exception as exc:
        note(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
