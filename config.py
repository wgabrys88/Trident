from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DEFAULT_MODELS_DIR = ROOT / "models"
DEFAULT_DATA_DIR = ROOT / "data"

THIRD_PARTY = ROOT / "third_party"
TOOLS = ROOT / "tools"
PATCHES = ROOT / "patches"
TTS = ROOT / "tts"
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML = CHATTERBOX / "ggml"
RUNTIMES = TOOLS / "runtime"
CONVERTER = TOOLS / "convert"

ASR_RATE = 16000
TTS_RATE = 24000
REFERENCE_MIN_SECONDS = 5.0

ASR_RUNTIME = {
    "threads": 4,
    "device": "Vulkan0",
    # Parakeet v0.5 server keeps one pk::Model/ggml context resident. The
    # selected backend is forced by PARAKEET_DEVICE for every request.
    "resident": True,
}

# Localhost-only persistent inference services. They are deliberately on
# separate ports/processes so Parakeet and Gemma remain independent pipelines.
RESIDENT_SERVERS = {
    "parakeet": {"host": "127.0.0.1", "port": 17931, "url": "http://127.0.0.1:17931", "startup_timeout_s": 120},
    "gemma": {"host": "127.0.0.1", "port": 17932, "url": "http://127.0.0.1:17932", "startup_timeout_s": 180},
    "chatterbox": {"host": "127.0.0.1", "port": 17933, "url": "tcp://127.0.0.1:17933", "startup_timeout_s": 300},
}

BRAIN_MODEL = "gemma"
BRAIN_RUNTIME = {
    "device": "Vulkan0",
    "gpu_layers": "all",
    "context": 4096,
    # Gemma 4 + the pinned llama.cpp Vulkan generation is kept on standard
    # attention: recent Vulkan FA auto/on paths have shown severe decode
    # regressions, while F16 KV does not require Flash Attention.
    "flash_attn": "off",
    # Do not let llama.cpp silently shrink GPU placement to satisfy a margin.
    # If full offload cannot be allocated, startup should fail visibly instead.
    "fit": "off",
    "split_mode": "none",
    "main_gpu": 0,
    "load_mode": "mmap",
    "parallel": 1,
    "cache_type_k": "f16",
    "cache_type_v": "f16",
    # Full-GPU inference still needs a host control thread for tokenization,
    # sampling and dispatch. Avoid ggml thread-pool busy-spin while the GPU runs.
    "poll": 0,
    "poll_batch": 0,
    # Keep host scheduling/tokenization lean. Model layers and KV are still
    # explicitly offloaded; these threads are for unavoidable host control work.
    "threads": 2,
    "threads_batch": 2,
    "threads_http": 1,
    "resident": True,
}
BRAIN_GENERATION = {
    "temperature": 0.3, "top_p": 0.90, "top_k": 40, "min_p": 0.0,
    "repeat_penalty": 1.05, "seed": 42, "max_tokens": 1024,
}
BRAIN_THINKING = False
BRAIN_SYSTEM = (
    "The incoming speech transcript is expected to be {asr_language_name} ({asr_language}), "
    "or auto-detected when the expected language is auto. Produce the final spoken response "
    "only in {tts_language_name} ({tts_language}). If the input language differs, preserve its "
    "meaning while translating or answering in the output language. Spoken prose only: short "
    "sentences ending with a period, question mark, or exclamation. No markdown, lists, code, "
    "URLs, emoji, or square-bracket tags. Expand numbers and abbreviations. Do not mention "
    "transcription, models, or reasoning."
)

LANGUAGES = {"en": "English", "pl": "Polish", "de": "German"}

# NVIDIA Parakeet TDT 0.6B v3 detects these languages automatically; the
# parakeet.cpp v0.5 server has no language-selection flag for this checkpoint.
# Trident records an expected input language for validation/prompt context only.
ASR_LANGUAGES = {
    "bg": "Bulgarian", "hr": "Croatian", "cs": "Czech", "da": "Danish",
    "nl": "Dutch", "en": "English", "et": "Estonian", "fi": "Finnish",
    "fr": "French", "de": "German", "el": "Greek", "hu": "Hungarian",
    "it": "Italian", "lv": "Latvian", "lt": "Lithuanian", "mt": "Maltese",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "sk": "Slovak",
    "sl": "Slovenian", "es": "Spanish", "sv": "Swedish", "ru": "Russian",
    "uk": "Ukrainian",
}


