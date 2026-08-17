from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import queue
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

from log import clear as reset_log, error, info

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
    "parakeet": {"label": "PARAKEET V0.5 VULKAN", "repo": "mudler/parakeet.cpp", "tag": "v0.5.0", "asset": "parakeet-v0.5.0-bin-win-vulkan-x64.zip", "exe": "parakeet-server.exe"},
    "gemma": {"label": "LLAMA.CPP B10453 VULKAN", "repo": "ggml-org/llama.cpp", "tag": "b10453", "asset": "llama-b10453-bin-win-vulkan-x64.zip", "exe": "llama-server.exe"},
}

MODELS = {
    "chatterbox-t3": {"label": "CHATTERBOX V3 T3", "repo": "BricksDisplay/Chatterbox-Multilingual-TTS-GGUF", "revision": "37277eeb9e26da8e3fba65b52727cb30b0bc5ae8", "file": "chatterbox-mtl-t3-q4_0.gguf", "size": 283389248, "sha256": "9a5b5e863d05da00f57ffb7d157f4135231ae17c926f97deb0070f9361205c30"},
    "chatterbox-codec": {"label": "CHATTERBOX V3 S3GEN", "repo": "BricksDisplay/Chatterbox-Multilingual-TTS-GGUF", "revision": "37277eeb9e26da8e3fba65b52727cb30b0bc5ae8", "file": "chatterbox-mtl-codec-f16.gguf", "size": 335027072, "sha256": "dce996594a43bcdb665b7a3f2b8e73b58ddca13eeb736f512ba0572d4e64954a"},
    "chatterbox-s3t": {"label": "CHATTERBOX V3 S3T", "repo": "BricksDisplay/Chatterbox-Multilingual-TTS-GGUF", "revision": "37277eeb9e26da8e3fba65b52727cb30b0bc5ae8", "file": "chatterbox-mtl-s3t.gguf", "size": 247487280, "sha256": "26592ce171dd40bb54468a32dd9a3b697e15bfc23ebc8f8d218e34c3962e69c4"},
    "parakeet": {"label": "PARAKEET TDT", "repo": "mudler/parakeet-cpp-gguf", "revision": "bf0af9f425fa01809cadec671b3cb672709d13e9", "file": "tdt-0.6b-v3-q4_k.gguf", "size": 675200864, "sha256": "993d73feb4206dadda865ab25bd64b50c48dc4d013c3bf6126a721f28b1d5ee8"},
    "gemma": {"label": "GEMMA 4 E2B", "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf", "revision": "675cff42a74c774d6cb76f76d8eacb49b48c9b93", "file": "gemma-4-E2B_q4_0-it.gguf", "size": 3349516256, "sha256": "fa401b55b07ee70a54c6dae3903c783a6e65064312529ea57175cb5f8dec6634"},
    "qwen35-0.8b": {"label": "QWEN3.5 0.8B", "repo": "unsloth/Qwen3.5-0.8B-GGUF", "revision": "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0", "file": "Qwen3.5-0.8B-Q4_K_M.gguf", "size": 532517120, "sha256": "bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517"},
    "qwen35-4b": {"label": "QWEN3.5 4B", "repo": "unsloth/Qwen3.5-4B-GGUF", "revision": "e87f176479d0855a907a41277aca2f8ee7a09523", "file": "Qwen3.5-4B-Q4_K_M.gguf", "size": 2740937888, "sha256": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"},
    "reference": {"label": "DEFAULT VOICE", "source": "assets/default-reference.wav", "file": "default-reference.wav", "directory": "data", "size": 1012558, "sha256": "de2579b22226261784d6a944c07b9c1fba7fdd0c7e8c9e90da6bc581c78171a9", "license": "Resemble demo prompt"},
}

PACKAGES = {
    "git": {"url": "https://github.com/git-for-windows/git/releases/download/v2.54.0.windows.1/MinGit-2.54.0-64-bit.zip", "file": "MinGit-2.54.0-64-bit.zip", "size": 39989839, "sha256": "04f937e1f0918b17b9be6f2294cb2bb66e96e1d9832d1c298e2de088a1d0e668"},
    "cmake": {"url": "https://github.com/Kitware/CMake/releases/download/v4.4.2/cmake-4.4.2-windows-x86_64.zip", "file": "cmake-4.4.2-windows-x86_64.zip", "size": 54405968, "sha256": "e8139d85b3813bc38833142ae1940472e9a587e9b5d2718ac1804c60f4e57a64"},
    "msvc": {"url": "https://download.visualstudio.microsoft.com/download/pr/00d9d26c-2727-42c2-aa9e-eda63b03e1ee/15df9d3b4c2b2eaf44704d5e938c895341b9cd8ba40a9a18610f8d18cbe01b53/vs_BuildTools.exe", "file": "vs_BuildTools.exe", "size": 4458736, "sha256": "15df9d3b4c2b2eaf44704d5e938c895341b9cd8ba40a9a18610f8d18cbe01b53"},
    "vulkan": {"url": "https://sdk.lunarg.com/sdk/download/1.4.350.0/windows/vulkan_sdk.exe", "file": "vulkansdk-windows-X64-1.4.350.0.exe", "size": 324012984, "sha256": "855b27ba05d2d8119c5114c5d4ff870ca38f2c632b11e1bb9923b9b7e6ecfe7b"},
}

TTS_LANGUAGES = {"ar": "Arabic", "da": "Danish", "de": "German", "el": "Greek", "en": "English", "es": "Spanish", "fi": "Finnish", "fr": "French", "he": "Hebrew", "hi": "Hindi", "it": "Italian", "ja": "Japanese", "ko": "Korean", "ms": "Malay", "nl": "Dutch", "no": "Norwegian", "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "sv": "Swedish", "sw": "Swahili", "tr": "Turkish", "zh": "Chinese"}
ASR_LANGUAGES = {"bg": "Bulgarian", "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch", "en": "English", "et": "Estonian", "fi": "Finnish", "fr": "French", "de": "German", "el": "Greek", "hu": "Hungarian", "it": "Italian", "lv": "Latvian", "lt": "Lithuanian", "mt": "Maltese", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "sk": "Slovak", "sl": "Slovenian", "es": "Spanish", "sv": "Swedish", "ru": "Russian", "uk": "Ukrainian"}
CONVERSATION_LANGUAGES = {code: TTS_LANGUAGES[code] for code in TTS_LANGUAGES if code in ASR_LANGUAGES}


def field(label: str, kind: str, default: Any, minimum: float | None = None, maximum: float | None = None, options: list[str] | None = None, multiline: bool = False) -> dict:
    return {key: value for key, value in {"label": label, "type": kind, "default": default, "min": minimum, "max": maximum, "options": options, "multiline": multiline}.items() if value is not None}


FIELDS = {
    "conversation.language": field("Conversation language", "string", "en", options=list(CONVERSATION_LANGUAGES)),
    "conversation.clone_voice": field("Use my recording as the voice", "bool", False),
    "speech.language": field("Speech language", "string", "en", options=list(TTS_LANGUAGES)),
    "speech.style": field("Voice style", "string", "natural", options=["natural", "expressive", "cross-language"]),
    "speech.text": field("Text to speak", "string", "This is a multilingual voice synthesis test.", multiline=True),
}

TTS_RUNTIME = {"gpu_layers": 99, "context": 512, "sessions": 1, "threads": 4}
VOICE_DEFAULTS = {
    "seed": 42, "max_tokens": 1000, "top_k": 0, "top_p": 0.95, "min_p": 0.05,
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
BRAIN_RUNTIME = {"context": 2048, "parallel": 1}
BRAIN_GENERATION = {"temperature": 0.2, "top_p": 0.9, "top_k": 40, "min_p": 0.0, "repeat_penalty": 1.05, "seed": 42, "max_tokens": 160}
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
RUNTIME = {
    "jobs": {},
    "engines": {name: {"status": "stopped", "error": "", "pid": None, "applied": {}} for name in ENGINE_MODELS},
    "lanes": {"a": {"status": "closed", "session": "", "request": "", "samples": 0, "error": ""}},
    "results": {"asr": None, "brain": None, "turn": None},
    "flow": {"stage": "idle", "transcript": "", "answer": "", "error": "", "language": "en", "started": 0.0},
    "reference_generation": 0,
}


class ApiError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


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


def load_config() -> dict:
    defaults = {path: spec["default"] for path, spec in FIELDS.items()}
    stored = load_json(CONFIG_FILE, defaults)
    if type(stored) is not dict:
        raise RuntimeError(f"{CONFIG_FILE} must contain an object")
    merged = {path: stored[path] if path in stored else default for path, default in defaults.items()}
    checked = {path: validate(path, value) for path, value in merged.items()}
    if set(stored) != set(checked):
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
    roots += [TOOLS / "VulkanSDK" / "1.4.350.0"]
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


def component_artifact(name: str) -> Path:
    if name == "tts":
        return TTS_SERVER
    spec = BINARIES[name]
    root = RUNTIMES / name
    matches = [path for path in root.rglob("*") if path.is_file() and path.name.lower() == spec["exe"].lower()] if root.is_dir() else []
    return matches[0] if len(matches) == 1 else root / spec["exe"]


def component_status(name: str) -> dict:
    path = component_artifact(name)
    revision = SOURCES["chatterbox"][1] if name == "tts" else BINARIES[name]["tag"]
    return {"status": "ready" if path.is_file() else "missing", "path": str(path), "revision": revision}


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
            "jobs": deepcopy(RUNTIME["jobs"]),
        }


def emit(event: str, data: dict):
    with LOCK:
        for subscriber in SUBSCRIBERS:
            subscriber.put((event, data))


def emit_state():
    emit("state", snapshot())


def set_flow(stage: str, *, transcript: str | None = None, answer: str | None = None, failure: str | None = None, language: str | None = None):
    with LOCK:
        flow = RUNTIME["flow"]
        flow["stage"] = stage
        if transcript is not None:
            flow["transcript"] = transcript
        if answer is not None:
            flow["answer"] = answer
        if failure is not None:
            flow["error"] = failure
        if language is not None:
            flow["language"] = language
        if stage == "listening":
            flow["started"] = time.time()
            flow["transcript"] = ""
            flow["answer"] = ""
            flow["error"] = ""
    emit_state()


def set_job(key: str, status: str, stage: str, progress: int, message: str, failure: str = ""):
    with LOCK:
        RUNTIME["jobs"][key] = {"status": status, "stage": stage, "progress": progress, "message": message, "error": failure}
        current = deepcopy(RUNTIME["jobs"][key])
    emit("job", {"key": key, **current})


def start_job(kind: str, name: str, work: Callable[[str], None]):
    key = f"{kind}:{name}"
    with LOCK:
        if RUNTIME["jobs"].get(key, {}).get("status") == "running":
            raise ApiError(409, f"{key} is already running")
    set_job(key, "running", "start", 0, f"starting {name}")

    def worker():
        try:
            work(key)
            set_job(key, "done", "done", 100, f"{name} complete")
        except Exception as exception:
            message = str(exception)
            error(key, "failed", {"error": message})
            set_job(key, "error", "error", 0, message, message)
        emit_state()

    threading.Thread(target=worker, daemon=True).start()


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


def run(component: str, stage: str, command: list[str], cwd: Path, env: dict | None = None):
    info(component, stage, {"command": command, "cwd": str(cwd)})
    process = subprocess.Popen(command, cwd=cwd, env=env or build_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    tail = failure = ""
    omitted = 0
    noisy = stage in {"configure", "build", "server-configure", "server-build"}
    if not process.stdout:
        raise RuntimeError(f"{component} {stage} has no output pipe")
    for line in process.stdout:
        tail = line.rstrip()
        lower = tail.lower()
        if "error" in lower or "fatal" in lower:
            failure = tail
        important = (
            not noisy or not tail or "error" in lower or "fatal" in lower or "warning" in lower
            or tail.startswith("--") or "built target" in lower or "creating library" in lower
            or "configuring done" in lower or "generating done" in lower
        )
        if important:
            if omitted:
                info(component, f"omitted {omitted} routine build lines", {"stage": stage})
                omitted = 0
            if tail:
                info(component, tail, {"stage": stage})
        else:
            omitted += 1
    if omitted:
        info(component, f"omitted {omitted} routine build lines", {"stage": stage})
    code = process.wait()
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
    src = PATCHES / "chatterbox.patch"
    raw = src.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    tmp = cwd / ".apply-chatterbox.patch"
    tmp.write_bytes(raw)
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
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "clone-reliable/3", "X-GitHub-Api-Version": "2026-03-10"})
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

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, destination: Path, size: int, digest: str, key: str):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size == size and sha256(destination) == digest:
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "clone-reliable/2"})
    hasher = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
        if response.status != 200:
            raise RuntimeError(f"download returned HTTP {response.status}: {url}")
        for block in iter(lambda: response.read(1024 * 1024), b""):
            output.write(block)
            hasher.update(block)
            done += len(block)
            set_job(key, "running", "download", done * 90 // size, f"{done} / {size} bytes")
    if done != size:
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
        destination = TOOLS / "VulkanSDK" / "1.4.350.0"
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


def log_process(name: str, process: subprocess.Popen):
    message = ""
    if not process.stdout:
        raise RuntimeError(f"{name} has no output pipe")
    for line in process.stdout:
        message = line.rstrip()
        info(name, message)
        with LOCK:
            if PROCESSES.get(name) is process:
                RUNTIME["engines"][name]["message"] = message
    code = process.wait()
    with LOCK:
        if PROCESSES.get(name) is process:
            PROCESSES.pop(name)
            RUNTIME["engines"][name].update(status="error", error=f"process exited {code}: {message}", pid=None)
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
        RUNTIME["engines"][name].update(status="stopping", error="")
    if process and process.poll() is None:
        process.terminate()
        process.wait(30)
    with LOCK:
        RUNTIME["engines"][name].update(status="stopped", error="", pid=None, applied={})
        if name == "tts":
            for lane in RUNTIME["lanes"].values():
                lane.update(status="closed", session="", request="", samples=0, error="")


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
        applied = {"runtime": deepcopy(TTS_RUNTIME), "voice": deepcopy(VOICE_DEFAULTS)}
        command = [str(executable_path), "--port", "8095", "--model", str(paths[0]), "--s3gen-gguf", str(paths[1]), "--n-gpu-layers", str(TTS_RUNTIME["gpu_layers"]), "--context", str(TTS_RUNTIME["context"]), "--max-sessions", str(TTS_RUNTIME["sessions"]), "--threads", str(TTS_RUNTIME["threads"])]
        cwd, health, env = executable_path.parent, "http://127.0.0.1:8095/health", os.environ.copy()
    elif name == "asr":
        applied = deepcopy(ASR_RUNTIME)
        command = [str(executable_path), "--model", str(paths[0]), "--host", "127.0.0.1", "--port", "8097", "--threads", str(ASR_RUNTIME["threads"])]
        cwd, health, env = executable_path.parent, "http://127.0.0.1:8097/health", os.environ.copy()
        env["PARAKEET_DEVICE"] = "Vulkan0"
    else:
        applied = {**deepcopy(BRAIN_RUNTIME), "id": active_brain_id(), "family": active_brain_family(), "path": str(paths[0])}
        command = [str(executable_path), "-m", str(paths[0]), "--host", "127.0.0.1", "--port", "8098", "--device", "Vulkan0", "--n-gpu-layers", "all", "--ctx-size", str(BRAIN_RUNTIME["context"]), "--parallel", str(BRAIN_RUNTIME["parallel"]), "--fit", "off", "--no-mmproj"]
        cwd, health, env = executable_path.parent, "http://127.0.0.1:8098/health", os.environ.copy()
    set_job(key, "running", "load", 20, f"loading {name}")
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    with LOCK:
        PROCESSES[name] = process
        RUNTIME["engines"][name].update(status="loading", error="", pid=process.pid, applied=applied)
    threading.Thread(target=log_process, args=(name, process), daemon=True).start()
    wait_ready(name, process, health)
    with LOCK:
        RUNTIME["engines"][name]["status"] = "running"
    set_job(key, "running", "ready", 95, f"{name} ready")


def multipart(audio: bytes) -> tuple[bytes, str]:
    boundary = "clone-reliable-" + uuid.uuid4().hex
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


def transcribe(audio: bytes) -> dict:
    body, content_type = multipart(audio)
    result = json.loads(remote("http://127.0.0.1:8097/v1/audio/transcriptions", body, content_type))
    with LOCK:
        RUNTIME["results"]["asr"] = result
    emit_state()
    return result


def brain(prompt: str, language: str = "en") -> dict:
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
        **BRAIN_GENERATION,
        "stream": False,
        **BRAIN_FAMILIES[active_brain_family()],
    }
    result = json.loads(remote("http://127.0.0.1:8098/v1/chat/completions", json.dumps(request, separators=(",", ":")).encode()))
    with LOCK:
        RUNTIME["results"]["brain"] = result
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


