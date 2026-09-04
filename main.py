import argparse
import sys
from pathlib import Path

from config import ASR_RATE, Paths, ROOT
from install import install
from journal import git_identity


_COMMANDS = {
    "install": "Download models and pin the parakeet ASR runtime.",
    "asr": "Transcribe with Parakeet from WAV files.",
}

_LOOP = """Transcribe WAV files with Parakeet:

  python asr.py --wav data/runs/<run-id>/speaker-16k.wav
"""


def _manifest(paths: Paths) -> dict:
    manifest = {
        "created_at": paths.stamp, "command": paths.command,
        "repositories": {
            "trident": git_identity(ROOT),
        },
        "interpreter": {"path": sys.executable, "version": sys.version},
        "machine": {"hardware": "bare-metal", "gpu": None, "backend": None,
                    "codec": None, "vulkan_env": {}},
        "runtime_knobs": {},
        "conversation": {},
        "audio": {},
    }
    return manifest


def _parser(command: str | None) -> argparse.ArgumentParser:
    if command:
        description, epilog = _COMMANDS[command], _LOOP if command == "asr" else ""
    else:
        description = "Trident spoken-conversation runtime. ASR and install are independent commands."
        epilog = "commands:\n" + "\n".join(f"  {name:11} {text}" for name, text in _COMMANDS.items()) + "\n\n" + _LOOP
    parser = argparse.ArgumentParser(prog=f"python {command}.py" if command else "python main.py",
                                     description=description, epilog=epilog.strip(),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    show = command is None
    parser.add_argument("--models-dir", type=Path, help="Model directory (default: ./models)")
    parser.add_argument("--data-dir", type=Path, help="Data directory for runs/<run-id> (default: ./data)")
    parser.add_argument("--console", action="store_true", help="Print each journal event as JSON on stdout")
    if show or command == "asr":
        parser.add_argument("--wav", action="append", type=Path, dest="wavs", metavar="WAV",
                            help="Wav for Parakeet (repeatable). Prefer a TTS run's speaker-16k.wav")
    if command is None:
        parser.add_argument("command", nargs="?", choices=tuple(_COMMANDS), default="install",
                            help="Command to run (default: install). Same as python <command>.py")
    return parser


def main(command: str | None = None) -> int:
    parser = _parser(command)
    args = parser.parse_args()
    cmd = command or args.command
    wavs = tuple(getattr(args, "wavs", None) or ())
    if cmd == "asr":
        missing = [str(path) for path in wavs if not path.is_file()]
        if missing: parser.error("wav does not exist: " + ", ".join(missing))
    elif wavs:
        parser.error("--wav requires command asr")
    if cmd in ("tts", "generation"):
        parser.error(f"{cmd} is not available in the simplified build")
    paths = Paths(args.models_dir, args.data_dir, cmd, args.console, wavs)
    try:
        paths.journal.write_manifest(_manifest(paths))
        paths.journal.emit("main", "start", command=cmd, hardware="bare-metal")
        if cmd == "install":
            install(args.models_dir, args.data_dir, paths)
        else:
            __import__(cmd).launch(paths)
        paths.journal.emit("main", "completed", command=cmd); print(f"trident.done {paths.run_dir}", flush=True); return 0
    except KeyboardInterrupt:
        paths.journal.emit("main", "stopped", reason="ctrl+c"); print(f"trident.interrupt {paths.run_dir}", flush=True); return 130
    except Exception as error:
        paths.journal.failure("main", error); print(f"trident.fail {type(error).__name__}: {error}\ntrident.run {paths.run_dir}", flush=True); return 1
    finally:
        paths.close()


if __name__ == "__main__":
    raise SystemExit(main())