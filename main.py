import argparse
from pathlib import Path

import trident


def main() -> int:
    parser = argparse.ArgumentParser(prog="python main.py",
        description="Trident: bare-metal parakeet streaming ASR (Nemotron 0.6b multilingual, Windows CPU)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="Download parakeet-cli and the streaming model")
    asr = sub.add_parser("asr", help="Transcribe one or more WAV files")
    asr.add_argument("--wav", action="append", type=Path, required=True, metavar="FILE",
                     help="WAV file to transcribe (repeat for multiple)")
    asr.add_argument("--lang", default="en",
                     help="BCP-47 locale: en, de, es, ja-JP, auto, ... (default: en)")
    args = parser.parse_args()
    return trident.install() if args.cmd == "install" else trident.asr(args.wav, args.lang)


if __name__ == "__main__":
    raise SystemExit(main())
