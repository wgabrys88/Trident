from __future__ import annotations
import hashlib
import io
import math
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
import webbrowser
import zipfile
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from log import (
    clear as reset_log,
    debug,
    error,
    info,
    ingest as ingest_trace,
    new_id as new_trace_id,
    read as read_trace,
    record as trace,
    run_id as trace_run_id,
    scope as trace_scope,
    set_listener as set_trace_listener,
    warn,
)
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
MODELS_DIR = ROOT / "models"
THIRD_PARTY = ROOT / "third_party"
TOOLS = ROOT / "tools"
PATCHES = ROOT / "patches"
SERVER = ROOT / "server"
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML = CHATTERBOX / "ggml"
RUNTIMES = TOOLS / "runtime"
SOURCES = {
    "chatterbox": ("https://github.com/gianni-cor/chatterbox.cpp", "ddca05fb69c2910b0d7b5eae420d360ed98c067b"),
    "ggml": ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951"),
}
BINARIES = {
    "parakeet": {"label": "PARAKEET.CPP V0.5 VULKAN", "repo": "mudler/parakeet.cpp", "tag": "v0.5.0", "asset": "parakeet-v0.5.0-bin-win-vulkan-x64.zip", "exe": "parakeet-server.exe"},
    "gemma": {"label": "LLAMA.CPP B10453 VULKAN", "repo": "ggml-org/llama.cpp", "tag": "b10453", "asset": "llama-b10453-bin-win-vulkan-x64.zip", "exe": "llama-server.exe"},
}
MODELS = {
    "chatterbox-t3": {"label": "CHATTERBOX V3 T3", "repo": "BricksDisplay/Chatterbox-Multilingual-TTS-GGUF", "revision": "37277eeb9e26da8e3fba65b52727cb30b0bc5ae8", "file": "chatterbox-mtl-t3-q4_0.gguf", "size": 283389248, "sha256": "9a5b5e863d05da00f57ffb7d157f4135231ae17c926f97deb0070f9361205c30"},
    "chatterbox-codec": {"label": "CHATTERBOX V3 S3GEN", "repo": "BricksDisplay/Chatterbox-Multilingual-TTS-GGUF", "revision": "37277eeb9e26da8e3fba65b52727cb30b0bc5ae8", "file": "chatterbox-mtl-codec-f16.gguf", "size": 335027072, "sha256": "dce996594a43bcdb665b7a3f2b8e73b58ddca13eeb736f512ba0572d4e64954a"},
    "chatterbox-s3t": {"label": "CHATTERBOX V3 S3T", "repo": "BricksDisplay/Chatterbox-Multilingual-TTS-GGUF", "revision": "37277eeb9e26da8e3fba65b52727cb30b0bc5ae8", "file": "chatterbox-mtl-s3t.gguf", "size": 247487280, "sha256": "26592ce171dd40bb54468a32dd9a3b697e15bfc23ebc8f8d218e34c3962e69c4"},
    "parakeet": {"label": "PARAKEET TDT 0.6B V3 Q4_K", "repo": "mudler/parakeet-cpp-gguf", "revision": "bf0af9f425fa01809cadec671b3cb672709d13e9", "file": "tdt-0.6b-v3-q4_k.gguf", "size": 675200864, "sha256": "993d73feb4206dadda865ab25bd64b50c48dc4d013c3bf6126a721f28b1d5ee8"},
    "gemma": {"label": "GEMMA 4 E2B", "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf", "revision": "675cff42a74c774d6cb76f76d8eacb49b48c9b93", "file": "gemma-4-E2B_q4_0-it.gguf", "size": 3349516256, "sha256": "fa401b55b07ee70a54c6dae3903c783a6e65064312529ea57175cb5f8dec6634"},
    "qwen35-0.8b": {"label": "QWEN3.5 0.8B", "repo": "unsloth/Qwen3.5-0.8B-GGUF", "revision": "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0", "file": "Qwen3.5-0.8B-Q4_K_M.gguf", "size": 532517120, "sha256": "bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517"},
    "qwen35-4b": {"label": "QWEN3.5 4B", "repo": "unsloth/Qwen3.5-4B-GGUF", "revision": "e87f176479d0855a907a41277aca2f8ee7a09523", "file": "Qwen3.5-4B-Q4_K_M.gguf", "size": 2740937888, "sha256": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"},
    "reference": {"label": "DEFAULT VOICE", "source": "assets/default-reference.wav", "file": "default-reference.wav", "directory": "data", "size": 1012558, "sha256": "de2579b22226261784d6a944c07b9c1fba7fdd0c7e8c9e90da6bc581c78171a9", "license": "Resemble demo prompt"},
}
VULKAN_VERSION = "1.4.357.0"
PACKAGES = {
    "git": {"url": "https://github.com/git-for-windows/git/releases/download/v2.54.0.windows.1/MinGit-2.54.0-64-bit.zip", "file": "MinGit-2.54.0-64-bit.zip", "size": 39989839, "sha256": "04f937e1f0918b17b9be6f2294cb2bb66e96e1d9832d1c298e2de088a1d0e668"},
    "cmake": {"url": "https://github.com/Kitware/CMake/releases/download/v4.4.2/cmake-4.4.2-windows-x86_64.zip", "file": "cmake-4.4.2-windows-x86_64.zip", "size": 54405968, "sha256": "e8139d85b3813bc38833142ae1940472e9a587e9b5d2718ac1804c60f4e57a64"},
    "msvc": {"url": "https://download.visualstudio.microsoft.com/download/pr/00d9d26c-2727-42c2-aa9e-eda63b03e1ee/15df9d3b4c2b2eaf44704d5e938c895341b9cd8ba40a9a18610f8d18cbe01b53/vs_BuildTools.exe", "file": "vs_BuildTools.exe", "size": 4458736, "sha256": "15df9d3b4c2b2eaf44704d5e938c895341b9cd8ba40a9a18610f8d18cbe01b53"},
    "vulkan": {"url": f"https://sdk.lunarg.com/sdk/download/{VULKAN_VERSION}/windows/vulkansdk-windows-X64-{VULKAN_VERSION}.exe", "file": f"vulkansdk-windows-X64-{VULKAN_VERSION}.exe", "size": 0, "sha256": "81f474711e9042f4cd22b31b2f7a8870db2e428b21586fb43dd80150be97310d"},
}
TTS_LANGUAGES = {"ar": "Arabic", "da": "Danish", "de": "German", "el": "Greek", "en": "English", "es": "Spanish", "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi", "it": "Italian", "ja": "Japanese", "ko": "Korean", "ms": "Malay", "nl": "Dutch", "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "sv": "Swedish", "sw": "Swahili", "tr": "Turkish", "zh": "Chinese"}
ASR_LANGUAGES = {"bg": "Bulgarian", "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch", "en": "English", "et": "Estonian", "fi": "Finnish", "fr": "French", "de": "German", "el": "Greek", "hu": "Hungarian", "it": "Italian", "lv": "Latvian", "lt": "Lithuanian", "mt": "Maltese", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "sk": "Slovak", "sl": "Slovenian", "es": "Spanish", "sv": "Swedish", "ru": "Russian", "uk": "Ukrainian"}
CONVERSATION_LANGUAGES = {code: TTS_LANGUAGES[code] for code in TTS_LANGUAGES if code in ASR_LANGUAGES}
ENGINE_LOG_TOKENS = ("vulkan", "uma", "model loaded", "listening", "server is listening", "n_ctx_slot", "prompt eval time", "eval time", "total time", "voiceencoder", "s3tokenizer", "prompt_feat", "t3 stop", "t3 done", "s3gen:", "bench", "metric")
BUILD_LOG_TOKENS = ("compiler identification", "found vulkan:", "build files have been written")
NATIVE_EVENT_PREFIX = "TRIDENT_EVENT "
T3_STOP_RE = re.compile(r"T3 stop reason=(\w+) prompt=(\d+) n_past=(\d+) speech_position=(\d+) generated=(\d+)(?: final_token=(-?\d+))?", re.I)
T3_DONE_RE = re.compile(r"T3 done tokens=(\d+)(?: segment=(\d+)/(\d+))?", re.I)
S3GEN_RE = re.compile(r"s3gen: tokens=(\d+) meanflow=(\d+) model_steps=(\d+)", re.I)
BENCH_RE = re.compile(r"BENCH:\s*([A-Z0-9_]+)=([0-9.]+)(?:\s+([A-Z0-9_]+)=([0-9.]+))?", re.I)
def field(label: str, kind: str, default: Any, minimum: float | None = None, maximum: float | None = None, options: list[str] | None = None, multiline: bool = False) -> dict:
    return {key: value for key, value in {"label": label, "type": kind, "default": default, "min": minimum, "max": maximum, "options": options, "multiline": multiline}.items() if value is not None}
TTS_MIN_CONTEXT = 1280
TTS_RUNTIME = {"gpu_layers": 99, "context": 1536, "sessions": 1, "threads": 4}
VOICE_DEFAULTS = {
    "seed": 42, "max_tokens": 1000, "top_k": 0, "top_p": 1.0, "min_p": 0.05,
    "temperature": 0.8, "repeat_penalty": 1.2, "cfg_weight": 0.5,
    "exaggeration": 0.5, "cfm_steps": 5, "first_chunk": 75,
    "chunk": 150, "max_sentence_chars": 180,
}
VOICE_STYLES = {
    "natural": {"cfg_weight": 0.5, "exaggeration": 0.5},
    "expressive": {"cfg_weight": 0.3, "exaggeration": 0.7},
    "cross-language": {"cfg_weight": 0.0, "exaggeration": 0.5},
}
ASR_RUNTIME = {"threads": 4, "response_format": "json"}
BRAIN_RUNTIME = {"context": 2048, "parallel": 1, "fit_target": 3072}
BRAIN_GENERATION = {"temperature": 0.2, "top_p": 0.9, "top_k": 40, "min_p": 0.0, "repeat_penalty": 1.05, "seed": 42, "max_tokens": 160}
FIELDS = {
    "conversation.language": field("Conversation language", "string", "en", options=list(CONVERSATION_LANGUAGES)),
    "conversation.clone_voice": field("Experimental mic voice clone", "bool", False),
    "conversation.vad": field("Voice activity detection", "bool", False),
    "speech.language": field("Speech language", "string", "en", options=list(TTS_LANGUAGES)),
    "speech.style": field("Voice style", "string", "natural", options=list(VOICE_STYLES)),
    "speech.text": field("Text to speak", "string", "This is a multilingual voice synthesis test.", multiline=True),
    "tts.engine.gpu_layers": field("TTS GPU layers", "int", TTS_RUNTIME["gpu_layers"], 0, 999),
    "tts.engine.context": field("TTS context tokens", "int", TTS_RUNTIME["context"], TTS_MIN_CONTEXT, 8192),
    "tts.engine.sessions": field("TTS max sessions", "int", TTS_RUNTIME["sessions"], 1, 8),
    "tts.engine.threads": field("TTS CPU threads", "int", TTS_RUNTIME["threads"], 1, 64),
    "tts.sample.seed": field("TTS seed", "int", VOICE_DEFAULTS["seed"], 0, 2147483647),
    "tts.sample.max_tokens": field("TTS max tokens", "int", VOICE_DEFAULTS["max_tokens"], 16, 4096),
    "tts.sample.top_k": field("TTS top-k", "int", VOICE_DEFAULTS["top_k"], 0, 200),
    "tts.sample.top_p": field("TTS top-p", "float", VOICE_DEFAULTS["top_p"], 0.0, 1.0),
    "tts.sample.min_p": field("TTS min-p", "float", VOICE_DEFAULTS["min_p"], 0.0, 1.0),
    "tts.sample.temperature": field("TTS temperature", "float", VOICE_DEFAULTS["temperature"], 0.01, 5.0),
    "tts.sample.repeat_penalty": field("TTS repeat penalty", "float", VOICE_DEFAULTS["repeat_penalty"], 0.5, 2.0),
    "tts.sample.cfm_steps": field("TTS CFM steps", "int", VOICE_DEFAULTS["cfm_steps"], 1, 50),
    "tts.stream.first_chunk": field("TTS first chunk tokens", "int", VOICE_DEFAULTS["first_chunk"], 8, 1000),
    "tts.stream.chunk": field("TTS later chunk tokens", "int", VOICE_DEFAULTS["chunk"], 8, 1000),
    "tts.stream.max_sentence_chars": field("TTS max sentence chars", "int", VOICE_DEFAULTS["max_sentence_chars"], 16, 2000),
    "tts.style.natural.cfg_weight": field("Natural CFG weight", "float", VOICE_STYLES["natural"]["cfg_weight"], 0.0, 2.0),
    "tts.style.natural.exaggeration": field("Natural exaggeration", "float", VOICE_STYLES["natural"]["exaggeration"], 0.0, 2.0),
    "tts.style.expressive.cfg_weight": field("Expressive CFG weight", "float", VOICE_STYLES["expressive"]["cfg_weight"], 0.0, 2.0),
    "tts.style.expressive.exaggeration": field("Expressive exaggeration", "float", VOICE_STYLES["expressive"]["exaggeration"], 0.0, 2.0),
    "tts.style.cross-language.cfg_weight": field("Less-accent CFG weight", "float", VOICE_STYLES["cross-language"]["cfg_weight"], 0.0, 2.0),
    "tts.style.cross-language.exaggeration": field("Less-accent exaggeration", "float", VOICE_STYLES["cross-language"]["exaggeration"], 0.0, 2.0),
    "asr.threads": field("ASR CPU threads", "int", ASR_RUNTIME["threads"], 1, 64),
    "asr.vad.threshold": field("VAD RMS start", "float", 0.02, 0.001, 0.5),
    "asr.vad.silence_ms": field("VAD silence to end utterance", "int", 700, 200, 3000),
    "asr.vad.min_speech_ms": field("VAD minimum speech", "int", 400, 100, 5000),
    "brain.engine.context": field("Brain context tokens", "int", BRAIN_RUNTIME["context"], 256, 32768),
    "brain.engine.parallel": field("Brain parallel slots", "int", BRAIN_RUNTIME["parallel"], 1, 1),
    "brain.engine.fit_target": field("Brain GPU headroom MiB", "int", BRAIN_RUNTIME["fit_target"], 2048, 4096),
    "brain.sample.temperature": field("Brain temperature", "float", BRAIN_GENERATION["temperature"], 0.0, 2.0),
    "brain.sample.top_p": field("Brain top-p", "float", BRAIN_GENERATION["top_p"], 0.0, 1.0),
    "brain.sample.top_k": field("Brain top-k", "int", BRAIN_GENERATION["top_k"], 0, 200),
    "brain.sample.min_p": field("Brain min-p", "float", BRAIN_GENERATION["min_p"], 0.0, 1.0),
    "brain.sample.repeat_penalty": field("Brain repeat penalty", "float", BRAIN_GENERATION["repeat_penalty"], 0.5, 2.0),
    "brain.sample.seed": field("Brain seed", "int", BRAIN_GENERATION["seed"], 0, 2147483647),
    "brain.sample.max_tokens": field("Brain max tokens", "int", BRAIN_GENERATION["max_tokens"], 8, 2048),
}
PARAM_GROUPS = [
    {"id": "tts-engine", "title": "TTS engine", "apply": "Restart the TTS engine to apply GPU layers, context, sessions, and threads.", "fields": ["tts.engine.gpu_layers", "tts.engine.context", "tts.engine.sessions", "tts.engine.threads"]},
    {"id": "tts-sample", "title": "TTS sampling", "apply": "Applied on the next TTS WebSocket init.", "fields": ["tts.sample.seed", "tts.sample.max_tokens", "tts.sample.top_k", "tts.sample.top_p", "tts.sample.min_p", "tts.sample.temperature", "tts.sample.repeat_penalty", "tts.sample.cfm_steps"]},
    {"id": "tts-stream", "title": "TTS streaming and chunking", "apply": "Applied on the next TTS WebSocket init. C++ VoiceConfig already reads these fields.", "fields": ["tts.stream.first_chunk", "tts.stream.chunk", "tts.stream.max_sentence_chars"]},
    {"id": "tts-style", "title": "TTS style overlays", "apply": "Overlay cfg_weight and exaggeration for the selected Speech-lab style. Conversation uses the natural overlay.", "fields": ["tts.style.natural.cfg_weight", "tts.style.natural.exaggeration", "tts.style.expressive.cfg_weight", "tts.style.expressive.exaggeration", "tts.style.cross-language.cfg_weight", "tts.style.cross-language.exaggeration"]},
    {"id": "asr-engine", "title": "ASR engine", "apply": "Restart the ASR engine to apply thread count.", "fields": ["asr.threads"]},
    {"id": "asr-vad", "title": "ASR voice activity", "apply": "Applied on the next captured frame. No engine restart.", "fields": ["asr.vad.threshold", "asr.vad.silence_ms", "asr.vad.min_speech_ms"]},
    {"id": "brain-engine", "title": "Brain engine", "apply": "Restart the brain engine to apply context and GPU headroom. Parallel slots stay at 1.", "fields": ["brain.engine.context", "brain.engine.parallel", "brain.engine.fit_target"]},
    {"id": "brain-sample", "title": "Brain sampling", "apply": "Applied on the next /v1/chat/completions request.", "fields": ["brain.sample.temperature", "brain.sample.top_p", "brain.sample.top_k", "brain.sample.min_p", "brain.sample.repeat_penalty", "brain.sample.seed", "brain.sample.max_tokens"]},
]
BRAIN_FAMILIES = {
    "gemma4": {"reasoning_format": "none", "chat_template_kwargs": {"enable_thinking": False}},
    "qwen35": {"reasoning_format": "none", "chat_template_kwargs": {"enable_thinking": False}},
    "generic": {},
}
BRAINS = {
    "gemma": {"label": "GEMMA 4 E2B", "model": "gemma", "family": "gemma4"},
    "qwen35-0.8b": {"label": "QWEN3.5 0.8B", "model": "qwen35-0.8b", "family": "qwen35"},
    "qwen35-4b": {"label": "QWEN3.5 4B", "model": "qwen35-4b", "family": "qwen35"},
    "custom": {"label": "CUSTOM GGUF", "model": None, "family": "generic"},
}
CUSTOM_BRAIN = MODELS_DIR / "custom-brain.gguf"
CHATTERBOX_LIBRARY = CHATTERBOX / "build" / "Release" / "tts-cpp.lib"
TTS_SERVER = SERVER / "build" / "Release" / "tts-server.exe"
ENGINE_MODELS = {"tts": ("chatterbox-t3", "chatterbox-codec", "chatterbox-s3t"), "asr": ("parakeet",), "brain": ("gemma",)}
CONFIG_FILE = DATA / "config.json"
RECEIPTS_FILE = DATA / "models.json"
BRAIN_FILE = DATA / "brains.json"
LOCK = threading.RLock()
SUBSCRIBERS: set[queue.Queue] = set()
PROCESSES: dict[str, subprocess.Popen] = {}
PROCESS_TRACES: dict[str, dict[str, str]] = {}
RUNTIME = {
    "jobs": {},
    "engines": {name: {"status": "stopped", "error": "", "pid": None, "applied": {}} for name in ENGINE_MODELS},
    "lanes": {"a": {"status": "closed", "session": "", "request": "", "config_id": "", "trace_id": "", "turn_id": "", "source": "", "language": "", "style": "", "reference": {}, "samples": 0, "chunks": 0, "error": ""}},
    "results": {"asr": None, "brain": None, "turn": None},
    "flow": {"stage": "idle", "transcript": "", "answer": "", "error": "", "language": "en", "started": 0.0, "trace_id": "", "turn_id": ""},
    "trace": {"run_id": trace_run_id(), "latest": "", "latest_turn": ""},
    "reference_generation": 0,
}
class ApiError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
def identifier(value: Any, field: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if not text and not required:
        return ""
    if not IDENTIFIER_RE.fullmatch(text):
        raise ApiError(400, f"{field} is not a valid trace identifier")
    return text
def client_gone(exception: BaseException) -> bool:
    if isinstance(exception, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    return getattr(exception, "winerror", None) in (10053, 10054)
def validate(path: str, value: Any) -> Any:
    if path not in FIELDS:
        raise ApiError(400, f"unknown state path: {path}")
    spec = FIELDS[path]
    kind = spec["type"]
    if kind == "bool":
        if type(value) is not bool:
            raise ApiError(400, f"{path} must be bool")
    elif kind == "int":
        if type(value) is not int:
            raise ApiError(400, f"{path} must be int")
    elif kind == "float":
        if type(value) not in (int, float):
            raise ApiError(400, f"{path} must be number")
        value = float(value)
    elif type(value) is not str or not value.strip():
        raise ApiError(400, f"{path} must be non-empty string")
    if ("min" in spec and value < spec["min"]) or ("max" in spec and value > spec["max"]):
        raise ApiError(400, f"{path} is outside its allowed range")
    if "options" in spec and value not in spec["options"]:
        raise ApiError(400, f"{path} must be one of {spec['options']}")
    return value
def load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return deepcopy(default)
    value = json.loads(path.read_text(encoding="ascii"))
    if type(value) is not dict:
        raise RuntimeError(f"{path} must contain an object")
    return value
def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(value, separators=(",", ":"), ensure_ascii=True), encoding="ascii")
    os.replace(partial, path)
def atomic_bytes(path: Path, value: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_bytes(value)
    os.replace(partial, path)
def load_config() -> dict:
    defaults = {path: spec["default"] for path, spec in FIELDS.items()}
    stored = load_json(CONFIG_FILE, defaults)
    if type(stored) is not dict:
        raise RuntimeError(f"{CONFIG_FILE} must contain an object")
    # Older Trident releases defaulted T3 to 512 context tokens even though a
    # session may generate 1,000 speech tokens after a 100-250 token prompt.
    # Migrate that impossible configuration before applying the new range.
    stored_context = stored.get("tts.engine.context")
    if type(stored_context) is int and stored_context < TTS_MIN_CONTEXT:
        stored = stored | {"tts.engine.context": TTS_RUNTIME["context"]}
    merged = {path: stored[path] if path in stored else default for path, default in defaults.items()}
    checked = {path: validate(path, value) for path, value in merged.items()}
    if stored != checked:
        atomic_json(CONFIG_FILE, checked)
    return checked
def default_brains() -> dict:
    return {"active": "gemma", "custom": {"url": "", "path": "", "sha256": "", "size": 0, "family": "generic"}}
def load_brains() -> dict:
    defaults = default_brains()
    stored = load_json(BRAIN_FILE, defaults)
    if type(stored) is not dict:
        raise RuntimeError(f"{BRAIN_FILE} must contain an object")
    active = stored.get("active") if stored.get("active") in BRAINS else defaults["active"]
    custom_in = stored.get("custom") if type(stored.get("custom")) is dict else {}
    custom = defaults["custom"] | {key: custom_in[key] for key in defaults["custom"] if key in custom_in}
    if custom.get("family") not in BRAIN_FAMILIES:
        custom["family"] = "generic"
    checked = {"active": active, "custom": custom}
    if stored != checked:
        atomic_json(BRAIN_FILE, checked)
    return checked
def save_brains():
    atomic_json(BRAIN_FILE, BRAIN_STATE)
CONFIG = load_config()
RECEIPTS = load_json(RECEIPTS_FILE, {})
BRAIN_STATE = load_brains()
def executable(name: str) -> str | None:
    local = {"git": TOOLS / "git" / "cmd" / "git.exe", "cmake": TOOLS / "cmake-4.4.2-windows-x86_64" / "bin" / "cmake.exe"}.get(name)
    return str(local) if local and local.is_file() else shutil.which(name)
def msvc_path() -> Path | None:
    root = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio"
    matches = sorted(root.glob("*/BuildTools/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe"), reverse=True)
    return matches[0] if matches else None
def vulkan_path() -> Path | None:
    roots = [Path(os.environ["VULKAN_SDK"])] if os.environ.get("VULKAN_SDK") else []
    roots += [TOOLS / "VulkanSDK" / VULKAN_VERSION]
    roots += sorted(Path("C:/VulkanSDK").glob("*"), reverse=True)
    return next((path for path in roots if (path / "Include/vulkan/vulkan.h").is_file() and (path / "Lib/vulkan-1.lib").is_file()), None)
def prerequisites() -> dict:
    paths = {"python": Path(sys.executable), "git": executable("git"), "cmake": executable("cmake"), "msvc": msvc_path(), "vulkan": vulkan_path()}
    return {name: {"status": "ready" if path else "missing", "path": str(path or "")} for name, path in paths.items()}
def model_path(name: str) -> Path:
    spec = MODELS[name]
    root = DATA if spec.get("directory") == "data" else MODELS_DIR
    return root / spec["file"]
def model_status(name: str) -> dict:
    spec = MODELS[name]
    path = model_path(name)
    size = path.stat().st_size if path.is_file() else 0
    verified = size == spec["size"] and RECEIPTS.get(name) == spec["sha256"]
    return {"status": "ready" if verified else "unverified" if size == spec["size"] else "missing", "path": str(path), "bytes": size, "size": spec["size"], "sha256": spec["sha256"], "revision": spec.get("revision", "")}
def tts_build_id() -> str:
    digest = hashlib.sha256((SOURCES["chatterbox"][1] + SOURCES["ggml"][1]).encode())
    for path in [PATCHES / "chatterbox.patch", SERVER / "CMakeLists.txt", *sorted((SERVER / "include").glob("*.hpp")), *sorted((SERVER / "src").glob("*.cpp"))]:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
def component_artifact(name: str) -> Path:
    spec = {"tts": {"exe": "tts-server.exe"}, **BINARIES}[name]
    root = RUNTIMES / name
    matches = [path for path in root.rglob("*") if path.is_file() and path.name.lower() == spec["exe"].lower()] if root.is_dir() else []
    return matches[0] if len(matches) == 1 else root / spec["exe"]
def component_status(name: str) -> dict:
    path = component_artifact(name)
    revision = SOURCES["chatterbox"][1] if name == "tts" else BINARIES[name]["tag"]
    status = "ready" if path.is_file() else "missing"
    if name == "tts" and status == "ready":
        receipt = load_json(path.parent / "build.json", {})
        status = "ready" if receipt.get("build_id") == tts_build_id() else "unverified"
    return {"status": status, "path": str(path), "revision": revision}
def reference_path() -> Path:
    custom = DATA / "reference.wav"
    if custom.is_file():
        return custom
    default = model_path("reference")
    if model_status("reference")["status"] == "ready":
        return default
    if default.is_file():
        raise ApiError(409, "default reference is present but not verified; download DEFAULT VOICE again")
    raise ApiError(409, "default reference is missing; download DEFAULT VOICE")
def bump_reference():
    with LOCK:
        RUNTIME["reference_generation"] = int(RUNTIME.get("reference_generation", 0)) + 1
def reference_state() -> dict:
    custom = DATA / "reference.wav"
    path = custom if custom.is_file() else model_path("reference")
    if not path.is_file():
        return {"status": "missing", "path": str(path), "duration": 0.0, "custom": False}
    if path != custom and model_status("reference")["status"] != "ready":
        return {"status": "unverified", "path": str(path), "duration": 0.0, "custom": False}
    try:
        with wave.open(str(path), "rb") as audio:
            valid = audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getcomptype() == "NONE"
            duration = audio.getnframes() / float(audio.getframerate() or 1)
        if not valid or duration < 5:
            return {"status": "invalid", "path": str(path), "duration": duration, "custom": path == custom}
    except (wave.Error, OSError):
        return {"status": "invalid", "path": str(path), "duration": 0.0, "custom": path == custom}
    return {"status": "ready", "path": str(path), "duration": duration, "custom": path == custom}
def reference_evidence() -> dict:
    state = reference_state()
    if state["status"] != "ready":
        return {**state, "generation": RUNTIME.get("reference_generation", 0), "sha256": "", "bytes": 0}
    path = Path(state["path"])
    audio = path.read_bytes()
    return {
        **state,
        "generation": RUNTIME.get("reference_generation", 0),
        "sha256": hashlib.sha256(audio).hexdigest(),
        "bytes": len(audio),
        "metrics": wav_metrics(audio),
    }
def custom_brain_ready() -> bool:
    custom = BRAIN_STATE["custom"]
    path = Path(custom["path"]) if custom.get("path") else CUSTOM_BRAIN
    return path.is_file() and custom.get("sha256") and sha256(path) == custom["sha256"] and path.stat().st_size == int(custom.get("size") or 0)
def active_brain_id() -> str:
    active = BRAIN_STATE.get("active")
    return active if active in BRAINS else "gemma"
def active_brain_family() -> str:
    active = active_brain_id()
    if active == "custom":
        family = BRAIN_STATE["custom"].get("family") or "generic"
        return family if family in BRAIN_FAMILIES else "generic"
    return BRAINS[active]["family"]
def active_brain_path() -> Path:
    active = active_brain_id()
    if active == "custom":
        custom = BRAIN_STATE["custom"]
        path = Path(custom["path"]) if custom.get("path") else CUSTOM_BRAIN
        if not custom_brain_ready():
            raise ApiError(409, "custom brain GGUF is missing or unverified")
        return path
    name = BRAINS[active]["model"]
    if model_status(name)["status"] != "ready":
        raise ApiError(409, f"brain model is not verified: {model_path(name)}")
    return model_path(name)
def brain_snapshot() -> dict:
    active = active_brain_id()
    spec = BRAINS[active]
    custom = deepcopy(BRAIN_STATE["custom"])
    custom_path = Path(custom["path"]) if custom.get("path") else CUSTOM_BRAIN
    custom.update({"status": "ready" if custom_brain_ready() else "missing", "path": str(custom_path)})
    if active == "custom":
        ready = custom["status"] == "ready"
        path = str(custom_path)
        model = "custom"
    else:
        model = spec["model"]
        status = model_status(model)
        ready = status["status"] == "ready"
        path = status["path"]
    return {
        "active": active,
        "model": model,
        "label": spec["label"],
        "family": active_brain_family(),
        "path": path,
        "ready": ready,
        "catalog": {name: {"label": value["label"], "model": value["model"], "family": value["family"]} for name, value in BRAINS.items()},
        "custom": custom,
    }
def snapshot() -> dict:
    with LOCK:
        engines = deepcopy(RUNTIME["engines"])
        for name, process in PROCESSES.items():
            engines[name]["pid"] = process.pid
        return {
            "prerequisites": prerequisites(),
            "components": {name: component_status(name) for name in ("tts", "parakeet", "gemma")},
            "models": {name: model_status(name) for name in MODELS},
            "engines": engines,
            "config": deepcopy(CONFIG),
            "reference": reference_state(),
            "reference_generation": RUNTIME.get("reference_generation", 0),
            "brain": brain_snapshot(),
            "lanes": deepcopy(RUNTIME["lanes"]),
            "results": deepcopy(RUNTIME["results"]),
            "flow": deepcopy(RUNTIME["flow"]),
            "trace": deepcopy(RUNTIME["trace"]),
            "jobs": deepcopy(RUNTIME["jobs"]),
        }
def emit(event: str, data: dict):
    with LOCK:
        for subscriber in SUBSCRIBERS:
            subscriber.put((event, data))
set_trace_listener(lambda entry: emit("trace", entry))
def emit_state():
    emit("state", snapshot())
def set_flow(stage: str, *, transcript: str | None = None, answer: str | None = None, failure: str | None = None, language: str | None = None, trace_id: str | None = None, turn_id: str | None = None):
    with LOCK:
        flow = RUNTIME["flow"]
        previous = flow["stage"]
        flow["stage"] = stage
        if transcript is not None:
            flow["transcript"] = transcript
        if answer is not None:
            flow["answer"] = answer
        if failure is not None:
            flow["error"] = failure
        if language is not None:
            flow["language"] = language
        if trace_id is not None:
            flow["trace_id"] = trace_id
            RUNTIME["trace"]["latest"] = trace_id
        if turn_id is not None:
            flow["turn_id"] = turn_id
            RUNTIME["trace"]["latest_turn"] = turn_id
        if stage == "listening":
            flow["started"] = time.time()
            flow["transcript"] = ""
            flow["answer"] = ""
            flow["error"] = ""
        current_trace = flow.get("trace_id", "")
        current_turn = flow.get("turn_id", "")
    info("pipeline", "pipeline.stage", {"from": previous, "to": stage, "language": language or flow.get("language", ""), "transcript_chars": len(transcript or "") if transcript is not None else None, "answer_chars": len(answer or "") if answer is not None else None, "error": failure or ""}, trace_id=current_trace, turn_id=current_turn)
    emit_state()
def set_job(key: str, status: str, stage: str, progress: int, message: str, failure: str = "", job_id: str = ""):
    with LOCK:
        previous = RUNTIME["jobs"].get(key, {})
        job_id = job_id or str(previous.get("job_id") or new_trace_id("job"))
        RUNTIME["jobs"][key] = {"status": status, "stage": stage, "progress": progress, "message": message, "error": failure, "job_id": job_id}
        current = deepcopy(RUNTIME["jobs"][key])
    important = status != previous.get("status") or stage != previous.get("stage") or progress == 100 or progress // 10 != int(previous.get("progress") or 0) // 10
    if important:
        (error if status == "error" else info)("job", "job.progress", {"key": key, **current}, job_id=job_id)
    emit("job", {"key": key, **current})
def start_job(kind: str, name: str, work: Callable[[str], None]):
    key = f"{kind}:{name}"
    with LOCK:
        if RUNTIME["jobs"].get(key, {}).get("status") == "running":
            raise ApiError(409, f"{key} is already running")
    job_id = new_trace_id("job")
    set_job(key, "running", "start", 0, f"starting {name}", job_id=job_id)
    def worker():
        with trace_scope(job_id=job_id):
            try:
                work(key)
                set_job(key, "done", "done", 100, f"{name} complete", job_id=job_id)
            except Exception as exception:
                message = str(exception)
                error("job", "job.failed", {"key": key, "error": message}, job_id=job_id)
                set_job(key, "error", "error", 0, message, message, job_id=job_id)
            emit_state()
    threading.Thread(target=worker, daemon=True).start()
    return job_id
def build_env() -> dict:
    env = os.environ.copy()
    sdk = vulkan_path()
    if not sdk:
        raise RuntimeError("Vulkan SDK is missing")
    env["VULKAN_SDK"] = str(sdk)
    paths = [str(sdk / "Bin")]
    for name in ("git", "cmake"):
        path = executable(name)
        if not path:
            raise RuntimeError(f"{name} is missing")
        paths.append(str(Path(path).parent))
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    return env
def line_level(text: str) -> str:
    lower = text.lower()
    if "fatal" in lower or lower.startswith("error") or " error " in f" {lower} " or " e " in lower[:40]:
        return "error"
    if "warning" in lower or "could not find" in lower or lower.startswith("w ") or " w " in lower[:40]:
        return "warn"
    return "info"
def run(component: str, stage: str, command: list[str], cwd: Path, env: dict | None = None):
    started = time.monotonic()
    info(component, "stage", {"stage": stage, "status": "start", "command": command, "cwd": str(cwd)})
    process = subprocess.Popen(command, cwd=cwd, env=env or build_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    tail = failure = ""
    suppressed, warnings = 0, {}
    if not process.stdout:
        raise RuntimeError(f"{component} {stage} has no output pipe")
    for raw in process.stdout:
        tail = raw.rstrip()
        level = line_level(tail)
        if level == "error":
            failure = tail
            error(component, tail, {"stage": stage})
        elif level == "warn":
            match = re.search(r"\b(C\d{4})\b", tail)
            key = match.group(1) if match else tail[:96]
            warnings[key] = warnings.get(key, 0) + 1
            if warnings[key] == 1: warn(component, tail, {"stage": stage})
        elif tail and any(token in tail.lower() for token in BUILD_LOG_TOKENS):
            info(component, tail, {"stage": stage})
        else:
            suppressed += bool(tail)
    code = process.wait()
    data = {"stage": stage, "status": "done" if not code else "failed", "code": code, "seconds": round(time.monotonic() - started, 3), "suppressed": suppressed, "warnings": warnings}
    (info if not code else error)(component, "stage", data)
    if code:
        raise RuntimeError(f"{component} {stage} exited {code}: {failure or tail}")
def checkout(component: str, path: Path, source: str):
    url, revision = SOURCES[source]
    git = executable("git")
    if not git:
        raise RuntimeError("git is missing")
    if path.exists() and not (path / ".git").is_dir():
        raise RuntimeError(f"non-git path blocks checkout: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        run(component, f"clone-{source}", [git, "clone", "--filter=blob:none", "--no-checkout", url, str(path)], path.parent)
    run(component, f"fetch-{source}", [git, "fetch", "--depth", "1", "origin", revision], path)
    run(component, f"checkout-{source}", [git, "checkout", "--detach", revision], path)
    run(component, f"reset-{source}", [git, "reset", "--hard", revision], path)
    run(component, f"clean-{source}", [git, "clean", "-fdx"], path)
def apply_chatterbox_patch(cwd: Path):
    git = executable("git")
    if not git:
        raise RuntimeError("git is missing")
    patch_text = (PATCHES / "chatterbox.patch").read_text(encoding="ascii")
    patch_text = patch_text.replace("__EM_DASH__", "\u2014").replace("__SECTION_SIGN__", "\u00a7").replace("__BLANK_CONTEXT__", "")
    tmp = cwd / ".apply-chatterbox.patch"
    tmp.write_text(patch_text, encoding="utf-8", newline="\n")
    try:
        run("tts", "patch", [git, "apply", "--unidiff-zero", str(tmp)], cwd)
    finally:
        tmp.unlink(missing_ok=True)
def require_build_tools():
    missing = [name for name, value in prerequisites().items() if name in ("git", "cmake", "msvc", "vulkan") and value["status"] != "ready"]
    if missing:
        raise RuntimeError("missing TTS build prerequisites: " + ", ".join(missing))
def github_release_asset(spec: dict) -> tuple[str, int, str]:
    repo = urllib.parse.quote(spec["repo"], safe="/")
    tag = urllib.parse.quote(spec["tag"], safe="")
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "trident/1", "X-GitHub-Api-Version": "2026-03-10"})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    if release.get("tag_name") != spec["tag"] or release.get("draft"):
        raise RuntimeError(f"unexpected GitHub release metadata for {spec['repo']} {spec['tag']}")
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == spec["asset"]]
    if len(matches) != 1:
        raise RuntimeError(f"GitHub release asset not found exactly once: {spec['asset']}")
    asset = matches[0]
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise RuntimeError(f"GitHub did not provide a SHA-256 digest for {spec['asset']}")
    size = int(asset.get("size") or 0)
    download = str(asset.get("browser_download_url") or "")
    if size <= 0 or not download.startswith("https://github.com/"):
        raise RuntimeError(f"invalid GitHub release metadata for {spec['asset']}")
    return download, size, digest.removeprefix("sha256:")
def extract_release_bundle(archive: Path, destination: Path, executable_name: str):
    partial = destination.with_name(destination.name + ".part")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    root = partial.resolve()
    try:
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                target = (partial / member.filename).resolve()
                if target != root and root not in target.parents:
                    raise RuntimeError(f"unsafe ZIP member: {member.filename}")
            package.extractall(partial)
        matches = [path for path in partial.rglob("*") if path.is_file() and path.name.lower() == executable_name.lower()]
        if len(matches) != 1:
            raise RuntimeError(f"release bundle must contain exactly one {executable_name}; found {len(matches)}")
        if destination.exists():
            shutil.rmtree(destination)
        partial.rename(destination)
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise
def install_release_binary(name: str, key: str):
    spec = BINARIES[name]
    set_job(key, "running", "metadata", 5, f"checking pinned {spec['tag']} release")
    url, size, digest = github_release_asset(spec)
    archive = TOOLS / "downloads" / spec["asset"]
    fetch(url, archive, size, digest, key)
    set_job(key, "running", "extract", 92, f"extracting {name} Vulkan bundle")
    extract_release_bundle(archive, RUNTIMES / name, spec["exe"])
    archive.unlink(missing_ok=True)
    if not component_artifact(name).is_file():
        raise RuntimeError(f"release did not create {spec['exe']}")
def install_component(name: str, key: str):
    if name in BINARIES:
        install_release_binary(name, key)
        return
    if name != "tts":
        raise RuntimeError(f"unknown component: {name}")
    require_build_tools()
    cmake = executable("cmake")
    if not cmake:
        raise RuntimeError("cmake is missing")
    set_job(key, "running", "source", 5, "checking out Chatterbox")
    checkout(name, CHATTERBOX, "chatterbox")
    apply_chatterbox_patch(CHATTERBOX)
    set_job(key, "running", "ggml", 18, "checking out ggml")
    checkout(name, GGML, "ggml")
    set_job(key, "running", "configure", 30, "configuring Chatterbox Vulkan")
    run(name, "configure", [cmake, "-S", ".", "-B", "build", "-A", "x64", "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=OFF"], CHATTERBOX)
    set_job(key, "running", "build", 48, "building Chatterbox")
    run(name, "build", [cmake, "--build", "build", "--config", "Release", "--target", "tts-cpp", "mtl_tokenizer", "--parallel"], CHATTERBOX)
    if not CHATTERBOX_LIBRARY.is_file():
        raise RuntimeError(f"Chatterbox build did not create {CHATTERBOX_LIBRARY}")
    set_job(key, "running", "server-configure", 70, "configuring TTS server")
    run(name, "server-configure", [cmake, "-S", ".", "-B", "build", "-A", "x64", f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}"], SERVER)
    set_job(key, "running", "server-build", 82, "building TTS server")
    run(name, "server-build", [cmake, "--build", "build", "--config", "Release", "--parallel"], SERVER)
    if not TTS_SERVER.is_file():
        raise RuntimeError(f"TTS build did not create {TTS_SERVER}")
    runtime = RUNTIMES / "tts"
    shutil.rmtree(runtime, ignore_errors=True)
    runtime.mkdir(parents=True)
    for artifact in TTS_SERVER.parent.iterdir():
        if artifact.is_file() and (artifact.name == TTS_SERVER.name or artifact.suffix.lower() == ".dll"):
            shutil.copy2(artifact, runtime / artifact.name)
    atomic_json(runtime / "build.json", {"build_id": tts_build_id(), "chatterbox": SOURCES["chatterbox"][1], "ggml": SOURCES["ggml"][1]})
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
def fetch(url: str, destination: Path, size: int, digest: str, key: str):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and (not size or destination.stat().st_size == size) and sha256(destination) == digest:
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
    hasher = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
        if response.status != 200:
            raise RuntimeError(f"download returned HTTP {response.status}: {url}")
        for block in iter(lambda: response.read(1024 * 1024), b""):
            output.write(block)
            hasher.update(block)
            done += len(block)
            set_job(key, "running", "download", done * 90 // size if size else min(89, done // (4 * 1024 * 1024)), f"{done} / {size} bytes" if size else f"{done} bytes")
    if size and done != size:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"download size mismatch: expected {size}, got {done}")
    actual = hasher.hexdigest()
    if actual != digest:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"download SHA-256 mismatch: expected {digest}, got {actual}")
    os.replace(partial, destination)
def install_prerequisite(name: str, key: str):
    if prerequisites()[name]["status"] == "ready":
        return
    if name == "python":
        raise RuntimeError("Python must be installed before running main.py")
    spec = PACKAGES[name]
    archive = TOOLS / "downloads" / spec["file"]
    fetch(spec["url"], archive, spec["size"], spec["sha256"], key)
    set_job(key, "running", "install", 92, f"installing {name}")
    if name == "git":
        destination = TOOLS / "git"
        if destination.exists():
            shutil.rmtree(destination)
        with zipfile.ZipFile(archive) as package:
            package.extractall(destination)
    elif name == "cmake":
        destination = TOOLS / "cmake-4.4.2-windows-x86_64"
        if destination.exists():
            shutil.rmtree(destination)
        with zipfile.ZipFile(archive) as package:
            package.extractall(TOOLS)
    elif name == "msvc":
        run(name, "install", [str(archive), "--quiet", "--wait", "--norestart", "--nocache", "--add", "Microsoft.VisualStudio.Workload.VCTools", "--includeRecommended"], ROOT, os.environ.copy())
    else:
        destination = TOOLS / "VulkanSDK" / VULKAN_VERSION
        run(name, "install", [str(archive), "--root", str(destination), "--accept-licenses", "--default-answer", "--confirm-command", "install"], ROOT, os.environ.copy())
    if prerequisites()[name]["status"] != "ready":
        raise RuntimeError(f"{name} installer completed but prerequisite is still missing")
def download_model(name: str, key: str):
    spec = MODELS[name]
    destination = model_path(name)
    if spec.get("source"):
        source = ROOT / spec["source"]
        if not source.is_file():
            raise RuntimeError(f"bundled asset missing: {source}")
        if source.stat().st_size != spec["size"] or sha256(source) != spec["sha256"]:
            raise RuntimeError("bundled default voice does not match its pin")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        set_job(key, "running", "copy", 90, f"installed {spec['file']}")
    else:
        url = spec.get("url") or f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{spec['file']}"
        fetch(url, destination, spec["size"], spec["sha256"], key)
    with LOCK:
        RECEIPTS[name] = spec["sha256"]
        atomic_json(RECEIPTS_FILE, RECEIPTS)
    if name == "reference":
        bump_reference()
def parsed_value(value: str) -> Any:
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value
def native_key_values(message: str) -> dict:
    return {key.lower(): parsed_value(value) for key, value in re.findall(r"([A-Za-z][A-Za-z0-9_]*)=([^\s]+)", message)}
def log_native_line(name: str, message: str):
    with LOCK:
        active = deepcopy(PROCESS_TRACES.get(name, {}))
    if message.startswith(NATIVE_EVENT_PREFIX):
        try:
            payload = json.loads(message[len(NATIVE_EVENT_PREFIX):])
            if type(payload) is not dict:
                raise ValueError("native event must be an object")
        except (json.JSONDecodeError, ValueError) as exception:
            error(name, "native.event.invalid", {"raw": message, "error": str(exception)}, source=f"{name}-process", **active)
            return
        for field in ("trace_id", "turn_id", "config_id", "session_id", "request_id", "lane", "client_id"):
            if payload.get(field):
                active[field] = str(payload[field])
        event_name = str(payload.get("event") or "")
        with LOCK:
            if active:
                PROCESS_TRACES[name] = active
        ingest_trace(name, payload, source=f"{name}-native", **active)
        if event_name in ("tts.synthesis.completed", "tts.synthesis.failed", "tts.synthesis.cancelled"):
            with LOCK:
                current = PROCESS_TRACES.get(name, {})
                for field in ("trace_id", "turn_id", "request_id"):
                    current.pop(field, None)
        return
    level = line_level(message)
    event_name = f"{name}.stdout"
    data: dict[str, Any] = {}
    match = T3_STOP_RE.search(message)
    if match:
        event_name = "tts.t3.stopped"
        reason, prompt, n_past, speech_position, generated, final_token = match.groups()
        data = {"reason": reason, "prompt_tokens": int(prompt), "global_position": int(n_past), "speech_position": int(speech_position), "generated_tokens": int(generated)}
        if final_token is not None:
            data["final_token"] = int(final_token)
        level = "warn" if reason in ("context_limit", "max_tokens", "repetition_guard", "step_error") else "info"
    elif (match := T3_DONE_RE.search(message)):
        event_name = "tts.t3.generated"
        tokens, segment, segments = match.groups()
        data = {"speech_tokens": int(tokens), "segment": int(segment or 1), "segments": int(segments or 1)}
    elif (match := S3GEN_RE.search(message)):
        event_name = "tts.s3gen.started"
        data = {"speech_tokens": int(match.group(1)), "meanflow": bool(int(match.group(2))), "model_steps": int(match.group(3))}
    elif (match := BENCH_RE.search(message)):
        event_name = "tts.native.benchmark"
        data = {match.group(1).lower(): float(match.group(2))}
        if match.group(3):
            data[match.group(3).lower()] = float(match.group(4))
    elif "speaker_emb from VoiceEncoder" in message:
        event_name = "tts.reference.t3_speaker_embedding"
        data = {"origin": "reference_voice_encoder", **native_key_values(message)}
    elif "prompt_token from S3TokenizerV2" in message:
        event_name = "tts.reference.prompt_tokens"
        counts = [int(value) for value in re.findall(r"\((\d+),\)", message)]
        data = {"origin": "reference_s3tokenizer", "s3gen_prompt_tokens": counts[0] if counts else 0, "t3_conditioning_tokens": counts[1] if len(counts) > 1 else 0}
    elif "prompt_feat from reference_audio" in message:
        event_name = "tts.reference.s3gen_prompt_features"
        match = re.search(r"\((\d+),\s*80\)", message)
        data = {"origin": "reference_audio", "mel_frames": int(match.group(1)) if match else 0, "mel_bins": 80}
    elif "voice conditioning" in message.lower():
        event_name = "tts.reference.conditioning_summary"
        data = native_key_values(message)
    elif "T3 prompt ok" in message:
        event_name = "tts.t3.prompt_ready"
        data = native_key_values(message)
    elif "T3 first token" in message:
        event_name = "tts.t3.first_token"
        data = native_key_values(message)
    elif "auto-split:" in message:
        event_name = "tts.text.segmented"
        data = native_key_values(message)
        match = re.search(r"auto-split:\s*(\d+) segments", message)
        if match:
            data["segments"] = int(match.group(1))
    elif message.startswith("METRIC "):
        event_name = "tts.audio.native_metrics"
        data = native_key_values(message)
    elif "listening on" in message.lower() or "server is listening" in message.lower():
        event_name = f"{name}.listening"
        data = {"raw": message}
    important = level != "info" or any(token in message.lower() for token in ENGINE_LOG_TOKENS)
    trace(level if important else "debug", name, event_name, data, message=message, source=f"{name}-process", **active)
def log_process(name: str, process: subprocess.Popen):
    message = ""
    if not process.stdout:
        raise RuntimeError(f"{name} has no output pipe")
    for raw in process.stdout:
        message = raw.rstrip()
        if message:
            log_native_line(name, message)
        with LOCK:
            if PROCESSES.get(name) is process:
                RUNTIME["engines"][name]["message"] = message
    code = process.wait()
    expected = True
    with LOCK:
        if PROCESSES.get(name) is process:
            expected = False
            PROCESSES.pop(name)
            PROCESS_TRACES.pop(name, None)
            RUNTIME["engines"][name].update(status="error", error=f"process exited {code}: {message}", pid=None)
    (info if expected and code == 0 else error)(name, "engine.process_exited", {"code": code, "last_message": message, "expected": expected}, source=f"{name}-process")
    emit_state()
def remote(url: str, body: bytes | None = None, content_type: str = "application/json", timeout: int = 600) -> bytes:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type} if body is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exception:
        detail = exception.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exception.code} from {url}: {detail}") from exception
def wait_ready(name: str, process: subprocess.Popen, url: str):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited {process.returncode} during load")
        try:
            remote(url, timeout=1)
            return
        except (urllib.error.URLError, TimeoutError, RuntimeError):
            time.sleep(.25)
    raise RuntimeError(f"{name} did not become ready within 600 seconds")
def stop_engine(name: str):
    with LOCK:
        process = PROCESSES.pop(name, None)
        PROCESS_TRACES.pop(name, None)
        RUNTIME["engines"][name].update(status="stopping", error="")
    info("engine", "engine.stop_requested", {"engine": name, "pid": process.pid if process else None})
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            warn(name, "terminate timed out; killing", {"pid": process.pid})
            process.kill()
            process.wait(5)
    with LOCK:
        RUNTIME["engines"][name].update(status="stopped", error="", pid=None, applied={})
        if name == "tts":
            for lane in RUNTIME["lanes"].values():
                lane.update(status="closed", session="", request="", config_id="", trace_id="", turn_id="", source="", language="", style="", reference={}, samples=0, chunks=0, error="")
    if process:
        info("engine", "engine.stopped", {"engine": name, "pid": process.pid})
def load_engine(name: str, key: str):
    stop_engine(name)
    if name == "brain":
        paths = [active_brain_path()]
    else:
        paths = [model_path(model) for model in ENGINE_MODELS[name]]
        for model, model_file in zip(ENGINE_MODELS[name], paths):
            if model_status(model)["status"] != "ready":
                raise RuntimeError(f"model is not verified: {model_file}")
    executable_path = component_artifact("tts" if name == "tts" else "parakeet" if name == "asr" else "gemma")
    if not executable_path.is_file():
        raise RuntimeError(f"component is missing: {executable_path}")
    if name == "tts":
        runtime = {key: CONFIG[f"tts.engine.{key}"] for key in ("gpu_layers", "context", "sessions", "threads")}
        applied = {"runtime": runtime}
        command = [str(executable_path), "--port", "8095", "--model", str(paths[0]), "--s3gen-gguf", str(paths[1]), "--n-gpu-layers", str(runtime["gpu_layers"]), "--context", str(runtime["context"]), "--max-sessions", str(runtime["sessions"]), "--threads", str(runtime["threads"])]
        cwd, health, env = executable_path.parent, "http://127.0.0.1:8095/health", os.environ.copy()
    elif name == "asr":
        applied = {"threads": CONFIG["asr.threads"]}
        command = [str(executable_path), "--model", str(paths[0]), "--host", "127.0.0.1", "--port", "8097", "--threads", str(applied["threads"])]
        cwd, health, env = executable_path.parent, "http://127.0.0.1:8097/health", os.environ.copy()
        env["PARAKEET_DEVICE"] = "Vulkan0"
    else:
        runtime = {"context": CONFIG["brain.engine.context"], "fit_target": CONFIG["brain.engine.fit_target"]}
        applied = {**runtime, "parallel": 1, "id": active_brain_id(), "family": active_brain_family(), "path": str(paths[0])}
        command = [str(executable_path), "-m", str(paths[0]), "--host", "127.0.0.1", "--port", "8098", "--device", "Vulkan0", "--n-gpu-layers", "all", "--ctx-size", str(runtime["context"]), "--parallel", "1", "--no-mmproj", "--load-mode", "auto", "--flash-attn", "on", "--repack", "--fit", "on", "--fit-target", str(runtime["fit_target"]), "--fit-ctx", "2048"]
        cwd, health, env = executable_path.parent, "http://127.0.0.1:8098/health", os.environ.copy()
    set_job(key, "running", "load", 20, f"loading {name}")
    info("engine", "engine.launch", {"engine": name, "command": command, "cwd": str(cwd), "applied": applied})
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    with LOCK:
        PROCESSES[name] = process
        PROCESS_TRACES[name] = {}
        RUNTIME["engines"][name].update(status="loading", error="", pid=process.pid, applied=applied)
    threading.Thread(target=log_process, args=(name, process), daemon=True).start()
    wait_ready(name, process, health)
    with LOCK:
        RUNTIME["engines"][name]["status"] = "running"
    info("engine", "engine.ready", {"engine": name, "pid": process.pid, "health": health, "applied": applied})
    set_job(key, "running", "ready", 95, f"{name} ready")
def multipart(audio: bytes) -> tuple[bytes, str]:
    boundary = "trident-" + uuid.uuid4().hex
    fields = [("file", "speech.wav", "audio/wav", audio), ("response_format", "", "text/plain", b"json")]
    body = bytearray()
    for name, filename, kind, value in fields:
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"".encode())
        if filename:
            body.extend(f"; filename=\"{filename}\"".encode())
        body.extend(f"\r\nContent-Type: {kind}\r\n\r\n".encode())
        body.extend(value)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"
