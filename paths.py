from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MODELS_DIR = ROOT / "models"
THIRD_PARTY = ROOT / "third_party"
TOOLS = ROOT / "tools"
PATCHES = ROOT / "patches"
TTS = ROOT / "tts"
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML = CHATTERBOX / "ggml"
RUNTIMES = TOOLS / "runtime"
CONVERTER = TOOLS / "convert"

TRANSCRIPT = DATA / "transcript.txt"
ANSWER = DATA / "answer.txt"
SYSTEM_PROMPT = DATA / "system.txt"

DEFAULT_REFERENCE = DATA / "default-reference.wav"
ASSETS_REFERENCE = ROOT / "assets" / "default-reference.wav"