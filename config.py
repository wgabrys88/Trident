from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS, DATA, THIRD_PARTY, TOOLS, TTS = (ROOT / n for n in ("models", "data", "third_party", "tools", "tts"))
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML, RUNTIMES, CONVERTER = CHATTERBOX / "ggml", TOOLS / "runtime", TOOLS / "convert"
CHATTERBOX_URL, CHATTERBOX_REV = "https://github.com/wgabrys88/chatterbox.cpp", "9714d18af1096db5c8a8ace2f6cd77cc6a2d64cf"
GGML_GIT = ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951")
ASR_RATE, TTS_RATE, VAD_FRAME, FEED_S, PLAY_SLICE_S, MIC_LIMIT_S = 16000, 24000, 512, .16, .06, 86400
NANO_FILES = ("t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")
NANO_REPO, NANO_REV = "ResembleAI/chatterbox-nano", "71ccd1d0081b430592cea481f4307e764e07bc64"
T3_FILE, PARAKEET_FILE, GEMMA_FILE = "chatterbox-t3-nano-q4_0.gguf", "tdt-0.6b-v3-q4_k.gguf", "gemma-4-E2B_q4_0-it.gguf"
PARAKEET_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9/" + PARAKEET_FILE
GEMMA_URL = "https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/675cff42a74c774d6cb76f76d8eacb49b48c9b93/" + GEMMA_FILE
PARAKEET_ZIP = ("https://github.com/mudler/parakeet.cpp/releases/download/v0.5.0/parakeet-v0.5.0-bin-win-vulkan-x64.zip", "parakeet-v0.5.0-bin-win-vulkan-x64.zip")
LLAMA_ZIP = ("https://github.com/ggml-org/llama.cpp/releases/download/b10453/llama-b10453-bin-win-vulkan-x64.zip", "llama-b10453-bin-win-vulkan-x64.zip")
VOICES = {"trump": ("audio/donald-trump.wav", "ref-trump.wav"), "obama": ("audio/barack-obama.wav", "ref-obama.wav"), "kamala": ("audio/kamala_harris.wav", "ref-kamala.wav")}
VOICE_HF = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4/"
PORTS = {"parakeet": 17931, "gemma": 17932, "chatterbox": 17933}
PROMPT = ("ASR may deliver incomplete fragments. If the user has not finished a request or thought, output nothing. "
          "When a spoken reply is needed now, produce only that reply in English. If the input language differs, preserve meaning while answering in English. "
          "Spoken prose only: short sentences ending with a period, question mark, or exclamation. No markdown, lists, code, URLs, emoji, or square-bracket tags. "
          "Expand numbers and abbreviations. Do not mention transcription, models, or reasoning.")
TTS_KNOBS = {"gpu_layers": 99, "context": 2048, "threads": 4, "fastconv": 1, "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": .95, "min_p": 0., "temperature": .8, "repeat_penalty": 1.2, "cfm_steps": 2, "cfg_weight": 0., "exaggeration": 0., "first_chars": 80, "chars": 280}
TTS_PROFILES = {
    "nano": TTS_KNOBS,
    "turbo": {**TTS_KNOBS, "max_tokens": 1000},
    "v3": {**TTS_KNOBS, "max_tokens": 1000, "top_k": 0, "top_p": 1., "min_p": .05, "cfm_steps": 0, "cfg_weight": .5, "exaggeration": .5},
}
V3_LANGUAGES = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "it", "ko", "ms", "nl", "no", "pl", "pt", "sv", "sw", "tr")
GEMMA_GEN = {"temperature": 1., "top_p": .95, "top_k": 64, "min_p": 0., "repeat_penalty": 1., "seed": 42, "max_tokens": 1024}
LOG_FILE: Path | None = None
_log_lock = threading.Lock()

def detect_hardware() -> str:
    if not sys.platform.startswith("win"):
        raise RuntimeError("Trident requires Windows")
    gpu = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name -join ';'"], text=True, encoding="utf-8", errors="replace", timeout=15).lower()
    if any(n in gpu for n in ("gtx 1050", "gtx 1060", "gtx 1070", "gtx 1080", "titan x (pascal)", "titan xp", "quadro p")):
        return "pascal"
    if "iris" in gpu and "xe" in gpu:
        return "irisxe"
    raise RuntimeError(f"unsupported GPU: {gpu.strip()}")

HARDWARE = detect_hardware()
VULKAN_ENV = {"GGML_VK_DISABLE_F16": "1"} if HARDWARE == "pascal" else {}
FLASH_ATTN = "on" if HARDWARE == "pascal" else "off"
CODEC_QUANT, CODEC_FILE = (("q4_0", "chatterbox-s3gen-nano-irisxe-q4_0-rawf32-v1.gguf") if HARDWARE == "irisxe" else ("f16", "chatterbox-s3gen-nano-f16.gguf"))
TTS_MODELS = {
    "nano": (T3_FILE, CODEC_FILE),
    "turbo": ("chatterbox-t3-turbo-q4_0.gguf", f"chatterbox-s3gen-turbo-{CODEC_QUANT}.gguf"),
    "v3": ("chatterbox-t3-mtl-v3-q4_0.gguf", f"chatterbox-s3gen-mtl-v3-{CODEC_QUANT}.gguf"),
}

def log(msg: str, file: Path | None = None) -> None:
    line = f"{datetime.now().astimezone().isoformat(timespec='milliseconds')} {msg}"
    print(line, flush=True)
    dest = file or LOG_FILE
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock, dest.open("a", encoding="utf-8", newline="\n") as h:
            h.write(line + "\n")

def find_exe(root: Path, name: str) -> Path | None:
    return next((p for p in root.rglob(name) if p.is_file()), None) if root.is_dir() else None

def load_settings(data_dir: Path) -> dict:
    path = Path(data_dir) / "live-settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"system_prompt": PROMPT, "tts_voice": "trump", "vad_silence_ms": 600, "vad_threshold": .5}

def voice_wav(data_dir: Path, value: str | None = None) -> Path:
    raw = (value or "trump").strip() or "trump"
    key = raw.lower()
    path = Path(data_dir) / (VOICES[key][1] if key in VOICES else f"voices/{key}.wav")
    if path.is_file():
        return path.resolve()
    path = Path(raw).expanduser()
    if path.is_file():
        return path.resolve()
    raise RuntimeError(f"unknown voice {raw!r}")

class Paths:
    def __init__(self, models_dir=None, data_dir=None) -> None:
        global LOG_FILE
        self.models_dir, self.data_dir = Path(models_dir or MODELS).resolve(), Path(data_dir or DATA).resolve()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.run_dir = self.data_dir / "runs" / stamp
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log = self.run_dir / f"{stamp}-trident.log"
        LOG_FILE = self.log