def require_engine(name: str):
    if name not in ENGINE_MODELS:
        raise ApiError(400, f"unknown engine: {name}")
    if RUNTIME["engines"][name]["status"] != "running":
        raise ApiError(409, f"{name} is not running")
def transcribe(audio: bytes, *, trace_id: str = "", turn_id: str = "") -> dict:
    require_engine("asr")
    started = time.monotonic()
    audio_data = {"bytes": len(audio), "sha256": hashlib.sha256(audio).hexdigest()}
    try:
        audio_data["metrics"] = wav_metrics(audio)
    except (wave.Error, EOFError):
        pass
    info("asr", "asr.requested", audio_data, trace_id=trace_id, turn_id=turn_id)
    body, content_type = multipart(audio)
    result = json.loads(remote("http://127.0.0.1:8097/v1/audio/transcriptions", body, content_type))
    with LOCK:
        RUNTIME["results"]["asr"] = result
    transcript = str(result.get("text") or "")
    info("asr", "asr.completed", {"duration_ms": round((time.monotonic() - started) * 1000, 3), "transcript": transcript, "characters": len(transcript), "result": result}, trace_id=trace_id, turn_id=turn_id)
    emit_state()
    return result
def brain(prompt: str, language: str, *, trace_id: str = "", turn_id: str = "") -> dict:
    require_engine("brain")
    if language not in CONVERSATION_LANGUAGES:
        raise ApiError(400, f"unsupported conversation language: {language}")
    language_name = CONVERSATION_LANGUAGES[language]
    system = (
        f"Reply in {language_name} ({language}). Give one or two short, natural spoken sentences. "
        "Do not analyze, list options, add a preamble, or mention transcription."
    )
    request = {
        "model": active_brain_id(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        **{key: CONFIG[f"brain.sample.{key}"] for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "seed", "max_tokens")},
        "stream": False,
        **BRAIN_FAMILIES[active_brain_family()],
    }
    started = time.monotonic()
    info("brain", "brain.requested", {"language": language, "language_name": language_name, "brain": active_brain_id(), "family": active_brain_family(), "system": system, "prompt": prompt, "sampling": {key: request[key] for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "seed", "max_tokens")}}, trace_id=trace_id, turn_id=turn_id)
    result = json.loads(remote("http://127.0.0.1:8098/v1/chat/completions", json.dumps(request, separators=(",", ":")).encode()))
    with LOCK:
        RUNTIME["results"]["brain"] = result
    response = brain_reply_text(result)
    info("brain", "brain.completed", {"duration_ms": round((time.monotonic() - started) * 1000, 3), "language": language, "response": response, "characters": len(response), "finish_reason": ((result.get("choices") or [{}])[0]).get("finish_reason"), "usage": result.get("usage", {}), "timings": result.get("timings", {})}, trace_id=trace_id, turn_id=turn_id)
    emit_state()
    return result
