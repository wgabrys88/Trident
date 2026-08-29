from __future__ import annotations
import argparse
from pathlib import Path
from config import HARDWARE, Paths, emit
from install import install

def main() -> int:
    p = argparse.ArgumentParser(prog="python main.py")
    p.add_argument("--models-dir", type=Path)
    p.add_argument("--data-dir", type=Path)
    p.add_argument("--family", choices=("nano", "turbo", "v3"), default="nano")
    p.add_argument("--language", default="en")
    p.add_argument("command", nargs="?", choices=("install", "talk"), default="install")
    args = p.parse_args()
    paths = Paths(args.models_dir, args.data_dir)
    emit("main", command=args.command, hardware=HARDWARE)
    if args.command == "install":
        install(args.models_dir, args.data_dir)
        return 0
    from talk import launch
    launch(paths, args.family, args.language)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
