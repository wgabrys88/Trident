from __future__ import annotations
import argparse
import traceback
from pathlib import Path
import config
from config import HARDWARE, Paths, ROOT, emit, git_sha, sidecar
from install import install

def main() -> int:
    p = argparse.ArgumentParser(prog="python main.py")
    p.add_argument("--models-dir", type=Path)
    p.add_argument("--data-dir", type=Path)
    p.add_argument("--family", choices=("nano", "turbo", "v3"), default="nano")
    p.add_argument("--language", default="en")
    p.add_argument("--console", action="store_true", help="also print JSON events to stdout; session files are always written")
    p.add_argument("--text")
    p.add_argument("--text-file", type=Path)
    p.add_argument("--interrupt-text")
    p.add_argument("--interrupt-file", type=Path)
    p.add_argument("--interrupt-after", type=float)
    p.add_argument("command", nargs="?", choices=("install", "talk", "tts"), default="install")
    args = p.parse_args()
    text_args = (args.text, args.text_file, args.interrupt_text, args.interrupt_file, args.interrupt_after)
    if args.command != "tts" and any(value is not None for value in text_args):
        p.error("TTS text arguments require command tts")
    primary = replacement = None
    if args.command == "tts":
        if (args.text is None) == (args.text_file is None):
            p.error("exactly one of --text and --text-file is required")
        if args.interrupt_text is not None and args.interrupt_file is not None:
            p.error("--interrupt-text and --interrupt-file are mutually exclusive")
        has_replacement = args.interrupt_text is not None or args.interrupt_file is not None
        if has_replacement != (args.interrupt_after is not None):
            p.error("interrupt content and --interrupt-after are required together")
        if args.interrupt_after is not None and args.interrupt_after < 0:
            p.error("--interrupt-after must be non-negative")
        primary = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8")
        replacement = args.interrupt_text if args.interrupt_text is not None else (args.interrupt_file.read_text(encoding="utf-8") if args.interrupt_file is not None else None)
        primary = primary.strip()
        replacement = replacement.strip() if replacement is not None else None
        if not primary:
            p.error("TTS input is empty")
        if replacement is not None and not replacement:
            p.error("TTS replacement is empty")
    family, language = (args.family, args.language) if args.command in ("talk", "tts") else ("all", "all")
    paths = Paths(args.models_dir, args.data_dir, args.command, family, language, args.console)
    emit("main", command=args.command, hardware=HARDWARE, family=family, language=language, trident_sha=git_sha(ROOT), console=config.CONSOLE)
    try:
        if args.command == "install":
            install(args.models_dir, args.data_dir)
            print(f"trident.done {paths.run_dir}", flush=True)
            return 0
        if args.command == "talk":
            from talk import launch
            launch(paths, args.family, args.language)
        else:
            from talk import launch_tts
            launch_tts(paths, args.family, args.language, primary, replacement, args.interrupt_after)
        print(f"trident.done {paths.run_dir}", flush=True)
        return 0
    except KeyboardInterrupt:
        emit("console.interrupt")
        print(f"trident.interrupt {paths.run_dir}", flush=True)
        return 130
    except Exception as error:
        emit("fail", type=type(error).__name__, error=str(error))
        text = traceback.format_exc()
        sidecar("fail").write_text(text, encoding="utf-8")
        print(f"trident.fail {type(error).__name__}: {error}\ntrident.run {paths.run_dir}", flush=True)
        if config.CONSOLE:
            print(text, flush=True)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