def discover_families() -> dict:
    found = {}
    for path in ROOT.glob("family_*.py"):
        spec = getattr(importlib.import_module(path.stem), "FAMILY", None)
        if spec and spec.get("name"):
            found[spec["name"]] = spec
    order = [name for name in ("v3", "turbo", "nano") if name in found]
    order += sorted(name for name in found if name not in {"v3", "turbo", "nano"})
    return {name: found[name] for name in order}


FAMILIES = discover_families()


def default_family() -> str:
    if not FAMILIES:
        raise RuntimeError("no TTS families found")
    runtime = RUNTIMES / "tts"
    if runtime.is_dir():
        present = [name for name, spec in FAMILIES.items() if (runtime / spec["TTS_EXE"]).is_file()]
        if present:
            return present[0]
    return next(iter(FAMILIES))


SHARED_MODELS = {
    "parakeet": {"label": "PARAKEET TDT 0.6B V3 Q4_K", "repo": "mudler/parakeet-cpp-gguf", "revision": "bf0af9f425fa01809cadec671b3cb672709d13e9", "file": "tdt-0.6b-v3-q4_k.gguf", "size": 675200864},
    "gemma": {"label": "GEMMA 4 E2B", "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf", "revision": "675cff42a74c774d6cb76f76d8eacb49b48c9b93", "file": "gemma-4-E2B_q4_0-it.gguf", "size": 3349516256},
}

VULKAN_VERSION = "1.4.357.0"

PACKAGES = {
    "git": {"url": "https://github.com/git-for-windows/git/releases/download/v2.54.0.windows.1/MinGit-2.54.0-64-bit.zip", "file": "MinGit-2.54.0-64-bit.zip", "size": 39989839},
    "cmake": {"url": "https://github.com/Kitware/CMake/releases/download/v4.4.2/cmake-4.4.2-windows-x86_64.zip", "file": "cmake-4.4.2-windows-x86_64.zip", "size": 54405968},
    "msvc": {"url": "https://download.visualstudio.microsoft.com/download/pr/00d9d26c-2727-42c2-aa9e-eda63b03e1ee/15df9d3b4c2b2eaf44704d5e938c895341b9cd8ba40a9a18610f8d18cbe01b53/vs_BuildTools.exe", "file": "vs_BuildTools.exe", "size": 4458736},
    "vulkan": {"url": f"https://sdk.lunarg.com/sdk/download/{VULKAN_VERSION}/windows/vulkansdk-windows-X64-{VULKAN_VERSION}.exe", "file": f"vulkansdk-windows-X64-{VULKAN_VERSION}.exe", "size": 0},
}

SOURCES = {
    "chatterbox": ("https://github.com/gianni-cor/chatterbox.cpp", "ddca05fb69c2910b0d7b5eae420d360ed98c067b"),
    "ggml": ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951"),
}

BINARIES = {
    "parakeet": {
        "label": "PARAKEET.CPP V0.5 VULKAN", "repo": "mudler/parakeet.cpp", "tag": "v0.5.0",
        "asset": "parakeet-v0.5.0-bin-win-vulkan-x64.zip",
        "exe": "parakeet-cli.exe", "server_exe": "parakeet-server.exe",
    },
    "gemma": {
        "label": "LLAMA.CPP B10453 VULKAN", "repo": "ggml-org/llama.cpp", "tag": "b10453",
        "asset": "llama-b10453-bin-win-vulkan-x64.zip",
        "exe": "llama-cli.exe", "server_exe": "llama-server.exe",
    },
}

CHATTERBOX_LIBRARY = CHATTERBOX / "build" / "Release" / "tts-cpp.lib"
TTS_BUILD = TTS / "build" / "Release"
TTS_SERVER_EXE = "trident-tts-server.exe"
TTS_BUILD_REVISION = "20260821-synthesis-performance-2"

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
        self.reference = self.data_dir / REFERENCE_VOICES[DEFAULT_VOICE]["file"]
        self.run_dir = None
        self.transcript = None
        self.answer = None
        self.system = None
        self.output = None
        self.log = None
        if command:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            self.run_dir = self.data_dir / "runs" / f"{stamp}-{command}"
            self.run_dir.mkdir(parents=True)
            self.transcript = self.run_dir / "transcript.txt"
            self.answer = self.run_dir / "answer.txt"
            self.system = self.run_dir / "system.txt"
            self.output = self.run_dir / "output.wav"
            self.log = self.run_dir / "log.txt"
