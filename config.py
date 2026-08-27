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
    if "iris" in gpu and "xe" in gpu: return "irisxe"
    raise RuntimeError(f"unsupported experimental GPU: {gpu.strip()}")


HARDWARE_PROFILE = detect_hardware_profile()


GGML_VULKAN_ENV = {
    "pascal": {"GGML_VK_DISABLE_F16": "1"},
    "irisxe": {},
}[HARDWARE_PROFILE]


def ggml_vulkan_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GGML_VK_DISABLE_F16", None)
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
SMART_TURN_SECONDS = 8
TTS_RATE = 24000
REFERENCE_MIN_SECONDS = 5.0

CABLE_INPUT = "CABLE Input"
CABLE_OUTPUT = "CABLE Output"

RESIDENT_SERVERS = {
    "parakeet": {"host": "127.0.0.1", "port": 17931, "url": "http://127.0.0.1:17931", "startup_timeout_s": 120},
    "gemma": {"host": "127.0.0.1", "port": 17932, "url": "http://127.0.0.1:17932", "startup_timeout_s": 180},
    "chatterbox": {"host": "127.0.0.1", "port": 17933, "url": "tcp://127.0.0.1:17933", "startup_timeout_s": 300},
}

BRAIN_MODEL = "gemma"
BRAIN_RUNTIME = {
    "gpu_layers": "all",
    "context": 4096,
    "flash_attn": "on" if HARDWARE_PROFILE == "pascal" else "off",
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
LIVE_SETTINGS_JSON = '{"ingestion_mode":"continuous","system_prompt":"The incoming speech transcript is auto-detected by ASR. Produce the final spoken response only in {tts_language_name} ({tts_language}). If the input language differs, preserve its meaning while translating or answering in the output language. Spoken prose only: short sentences ending with a period, question mark, or exclamation. No markdown, lists, code, URLs, emoji, or square-bracket tags. Expand numbers and abbreviations. Do not mention transcription, models, or reasoning.","tts_family":"v3","tts_join":"crossfade","tts_language":"en","tts_mode":"real","tts_voice":"trump","vad_silence_ms":200,"vad_threshold":0.5}'
LIVE_SETTINGS = json.loads(LIVE_SETTINGS_JSON)
LIVE_AUDIO = {
    "asr_feed_seconds": 0.16,
    "vad_frame_samples": 512,
    "mic_time_limit_seconds": 86400,
    "llm_history_turns": 6,
}

TTS_FIELDS = (
    ("n_gpu_layers", "TTS_RUNTIME", "gpu_layers", int, "--n-gpu-layers", "GPU layers", False),
    ("context", "TTS_RUNTIME", "context", int, "--context", "Context", False),
    ("threads", "TTS_RUNTIME", "threads", int, "--threads", "Threads", False),
    ("seed", "TTS_SAMPLE", "seed", int, "--seed", "Seed", False),
    ("max_tokens", "TTS_SAMPLE", "max_tokens", int, "--max-tokens", "Max T3 tokens", False),
    ("top_k", "TTS_SAMPLE", "top_k", int, "--top-k", "Top K", False),
    ("cfm_steps", "TTS_SAMPLE", "cfm_steps", int, "--cfm-steps", "CFM steps", False),
    ("first_chunk_chars", "TTS_CHUNK", "first_chars", int, "--first-chunk-chars", "First streaming text-unit chars", False),
    ("chunk_chars", "TTS_CHUNK", "chars", int, "--chunk-chars", "Text-unit chars", False),
    ("top_p", "TTS_SAMPLE", "top_p", float, "--top-p", "Top P", False),
    ("min_p", "TTS_SAMPLE", "min_p", float, "--min-p", "Min P", True),
    ("temperature", "TTS_SAMPLE", "temperature", float, "--temperature", "Temperature", False),
    ("repeat_penalty", "TTS_SAMPLE", "repeat_penalty", float, "--repeat-penalty", "Repeat penalty", False),
    ("cfg_weight", "TTS_VOICE", "cfg_weight", float, "--cfg-weight", "CFG weight", True),
    ("exaggeration", "TTS_VOICE", "exaggeration", float, "--exaggeration", "Exaggeration", True),
)


def live_settings_path(data_dir: Path) -> Path:
    return Path(data_dir) / "live-settings.json"


def load_live_settings(data_dir: Path) -> dict:
    path = live_settings_path(data_dir)
    settings = json.loads(path.read_text(encoding="utf-8") if path.is_file() else LIVE_SETTINGS_JSON)
    LIVE_SETTINGS.clear()
    LIVE_SETTINGS.update(settings)
    return dict(LIVE_SETTINGS)


def save_live_settings(data_dir: Path, settings: dict) -> None:
    path = live_settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    synced = json.loads(payload)
    LIVE_SETTINGS.clear()
    LIVE_SETTINGS.update(synced)

LANGUAGES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "tr": "Turkish", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "no": "Norwegian", "el": "Greek", "ms": "Malay", "sw": "Swahili",
    "ar": "Arabic", "ko": "Korean",
}


def _model(label, repo, revision, file, size, script, quant, files, *, variant=None, copy=None):
    recipe = {"script": script, "quant": quant, "files": files}
    if variant: recipe["variant"] = variant
    if copy: recipe["copy"] = copy
    return {"label": label, "repo": repo, "revision": revision, "file": file, "size": size, "convert": recipe}


