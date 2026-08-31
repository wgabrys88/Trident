from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import wave
from pathlib import Path

ROOT_BOOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT_BOOT / ".venv" / "Scripts" / "python.exe"
if sys.platform.startswith("win") and VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

import config
from config import (
    CHATTERBOX, CHATTERBOX_REV, GEMMA_FILE, GGML, GGML_GIT, HARDWARE, PARAKEET_FILE,
    Paths, ROOT, RUNTIMES, SMART_TURN_FILE, TTS_MODELS, TTS_PROFILES, find_exe, load_settings, voice_wav,
)
from install import install
from journal import file_identity, git_identity


def _read_utf8(path: Path, parser: argparse.ArgumentParser, label: str) -> str:
    if not path.is_file(): parser.error(f"{label} does not exist: {path}")
    try: return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError: parser.error(f"{label} is not valid UTF-8")


def _validate_wav(path: Path, parser: argparse.ArgumentParser) -> None:
    if not path.is_file(): parser.error(f"input file does not exist: {path}")
    try:
        with wave.open(str(path), "rb") as src:
            if (src.getnchannels(), src.getsampwidth(), src.getframerate(), src.getcomptype()) != (1, 2, 24000, "NONE"):
                parser.error("--input-file must be finalized 24 kHz mono PCM16 WAV")
            nframes = src.getnframes()
            if nframes <= 0: parser.error("--input-file is empty")
            nonzero = False; total = 0
            while raw := src.readframes(1 << 16):
                total += len(raw) // 2
                if not nonzero and any(raw): nonzero = True
            if total != nframes: parser.error("--input-file WAV data is truncated or not finalized")
            if not nonzero: parser.error("--input-file contains no audio content")
    except (wave.Error, EOFError):
        parser.error("--input-file is not a finalized PCM WAV")