def validate_wav(data: bytes):
    partial = DATA / "reference.wav.part"
    DATA.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(data)
    try:
        with wave.open(str(partial), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getcomptype() != "NONE" or audio.getnframes() / audio.getframerate() < 5:
                raise ApiError(400, "reference must be mono PCM16 WAV at least 5 seconds long")
    except (EOFError, wave.Error) as exception:
        partial.unlink(missing_ok=True)
        raise ApiError(400, f"invalid WAV: {str(exception) or 'truncated header'}") from exception
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, DATA / "reference.wav")
    bump_reference()
    info("reference", "reference.updated", {"path": str(DATA / "reference.wav"), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "generation": RUNTIME.get("reference_generation", 0), "metrics": wav_metrics(data)})
def set_config(values: dict):
    if type(values) is not dict or not values:
        raise ApiError(400, "values must be a non-empty object")
    checked = {path: validate(path, value) for path, value in values.items()}
    with LOCK:
        previous = {path: CONFIG.get(path) for path in checked}
        CONFIG.update(checked)
        atomic_json(CONFIG_FILE, CONFIG)
    info("config", "config.changed", {"before": previous, "after": checked})
    emit_state()
def voice_options(language: str, style: str) -> dict:
    if language not in TTS_LANGUAGES or style not in VOICE_STYLES:
        raise ApiError(400, "unsupported speech language or voice style")
    voice = {key: CONFIG[f"tts.sample.{key}"] for key in ("seed", "max_tokens", "top_k", "top_p", "min_p", "temperature", "repeat_penalty", "cfm_steps")}
    voice.update(first_chunk=CONFIG["tts.stream.first_chunk"], chunk=CONFIG["tts.stream.chunk"], max_sentence_chars=CONFIG["tts.stream.max_sentence_chars"], cfg_weight=CONFIG[f"tts.style.{style}.cfg_weight"], exaggeration=CONFIG[f"tts.style.{style}.exaggeration"] )
    return voice
