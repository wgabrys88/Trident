from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
DATA = ROOT / "data"
TOOLS = ROOT / "tools"
TTS = ROOT / "tts"
CHATTERBOX = ROOT / "third_party" / "chatterbox.cpp"
RUNTIMES = TOOLS / "runtime"
CONVERTER = TOOLS / "convert"

ASR_RATE = 16000
TTS_RATE = 24000
VAD_FRAME = 512
FEED_S = 0.16
MIC_LIMIT_S = 86400

NANO_FILES = (
    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)
NANO_REPO, NANO_REV = "ResembleAI/chatterbox-nano", "71ccd1d0081b430592cea481f4307e764e07bc64"
T3_FILE = "chatterbox-t3-nano-q4_0.gguf"
PARAKEET_FILE = "tdt-0.6b-v3-q4_k.gguf"
GEMMA_FILE = "gemma-4-E2B_q4_0-it.gguf"
PARAKEET_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9/" + PARAKEET_FILE
GEMMA_URL = "https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/675cff42a74c774d6cb76f76d8eacb49b48c9b93/" + GEMMA_FILE
PARAKEET_ZIP = (
    "https://github.com/mudler/parakeet.cpp/releases/download/v0.5.0/parakeet-v0.5.0-bin-win-vulkan-x64.zip",
    "parakeet-v0.5.0-bin-win-vulkan-x64.zip",
)
LLAMA_ZIP = (
    "https://github.com/ggml-org/llama.cpp/releases/download/b10453/llama-b10453-bin-win-vulkan-x64.zip",
    "llama-b10453-bin-win-vulkan-x64.zip",
)

VOICES = {
    "trump": "ref-trump.wav",
    "obama": "ref-obama.wav",
    "kamala": "ref-kamala.wav",
}
VOICE_HF = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4/"
DEFAULT_VOICE = "trump"

PORTS = {"parakeet": 17931, "gemma": 17932, "chatterbox": 17933}
URLS = {k: f"{'http' if k != 'chatterbox' else 'tcp'}://127.0.0.1:{v}" for k, v in PORTS.items()}

PROMPT = (
    "ASR may deliver incomplete fragments. If the user has not finished a request or thought, output nothing. "
    "When a spoken reply is needed now, produce only that reply in English. If the input language differs, "
    "preserve meaning while answering in English. Spoken prose only: short sentences ending with a period, "
    "question mark, or exclamation. No markdown, lists, code, URLs, emoji, or square-bracket tags. "
    "Expand numbers and abbreviations. Do not mention transcription, models, or reasoning."
)

TTS_KNOBS = {
    "gpu_layers": 99, "context": 2048, "threads": 4, "fastconv": 1,
    "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.95, "min_p": 0.0,
    "temperature": 0.8, "repeat_penalty": 1.2, "cfm_steps": 2,
    "cfg_weight": 0.0, "exaggeration": 0.0, "first_chars": 80, "chars": 280,
}
GEMMA_GEN = {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0, "repeat_penalty": 1.0, "seed": 42, "max_tokens": 1024}


def detect_hardware() -> str:
    if not sys.platform.startswith("win"):
        raise RuntimeError("Trident requires Windows")
    gpu = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name -join ';'"],
        text=True, encoding="utf-8", errors="replace", timeout=15,
    ).lower()
    if any(n in gpu for n in ("gtx 1050", "gtx 1060", "gtx 1070", "gtx 1080", "titan x (pascal)", "titan xp", "quadro p")):
        return "pascal"
    if "iris" in gpu and "xe" in gpu:
        return "irisxe"
    raise RuntimeError(f"unsupported GPU: {gpu.strip()}")


HARDWARE = detect_hardware()
VULKAN_ENV = {"GGML_VK_DISABLE_F16": "1"} if HARDWARE == "pascal" else {}
FLASH_ATTN = "on" if HARDWARE == "pascal" else "off"
CODEC_QUANT = "q4_0" if HARDWARE == "irisxe" else "f16"
CODEC_FILE = "chatterbox-s3gen-nano-irisxe-q4_0-rawf32-v1.gguf" if HARDWARE == "irisxe" else "chatterbox-s3gen-nano-f16.gguf"

_log_lock = threading.Lock()


def log(msg: str, file: Path | None = None) -> None:
    line = f"{datetime.now().astimezone().isoformat(timespec='milliseconds')} {msg}"
    print(line, flush=True)
    if file is None:
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock, file.open("a", encoding="utf-8", newline="\n") as h:
        h.write(line + "\n")


def load_settings(data_dir: Path) -> dict:
    path = Path(data_dir) / "live-settings.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "system_prompt": PROMPT, "tts_voice": DEFAULT_VOICE,
        "vad_silence_ms": 600, "vad_threshold": 0.5,
    }


def voice_wav(data_dir: Path, value: str | None = None) -> Path:
    raw = (value or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    key = raw.lower()
    if key in VOICES:
        return (Path(data_dir) / VOICES[key]).resolve()
    clone = Path(data_dir) / "voices" / f"{key}.wav"
    if clone.is_file():
        return clone.resolve()
    path = Path(raw).expanduser()
    if path.is_file():
        return path.resolve()
    raise RuntimeError(f"unknown voice {raw!r}")


class Paths:
    def __init__(self, models_dir=None, data_dir=None) -> None:
        self.models_dir = Path(models_dir or MODELS).resolve()
        self.data_dir = Path(data_dir or DATA).resolve()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.run_dir = self.data_dir / "runs" / stamp
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log = self.run_dir / f"{stamp}-trident.log"
