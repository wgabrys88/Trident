from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from config import HARDWARE, Paths, log, set_log, voice_wav
from install import install
from runtime import pcm24, require_alive, start_chatterbox, start_gemma, start_parakeet, status, stop_all


def boot(models_dir=None, data_dir=None, voice: str | None = None) -> Path:
    paths = Paths(models_dir, data_dir)
    from config import load_settings
    settings = load_settings(paths.data_dir)
    ref: list[Path] = []
    errors: list[BaseException] = []

    def run(name, fn):
        try:
            fn()
        except BaseException as exc:
            errors.append(exc)
            log(f"boot failed {name}: {exc}")

    workers = [
        threading.Thread(target=run, args=("reference", lambda: ref.append(pcm24(voice_wav(paths.data_dir, voice or settings["tts_voice"]), paths.data_dir / "prepared"))), name="boot-ref"),
        threading.Thread(target=run, args=("parakeet", lambda: start_parakeet(paths.models_dir)), name="boot-parakeet"),
        threading.Thread(target=run, args=("gemma", lambda: start_gemma(paths.models_dir)), name="boot-gemma"),
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    if errors:
        raise errors[0]
    start_chatterbox(paths.models_dir, ref[0])
    require_alive("parakeet")
    require_alive("gemma")
    require_alive("chatterbox")
    log("residents ready family=nano language=en")
    return ref[0]


def main() -> int:
    p = argparse.ArgumentParser(prog="python main.py")
    p.add_argument("--models-dir", type=Path)
    p.add_argument("--data-dir", type=Path)
    sub = p.add_subparsers(dest="command")
    sub.add_parser("install")
    sub.add_parser("ui")
    r = sub.add_parser("resident")
    r.add_argument("action", choices=("status", "boot", "stop"))
    r.add_argument("-r", "--reference")
    args = p.parse_args()

    if not args.command:
        paths = Paths(args.models_dir, args.data_dir, "install")
        set_log(paths.log)
        python = install(args.models_dir, args.data_dir)
        os.execv(str(python), [str(python), "-X", "utf8", str(Path(__file__).resolve()), *sys.argv[1:], "ui"])

    if args.command == "install":
        paths = Paths(args.models_dir, args.data_dir, "install")
        set_log(paths.log)
        install(args.models_dir, args.data_dir)
        return 0

    if args.command == "ui":
        paths = Paths(args.models_dir, args.data_dir, "ui")
        set_log(paths.log)
        log(f"ui hardware={HARDWARE}")
        boot(paths.models_dir, paths.data_dir)
        print(status())
        from talk import launch
        launch(paths.models_dir, paths.data_dir)
        log("ui finish")
        return 0

    if args.action == "stop":
        stop_all()
    elif args.action == "boot":
        paths = Paths(args.models_dir, args.data_dir, "resident")
        set_log(paths.log)
        boot(paths.models_dir, paths.data_dir, args.reference)
    print(status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
