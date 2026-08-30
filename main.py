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
    p.add_argument("command", nargs="?", choices=("install", "talk"), default="install")
    args = p.parse_args()
    family, language = (args.family, args.language) if args.command == "talk" else ("all", "all")
    paths = Paths(args.models_dir, args.data_dir, args.command, family, language, args.console)
    emit("main", command=args.command, hardware=HARDWARE, family=family, language=language, trident_sha=git_sha(ROOT), console=config.CONSOLE)
    try:
        if args.command == "install":
            install(args.models_dir, args.data_dir)
            print(f"trident.done {paths.run_dir}", flush=True)
            return 0
        from talk import launch
        launch(paths, args.family, args.language)
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