def tts_session(lane: str, language: str, style: str, *, trace_id: str = "", turn_id: str = "", source: str = "api", client_id: str = "") -> dict:
    if lane not in RUNTIME["lanes"]:
        raise ApiError(400, f"unknown lane: {lane}")
    require_engine("tts")
    voice = voice_options(language, style)
    config_id = new_trace_id("tts-config")
    reference = reference_evidence()
    init = {
        "type": "init", "reference_audio": str(reference_path()), "language": language,
        "config_id": config_id, "trace_id": trace_id, "turn_id": turn_id, "lane": lane,
        "source": source, "client_id": client_id,
        "seed": voice["seed"], "max_tokens": voice["max_tokens"], "top_k": voice["top_k"],
        "top_p": voice["top_p"], "min_p": voice["min_p"], "temperature": voice["temperature"],
        "repeat_penalty": voice["repeat_penalty"], "cfg_weight": voice["cfg_weight"],
        "exaggeration": voice["exaggeration"], "cfm_steps": voice["cfm_steps"],
        "stream_first_chunk_tokens": voice["first_chunk"], "stream_chunk_tokens": voice["chunk"],
        "max_sentence_chars": voice["max_sentence_chars"],
    }
    context = {key: value for key, value in {"trace_id": trace_id, "turn_id": turn_id, "config_id": config_id, "lane": lane, "client_id": client_id}.items() if value}
    with LOCK:
        RUNTIME["lanes"][lane].update(status="connecting", session="", request="", config_id=config_id, trace_id=trace_id, turn_id=turn_id, source=source, language=language, style=style, reference=reference, samples=0, chunks=0, error="")
        PROCESS_TRACES["tts"] = context
    info("tts", "tts.session.configured", {
        "language": language,
        "style": style,
        "voice": voice,
        "reference": reference,
        "conditioning_contract": {
            "t3_speaker_embedding": "reference_voice_encoder",
            "t3_conditioning_tokens": "reference_s3tokenizer",
            "s3gen_prompt_tokens": "reference_s3tokenizer",
            "s3gen_prompt_features": "reference_audio",
            "s3gen_speaker_embedding": "builtin_fallback_no_campplus",
        },
    }, source="controller", **context)
    emit_state()
    return {"url": "ws://127.0.0.1:8095/tts", "message": init, "language": language, "style": style, "config_id": config_id, **context}