def _family(name, languages, sample, voice, chunk, t3, codec):
    return {
        "name": name, "TTS_LANGUAGES": languages, "DEFAULT_REPLY_LANGUAGE": "en",
        "TTS_RUNTIME": {
            "gpu_layers": 99, "context": 2048, "threads": 4, "fastconv": True,
        },
        "TTS_SAMPLE": sample, "TTS_VOICE": voice, "TTS_CHUNK": chunk,
        "TTS_STREAM": {"enabled": True, "join": "crossfade"},
        "TTS_MODELS": {"chatterbox-t3": t3, "chatterbox-codec": codec},
    }


_TURBO_FILES = (
    "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)
_NANO_FILES = ("t3_nano_v1.safetensors", *_TURBO_FILES[1:])
_MTL_FILES = (
    "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen_v3.pt",
    "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
)


def _english_family(name, repo, revision, t3_source, t3_file, t3_size, first_chars):
    files = _NANO_FILES if name == "nano" else _TURBO_FILES
    copy = {t3_source: "t3_turbo_v1.safetensors"} if name == "nano" else None
    t3 = _model(f"CHATTERBOX {name.upper()} T3", repo, revision, t3_file, t3_size,
                "convert-t3-turbo-to-gguf.py", "q4_0", files, copy=copy)
    codec = _model(f"CHATTERBOX {name.upper()} S3GEN", repo, revision,
                   f"chatterbox-s3gen-{name}-f16.gguf", 1064879936,
                   "convert-s3gen-to-gguf.py", "f16", files, variant="turbo")
    return _family(
        name, {"en": "English"},
        {"seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.95,
         "min_p": 0.0, "temperature": 0.8, "repeat_penalty": 1.2, "cfm_steps": 2},
        {"cfg_weight": 0.0, "exaggeration": 0.0},
        {"first_chars": first_chars, "chars": 280}, t3, codec,
    )


_v3_t3 = _model(
    "CHATTERBOX V3 T3", "ResembleAI/chatterbox", "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
    "chatterbox-t3-mtl-v3-q4_0.gguf", 344985408, "convert-t3-mtl-to-gguf.py", "q4_0", _MTL_FILES,
    copy={"t3_mtl23ls_v3.safetensors": "t3_mtl23ls_v2.safetensors"},
)
_v3_codec = _model(
    "CHATTERBOX V3 S3GEN", "ResembleAI/chatterbox", "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
    "chatterbox-s3gen-mtl-v3-f16.gguf", 1056431360, "convert-s3gen-to-gguf.py", "f16", _MTL_FILES,
    variant="mtl", copy={"s3gen_v3.pt": "s3gen.pt"},
)
FAMILIES = {
    "v3": _family(
        "v3", LANGUAGES,
        {"seed": 42, "max_tokens": 768, "top_k": 0, "top_p": 1.0,
         "min_p": 0.05, "temperature": 0.8, "repeat_penalty": 1.2, "cfm_steps": 5},
        {"cfg_weight": 0.5, "exaggeration": 0.5}, {"first_chars": 180, "chars": 300},
        _v3_t3, _v3_codec,
    ),
    "turbo": _english_family(
        "turbo", "ResembleAI/chatterbox-turbo", "749d1c1a46eb10492095d68fbcf55691ccf137cd",
        "t3_turbo_v1.safetensors", "chatterbox-t3-turbo-q4_0.gguf", 333506240, 120,
    ),
    "nano": _english_family(
        "nano", "ResembleAI/chatterbox-nano", "71ccd1d0081b430592cea481f4307e764e07bc64",
        "t3_nano_v1.safetensors", "chatterbox-t3-nano-q4_0.gguf", 171901536, 80,
    ),
}

if HARDWARE_PROFILE == "irisxe":
    for _family_spec in FAMILIES.values():
        _codec = _family_spec["TTS_MODELS"]["chatterbox-codec"]
        _codec["convert"]["quant"] = "q4_0"
        _codec["size"] = 0
        _codec["file"] = _codec["file"].replace("-f16.gguf", "-irisxe-q4_0-rawf32-v1.gguf")


def default_family() -> str:
    return next(iter(FAMILIES))


SHARED_MODELS = {
    "parakeet": {
        "label": "PARAKEET TDT 0.6B V3 Q4_K",
        "repo": "mudler/parakeet-cpp-gguf", "revision": "bf0af9f425fa01809cadec671b3cb672709d13e9",
        "file": "tdt-0.6b-v3-q4_k.gguf", "size": 675200864,
    },
    "smart-turn": {
        "label": "SMART TURN V3.2 MULTILINGUAL CPU INT8",
        "repo": "pipecat-ai/smart-turn-v3", "revision": "f766f81d3cfdf7737ac64aad813d91bbfd56bf93",
        "file": "smart-turn-v3.2-cpu.onnx", "size": 8679182,
        "sha256": "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f",
    },
    "gemma": {"label": "GEMMA 4 E2B", "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf", "revision": "675cff42a74c774d6cb76f76d8eacb49b48c9b93", "file": "gemma-4-E2B_q4_0-it.gguf", "size": 3349516256},
}


SOURCES = {
    "chatterbox": ("https://github.com/wgabrys88/chatterbox.cpp", "77e9b0501aa76a46845d8b13cf956c21d060b593"),
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


def resolve_voice(data_dir: Path, value: str | None = None) -> Path:
    key = None if value is None else value.lower()
    if key is None or key in REFERENCE_VOICES:
        return (data_dir / REFERENCE_VOICES[key or DEFAULT_VOICE]["file"]).resolve()
    return Path(value).expanduser().resolve()


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