def set_config(values: dict):
    if type(values) is not dict or not values:
        raise ApiError(400, "values must be a non-empty object")
    checked = {path: validate(path, value) for path, value in values.items()}
    with LOCK:
        CONFIG.update(checked)
        atomic_json(CONFIG_FILE, CONFIG)
    emit_state()


def voice_options(language: str, style: str) -> dict:
    if language not in TTS_LANGUAGES:
        raise ApiError(400, f"unsupported speech language: {language}")
    if style not in VOICE_STYLES:
        raise ApiError(400, f"unsupported voice style: {style}")
    return {**VOICE_DEFAULTS, **VOICE_STYLES[style]}


def tts_session(lane: str, language: str | None = None, style: str | None = None) -> dict:
    if lane not in RUNTIME["lanes"]:
        raise ApiError(400, f"unknown lane: {lane}")
    if RUNTIME["engines"]["tts"]["status"] != "running":
        raise ApiError(409, "tts is not running")
    language = language or CONFIG["speech.language"]
    style = style or CONFIG["speech.style"]
    voice = voice_options(language, style)
    init = {
        "type": "init", "reference_audio": str(reference_path()), "language": language,
        "seed": voice["seed"], "max_tokens": voice["max_tokens"], "top_k": voice["top_k"],
        "top_p": voice["top_p"], "min_p": voice["min_p"], "temperature": voice["temperature"],
        "repeat_penalty": voice["repeat_penalty"], "cfg_weight": voice["cfg_weight"],
        "exaggeration": voice["exaggeration"], "cfm_steps": voice["cfm_steps"],
        "stream_first_chunk_tokens": voice["first_chunk"], "stream_chunk_tokens": voice["chunk"],
        "max_sentence_chars": voice["max_sentence_chars"],
    }
    with LOCK:
        RUNTIME["lanes"][lane].update(status="connecting", session="", request="", samples=0, error="")
    return {"url": "ws://127.0.0.1:8095/tts", "message": init, "language": language, "style": style}