def _machine_identity() -> dict:
    if not sys.platform.startswith("win"): return {"hardware": HARDWARE}
    script = "$g=Get-CimInstance Win32_VideoController|Select-Object Name,DriverVersion,AdapterRAM;$c=Get-CimInstance Win32_ComputerSystem|Select-Object TotalPhysicalMemory;$p=Get-CimInstance Win32_Processor|Select-Object Name;@{gpu=$g;computer=$c;cpu=$p}|ConvertTo-Json -Compress -Depth 4"
    try:
        data = json.loads(subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", script], text=True, encoding="utf-8", errors="replace", timeout=20))
    except Exception as error:
        data = {"probe_error": str(error)}
    data["hardware_profile"] = HARDWARE
    data["gpu_name"] = config.GPU_NAME
    data["tts_backend"] = config.TTS_BACKEND
    data["nvidia_compute_capability"] = config.CUDA_ARCH
    data["vulkan_env"] = dict(config.VULKAN_ENV)
    if HARDWARE == "pascal":
        try:
            data["nvidia_smi"] = subprocess.check_output(["nvidia-smi", "--query-gpu=name,driver_version,compute_cap,memory.total,memory.free", "--format=csv,noheader,nounits"], text=True, encoding="utf-8", errors="replace", timeout=20).splitlines()
        except Exception as error:
            data["nvidia_smi"] = f"unavailable: {error}"
    try:
        data["vulkan_summary"] = subprocess.check_output(["vulkaninfo.exe", "--summary"], text=True, encoding="utf-8", errors="replace", timeout=20)[-12000:]
    except Exception:
        data["vulkan_summary"] = "unavailable; native allocation events are authoritative"
    return data


def _manifest(paths: Paths, input_file: Path | None, output_file: Path | None) -> dict:
    settings = load_settings(paths.data_dir)
    manifest = {
        "created_at": paths.stamp,
        "command": paths.command,
        "source_type": paths.source,
        "sink_type": paths.sink,
        "family": paths.family,
        "language": paths.language,
        "voice": paths.voice,
        "repositories": {
            "trident": git_identity(ROOT),
            "chatterbox": {**git_identity(CHATTERBOX), "configured_pin": CHATTERBOX_REV},
            "ggml": {**git_identity(GGML), "configured_pin": GGML_GIT[1]},
        },
        "interpreter": {"path": sys.executable, "version": sys.version},
        "machine": _machine_identity(),
        "runtime_knobs": TTS_PROFILES.get(paths.family, {}) if paths.family in TTS_PROFILES else {},
        "conversation": {k: settings.get(k) for k in ("candidate_silence_ms", "completion_threshold", "acoustic_context_seconds")},
        "executables": {},
        "models": {},
        "audio": {},
    }
    if paths.source == "wav" and input_file is not None:
        manifest["audio"]["source"] = {"type": "wav", "format": "pcm16-mono", "rate": 24000, **file_identity(input_file)}
    elif paths.source == "microphone":
        index, device, host = config.wasapi_device("input"); native_rate = config.wasapi_native_rate(device)
        manifest["audio"]["source"] = {"type": "physical", "selection": "stable-host-api/name", "host_api": host.get("name"),
            "device": device.get("name"), "resolved_index": index, "native_rate": native_rate, "capture_rate": 16000,
            "advertised_low_latency": device.get("default_low_input_latency"), "default_samplerate": device.get("default_samplerate"), "mode": "wasapi-shared-low-latency"}
    if paths.sink == "wav" and output_file is not None:
        manifest["audio"]["sink"] = {"type": "wav", "path": str(output_file), "format": "pcm16-mono", "rate": 24000}
    elif paths.sink == "speaker":
        index, device, host = config.wasapi_device("output"); native_rate = config.wasapi_native_rate(device)
        manifest["audio"]["sink"] = {"type": "physical", "selection": "stable-host-api/name", "host_api": host.get("name"),
            "device": device.get("name"), "resolved_index": index, "native_rate": native_rate, "render_rate": 24000,
            "advertised_low_latency": device.get("default_low_output_latency"), "default_samplerate": device.get("default_samplerate"), "mode": "wasapi-shared-low-latency"}
    for role, folder, name in (("chatterbox", "tts", "trident-tts-server.exe"), ("parakeet", "parakeet", "parakeet-server.exe"), ("gemma", "gemma", "llama-server.exe")):
        exe = find_exe(RUNTIMES / folder, name)
        if exe is not None: manifest["executables"][role] = file_identity(exe)
    models: list[tuple[str, Path]] = []
    if paths.family in TTS_MODELS:
        t3, s3 = TTS_MODELS[paths.family]; models.extend((("t3", paths.models_dir / t3), ("s3gen", paths.models_dir / s3)))
        try: models.append(("voice", voice_wav(paths.data_dir, paths.voice)))
        except RuntimeError: pass
    if paths.command == "talk":
        models.extend((("parakeet", paths.models_dir / PARAKEET_FILE), ("gemma", paths.models_dir / GEMMA_FILE), ("smart_turn", paths.models_dir / SMART_TURN_FILE)))
    manifest["models"] = {name: file_identity(path) for name, path in models}
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="python main.py")
    parser.add_argument("--models-dir", type=Path); parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--family", choices=("nano", "turbo", "v3"), default="nano"); parser.add_argument("--language", default="en")
    parser.add_argument("--console", action="store_true")
    parser.add_argument("--input-file", type=Path); parser.add_argument("--output-file", type=Path)
    parser.add_argument("--text"); parser.add_argument("--text-file", type=Path)
    parser.add_argument("--interrupt-text"); parser.add_argument("--interrupt-file", type=Path); parser.add_argument("--interrupt-after", type=float)
    parser.add_argument("command", nargs="?", choices=("install", "talk", "tts"), default="install")
    args = parser.parse_args()

    if args.command == "install":
        if any(v is not None for v in (args.input_file, args.output_file, args.text, args.text_file, args.interrupt_text, args.interrupt_file, args.interrupt_after)):
            parser.error("install does not accept streaming or TTS content flags")
    elif args.command == "talk":
        if any(v is not None for v in (args.text, args.text_file, args.interrupt_text, args.interrupt_file, args.interrupt_after)):
            parser.error("TTS text and replacement flags require command tts")
        if args.input_file is not None: _validate_wav(args.input_file, parser)
    else:
        if args.input_file is not None: parser.error("--input-file is supported only by talk")
        if (args.text is None) == (args.text_file is None): parser.error("exactly one of --text and --text-file is required")
        if args.interrupt_text is not None and args.interrupt_file is not None: parser.error("--interrupt-text and --interrupt-file are mutually exclusive")
        replacement_given = args.interrupt_text is not None or args.interrupt_file is not None
        if replacement_given != (args.interrupt_after is not None): parser.error("interrupt content and --interrupt-after are required together")
        if args.interrupt_after is not None and (not math.isfinite(args.interrupt_after) or args.interrupt_after < 0): parser.error("--interrupt-after must be finite and non-negative")

    if args.output_file is not None:
        args.output_file = args.output_file.expanduser().resolve()
        if args.output_file.exists(): parser.error("--output-file target already exists")
        if not args.output_file.parent.is_dir(): parser.error("--output-file parent directory does not exist")
    if args.input_file is not None:
        args.input_file = args.input_file.expanduser().resolve()
        if args.output_file is not None and args.input_file == args.output_file: parser.error("input and output paths must differ")

    primary = replacement = None
    if args.command == "tts":
        primary = args.text if args.text is not None else _read_utf8(args.text_file, parser, "--text-file")
        replacement = args.interrupt_text if args.interrupt_text is not None else (_read_utf8(args.interrupt_file, parser, "--interrupt-file") if args.interrupt_file is not None else None)
        primary = primary.strip(); replacement = replacement.strip() if replacement is not None else None
        if not primary: parser.error("TTS input is empty")
        if replacement is not None and not replacement: parser.error("TTS replacement is empty")

    family, language = (args.family, args.language.strip().lower()) if args.command in ("talk", "tts") else ("all", "all")
    source = "wav" if args.input_file else ("microphone" if args.command == "talk" else "text" if args.command == "tts" else "none")
    sink = "wav" if args.output_file else ("speaker" if args.command in ("talk", "tts") else "none")
    paths = Paths(args.models_dir, args.data_dir, args.command, family, language, args.console, source, sink)
    try:
        paths.journal.write_manifest(_manifest(paths, args.input_file, args.output_file))
        paths.journal.emit("main", "start", command=args.command, hardware=HARDWARE, family=family, language=language)
        if args.command == "install":
            install(args.models_dir, args.data_dir, paths)
        elif args.command == "talk":
            from talk import launch
            launch(paths, args.family, language, args.input_file, args.output_file)
        else:
            from talk import launch_tts
            launch_tts(paths, args.family, language, primary, replacement, args.interrupt_after, args.output_file)
        paths.journal.emit("main", "completed", command=args.command); print(f"trident.done {paths.run_dir}", flush=True); return 0
    except KeyboardInterrupt:
        paths.journal.emit("main", "stopped", reason="ctrl+c"); print(f"trident.interrupt {paths.run_dir}", flush=True); return 130
    except Exception as error:
        paths.journal.failure("main", error); print(f"trident.fail {type(error).__name__}: {error}\ntrident.run {paths.run_dir}", flush=True); return 1
    finally:
        paths.close()


if __name__ == "__main__":
    raise SystemExit(main())
