from __future__ import annotations
import argparse
from pathlib import Path
from config import HARDWARE, Paths, log
from install import install

def main() -> int:
    p = argparse.ArgumentParser(prog="python main.py")
    p.add_argument("--models-dir", type=Path)
    p.add_argument("--data-dir", type=Path)
    p.add_argument("command", nargs="?", choices=("install", "ui"), default="install")
    args = p.parse_args()
    paths = Paths(args.models_dir, args.data_dir)
    log(f"main command={args.command} hardware={HARDWARE}", file=paths.log)
    if args.command == "install":
        install(args.models_dir, args.data_dir)
        return 0
    from talk import launch
    launch(paths)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