def tts_request(lane: str, text: str | None = None) -> dict:
    if lane not in RUNTIME["lanes"]:
        raise ApiError(400, f"unknown lane: {lane}")
    if RUNTIME["engines"]["tts"]["status"] != "running":
        raise ApiError(409, "tts is not running")
    if RUNTIME["lanes"][lane]["status"] != "ready" or not RUNTIME["lanes"][lane]["session"]:
        raise ApiError(409, f"lane {lane} has no ready session")
    text = str(text if text is not None else CONFIG["speech.text"]).strip()
    if not text:
        raise ApiError(400, "speech text is empty")
    request_id = uuid.uuid4().hex
    with LOCK:
        RUNTIME["lanes"][lane].update(status="queued", request=request_id, samples=0, error="")
    emit_state()
    return {"message": {"type": "synthesize", "text": text, "request_id": request_id}}


def tts_event(data: dict):
    lane = data.get("lane")
    event = data.get("event")
    if lane not in RUNTIME["lanes"] or event not in ("ready", "synthesize_started", "chunk_done", "cancelled", "error", "closed"):
        raise ApiError(400, "invalid TTS event")
    with LOCK:
        state = RUNTIME["lanes"][lane]
        state["status"] = {"ready": "ready", "synthesize_started": "streaming", "chunk_done": "ready", "cancelled": "cancelled", "error": "error", "closed": "closed"}[event]
        if "session_id" in data:
            state["session"] = str(data["session_id"])
        if "request_id" in data:
            state["request"] = str(data["request_id"])
        if "samples" in data:
            state["samples"] = int(data["samples"])
        state["error"] = str(data.get("message", "")) if event == "error" else ""
    emit_state()


