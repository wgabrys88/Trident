import argparse
import math
import sys
from pathlib import Path

from config import (
    ASR_RATE, CHATTERBOX, CHATTERBOX_REV, FLASH_ATTN, GGML, GGML_GIT, HARDWARE, Paths, ROOT,
    TTS_MODELS, TTS_PROFILES, TTS_RATE, VULKAN_ENV, ensure_venv, load_settings, wasapi_device,
)
import config
from install import install, tts_provenance
from journal import git_identity

ensure_venv(__file__)

_COMMANDS = {
    "install": "Download models, pin Chatterbox, and build the native TTS server.",
    "talk": "Live conversation on the WASAPI microphone and speakers. SmartTurn ends user speech; every transcript goes to Gemma; TTS speaks the reply.",
    "tts": "Speak text through the WASAPI speakers. Writes speaker.wav (24 kHz mix sent to the device) and speaker-16k.wav (ffmpeg, 16 kHz mono for Parakeet).",
    "asr": "Transcribe with Parakeet. Pass --wav to read files (use a TTS run's speaker-16k.wav). Omit --wav to listen on the WASAPI microphone until Ctrl+C.",
    "generation": "Ask Gemma for a spoken-style text reply. No audio.",
}
_LOOP = """Speak, then transcribe what actually played:

  python tts.py --text "Hello. This is a spoken test."
  python asr.py --wav data/runs/<run-id>/speaker-16k.wav

tts prints trident.done <run-dir>. Pass that directory's speaker-16k.wav to asr.
speaker.wav is also accepted; ffmpeg resamples it. Leftover audio thrown away on
interrupt is dropped/<epoch>-<response>-<piece>-<chunk>.wav next to events.jsonl.

  python tts.py --text "Count from one to twenty." --interrupt-text "Now say your name." --interrupt-after 3.5
  python asr.py --wav data/runs/<run-id>/speaker-16k.wav
"""


def _read_utf8(path: Path, parser: argparse.ArgumentParser, label: str) -> str:
    if not path.is_file(): parser.error(f"{label} does not exist: {path}")
    try: return path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError: parser.error(f"{label} is not valid UTF-8")


def _manifest(paths: Paths) -> dict:
    installed_tts = tts_provenance() if paths.command in ("talk", "tts") else None
    settings, audio = load_settings(paths.data_dir), {}
    kinds = {"talk": ("input", "output"), "tts": ("output",), "asr": ("input",)}.get(paths.command, ())
    if paths.command == "asr" and paths.wavs: kinds = ()
    for kind in kinds:
        index, device, host = wasapi_device(kind)
        audio[kind] = {"device": device["name"], "index": index, "host_api": host["name"],
                       "channels": 1, "rate": ASR_RATE if kind == "input" else TTS_RATE, "auto_convert": True}
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
                    "codec": TTS_MODELS.get(paths.family, (None, None))[1], "vulkan_env": VULKAN_ENV,
                    "flash_attn": FLASH_ATTN},
        "runtime_knobs": TTS_PROFILES.get(paths.family, {}),
        "conversation": {k: settings.get(k) for k in ("candidate_silence_ms", "completion_threshold", "acoustic_context_seconds")},
        "audio": audio,
    }
    if installed_tts is not None: manifest["installed_tts"] = installed_tts
    return manifest


