from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

ROOT_BOOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT_BOOT / ".venv" / "Scripts" / "python.exe"
if sys.platform.startswith("win") and VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

import config
from config import (
    CABLE_CHANNELS, CABLE_RATE, CHATTERBOX, CHATTERBOX_REV, FLASH_ATTN,
    GGML, GGML_GIT, HARDWARE, Paths, ROOT, TTS_MODELS, TTS_PROFILES, VULKAN_ENV,
    cable_device, load_settings,
)
from install import install
from journal import git_identity


def _read_utf8(path: Path, parser: argparse.ArgumentParser, label: str) -> str:
    if not path.is_file(): parser.error(f"{label} does not exist: {path}")
    try: return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError: parser.error(f"{label} is not valid UTF-8")


def _manifest(paths: Paths) -> dict:
    settings = load_settings(paths.data_dir)
    audio = {}
    for kind in (("input",) if paths.command == "talk" else ()) + (("output",) if paths.command in ("talk", "tts") else ()):
        index, device, host = cable_device(kind)
        audio[kind] = {"device": device["name"], "index": index, "host_api": host["name"],
                       "channels": CABLE_CHANNELS, "rate": CABLE_RATE, "auto_convert": True}
    manifest = {
        "created_at": paths.stamp,
        "command": paths.command,
        "family": paths.family,
        "language": paths.language,
        "voice": paths.voice,
        "repositories": {
            "trident": git_identity(ROOT),
            "chatterbox": {**git_identity(CHATTERBOX), "configured_pin": CHATTERBOX_REV},
            "ggml": {**git_identity(GGML), "configured_pin": GGML_GIT[1]},
        },
        "interpreter": {"path": sys.executable, "version": sys.version},
        "machine": {"hardware": HARDWARE, "gpu": config.GPU_NAME, "backend": config.TTS_BACKEND,
                    "codec": TTS_MODELS.get(paths.family, (None, None))[1], "vulkan_env": VULKAN_ENV,
                    "flash_attn": FLASH_ATTN},
        "runtime_knobs": TTS_PROFILES.get(paths.family, {}) if paths.family in TTS_PROFILES else {},
        "conversation": {k: settings.get(k) for k in ("candidate_silence_ms", "completion_threshold", "acoustic_context_seconds")},
        "audio": audio,
    }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="python main.py")
    parser.add_argument("--models-dir", type=Path); parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--family", choices=("nano", "turbo", "v3"), default="nano"); parser.add_argument("--language", default="en")
    parser.add_argument("--console", action="store_true")
    parser.add_argument("--text"); parser.add_argument("--text-file", type=Path)
    parser.add_argument("--interrupt-text"); parser.add_argument("--interrupt-file", type=Path); parser.add_argument("--interrupt-after", type=float)
    parser.add_argument("command", nargs="?", choices=("install", "talk", "tts"), default="install")
    args = parser.parse_args()

    if args.command == "install":
        if any(v is not None for v in (args.text, args.text_file, args.interrupt_text, args.interrupt_file, args.interrupt_after)):
            parser.error("install does not accept streaming or TTS content flags")
    elif args.command == "talk":
        if any(v is not None for v in (args.text, args.text_file, args.interrupt_text, args.interrupt_file, args.interrupt_after)):
            parser.error("TTS text and replacement flags require command tts")
    else:
        if (args.text is None) == (args.text_file is None): parser.error("exactly one of --text and --text-file is required")
        if args.interrupt_text is not None and args.interrupt_file is not None: parser.error("--interrupt-text and --interrupt-file are mutually exclusive")
        replacement_given = args.interrupt_text is not None or args.interrupt_file is not None
        if replacement_given != (args.interrupt_after is not None): parser.error("interrupt content and --interrupt-after are required together")
        if args.interrupt_after is not None and (not math.isfinite(args.interrupt_after) or args.interrupt_after < 0): parser.error("--interrupt-after must be finite and non-negative")

    primary = replacement = None
    if args.command == "tts":
        primary = args.text if args.text is not None else _read_utf8(args.text_file, parser, "--text-file")
        replacement = args.interrupt_text if args.interrupt_text is not None else (_read_utf8(args.interrupt_file, parser, "--interrupt-file") if args.interrupt_file is not None else None)
        primary = primary.strip(); replacement = replacement.strip() if replacement is not None else None
        if not primary: parser.error("TTS input is empty")
        if replacement is not None and not replacement: parser.error("TTS replacement is empty")

    family, language = (args.family, args.language.strip().lower()) if args.command in ("talk", "tts") else ("all", "all")
    if HARDWARE == "irisxe" and args.command in ("talk", "tts") and family != "nano":
        parser.error("Iris Xe supports Nano English only")
    paths = Paths(args.models_dir, args.data_dir, args.command, family, language, args.console)
    try:
        paths.journal.write_manifest(_manifest(paths))
        paths.journal.emit("main", "start", command=args.command, hardware=HARDWARE, family=family, language=language)
        if args.command == "install":
            install(args.models_dir, args.data_dir, paths)
        elif args.command == "talk":
            from talk import launch
            launch(paths, args.family, language)
        else:
            from talk import launch
            launch(paths, args.family, language, primary, replacement, args.interrupt_after)
        paths.journal.emit("main", "completed", command=args.command); print(f"trident.done {paths.run_dir}", flush=True); return 0
    except KeyboardInterrupt:
        paths.journal.emit("main", "stopped", reason="ctrl+c"); print(f"trident.interrupt {paths.run_dir}", flush=True); return 130
    except Exception as error:
        paths.journal.failure("main", error); print(f"trident.fail {type(error).__name__}: {error}\ntrident.run {paths.run_dir}", flush=True); return 1
    finally:
        paths.close()


if __name__ == "__main__":
    raise SystemExit(main())