def tts_request(lane: str, text: str, *, trace_id: str = "", turn_id: str = "", source: str = "api", client_id: str = "") -> dict:
    if lane not in RUNTIME["lanes"]:
        raise ApiError(400, f"unknown lane: {lane}")
    require_engine("tts")
    if RUNTIME["lanes"][lane]["status"] != "ready" or not RUNTIME["lanes"][lane]["session"]:
        raise ApiError(409, f"lane {lane} has no ready session")
    text = str(text or "").strip()
    if not text:
        raise ApiError(400, "speech text is empty")
    trace_id = trace_id or new_trace_id("trace")
    request_id = new_trace_id("tts-request")
    with LOCK:
        current = RUNTIME["lanes"][lane]
        config_id = str(current.get("config_id") or "")
        session_id = str(current.get("session") or "")
        language = str(current.get("language") or "")
        style = str(current.get("style") or "")
        reference = deepcopy(current.get("reference") or {})
        current.update(status="queued", request=request_id, trace_id=trace_id, turn_id=turn_id, source=source, samples=0, chunks=0, error="")
        RUNTIME["trace"].update(latest=trace_id, latest_turn=turn_id or RUNTIME["trace"].get("latest_turn", ""))
        context = {key: value for key, value in {"trace_id": trace_id, "turn_id": turn_id, "config_id": config_id, "session_id": session_id, "request_id": request_id, "lane": lane, "client_id": client_id}.items() if value}
        PROCESS_TRACES["tts"] = context
    info("tts", "tts.request.created", {"source": source, "text": text, "characters": len(text), "language": language, "style": style, "reference": reference}, **context)
    emit_state()
    message = {"type": "synthesize", "text": text, "source": source, **context}
    return {"message": message, **context}