def _parser(command: str | None) -> argparse.ArgumentParser:
    if command:
        description, epilog = _COMMANDS[command], _LOOP if command in ("tts", "asr") else ""
    else:
        description = "Trident spoken-conversation runtime. Capture/ASR, Gemma, native TTS, and talk are independent commands."
        epilog = "commands:\n" + "\n".join(f"  {name:11} {text}" for name, text in _COMMANDS.items()) + "\n\n" + _LOOP
    parser = argparse.ArgumentParser(prog=f"python {command}.py" if command else "python main.py",
                                     description=description, epilog=epilog.strip(),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    show = command is None
    parser.add_argument("--models-dir", type=Path, help="Model directory (default: ./models)")
    parser.add_argument("--data-dir", type=Path, help="Data directory for voices and data/runs/<run-id> (default: ./data)")
    parser.add_argument("--family", choices=("nano", "turbo", "v3"), default="nano",
                        help="TTS family (default: nano). Iris Xe talk/tts supports nano only")
    parser.add_argument("--language", default="en", help="Spoken language code (default: en). Non-English requires --family v3")
    parser.add_argument("--console", action="store_true", help="Print each journal event as JSON on stdout")
    if show or command in ("tts", "talk", "asr"):
        parser.add_argument("--spectrogram", action="store_true", help="Write an ffmpeg spectrogram PNG next to speaker.wav or each --wav (off by default)")
    if show or command in ("tts", "generation"):
        if command == "generation":
            text_help, file_help = "User text sent to Gemma", "UTF-8 file of user text sent to Gemma"
        elif command == "tts":
            text_help, file_help = "Text to speak", "UTF-8 file of text to speak"
        else:
            text_help, file_help = "Text to speak (tts) or send to Gemma (generation)", "UTF-8 file of that text"
        parser.add_argument("--text", help=f"{text_help}. Mutually exclusive with --text-file")
        parser.add_argument("--text-file", type=Path, metavar="PATH", help=f"{file_help}. Mutually exclusive with --text")
    if show or command == "tts":
        who = " (tts only)" if show else ""
        parser.add_argument("--interrupt-text", help=f"Replacement spoken after --interrupt-after{who}. Mutually exclusive with --interrupt-file")
        parser.add_argument("--interrupt-file", type=Path, metavar="PATH",
                            help=f"UTF-8 file of replacement speech{who}. Mutually exclusive with --interrupt-text")
        parser.add_argument("--interrupt-after", type=float, metavar="SECONDS",
                            help=f"Seconds after trident.ready to cut current speech and speak the interrupt text{who}")
    if show or command == "asr":
        who = " (asr only; repeatable)" if show else " (repeatable)"
        parser.add_argument("--wav", action="append", type=Path, dest="wavs", metavar="WAV",
                            help=f"Wav for Parakeet{who}. Prefer a TTS run's speaker-16k.wav. Omit to use the microphone")
    if command is None:
        parser.add_argument("command", nargs="?", choices=tuple(_COMMANDS), default="install",
                            help="Command to run (default: install). Same as python <command>.py")
    return parser


def main(command: str | None = None) -> int:
    parser = _parser(command)
    args = parser.parse_args()
    cmd = command or args.command
    primary = replacement = None
    text, text_file = getattr(args, "text", None), getattr(args, "text_file", None)
    interrupt_text, interrupt_file = getattr(args, "interrupt_text", None), getattr(args, "interrupt_file", None)
    interrupt_after = getattr(args, "interrupt_after", None)
    flags = (text, text_file, interrupt_text, interrupt_file, interrupt_after)
    wavs = tuple(getattr(args, "wavs", None) or ())
    if cmd == "asr":
        missing = [str(path) for path in wavs if not path.is_file()]
        if missing: parser.error("wav does not exist: " + ", ".join(missing))
    elif wavs:
        parser.error("--wav requires command asr")
    if cmd in ("tts", "generation"):
        if cmd == "generation" and any(v is not None for v in flags[2:]):
            parser.error("generation does not accept interrupt flags")
        if (text is None) == (text_file is None): parser.error("exactly one of --text and --text-file is required")
        if cmd == "tts":
            if interrupt_text is not None and interrupt_file is not None: parser.error("--interrupt-text and --interrupt-file are mutually exclusive")
            if (interrupt_text is not None or interrupt_file is not None) != (interrupt_after is not None): parser.error("interrupt content and --interrupt-after are required together")
            if interrupt_after is not None and (not math.isfinite(interrupt_after) or interrupt_after < 0): parser.error("--interrupt-after must be finite and non-negative")
        primary = (text if text is not None else _read_utf8(text_file, parser, "--text-file")).strip()
        replacement = interrupt_text if interrupt_text is not None else (_read_utf8(interrupt_file, parser, "--interrupt-file") if interrupt_file is not None else None)
        replacement = replacement.strip() if replacement is not None else None
        if not primary: parser.error("TTS input is empty" if cmd == "tts" else "generation input is empty")
        if replacement is not None and not replacement: parser.error("TTS replacement is empty")
    elif any(v is not None for v in flags):
        parser.error("TTS text and replacement flags require command tts" if cmd == "talk" else f"{cmd} does not accept streaming or TTS content flags")
    family, language = (args.family, args.language.strip().lower()) if cmd != "install" else ("all", "all")
    if HARDWARE == "irisxe" and cmd in ("talk", "tts") and family != "nano":
        parser.error("Iris Xe supports Nano English only")
    spectrogram = bool(getattr(args, "spectrogram", False))
    if cmd not in ("tts", "talk", "asr") and spectrogram:
        parser.error("--spectrogram requires command tts, talk, or asr")
    paths = Paths(args.models_dir, args.data_dir, cmd, family, language, args.console, wavs, spectrogram)
    try:
        paths.journal.write_manifest(_manifest(paths))
        paths.journal.emit("main", "start", command=cmd, hardware=HARDWARE, family=family, language=language)
        if cmd == "install":
            install(args.models_dir, args.data_dir, paths)
        else:
            __import__(cmd).launch(paths, args.family, language, primary, replacement, interrupt_after)
        paths.journal.emit("main", "completed", command=cmd); print(f"trident.done {paths.run_dir}", flush=True); return 0
    except KeyboardInterrupt:
        paths.journal.emit("main", "stopped", reason="ctrl+c"); print(f"trident.interrupt {paths.run_dir}", flush=True); return 130
    except Exception as error:
        paths.journal.failure("main", error); print(f"trident.fail {type(error).__name__}: {error}\ntrident.run {paths.run_dir}", flush=True); return 1
    finally:
        paths.close()


if __name__ == "__main__":
    raise SystemExit(main())
