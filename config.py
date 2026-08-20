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

ASR_RUNTIME = {"threads": 4, "device": "Vulkan0"}

BRAIN_MODEL = "gemma"
BRAIN_RUNTIME = {
    "device": "Vulkan0", "gpu_layers": "all", "context": 4096,
    "flash_attn": "on", "fit": "on", "fit_target": 1024, "fit_ctx": 4096,
}
BRAIN_GENERATION = {
    "temperature": 0.3, "top_p": 0.90, "top_k": 40, "min_p": 0.0,
    "repeat_penalty": 1.05, "seed": 42, "max_tokens": 1024,
}
BRAIN_THINKING = False
BRAIN_SYSTEM = (
    "Answer only in {language_name} ({language}). The user may have spoken "
    "another language; still answer only in {language_name}. Spoken prose: "
    "short sentences that end with a period, question mark, or exclamation. "
    "No markdown, lists, code, URLs, emoji, or square-bracket tags. Expand "
    "numbers and abbreviations. Match the user's level of detail. Do not "
    "mention transcription, models, or reasoning."
)

LANGUAGES = {"en": "English", "pl": "Polish", "de": "German"}


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
    "parakeet": {"label": "PARAKEET.CPP V0.5 VULKAN", "repo": "mudler/parakeet.cpp", "tag": "v0.5.0", "asset": "parakeet-v0.5.0-bin-win-vulkan-x64.zip", "exe": "parakeet-cli.exe"},
    "gemma": {"label": "LLAMA.CPP B10453 VULKAN", "repo": "ggml-org/llama.cpp", "tag": "b10453", "asset": "llama-b10453-bin-win-vulkan-x64.zip", "exe": "llama-cli.exe"},
}

CHATTERBOX_LIBRARY = CHATTERBOX / "build" / "Release" / "tts-cpp.lib"
TTS_BUILD = TTS / "build" / "Release"

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
    "oprah": {
        "label": "VOICE OPRAH", "name": "Oprah Winfrey",
        "repo": "sdialog/voices-celebrities", "revision": "57746b866d470be717097b87ba0428f8dd73e4f4",
        "source": "audio/oprah_winfrey.wav", "file": "ref-oprah.wav", "size": 16891982,
    },
}
DEFAULT_VOICE = "trump"


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
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.run_dir = self.data_dir / "runs" / f"{stamp}-{command}"
            self.run_dir.mkdir(parents=True)
            self.transcript = self.run_dir / "transcript.txt"
            self.answer = self.run_dir / "answer.txt"
            self.system = self.run_dir / "system.txt"
            self.output = self.run_dir / "output.wav"
            self.log = self.run_dir / "log.txt"