def tts_event(data: dict):
    lane = data.get("lane")
    event = data.get("event")
    allowed = ("ready", "synthesize_started", "audio_received", "chunk_done", "playback_started", "playback_complete", "cancelled", "error", "closed")
    if lane not in RUNTIME["lanes"] or event not in allowed:
        raise ApiError(400, "invalid TTS event")
    with LOCK:
        state = RUNTIME["lanes"][lane]
        previous_request = str(state.get("request") or "")
        if event in ("ready", "synthesize_started", "chunk_done", "cancelled", "error", "closed"):
            state["status"] = {"ready": "ready", "synthesize_started": "streaming", "chunk_done": "ready", "cancelled": "cancelled", "error": "error", "closed": "closed"}[event]
        if "session_id" in data:
            state["session"] = str(data["session_id"])
        if "request_id" in data:
            state["request"] = str(data["request_id"])
        if "samples" in data:
            state["samples"] = int(data["samples"])
        if "chunks" in data:
            state["chunks"] = int(data["chunks"])
        state["error"] = str(data.get("message", "")) if event == "error" else ""
        context = {key: str(data.get(key) or state.get(key) or "") for key in ("trace_id", "turn_id", "config_id", "session_id", "request_id", "lane", "client_id") if data.get(key) or state.get(key)}
    request_mismatch = bool(data.get("request_id") and previous_request and str(data["request_id"]) != previous_request)
    event_data = {key: value for key, value in data.items() if key not in {"op", "lane", "event", "trace_id", "turn_id", "config_id", "session_id", "request_id", "client_id"}}
    event_data["request_mismatch"] = request_mismatch
    (warn if request_mismatch or event == "error" else info)("browser", f"browser.tts.{event}", event_data, source="browser", **context)
    emit_state()
def browser_trace(data: dict) -> dict:
    event = str(data.get("event") or "").strip().lower()
    if not re.fullmatch(r"browser\.[a-z0-9_.-]{1,80}", event):
        raise ApiError(400, "browser trace event must start with browser.")
    level = str(data.get("level") or "info").lower()
    if level not in ("debug", "info", "warn", "error"):
        raise ApiError(400, "browser trace level is invalid")
    context = {field: identifier(data.get(field), field) for field in ("trace_id", "turn_id", "config_id", "session_id", "request_id", "lane", "client_id") if data.get(field)}
    details = data.get("data") if type(data.get("data")) is dict else {}
    message = str(data.get("message") or "")
    entry = trace(level, "browser", event, details, message=message, source="browser", **context)
    with LOCK:
        if context.get("trace_id"):
            RUNTIME["trace"]["latest"] = context["trace_id"]
        if context.get("turn_id"):
            RUNTIME["trace"]["latest_turn"] = context["turn_id"]
    emit_state()
    return entry
def tts_cancel(session_id: str) -> dict:
    if not session_id:
        raise ApiError(400, "session_id is required")
    info("tts", "tts.cancel.requested", {"session_id": session_id}, session_id=session_id)
    payload = json.dumps({"session_id": session_id}, separators=(",", ":")).encode()
    return json.loads(remote("http://127.0.0.1:8095/cancel", payload))
def read_log(options: dict | int | None = None) -> list:
    if isinstance(options, int):
        return read_trace(options)
    options = options if type(options) is dict else {}
    filters = {key: value for key, value in options.items() if key != "limit"}
    return read_trace(options.get("limit", 200), **filters)
def brain_reply_text(result: dict | None) -> str:
    if not result:
        return ""
    message = ((result.get("choices") or [{}])[0].get("message") or {})
    content = str(message.get("content") or "").strip()
    if content:
        return content
    skip = ("thinking", "analyze", "analysis", "option", "theme", "constraint", "input", "role", "task", "draft", "determine")
    spoken = []
    for line in str(message.get("reasoning_content") or "").splitlines():
        text = line.strip(" -*\t")
        if not text or text.startswith(("#", "1.", "2.", "3.", "4.", "5.")) or text.lower().startswith(skip):
            continue
        spoken.append(text)
    return spoken[-1] if spoken else ""