def tts_cancel(session_id: str) -> dict:
    if not session_id:
        raise ApiError(400, "session_id is required")
    payload = json.dumps({"session_id": session_id}, separators=(",", ":")).encode()
    return json.loads(remote("http://127.0.0.1:8095/cancel", payload))


def read_log(limit: int = 200) -> list:
    limit = max(1, min(int(limit), 2000))
    log_file = ROOT / "install.log.jsonl"
    if not log_file.is_file():
        return []
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()[-limit:]]


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


def wait_job(key: str, timeout: float = 600) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with LOCK:
            job = deepcopy(RUNTIME["jobs"].get(key, {}))
        status = job.get("status")
        if status == "done":
            return job
        if status == "error":
            raise ApiError(500, job.get("error") or f"{key} failed")
        time.sleep(0.25)
    raise ApiError(504, f"{key} timed out")


def ensure_engine(name: str, load: bool):
    if name not in ENGINE_MODELS:
        raise ApiError(400, f"unknown engine: {name}")
    if RUNTIME["engines"][name]["status"] == "running":
        return
    if not load:
        raise ApiError(409, f"{name} is not running")
    start_job("engine", name, lambda key: load_engine(name, key))
    wait_job(f"engine:{name}")


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as audio:
        return audio.getnframes() / float(audio.getframerate() or 1)


