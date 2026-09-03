import argparse
import sys
from pathlib import Path

from config import CHATTERBOX, CHATTERBOX_REV, GGML, GGML_GIT, HARDWARE, Paths, ROOT, TTS_RATE, ensure_venv
import config
from install import install, tts_provenance
from journal import git_identity

ensure_venv(__file__)

_COMMANDS = {
    "install": "Download models, pin Chatterbox, and build the native TTS server.",
    "tts": "Cook PCM from --text via the pinned native TTS server; writes out.wav and out-spec.png.",
}


def _read_utf8(path: Path, parser: argparse.ArgumentParser, label: str) -> str:
    if not path.is_file():
        parser.error(f"{label} does not exist: {path}")
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        parser.error(f"{label} is not valid UTF-8")


def _manifest(paths: Paths) -> dict:
    installed_tts = tts_provenance() if paths.command == "tts" else None
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
                    "codec": config.TTS_MODELS[paths.family][1], "vulkan_env": config.VULKAN_ENV,
                    "flash_attn": config.FLASH_ATTN, "tts_rate": TTS_RATE},
    }
    if installed_tts is not None:
        manifest["installed_tts"] = installed_tts
    return manifest


def _parser(command: str | None) -> argparse.ArgumentParser:
    if command:
        description = _COMMANDS[command]
        epilog = ""
    else:
        description = "Trident TTS-only runtime. Two commands: install and tts."
        epilog = "commands:\n" + "\n".join(f"  {name:11} {text}" for name, text in _COMMANDS.items())
    parser = argparse.ArgumentParser(prog=f"python {command}.py" if command else "python main.py",
                                     description=description, epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    show = command is None
    parser.add_argument("--models-dir", type=Path, help="Model directory (default: ./models)")
    parser.add_argument("--data-dir", type=Path, help="Data directory for voices and data/runs/<run-id> (default: ./data)")
    parser.add_argument("--family", choices=("nano",), default="nano",
                        help="TTS family (default: nano). TTS-only mode supports nano only.")
    parser.add_argument("--language", default="en", help="Spoken language code (default: en). TTS-only mode supports English only.")
    parser.add_argument("--console", action="store_true", help="Print each journal event as JSON on stdout")
    if show or command == "tts":
        parser.add_argument("--text", help="Text to cook. Mutually exclusive with --text-file")
        parser.add_argument("--text-file", type=Path, metavar="PATH", help="UTF-8 file of text to cook. Mutually exclusive with --text")
    if command is None:
        parser.add_argument("command", nargs="?", choices=tuple(_COMMANDS), default="install",
                            help="Command to run (default: install). Same as python <command>.py")
    return parser


def main(command: str | None = None) -> int:
    parser = _parser(command)
    args = parser.parse_args()
    cmd = command or args.command
    if cmd not in _COMMANDS:
        parser.error(f"TTS-only mode supports: {', '.join(_COMMANDS)} (got {cmd!r})")
    text = getattr(args, "text", None)
    text_file = getattr(args, "text_file", None)
    if cmd == "tts":
        if (text is None) == (text_file is None):
            parser.error("exactly one of --text and --text-file is required")
        primary = (text if text is not None else _read_utf8(text_file, parser, "--text-file")).strip()
        if not primary:
            parser.error("TTS input is empty")
    else:
        primary = None
    family, language = ("nano", "en") if cmd != "install" else ("nano", "en")
    paths = Paths(args.models_dir, args.data_dir, cmd, family, language, args.console)
    try:
        paths.journal.write_manifest(_manifest(paths))
        paths.journal.emit("main", "start", command=cmd, hardware=HARDWARE, family=family, language=language)
        if cmd == "install":
            install(args.models_dir, args.data_dir, paths)
        else:
            __import__(cmd).launch(paths, family, language, primary, None, None)
        paths.journal.emit("main", "completed", command=cmd)
        print(f"trident.done {paths.run_dir}", flush=True)
        return 0
    except KeyboardInterrupt:
        paths.journal.emit("main", "stopped", reason="ctrl+c")
        print(f"trident.interrupt {paths.run_dir}", flush=True)
        return 130
    except Exception as error:
        paths.journal.failure("main", error)
        print(f"trident.fail {type(error).__name__}: {error}\ntrident.run {paths.run_dir}", flush=True)
        return 1
    finally:
        paths.close()


if __name__ == "__main__":
    raise SystemExit(main())
