from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def detect_hardware_profile() -> str:
    if not sys.platform.startswith("win"):
        raise RuntimeError("Trident requires Windows for GPU auto-discovery")
    gpu = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name -join ';'"],
        text=True, encoding="utf-8", errors="replace", timeout=15,
    ).lower()
    if any(name in gpu for name in ("gtx 1050", "gtx 1060", "gtx 1070", "gtx 1080", "titan x (pascal)", "titan xp", "quadro p")): return "pascal"
    raise RuntimeError(f"unsupported GPU: {gpu.strip()}")


HARDWARE_PROFILE = detect_hardware_profile()


GGML_VULKAN_ENV = {"GGML_VK_DISABLE_F16": "1"}


def ggml_vulkan_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(GGML_VULKAN_ENV)
    return env

DEFAULT_MODELS_DIR = ROOT / "models"
DEFAULT_DATA_DIR = ROOT / "data"

THIRD_PARTY = ROOT / "third_party"
TOOLS = ROOT / "tools"
TTS = ROOT / "tts"
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML = CHATTERBOX / "ggml"
RUNTIMES = TOOLS / "runtime"
CONVERTER = TOOLS / "convert"

ASR_RATE = 16000
ASR_CHUNK_SECONDS = 30
ASR_CHUNK_OVERLAP_SECONDS = 4
TTS_RATE = 24000
REFERENCE_MIN_SECONDS = 5.0
ECHO_RING_MS = 1500
ASR_FEED_SECONDS = 0.16
MIC_TIME_LIMIT_SECONDS = 86400

RESIDENT_SERVERS = {
    "parakeet": {"host": "127.0.0.1", "port": 17931, "url": "http://127.0.0.1:17931", "startup_timeout_s": 120},
    "gemma": {"host": "127.0.0.1", "port": 17932, "url": "http://127.0.0.1:17932", "startup_timeout_s": 180},
    "chatterbox": {"host": "127.0.0.1", "port": 17933, "url": "tcp://127.0.0.1:17933", "startup_timeout_s": 300},
}

BRAIN_MODEL = "gemma"
BRAIN_RUNTIME = {
    "gpu_layers": "all",
    "context": 4096,
    "flash_attn": "on",
    "fit": "off",
    "load_mode": "mmap",
    "parallel": 1,
    "cache_type_k": "f16",
    "cache_type_v": "f16",
    "poll": 0,
    "poll_batch": 0,
    "threads": 2,
    "threads_batch": 2,
    "threads_http": 1,
}
BRAIN_GENERATION = {
    "temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0,
    "repeat_penalty": 1.0, "seed": 42, "max_tokens": 1024,
}
BRAIN_THINKING = False
LIVE_SETTINGS_JSON = '{"system_prompt":"ASR may deliver incomplete fragments. If the user has not finished a request or thought, output nothing. When a spoken reply is needed now, produce only that reply in English. If the input language differs, preserve meaning while answering in English. Spoken prose only: short sentences ending with a period, question mark, or exclamation. No markdown, lists, code, URLs, emoji, or square-bracket tags. Expand numbers and abbreviations. Do not mention transcription, models, or reasoning.","tts_voice":"trump","vad_silence_ms":200,"vad_threshold":0.5}'
LIVE_SETTINGS = json.loads(LIVE_SETTINGS_JSON)
VAD_FRAME_SAMPLES = 512

TTS_FIELDS = (
    ("TTS_RUNTIME", "gpu_layers", "--n-gpu-layers"),
    ("TTS_RUNTIME", "context", "--context"),
    ("TTS_RUNTIME", "threads", "--threads"),
    ("TTS_SAMPLE", "seed", "--seed"),
    ("TTS_SAMPLE", "max_tokens", "--max-tokens"),
    ("TTS_SAMPLE", "top_k", "--top-k"),
    ("TTS_SAMPLE", "cfm_steps", "--cfm-steps"),
    ("TTS_CHUNK", "first_chars", "--first-chunk-chars"),
    ("TTS_CHUNK", "chars", "--chunk-chars"),
    ("TTS_SAMPLE", "top_p", "--top-p"),
    ("TTS_SAMPLE", "min_p", "--min-p"),
    ("TTS_SAMPLE", "temperature", "--temperature"),
    ("TTS_SAMPLE", "repeat_penalty", "--repeat-penalty"),
    ("TTS_VOICE", "cfg_weight", "--cfg-weight"),
    ("TTS_VOICE", "exaggeration", "--exaggeration"),
    ("TTS_CHUNK", "stream_chunk_tokens", "--stream-chunk-tokens"),
    ("TTS_CHUNK", "stream_first_chunk_tokens", "--stream-first-chunk-tokens"),
    ("TTS_SAMPLE", "stream_cfm_steps", "--stream-cfm-steps"),
)


def live_settings_path(data_dir: Path) -> Path:
    return Path(data_dir) / "live-settings.json"


def load_live_settings(data_dir: Path) -> dict:
    path = live_settings_path(data_dir)
    return json.loads(path.read_text(encoding="utf-8") if path.is_file() else LIVE_SETTINGS_JSON)


def _model(label, repo, revision, file, size, script, quant, files, *, variant=None, copy=None):
    recipe = {"script": script, "quant": quant, "files": files}
    if variant: recipe["variant"] = variant
    if copy: recipe["copy"] = copy
    return {"label": label, "repo": repo, "revision": revision, "file": file, "size": size, "convert": recipe}


_NANO_FILES = (
    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)