def turn_audio(payload: dict, raw: bytes | None) -> bytes:
    source = payload.get("source", "upload" if raw or payload.get("audio_base64") else "reference")
    if source == "reference":
        return reference_path().read_bytes()
    if source == "upload":
        data = raw
        if data is None and payload.get("audio_base64"):
            data = base64.b64decode(payload["audio_base64"])
        if not data:
            raise ApiError(400, "turn WAV body is required")
        return data
    raise ApiError(400, "turn source must be reference or a WAV body")


def run_turn(payload: dict, raw: bytes | None = None) -> dict:
    audio = turn_audio(payload, raw)
    language = str(payload.get("language") or CONFIG["conversation.language"])
    if language not in CONVERSATION_LANGUAGES:
        raise ApiError(400, f"conversation language must be one of {list(CONVERSATION_LANGUAGES)}")
    clone = payload.get("clone", CONFIG["conversation.clone_voice"])
    if type(clone) is str:
        clone = clone.strip().lower() in ("1", "true", "yes", "on")
    else:
        clone = bool(clone)
    results: dict[str, Any] = {}
    report = {"ok": False, "clone": clone, "cloned": False, "language": language, "text": "", "results": results, "error": ""}
    try:
        if clone:
            try:
                validate_wav(audio)
                report["cloned"] = True
                info("turn", "clone reference from ask audio", {"seconds": round(wav_duration(audio), 2)})
            except ApiError as exception:
                info("turn", "clone skipped", {"error": str(exception)})
                report["clone_error"] = str(exception)
        set_flow("transcribing", language=language)
        ensure_engine("asr", payload.get("load", True) is not False)
        results["asr"] = transcribe(audio)
        transcript = str(results["asr"].get("text") or "").strip()
        if not transcript:
            raise ApiError(422, "speech was not recognized")
        set_flow("thinking", transcript=transcript, language=language)
        ensure_engine("brain", payload.get("load", True) is not False)
        prompt = str(payload.get("prompt") or f"Respond naturally to this speech transcript:\n\n{transcript}")
        results["brain"] = brain(prompt, language)
        speak = brain_reply_text(results["brain"]) or transcript
        report["text"] = speak
        ensure_engine("tts", payload.get("load", True) is not False)
        set_flow("ready_to_speak", transcript=transcript, answer=speak, language=language)
        report.update(ok=True, results=results)
        return report
    except Exception as exception:
        report["error"] = str(exception)
        report["results"] = results
        set_flow("error", failure=report["error"], language=language)
        error("turn", "failed", {"error": report["error"]})
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
    request = urllib.request.Request(url, headers={"User-Agent": "clone-reliable/2"})
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
    "log": {"doc": "install.log.jsonl tail", "fields": ["limit"]},
    "clear_log": {"doc": "erase install.log.jsonl and the panel log pane", "fields": []},
    "note": {"doc": "append a line to the panel log", "fields": ["msg", "data"]},
    "set": {"doc": "write user-facing configuration", "fields": ["values"]},
    "install_prerequisite": {"doc": "install a host prerequisite", "fields": ["name"]},
    "install_component": {"doc": "install a pinned runtime component; only Chatterbox TTS builds locally", "fields": ["name"]},
    "download_model": {"doc": "download a pinned model or default reference asset", "fields": ["name"]},
    "set_brain": {"doc": "select a catalog brain or download a custom GGUF URL", "fields": ["name", "url", "family"]},
    "load_engine": {"doc": "load tts, asr, or brain", "fields": ["name"]},
    "unload_engine": {"doc": "unload tts, asr, or brain", "fields": ["name"]},
    "upload_reference": {"doc": "replace reference.wav", "fields": ["audio_base64"], "body": "audio/wav"},
    "asr": {"doc": "transcribe WAV via Parakeet", "fields": ["source", "audio_base64"], "source": ["reference", "upload"]},
    "brain": {"doc": "ask the active brain", "fields": ["prompt", "language"]},
    "tts_session": {"doc": "open a Chatterbox V3 session", "fields": ["lane", "language", "style"]},
    "tts_request": {"doc": "queue a synthesize message", "fields": ["lane", "text"]},
    "tts_event": {"doc": "report a lane websocket event", "fields": ["lane", "event"]},
    "tts_cancel": {"doc": "cancel a TTS session", "fields": ["session_id"]},
    "turn": {"doc": "WAV input -> Parakeet -> brain; browser streams Chatterbox audio", "fields": ["source", "clone", "language", "audio_base64", "load", "prompt"], "source": ["reference", "upload"], "body": "audio/wav"},
}