def wav_metrics(data: bytes) -> dict:
    with wave.open(io.BytesIO(data), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        raw = audio.readframes(frames)
    samples = memoryview(raw).cast("h")
    if not samples:
        return {"seconds": 0.0, "rate": rate, "rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip_pct": 0.0}
    squares = sum(value * value for value in samples) / len(samples)
    rms = math.sqrt(squares) / 32768.0
    peak = max(abs(value) for value in samples) / 32768.0
    clipped = sum(abs(value) >= 32760 for value in samples)
    db = lambda value: round(20 * math.log10(max(value, 1e-6)), 2)
    return {"seconds": round(len(samples) / float(rate or 1), 3), "rate": rate, "rms_dbfs": db(rms), "peak_dbfs": db(peak), "clip_pct": round(clipped * 100 / len(samples), 4)}
def wav_body(raw: bytes | None) -> bytes:
    if not raw:
        raise ApiError(400, "WAV body is required")
    return raw
def run_turn(payload: dict, raw: bytes | None = None) -> dict:
    audio = wav_body(raw)
    language = str(payload.get("language") or "")
    if language not in CONVERSATION_LANGUAGES:
        raise ApiError(400, f"conversation language must be one of {list(CONVERSATION_LANGUAGES)}")
    trace_id = identifier(payload.get("trace_id"), "trace_id") or new_trace_id("trace")
    turn_id = identifier(payload.get("turn_id"), "turn_id") or new_trace_id("turn")
    client_id = identifier(payload.get("client_id"), "client_id")
    require_engine("asr")
    require_engine("brain")
    require_engine("tts")
    clone = bool(CONFIG["conversation.clone_voice"])
    results: dict[str, Any] = {}
    report = {"ok": False, "clone": clone, "cloned": False, "language": language, "text": "", "trace_id": trace_id, "turn_id": turn_id, "client_id": client_id, "results": results, "error": ""}
    started = time.monotonic()
    with trace_scope(trace_id=trace_id, turn_id=turn_id, client_id=client_id):
        try:
            atomic_bytes(DATA / "last-input.wav", audio)
            metrics = wav_metrics(audio)
            input_evidence = {"path": str(DATA / "last-input.wav"), "bytes": len(audio), "sha256": hashlib.sha256(audio).hexdigest(), "metrics": metrics}
            with LOCK:
                RUNTIME["trace"].update(latest=trace_id, latest_turn=turn_id)
            info("turn", "turn.started", {"language": language, "clone_requested": clone, "vad": bool(CONFIG["conversation.vad"]), "input": input_evidence, "reference_before": reference_evidence(), "engines": {name: {"status": value["status"], "applied": value.get("applied", {})} for name, value in RUNTIME["engines"].items()}})
            if clone and metrics["seconds"] >= 10:
                validate_wav(audio)
                report["cloned"] = True
                info("turn", "turn.clone.accepted", {"minimum_seconds": 10, "input": input_evidence, "reference_after": reference_evidence()})
            elif clone:
                warn("turn", "turn.clone.skipped", {"reason": "recording_too_short", "minimum_seconds": 10, "actual_seconds": metrics["seconds"], "reference_retained": reference_evidence()})
            else:
                info("turn", "turn.clone.disabled", {"reference_used": reference_evidence()})
            set_flow("transcribing", language=language, trace_id=trace_id, turn_id=turn_id)
            results["asr"] = transcribe(audio, trace_id=trace_id, turn_id=turn_id)
            transcript = str(results["asr"].get("text") or "").strip()
            if not transcript:
                raise ApiError(422, "speech was not recognized")
            set_flow("thinking", transcript=transcript, language=language, trace_id=trace_id, turn_id=turn_id)
            results["brain"] = brain(f"Respond naturally to this speech transcript:\n\n{transcript}", language, trace_id=trace_id, turn_id=turn_id)
            speak = brain_reply_text(results["brain"]) or transcript
            report["text"] = speak
            set_flow("ready_to_speak", transcript=transcript, answer=speak, language=language, trace_id=trace_id, turn_id=turn_id)
            report.update(ok=True, results=results, reference=reference_evidence())
            info("turn", "turn.response_ready", {"duration_ms": round((time.monotonic() - started) * 1000, 3), "language": language, "transcript": transcript, "response": speak, "clone_requested": clone, "cloned": report["cloned"], "reference": report["reference"]})
            return report
        except Exception as exception:
            report["error"] = str(exception)
            report["results"] = results
            set_flow("error", failure=report["error"], language=language, trace_id=trace_id, turn_id=turn_id)
            error("turn", "turn.failed", {"duration_ms": round((time.monotonic() - started) * 1000, 3), "stage": RUNTIME["flow"].get("stage"), "error": report["error"], "partial_results": results})
            if isinstance(exception, ApiError):
                raise
            raise ApiError(500, report["error"]) from exception
        finally:
            with LOCK:
                RUNTIME["results"]["turn"] = report
            emit_state()
def resolve_brain_url(spec: str) -> str:
    spec = spec.strip()
    if not spec:
        raise ApiError(400, "brain URL is required")
    if spec.startswith("https://") or spec.startswith("http://"):
        if not spec.lower().split("?", 1)[0].endswith(".gguf"):
            raise ApiError(400, "brain URL must point to a .gguf file")
        return spec
    parts = [part for part in spec.replace("\\", "/").split("/") if part]
    if len(parts) >= 3 and parts[-1].lower().endswith(".gguf"):
        return f"https://huggingface.co/{parts[0]}/{parts[1]}/resolve/main/{'/'.join(parts[2:])}"
    raise ApiError(400, "brain URL must be an https GGUF link or owner/repo/file.gguf")
def fetch_any(url: str, destination: Path, key: str) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
    hasher = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
        if response.status != 200:
            raise RuntimeError(f"download returned HTTP {response.status}: {url}")
        for block in iter(lambda: response.read(1024 * 1024), b""):
            output.write(block)
            hasher.update(block)
            done += len(block)
            set_job(key, "running", "download", min(89, done // (1024 * 1024)), f"{done} bytes")
    if done < 64:
        partial.unlink(missing_ok=True)
        raise RuntimeError("download was too small to be a GGUF")
    digest = hasher.hexdigest()
    os.replace(partial, destination)
    return done, digest
def install_custom_brain(url: str, family: str, key: str):
    if family not in BRAIN_FAMILIES:
        raise RuntimeError(f"unsupported brain family: {family}")
    resolved = resolve_brain_url(url)
    set_job(key, "running", "download", 5, "downloading custom brain")
    size, digest = fetch_any(resolved, CUSTOM_BRAIN, key)
    header = CUSTOM_BRAIN.read_bytes()[:4]
    if header != b"GGUF":
        CUSTOM_BRAIN.unlink(missing_ok=True)
        raise RuntimeError("downloaded file is not a GGUF")
    with LOCK:
        BRAIN_STATE["active"] = "custom"
        BRAIN_STATE["custom"] = {"url": resolved, "path": str(CUSTOM_BRAIN), "sha256": digest, "size": size, "family": family}
        save_brains()
    if RUNTIME["engines"]["brain"]["status"] in ("running", "loading", "error"):
        load_engine("brain", key)
def apply_brain(name: str, family: str | None = None):
    if name not in BRAINS:
        raise ApiError(404, f"unknown brain: {name}")
    if name == "custom" and not custom_brain_ready():
        raise ApiError(409, "custom brain GGUF is missing; provide a URL to download one")
    with LOCK:
        BRAIN_STATE["active"] = name
        if name == "custom" and family:
            if family not in BRAIN_FAMILIES:
                raise ApiError(400, f"family must be one of {list(BRAIN_FAMILIES)}")
            BRAIN_STATE["custom"]["family"] = family
        save_brains()
    running = RUNTIME["engines"]["brain"]["status"] == "running"
    ready = name == "custom" or model_status(BRAINS[name]["model"])["status"] == "ready"
    if running and ready:
        start_job("engine", "brain", lambda key: load_engine("brain", key))
    elif running:
        stop_engine("brain")
OPS = {
    "inspect": {"doc": "schema + snapshot + op catalog", "fields": []},
    "schema": {"doc": "field and install catalog", "fields": []},
    "state": {"doc": "live snapshot", "fields": []},
    "log": {"doc": "query the canonical correlated event stream", "fields": ["limit", "since_seq", "trace_id", "turn_id", "config_id", "session_id", "request_id", "source", "component", "level", "event"]},
    "clear_log": {"doc": "erase the canonical event stream and panel trace pane", "fields": []},
    "note": {"doc": "append a compatibility diagnostic event", "fields": ["component", "msg", "data"]},
    "trace": {"doc": "append a correlated browser lifecycle event", "fields": ["event", "level", "trace_id", "turn_id", "config_id", "session_id", "request_id", "lane", "client_id", "data"]},
    "set": {"doc": "write user-facing configuration", "fields": ["values"]},
    "install_prerequisite": {"doc": "install a host prerequisite", "fields": ["name"]},
    "install_component": {"doc": "install a pinned runtime component; only Chatterbox TTS builds locally", "fields": ["name"]},
    "download_model": {"doc": "download a pinned model or default reference asset", "fields": ["name"]},
    "set_brain": {"doc": "select a catalog brain or download a custom GGUF URL", "fields": ["name", "url", "family"]},
    "load_engine": {"doc": "load tts, asr, or brain", "fields": ["name"]},
    "unload_engine": {"doc": "unload tts, asr, or brain", "fields": ["name"]},
    "upload_reference": {"doc": "replace reference.wav", "fields": [], "body": "audio/wav"},
    "asr": {"doc": "transcribe WAV via Parakeet", "fields": [], "body": "audio/wav"},
    "brain": {"doc": "ask the active brain", "fields": ["prompt", "language"]},
    "tts_session": {"doc": "open a Chatterbox V3 session", "fields": ["lane", "language", "style"]},
    "tts_request": {"doc": "queue a synthesize message", "fields": ["lane", "text"]},
    "tts_event": {"doc": "report a lane websocket event", "fields": ["lane", "event"]},
    "tts_cancel": {"doc": "cancel a TTS session", "fields": ["session_id"]},
    "turn": {"doc": "WAV input -> Parakeet -> brain; browser streams Chatterbox audio", "fields": ["language"], "body": "audio/wav"},
}
def inspect() -> dict:
    return {"ok": True, "version": 5, "control": "/api", "ops": OPS, "schema": SCHEMA, "state": snapshot()}
def dispatch(op: str, payload: dict | None = None, raw: bytes | None = None) -> tuple[dict, int]:
    payload = payload or {}
    if not op:
        raise ApiError(400, "op is required")
    if op not in OPS:
        raise ApiError(400, f"unknown op: {op}")
    if op == "inspect":
        return inspect(), 200
    if op == "schema":
        return SCHEMA, 200
    if op == "state":
        return snapshot(), 200
    if op == "log":
        return {"ok": True, "run_id": trace_run_id(), "lines": read_log(payload)}, 200
    if op == "clear_log":
        reset_log()
        emit("log", {"lines": []})
        return {"ok": True, "lines": []}, 200
    if op == "note":
        msg = payload.get("msg") or payload.get("message")
        if type(msg) is not str or not msg.strip():
            raise ApiError(400, "msg is required")
        data = payload.get("data") if type(payload.get("data")) is dict else {}
        component = str(payload.get("component") or "api").strip()[:32] or "api"
        info(component, "compatibility.note", data, message=msg.strip(), source="api")
        lines = read_log({"limit": payload.get("limit", 120)})
        emit("log", {"lines": lines})
        return {"ok": True, "lines": lines}, 200
    if op == "trace":
        return {"ok": True, "event": browser_trace(payload)}, 200
    if op == "set":
        set_config(payload.get("values"))
        return {"ok": True, "state": snapshot()}, 200
    if op == "install_prerequisite":
        name = payload.get("name")
        if name not in SCHEMA["prerequisites"]:
            raise ApiError(404, f"unknown prerequisite: {name}")
        job_id = start_job("prerequisite", name, lambda key: install_prerequisite(name, key))
        return {"ok": True, "accepted": True, "op": op, "name": name, "job_id": job_id}, 202
    if op == "install_component":
        name = payload.get("name")
        if name not in ("tts", *BINARIES):
            raise ApiError(404, f"unknown component: {name}")
        job_id = start_job("component", name, lambda key: install_component(name, key))
        return {"ok": True, "accepted": True, "op": op, "name": name, "job_id": job_id}, 202
    if op == "download_model":
        name = payload.get("name")
        if name not in MODELS:
            raise ApiError(404, f"unknown model: {name}")
        job_id = start_job("model", name, lambda key: download_model(name, key))
        return {"ok": True, "accepted": True, "op": op, "name": name, "job_id": job_id}, 202
    if op == "set_brain":
        name = str(payload.get("name") or "custom")
        family = str(payload.get("family") or ("generic" if name == "custom" else BRAINS.get(name, {}).get("family") or "generic"))
        url = str(payload.get("url") or "").strip()
        if name == "custom" and url:
            resolve_brain_url(url)
            if family not in BRAIN_FAMILIES:
                raise ApiError(400, f"family must be one of {list(BRAIN_FAMILIES)}")
            job_id = start_job("brain", "custom", lambda key: install_custom_brain(url, family, key))
            return {"ok": True, "accepted": True, "op": op, "name": name, "job_id": job_id}, 202
        apply_brain(name, family if name == "custom" else None)
        return {"ok": True, "brain": brain_snapshot(), "state": snapshot()}, 200
    if op == "load_engine":
        name = payload.get("name")
        if name not in ENGINE_MODELS:
            raise ApiError(404, f"unknown engine: {name}")
        job_id = start_job("engine", name, lambda key: load_engine(name, key))
        return {"ok": True, "accepted": True, "op": op, "name": name, "job_id": job_id}, 202
    if op == "unload_engine":
        name = payload.get("name")
        if name not in ENGINE_MODELS:
            raise ApiError(404, f"unknown engine: {name}")
        job_id = start_job("engine", name, lambda key: stop_engine(name))
        return {"ok": True, "accepted": True, "op": op, "name": name, "job_id": job_id}, 202
    if op == "upload_reference":
        validate_wav(wav_body(raw))
        emit_state()
        return {"ok": True, "reference": reference_state()}, 200
    if op == "asr":
        return {"ok": True, "result": transcribe(wav_body(raw), trace_id=identifier(payload.get("trace_id"), "trace_id"), turn_id=identifier(payload.get("turn_id"), "turn_id"))}, 200
    if op == "brain":
        prompt = str(payload.get("prompt") or "").strip()
        language = str(payload.get("language") or "")
        if not prompt:
            raise ApiError(400, "prompt is required")
        return {"ok": True, "result": brain(prompt, language, trace_id=identifier(payload.get("trace_id"), "trace_id"), turn_id=identifier(payload.get("turn_id"), "turn_id"))}, 200
    if op == "tts_session":
        return tts_session(payload["lane"], payload["language"], payload["style"], trace_id=identifier(payload.get("trace_id"), "trace_id"), turn_id=identifier(payload.get("turn_id"), "turn_id"), source=str(payload.get("source") or "api")[:32], client_id=identifier(payload.get("client_id"), "client_id")), 200
    if op == "tts_request":
        return tts_request(payload["lane"], payload["text"], trace_id=identifier(payload.get("trace_id"), "trace_id"), turn_id=identifier(payload.get("turn_id"), "turn_id"), source=str(payload.get("source") or "api")[:32], client_id=identifier(payload.get("client_id"), "client_id")), 200
    if op == "tts_event":
        tts_event(payload)
        return {"ok": True}, 200
    if op == "tts_cancel":
        return {"ok": True, "result": tts_cancel(payload.get("session_id", ""))}, 200
    if op == "turn":
        return run_turn(payload, raw), 200
    raise ApiError(400, f"unhandled op: {op}")
SCHEMA = {
    "version": 5,
    "control": "/api",
    "fields": FIELDS,
    "languages": {"conversation": CONVERSATION_LANGUAGES, "speech": TTS_LANGUAGES, "asr": ASR_LANGUAGES},
    "voice_styles": VOICE_STYLES,
    "param_groups": PARAM_GROUPS,
    "ops": OPS,
    "prerequisites": {name: {"label": label, "op": "install_prerequisite", "name": name} for name, label in {"python": "PYTHON 3.11+", "git": "GIT (TTS BUILD)", "cmake": "CMAKE (TTS BUILD)", "msvc": "MSVC (TTS BUILD)", "vulkan": "VULKAN SDK (TTS BUILD)"}.items()},
    "components": {
        "tts": {"label": "CHATTERBOX TTS V3", "op": "install_component", "name": "tts"},
        **{name: {"label": spec["label"], "op": "install_component", "name": name, "tag": spec["tag"]} for name, spec in BINARIES.items()},
    },
    "models": {name: {"label": spec["label"], "op": "download_model", "name": name, "revision": spec.get("revision", ""), "size": spec["size"], "sha256": spec["sha256"], **({"license": spec["license"]} if spec.get("license") else {})} for name, spec in MODELS.items()},
    "brains": {name: {"label": spec["label"], "model": spec["model"], "family": spec["family"]} for name, spec in BRAINS.items()},
    "brain_families": list(BRAIN_FAMILIES),
    "engines": {name: {"load_op": "load_engine", "unload_op": "unload_engine", "name": name} for name in ENGINE_MODELS},
    "defaults": {"tts_runtime": TTS_RUNTIME, "voice": VOICE_DEFAULTS, "asr": ASR_RUNTIME, "brain_runtime": BRAIN_RUNTIME, "brain_generation": BRAIN_GENERATION},
    "trace": {"schema": "trident.event", "version": 1, "run_id": trace_run_id(), "identifiers": ["trace_id", "turn_id", "http_id", "job_id", "config_id", "session_id", "request_id", "lane", "client_id"]},
    "tts": {"url": "ws://127.0.0.1:8095/tts", "text": "JSON with trace identifiers", "audio": "binary PCM16LE mono 24000 Hz", "messages": ["init", "synthesize", "cancel", "close"], "events": ["ready", "synthesize_started", "audio", "chunk_done", "cancelled", "error"]},
}
def api_payload_evidence(payload: dict) -> dict:
    """Keep request routing evidence without duplicating prompts, URLs, or secrets."""
    evidence: dict[str, Any] = {}
    safe_fields = (
        "op", "name", "lane", "language", "style", "event", "level", "source", "family",
        "trace_id", "turn_id", "config_id", "session_id", "request_id", "client_id",
    )
    for field in safe_fields:
        if field in payload and payload[field] not in (None, ""):
            evidence[field] = payload[field]
    for field in ("text", "prompt", "msg", "message"):
        value = payload.get(field)
        if isinstance(value, str):
            evidence[f"{field}_evidence"] = {
                "characters": len(value),
                "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
    values = payload.get("values")
    if isinstance(values, dict):
        evidence["configuration_fields"] = sorted(str(field) for field in values)
    details = payload.get("data")
    if isinstance(details, dict):
        evidence["data_fields"] = sorted(str(field) for field in details)
    url = payload.get("url")
    if isinstance(url, str) and url:
        parsed = urllib.parse.urlsplit(url)
        evidence["url"] = {"scheme": parsed.scheme, "host": parsed.hostname or "", "path_name": Path(parsed.path).name}
    return evidence
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def body(self, limit: int = 50 * 1024 * 1024) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > limit:
            raise ApiError(400, f"body length must be between 1 and {limit}")
        return self.rfile.read(length)
    def request_json(self, optional: bool = False) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if optional and not length:
            return {}
        try:
            value = json.loads(self.body(1024 * 1024))
        except (json.JSONDecodeError, UnicodeDecodeError) as exception:
            raise ApiError(400, f"invalid JSON: {exception}") from exception
        if type(value) is not dict:
            raise ApiError(400, "JSON body must be an object")
        return value
    def send_bytes(self, data: bytes, content_type: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
    def send_json(self, value: Any, code: int = 200):
        self.send_bytes(json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii"), "application/json", code)
    def do_GET(self):
        http_id = new_trace_id("http")
        started = time.monotonic()
        op = ""
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
            files = {
                "/": (ROOT / "panel.html", "text/html; charset=utf-8"),
                "/panel.html": (ROOT / "panel.html", "text/html; charset=utf-8"),
                "/panel.css": (ROOT / "panel.css", "text/css; charset=utf-8"),
                "/panel.js": (ROOT / "panel.js", "text/javascript; charset=utf-8"),
                "/audio-processor.js": (ROOT / "audio-processor.js", "text/javascript; charset=utf-8"),
            }
            if path in files:
                target, content_type = files[path]
                self.send_bytes(target.read_bytes(), content_type)
                return
            if path != "/api":
                raise ApiError(404, f"unknown endpoint: {path}")
            op = query.get("op") or "inspect"
            if op == "events":
                self.events(http_id)
                return
            if op not in ("inspect", "schema", "state", "log"):
                raise ApiError(404, f"GET /api accepts op=inspect, schema, state, log, or events")
            debug("api", "api.request", {"method": "GET", "op": op, "query": query}, http_id=http_id)
            response_body, code = dispatch(op, query)
            debug("api", "api.response", {"method": "GET", "op": op, "status": code, "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id)
            self.send_json(response_body, code)
        except ApiError as exception:
            warn("api", "api.rejected", {"method": "GET", "op": op, "path": self.path, "status": exception.code, "error": str(exception), "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id)
            self.send_json({"error": str(exception)}, exception.code)
        except Exception as exception:
            if client_gone(exception):
                return
            error("api", "api.failed", {"method": "GET", "op": op, "path": self.path, "error": str(exception), "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id)
            self.send_json({"error": str(exception)}, 500)
    def events(self, http_id: str):
        subscriber: queue.Queue = queue.Queue()
        with LOCK:
            SUBSCRIBERS.add(subscriber)
        info("api", "api.events.connected", {"subscribers": len(SUBSCRIBERS)}, http_id=http_id)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            subscriber.put(("state", snapshot()))
            while True:
                try:
                    event, data = subscriber.get(timeout=15)
                    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
                    self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("ascii"))
                except queue.Empty:
                    self.wfile.write(b"event: ping\ndata:{}\n\n")
                self.wfile.flush()
        except Exception as exception:
            if not client_gone(exception):
                raise
        finally:
            with LOCK:
                SUBSCRIBERS.discard(subscriber)
                subscribers = len(SUBSCRIBERS)
            info("api", "api.events.disconnected", {"subscribers": subscribers}, http_id=http_id)
    def do_POST(self):
        http_id = new_trace_id("http")
        started = time.monotonic()
        op = ""
        trace_id = turn_id = client_id = ""
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/api":
                raise ApiError(404, f"unknown endpoint: {parsed.path}")
            query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type == "audio/wav":
                op = query.get("op")
                if op not in ("turn", "asr", "upload_reference"):
                    raise ApiError(400, "WAV body requires op=turn, op=asr, or op=upload_reference")
                payload = query
                request_body = self.body()
            else:
                payload = self.request_json(True)
                op = payload.get("op") or query.get("op") or "inspect"
                request_body = None
            trace_id = identifier(payload.get("trace_id"), "trace_id")
            turn_id = identifier(payload.get("turn_id"), "turn_id")
            client_id = identifier(payload.get("client_id"), "client_id")
            context = {key: value for key, value in {"http_id": http_id, "trace_id": trace_id, "turn_id": turn_id, "client_id": client_id}.items() if value}
            with trace_scope(**context):
                info("api", "api.request", {"method": "POST", "op": op, "content_type": content_type, "content_length": int(self.headers.get("Content-Length", "0") or 0), "fields": sorted(payload), "request": api_payload_evidence(payload), **({"audio_bytes": len(request_body or b"")} if content_type == "audio/wav" else {})})
                response_body, code = dispatch(op, payload, request_body)
                info("api", "api.response", {"method": "POST", "op": op, "status": code, "duration_ms": round((time.monotonic() - started) * 1000, 3), "accepted": response_body.get("accepted") if type(response_body) is dict else None})
            self.send_json(response_body, code)
        except ApiError as exception:
            warn("api", "api.rejected", {"method": "POST", "op": op, "path": self.path, "status": exception.code, "error": str(exception), "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id, trace_id=trace_id, turn_id=turn_id, client_id=client_id)
            self.send_json({"error": str(exception)}, exception.code)
        except (KeyError, TypeError, ValueError) as exception:
            warn("api", "api.rejected", {"method": "POST", "op": op, "path": self.path, "status": 400, "error": str(exception), "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id, trace_id=trace_id, turn_id=turn_id, client_id=client_id)
            self.send_json({"error": f"invalid request: {exception}"}, 400)
        except Exception as exception:
            if client_gone(exception):
                return
            error("api", "api.failed", {"method": "POST", "op": op, "path": self.path, "error": str(exception), "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id, trace_id=trace_id, turn_id=turn_id, client_id=client_id)
            self.send_json({"error": str(exception)}, 500)
class Server(ThreadingHTTPServer):
    daemon_threads = True
def main() -> int:
    server = Server(("127.0.0.1", 8765), Handler)
    info("controller", "controller.started", {"host": "127.0.0.1", "port": 8765, "api_version": 5, "trace_run_id": trace_run_id(), "canonical_log": str(ROOT / "trident.log.jsonl"), "legacy_log": str(ROOT / "install.log.jsonl"), "pid": os.getpid()})
    timer = threading.Timer(.4, webbrowser.open, args=("http://127.0.0.1:8765/",))
    timer.daemon = True
    timer.start()
    print("TRIDENT  http://127.0.0.1:8765/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        info("controller", "controller.interrupted", {})
    finally:
        info("controller", "controller.stopping", {"engines": list(PROCESSES)})
        server.server_close()
        for name in list(PROCESSES):
            stop_engine(name)
        info("controller", "controller.stopped", {})
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
