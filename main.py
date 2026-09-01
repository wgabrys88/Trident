import argparse
import math
import sys
from pathlib import Path

from config import (
    ASR_RATE, CHATTERBOX, CHATTERBOX_REV, FLASH_ATTN, GGML, GGML_GIT, HARDWARE, Paths, ROOT,
    TTS_MODELS, TTS_PROFILES, TTS_RATE, VULKAN_ENV, ensure_venv, load_settings, wasapi_device,
)
import config
from install import install, tts_provenance
from journal import git_identity

ensure_venv(__file__)


def _read_utf8(path: Path, parser: argparse.ArgumentParser, label: str) -> str:
    if not path.is_file(): parser.error(f"{label} does not exist: {path}")
    try: return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError: parser.error(f"{label} is not valid UTF-8")


def _manifest(paths: Paths) -> dict:
    installed_tts = tts_provenance() if paths.command in ("talk", "tts") else None
    settings, audio = load_settings(paths.data_dir), {}
    for kind in {"talk": ("input", "output"), "tts": ("output",), "asr": ("input",)}.get(paths.command, ()):
        index, device, host = wasapi_device(kind)
        audio[kind] = {"device": device["name"], "index": index, "host_api": host["name"],
                       "channels": 1, "rate": ASR_RATE if kind == "input" else TTS_RATE, "auto_convert": True}
    manifest = {
        "created_at": paths.stamp, "command": paths.command, "family": paths.family,
        "language": paths.language, "voice": paths.voice,
        "repositories": {
            "trident": git_identity(ROOT),
            "chatterbox": {**git_identity(CHATTERBOX), "configured_pin": CHATTERBOX_REV},
            "ggml": {**git_identity(GGML), "configured_pin": GGML_GIT[1]},
        },
        "interpreter": {"path": sys.executable, "version": sys.version},
        "machine": {"hardware": HARDWARE, "gpu": config.GPU_NAME, "backend": config.TTS_BACKEND,
                    "codec": TTS_MODELS.get(paths.family, (None, None))[1], "vulkan_env": VULKAN_ENV,
                    "flash_attn": FLASH_ATTN},
        "runtime_knobs": TTS_PROFILES.get(paths.family, {}),
        "conversation": {k: settings.get(k) for k in ("candidate_silence_ms", "completion_threshold", "acoustic_context_seconds")},
        "audio": audio,
    }
    if installed_tts is not None: manifest["installed_tts"] = installed_tts
    return manifest


def main(command: str | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"python {command}.py" if command else "python main.py")
    parser.add_argument("--models-dir", type=Path); parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--family", choices=("nano", "turbo", "v3"), default="nano"); parser.add_argument("--language", default="en")
    parser.add_argument("--console", action="store_true")
    parser.add_argument("--text"); parser.add_argument("--text-file", type=Path)
    parser.add_argument("--interrupt-text"); parser.add_argument("--interrupt-file", type=Path); parser.add_argument("--interrupt-after", type=float)
    if command is None:
        parser.add_argument("command", nargs="?", choices=("install", "talk", "tts", "asr", "generation"), default="install")
    args = parser.parse_args()
    cmd = command or args.command
    primary = replacement = None
    flags = (args.text, args.text_file, args.interrupt_text, args.interrupt_file, args.interrupt_after)
    if cmd in ("tts", "generation"):
        if cmd == "generation" and any(v is not None for v in flags[2:]):
            parser.error("generation does not accept interrupt flags")
        if (args.text is None) == (args.text_file is None): parser.error("exactly one of --text and --text-file is required")
        if cmd == "tts":
            if args.interrupt_text is not None and args.interrupt_file is not None: parser.error("--interrupt-text and --interrupt-file are mutually exclusive")
            if (args.interrupt_text is not None or args.interrupt_file is not None) != (args.interrupt_after is not None): parser.error("interrupt content and --interrupt-after are required together")
            if args.interrupt_after is not None and (not math.isfinite(args.interrupt_after) or args.interrupt_after < 0): parser.error("--interrupt-after must be finite and non-negative")
        primary = (args.text if args.text is not None else _read_utf8(args.text_file, parser, "--text-file")).strip()
        replacement = args.interrupt_text if args.interrupt_text is not None else (_read_utf8(args.interrupt_file, parser, "--interrupt-file") if args.interrupt_file is not None else None)
        replacement = replacement.strip() if replacement is not None else None
        if not primary: parser.error("TTS input is empty" if cmd == "tts" else "generation input is empty")
        if replacement is not None and not replacement: parser.error("TTS replacement is empty")
    elif any(v is not None for v in flags):
        parser.error("TTS text and replacement flags require command tts" if cmd == "talk" else f"{cmd} does not accept streaming or TTS content flags")
    family, language = (args.family, args.language.strip().lower()) if cmd != "install" else ("all", "all")
    if HARDWARE == "irisxe" and cmd in ("talk", "tts") and family != "nano":
        parser.error("Iris Xe supports Nano English only")
    paths = Paths(args.models_dir, args.data_dir, cmd, family, language, args.console)
    try:
        paths.journal.write_manifest(_manifest(paths))
        paths.journal.emit("main", "start", command=cmd, hardware=HARDWARE, family=family, language=language)
        if cmd == "install":
            install(args.models_dir, args.data_dir, paths)
        else:
            __import__(cmd).launch(paths, args.family, language, primary, replacement, args.interrupt_after)
        paths.journal.emit("main", "completed", command=cmd); print(f"trident.done {paths.run_dir}", flush=True); return 0
    except KeyboardInterrupt:
        paths.journal.emit("main", "stopped", reason="ctrl+c"); print(f"trident.interrupt {paths.run_dir}", flush=True); return 130
    except Exception as error:
        paths.journal.failure("main", error); print(f"trident.fail {type(error).__name__}: {error}\ntrident.run {paths.run_dir}", flush=True); return 1
    finally:
        paths.close()


if __name__ == "__main__":
    raise SystemExit(main())
