from __future__ import annotations

import argparse
from pathlib import Path

from config import HARDWARE, Paths, log
from install import install
from runtime import start_chatterbox, start_gemma, start_parakeet, status, stop_all


def boot(paths: Paths, reference: Path | None = None) -> None:
    log("boot begin")
    if reference is None:
        from config import load_settings, voice_wav
        settings = load_settings(paths.data_dir)
        reference = voice_wav(paths.data_dir, settings["tts_voice"])
    start_parakeet(paths.models_dir)
    start_gemma(paths.models_dir)
    start_chatterbox(paths.models_dir, reference)
    log("residents ready family=nano language=en")


def main() -> int:
    p = argparse.ArgumentParser(prog="python main.py")
    p.add_argument("--models-dir", type=Path)
    p.add_argument("--data-dir", type=Path)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("install")
    sub.add_parser("ui")
    r = sub.add_parser("resident")
    r.add_argument("action", choices=("status", "stop"))
    args = p.parse_args()

    paths = Paths(args.models_dir, args.data_dir)
    log(f"main command={args.command} hardware={HARDWARE}", file=paths.log)

    if args.command == "install":
        install(args.models_dir, args.data_dir)
        return 0
    if args.command == "ui":
        boot(paths)
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