def inspect() -> dict:
    return {"ok": True, "version": 3, "control": "/api", "ops": OPS, "schema": SCHEMA, "state": snapshot()}


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
        return {"ok": True, "lines": read_log(payload.get("limit", 120))}, 200
    if op == "clear_log":
        reset_log()
        emit("log", {"lines": []})
        return {"ok": True, "lines": []}, 200
    if op == "note":
        msg = payload.get("msg") or payload.get("message")
        if type(msg) is not str or not msg.strip():
            raise ApiError(400, "msg is required")
        data = payload.get("data") if type(payload.get("data")) is dict else {}
        info("api", msg.strip(), data)
        lines = read_log(payload.get("limit", 120))
        emit("log", {"lines": lines})
        return {"ok": True, "lines": lines}, 200
    if op == "set":
        set_config(payload.get("values"))
        return {"ok": True, "state": snapshot()}, 200
    if op == "install_prerequisite":
        name = payload.get("name")
        if name not in SCHEMA["prerequisites"]:
            raise ApiError(404, f"unknown prerequisite: {name}")
        start_job("prerequisite", name, lambda key: install_prerequisite(name, key))
        return {"ok": True, "accepted": True, "op": op, "name": name}, 202
    if op == "install_component":
        name = payload.get("name")
        if name not in ("tts", *BINARIES):
            raise ApiError(404, f"unknown component: {name}")
        start_job("component", name, lambda key: install_component(name, key))
        return {"ok": True, "accepted": True, "op": op, "name": name}, 202
    if op == "download_model":
        name = payload.get("name")
        if name not in MODELS:
            raise ApiError(404, f"unknown model: {name}")
        start_job("model", name, lambda key: download_model(name, key))
        return {"ok": True, "accepted": True, "op": op, "name": name}, 202
    if op == "set_brain":
        name = str(payload.get("name") or "custom")
        family = str(payload.get("family") or ("generic" if name == "custom" else BRAINS.get(name, {}).get("family") or "generic"))
        url = str(payload.get("url") or "").strip()
        if name == "custom" and url:
            resolve_brain_url(url)
            if family not in BRAIN_FAMILIES:
                raise ApiError(400, f"family must be one of {list(BRAIN_FAMILIES)}")
            start_job("brain", "custom", lambda key: install_custom_brain(url, family, key))
            return {"ok": True, "accepted": True, "op": op, "name": name}, 202
        apply_brain(name, family if name == "custom" else None)
        return {"ok": True, "brain": brain_snapshot(), "state": snapshot()}, 200
    if op == "load_engine":
        name = payload.get("name")
        if name not in ENGINE_MODELS:
            raise ApiError(404, f"unknown engine: {name}")
        start_job("engine", name, lambda key: load_engine(name, key))
        return {"ok": True, "accepted": True, "op": op, "name": name}, 202
    if op == "unload_engine":
        name = payload.get("name")
        if name not in ENGINE_MODELS:
            raise ApiError(404, f"unknown engine: {name}")
        start_job("engine", name, lambda key: stop_engine(name))
        return {"ok": True, "accepted": True, "op": op, "name": name}, 202
    if op == "upload_reference":
        data = raw
        if data is None and payload.get("audio_base64"):
            data = base64.b64decode(payload["audio_base64"])
        if not data:
            raise ApiError(400, "reference WAV body is required")
        validate_wav(data)
        emit_state()
        return {"ok": True, "reference": reference_state()}, 200
    if op == "asr":
        source = payload.get("source", "upload" if raw or payload.get("audio_base64") else "reference")
        if source == "reference":
            data = reference_path().read_bytes()
        elif source == "upload":
            data = raw
            if data is None and payload.get("audio_base64"):
                data = base64.b64decode(payload["audio_base64"])
            if not data:
                raise ApiError(400, "asr WAV body is required")
        else:
            raise ApiError(400, "asr source must be reference or upload")
        return {"ok": True, "result": transcribe(data)}, 200
    if op == "brain":
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ApiError(400, "prompt is required")
        return {"ok": True, "result": brain(prompt, str(payload.get("language") or CONFIG["conversation.language"]))}, 200
    if op == "tts_session":
        return tts_session(payload["lane"], payload.get("language"), payload.get("style")), 200
    if op == "tts_request":
        return tts_request(payload["lane"], payload.get("text")), 200
    if op == "tts_event":
        tts_event(payload)
        return {"ok": True}, 200
    if op == "tts_cancel":
        return {"ok": True, "result": tts_cancel(payload.get("session_id", ""))}, 200
    if op == "turn":
        return run_turn(payload, raw), 200
    raise ApiError(400, f"unhandled op: {op}")


