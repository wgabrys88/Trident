from __future__ import annotations

from pathlib import Path

from config import DEFAULT_MODELS_DIR, DEFAULT_DATA_DIR, ROOT, THIRD_PARTY, TOOLS, PATCHES, TTS, CHATTERBOX, GGML, RUNTIMES, CONVERTER

MODELS_DIR = DEFAULT_MODELS_DIR
DATA = DEFAULT_DATA_DIR

TRANSCRIPT = DATA / "transcript.txt"
ANSWER = DATA / "answer.txt"
SYSTEM_PROMPT = DATA / "system.txt"

DEFAULT_REFERENCE = DATA / "default-reference.wav"
ASSETS_REFERENCE = ROOT / "assets" / "default-reference.wav"