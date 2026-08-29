from __future__ import annotations

import argparse
from pathlib import Path

from config import HARDWARE, Paths, log
from install import install
from runtime import boot, status, stop_all


def main() -> int:
    p = argparse.ArgumentParser(prog="python main.py")
    p.add_argument("--models-dir", type=Path)
    p.add_argument("--data-dir", type=Path)
    sub = p.add_subparsers(dest="command")
    sub.add_parser("install")
    sub.add_parser("ui")
    r = sub.add_parser("resident")
    r.add_argument("action", choices=("status", "stop"))
    args = p.parse_args()

    paths = Paths(args.models_dir, args.data_dir)
    command = args.command or "install"
    log(f"main command={command} hardware={HARDWARE}", file=paths.log)

    if command == "install":
        install(args.models_dir, args.data_dir)
        return 0
    if command == "ui":
        boot(paths.models_dir, paths.data_dir)
        print(status())
        from talk import launch
        launch(paths)
        return 0
    if args.action == "status":
        print(status())
    else:
        stop_all()
        print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