_NANO_T3 = _model(
    "CHATTERBOX NANO T3", "ResembleAI/chatterbox-nano", "71ccd1d0081b430592cea481f4307e764e07bc64",
    "chatterbox-t3-nano-q4_0.gguf", 171901536, "convert-t3-turbo-to-gguf.py", "q4_0", _NANO_FILES,
    copy={"t3_nano_v1.safetensors": "t3_turbo_v1.safetensors"},
)
_NANO_CODEC = _model(
    "CHATTERBOX NANO S3GEN", "ResembleAI/chatterbox-nano", "71ccd1d0081b430592cea481f4307e764e07bc64",
    "chatterbox-s3gen-nano-f16.gguf", 1064879936, "convert-s3gen-to-gguf.py", "f16", _NANO_FILES,
    variant="turbo",
)
FAMILIES = {
    "nano": {
        "name": "nano", "TTS_LANGUAGES": {"en": "English"}, "DEFAULT_REPLY_LANGUAGE": "en",
        "TTS_RUNTIME": {"gpu_layers": 99, "context": 2048, "threads": 4, "fastconv": True},
        "TTS_SAMPLE": {
            "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.95,
            "min_p": 0.0, "temperature": 0.8, "repeat_penalty": 1.2, "cfm_steps": 2,
            "stream_cfm_steps": 0,
        },
        "TTS_VOICE": {"cfg_weight": 0.0, "exaggeration": 0.0},
        "TTS_CHUNK": {"first_chars": 80, "chars": 280, "stream_chunk_tokens": 0, "stream_first_chunk_tokens": 0},
        "TTS_MODELS": {"chatterbox-t3": _NANO_T3, "chatterbox-codec": _NANO_CODEC},
    },
}

SHARED_MODELS = {
    "parakeet": {
        "label": "PARAKEET TDT 0.6B V3 Q4_K",
        "repo": "mudler/parakeet-cpp-gguf", "revision": "bf0af9f425fa01809cadec671b3cb672709d13e9",
        "file": "tdt-0.6b-v3-q4_k.gguf", "size": 675200864,
    },
    "gemma": {"label": "GEMMA 4 E2B", "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf", "revision": "675cff42a74c774d6cb76f76d8eacb49b48c9b93", "file": "gemma-4-E2B_q4_0-it.gguf", "size": 3349516256},
}


SOURCES = {
    "chatterbox": ("https://github.com/wgabrys88/chatterbox.cpp", "refs/heads/main"),
    "ggml": ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951"),
}

BINARIES = {
    "parakeet": {
        "label": "PARAKEET.CPP V0.5 VULKAN", "repo": "mudler/parakeet.cpp", "tag": "v0.5.0",
        "asset": "parakeet-v0.5.0-bin-win-vulkan-x64.zip",
        "server_exe": "parakeet-server.exe",
    },
    "gemma": {
        "label": "LLAMA.CPP B10453 VULKAN", "repo": "ggml-org/llama.cpp", "tag": "b10453",
        "asset": "llama-b10453-bin-win-vulkan-x64.zip",
        "server_exe": "llama-server.exe",
    },
}

CHATTERBOX_LIBRARY = CHATTERBOX / "build" / "Release" / "tts-cpp.lib"
TTS_BUILD = TTS / "build" / "Release"
TTS_SERVER_EXE = "trident-tts-server.exe"

REFERENCE_VOICES = {
    "trump": {
        "label": "VOICE TRUMP", "name": "Donald Trump",
        "repo": "sdialog/voices-celebrities", "revision": "57746b866d470be717097b87ba0428f8dd73e4f4",
        "source": "audio/donald-trump.wav", "file": "ref-trump.wav", "size": 4210766,
    },
    "obama": {
        "label": "VOICE OBAMA", "name": "Barack Obama",
        "repo": "sdialog/voices-celebrities", "revision": "57746b866d470be717097b87ba0428f8dd73e4f4",
        "source": "audio/barack-obama.wav", "file": "ref-obama.wav", "size": 8454222,
    },
    "kamala": {
        "label": "VOICE KAMALA", "name": "Kamala Harris",
        "repo": "sdialog/voices-celebrities", "revision": "57746b866d470be717097b87ba0428f8dd73e4f4",
        "source": "audio/kamala_harris.wav", "file": "ref-kamala.wav", "size": 7487566,
    },
}
DEFAULT_VOICE = "trump"


def voices_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "voices"


def resolve_voice(data_dir: Path, value: str | None = None) -> Path:
    raw = (value or DEFAULT_VOICE).strip()
    if not raw:
        raw = DEFAULT_VOICE
    key = raw.lower()
    if key in REFERENCE_VOICES:
        return (data_dir / REFERENCE_VOICES[key]["file"]).resolve()
    clone = voices_dir(data_dir) / f"{key}.wav"
    if clone.is_file():
        return clone.resolve()
    path = Path(raw).expanduser()
    if path.is_file():
        return path.resolve()
    raise RuntimeError(f"unknown voice {raw!r}")


class Paths:
    def __init__(self, models_dir: Path | None = None, data_dir: Path | None = None, command: str | None = None) -> None:
        self.models_dir = (models_dir or DEFAULT_MODELS_DIR).resolve()
        self.data_dir = (data_dir or DEFAULT_DATA_DIR).resolve()
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f") if command else None
        self.run_dir = self.data_dir / "runs" / f"{self.stamp}-{command}" if command else None
        if self.run_dir:
            self.run_dir.mkdir(parents=True)
        def artifact(name: str):
            return self.run_dir / f"{self.stamp}-{name}" if self.run_dir else None
        self.transcript = artifact("transcript.txt")
        self.answer = artifact("answer.txt")
        self.system = artifact("system.txt")
        self.output = artifact("output.wav")
        self.input = artifact("input.wav")
        self.literal = artifact("literal.txt")
        self.log = artifact("trident.log")
        self.meta = artifact("meta.txt")
