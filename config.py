from __future__ import annotations

from paths import DATA, MODELS_DIR, ROOT, THIRD_PARTY, TOOLS, PATCHES, TTS, CHATTERBOX, GGML, RUNTIMES, CONVERTER

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

_EN = {"en": "English"}

V3_SAMPLE = {
    "seed": 42, "max_tokens": 768, "top_p": 1.0,
    "temperature": 0.8, "repeat_penalty": 1.2,
    "min_p": 0.05, "cfm_steps": 7,
}

TURBO_SAMPLE = {
    "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.99,
    "temperature": 0.6, "repeat_penalty": 1.3,
    "cfm_steps": 2,
}

NANO_SAMPLE = {
    "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.95,
    "temperature": 0.8, "repeat_penalty": 1.2,
    "cfm_steps": 2,
}

V3_CKPT = (
    "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen_v3.pt",
    "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
)

TURBO_CKPT = (
    "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)

NANO_CKPT = (
    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)


def _gguf(label, repo, revision, file, size, script, files, quant="q4_0", variant=None, copy=None):
    convert = {"script": script, "quant": quant, "files": files}
    if variant:
        convert["variant"] = variant
    if copy:
        convert["copy"] = copy
    return {"label": label, "repo": repo, "revision": revision, "file": file, "size": size, "convert": convert}


def _family(label, languages, context, chars, sample_cfg, voice_cfg, models, exe):
    return {
        "TTS_LANGUAGES": languages,
        "DEFAULT_REPLY_LANGUAGE": "en",
        "TTS_LABEL": label,
        "TTS_EXE": exe,
        "TTS_RUNTIME": {"gpu_layers": 99, "context": context, "threads": 4},
        "TTS_SAMPLE": sample_cfg,
        "TTS_VOICE": voice_cfg,
        "TTS_CHUNK": {"chars": chars},
        "TTS_MODELS": models,
    }


FAMILIES = {
    "v3": _family(
        "CHATTERBOX TTS V3", {"en": "English", "pl": "Polish", "de": "German"},
        2048, 180,
        V3_SAMPLE,
        {"cfg_weight": 0.5, "exaggeration": 0.3},
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX V3 T3", "ResembleAI/chatterbox",
                "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                "chatterbox-t3-mtl-v3-q4_0.gguf", 344985408,
                "convert-t3-mtl-to-gguf.py", V3_CKPT,
                copy={"t3_mtl23ls_v3.safetensors": "t3_mtl23ls_v2.safetensors"},
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX V3 S3GEN", "ResembleAI/chatterbox",
                "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                "chatterbox-s3gen-mtl-v3-f16.gguf", 1056431360,
                "convert-s3gen-to-gguf.py", V3_CKPT, quant="f16", variant="mtl",
                copy={"s3gen_v3.pt": "s3gen.pt"},
            ),
        },
        "trident-tts-v3.exe",
    ),
    "turbo": _family(
        "CHATTERBOX TTS TURBO", dict(_EN),
        2048, 120,
        TURBO_SAMPLE,
        {"cfg_weight": 0.0, "exaggeration": 0.0},
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX TURBO T3", "ResembleAI/chatterbox-turbo",
                "749d1c1a46eb10492095d68fbcf55691ccf137cd",
                "chatterbox-t3-turbo-q4_0.gguf", 333506240,
                "convert-t3-turbo-to-gguf.py", TURBO_CKPT,
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX TURBO S3GEN", "ResembleAI/chatterbox-turbo",
                "749d1c1a46eb10492095d68fbcf55691ccf137cd",
                "chatterbox-s3gen-turbo-f16.gguf", 1064879936,
                "convert-s3gen-to-gguf.py", TURBO_CKPT, quant="f16", variant="turbo",
            ),
        },
        "trident-tts-turbo.exe",
    ),
    "nano": _family(
        "CHATTERBOX TTS NANO", dict(_EN),
        2048, 180,
        NANO_SAMPLE,
        {"cfg_weight": 0.0, "exaggeration": 0.0},
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX NANO T3", "ResembleAI/chatterbox-nano",
                "71ccd1d0081b430592cea481f4307e764e07bc64",
                "chatterbox-t3-nano-q4_0.gguf", 171901536,
                "convert-t3-turbo-to-gguf.py", NANO_CKPT,
                copy={"t3_nano_v1.safetensors": "t3_turbo_v1.safetensors"},
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX NANO S3GEN", "ResembleAI/chatterbox-nano",
                "71ccd1d0081b430592cea481f4307e764e07bc64",
                "chatterbox-s3gen-nano-f16.gguf", 1064879936,
                "convert-s3gen-to-gguf.py", NANO_CKPT, quant="f16", variant="turbo",
            ),
        },
        "trident-tts-nano.exe",
    ),
}

SHARED_MODELS = {
    "parakeet": {"label": "PARAKEET TDT 0.6B V3 Q4_K", "repo": "mudler/parakeet-cpp-gguf", "revision": "bf0af9f425fa01809cadec671b3cb672709d13e9", "file": "tdt-0.6b-v3-q4_k.gguf", "size": 675200864},
    "gemma": {"label": "GEMMA 4 E2B", "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf", "revision": "675cff42a74c774d6cb76f76d8eacb49b48c9b93", "file": "gemma-4-E2B_q4_0-it.gguf", "size": 3349516256},
    "reference": {"label": "DEFAULT VOICE", "source": "assets/default-reference.wav", "file": "default-reference.wav", "directory": "data", "size": 1440078},
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
TTS_FAMILY_EXES = tuple(family["TTS_EXE"] for family in FAMILIES.values())

PROBE_EN = "When the sunlight strikes raindrops in the air, they act as a prism and form a rainbow."

REFERENCE_VOICES = {
    "trump": {"repo": "sdialog/voices-celebrities", "file": "audio/donald-trump.wav", "name": "Donald Trump"},
    "mj": {"repo": "amphion/singing_voice_conversion", "file": "michael-jackson.wav", "name": "Michael Jackson"},
    "shakira": {"repo": "QuickWick/Music-AI-Voices", "file": "shakira.wav", "name": "Shakira"},
}