SCHEMA = {
    "version": 3,
    "control": "/api",
    "fields": FIELDS,
    "languages": {"conversation": CONVERSATION_LANGUAGES, "speech": TTS_LANGUAGES, "asr": ASR_LANGUAGES},
    "voice_styles": VOICE_STYLES,
    "ops": OPS,
    "prerequisites": {name: {"label": label, "install": f"/api/prerequisites/{name}/install", "op": "install_prerequisite", "name": name} for name, label in {"python": "PYTHON 3.11+", "git": "GIT (TTS BUILD)", "cmake": "CMAKE (TTS BUILD)", "msvc": "MSVC (TTS BUILD)", "vulkan": "VULKAN SDK (TTS BUILD)"}.items()},
    "components": {
        "tts": {"label": "CHATTERBOX TTS (BUILD)", "install": "/api/components/tts/install", "op": "install_component", "name": "tts"},
        **{name: {"label": spec["label"], "install": f"/api/components/{name}/install", "op": "install_component", "name": name, "tag": spec["tag"]} for name, spec in BINARIES.items()},
    },
    "models": {name: {"label": spec["label"], "download": f"/api/models/{name}/download", "op": "download_model", "name": name, "revision": spec.get("revision", ""), "size": spec["size"], "sha256": spec["sha256"], **({"license": spec["license"]} if spec.get("license") else {})} for name, spec in MODELS.items()},
    "brains": {name: {"label": spec["label"], "model": spec["model"], "family": spec["family"]} for name, spec in BRAINS.items()},
    "brain_families": list(BRAIN_FAMILIES),
    "engines": {name: {"load": f"/api/engines/{name}/load", "unload": f"/api/engines/{name}/unload", "load_op": "load_engine", "unload_op": "unload_engine", "name": name} for name in ENGINE_MODELS},
    "endpoints": {"control": "/api", "events": "/api/events", "state": "/api/state", "reference": "/api/reference", "asr": "/api/asr", "brain": "/api/brain", "tts_session": "/api/tts/session", "tts_request": "/api/tts/request", "tts_cancel": "/api/tts/cancel", "tts_event": "/api/tts/event", "turn": "/api?op=turn", "log": "/api/log", "set_brain": "/api?op=set_brain"},
    "defaults": {"tts_runtime": TTS_RUNTIME, "voice": VOICE_DEFAULTS, "asr": ASR_RUNTIME, "brain_runtime": BRAIN_RUNTIME, "brain_generation": BRAIN_GENERATION},
    "tts": {"url": "ws://127.0.0.1:8095/tts", "text": "JSON", "audio": "binary PCM16LE mono 24000 Hz", "messages": ["init", "synthesize", "cancel", "close"], "events": ["ready", "synthesize_started", "audio", "chunk_done", "cancelled", "error"]},
}


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
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
            if path in ("/", "/panel.html"):
                self.send_bytes((ROOT / "panel.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/panel.css":
                self.send_bytes((ROOT / "panel.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/panel.js":
                self.send_bytes((ROOT / "panel.js").read_bytes(), "text/javascript; charset=utf-8")
            elif path == "/audio-processor.js":
                self.send_bytes((ROOT / "audio-processor.js").read_bytes(), "text/javascript; charset=utf-8")
            elif path == "/api/events":
                self.events()
            elif path == "/api" or path.startswith("/api/"):
                op = query.get("op")
                if path == "/api/schema" or op == "schema":
                    self.send_json(SCHEMA)
                elif path == "/api/state" or op == "state":
                    self.send_json(snapshot())
                elif path == "/api/reference" or op == "reference":
                    self.send_bytes(reference_path().read_bytes(), "audio/wav")
                elif path == "/api/log" or op == "log":
                    self.send_json(read_log(query.get("limit", 200)))
                elif path == "/api":
                    body, code = dispatch(op or "inspect", query)
                    self.send_json(body, code)
                else:
                    raise ApiError(404, f"unknown endpoint: {path}")
            else:
                raise ApiError(404, f"unknown endpoint: {path}")
        except ApiError as exception:
            self.send_json({"error": str(exception)}, exception.code)
        except Exception as exception:
            if client_gone(exception):
                return
            error("api", "GET failed", {"path": self.path, "error": str(exception)})
            self.send_json({"error": str(exception)}, 500)

    def events(self):
        subscriber: queue.Queue = queue.Queue()
        with LOCK:
            SUBSCRIBERS.add(subscriber)
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

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
            parts = path.strip("/").split("/")
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if path == "/api":
                raw_ops = {"asr", "upload_reference", "turn"}
                if content_type in ("audio/wav", "application/octet-stream") or query.get("op") in raw_ops and content_type != "application/json":
                    body, code = dispatch(query.get("op") or "asr", query, self.body())
                else:
                    payload = self.request_json(True)
                    body, code = dispatch(payload.get("op") or query.get("op") or "inspect", payload)
                self.send_json(body, code)
            elif path == "/api/state":
                body, code = dispatch("set", self.request_json())
                self.send_json(body.get("state", body), code)
            elif len(parts) == 4 and parts[:2] == ["api", "prerequisites"] and parts[3] == "install":
                body, code = dispatch("install_prerequisite", {"name": parts[2]})
                self.send_json(body, code)
            elif len(parts) == 4 and parts[:2] == ["api", "components"] and parts[3] == "install":
                body, code = dispatch("install_component", {"name": parts[2]})
                self.send_json(body, code)
            elif len(parts) == 4 and parts[:2] == ["api", "models"] and parts[3] == "download":
                body, code = dispatch("download_model", {"name": parts[2]})
                self.send_json(body, code)
            elif len(parts) == 4 and parts[:2] == ["api", "engines"] and parts[3] in ("load", "unload"):
                body, code = dispatch("load_engine" if parts[3] == "load" else "unload_engine", {"name": parts[2]})
                self.send_json(body, code)
            elif path == "/api/reference":
                body, code = dispatch("upload_reference", {}, self.body())
                self.send_json(body.get("reference", body), code)
            elif path == "/api/asr":
                body, code = dispatch("asr", {"source": "upload"}, self.body())
                self.send_json(body.get("result", body), code)
            elif path == "/api/brain":
                body, code = dispatch("brain", self.request_json(True))
                self.send_json(body.get("result", body), code)
            elif path == "/api/tts/session":
                body, code = dispatch("tts_session", self.request_json())
                self.send_json(body, code)
            elif path == "/api/tts/request":
                body, code = dispatch("tts_request", self.request_json())
                self.send_json(body, code)
            elif path == "/api/tts/event":
                body, code = dispatch("tts_event", self.request_json())
                self.send_json(body, code)
            elif path == "/api/tts/cancel":
                body, code = dispatch("tts_cancel", self.request_json())
                self.send_json(body.get("result", body), code)
            else:
                raise ApiError(404, f"unknown endpoint: {path}")
        except ApiError as exception:
            self.send_json({"error": str(exception)}, exception.code)
        except (KeyError, TypeError, ValueError) as exception:
            self.send_json({"error": f"invalid request: {exception}"}, 400)
        except Exception as exception:
            if client_gone(exception):
                return
            error("api", "POST failed", {"path": self.path, "error": str(exception)})
            self.send_json({"error": str(exception)}, 500)


class Server(ThreadingHTTPServer):
    daemon_threads = True


def main() -> int:
    server = Server(("127.0.0.1", 8765), Handler)
    timer = threading.Timer(.4, webbrowser.open, args=("http://127.0.0.1:8765/",))
    timer.daemon = True
    timer.start()
    print("CLONE RELIABLE  http://127.0.0.1:8765/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for name in list(PROCESSES):
            stop_engine(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
