# Edit this file and restart the controller. The panel does not re-encode these values.

CONTROLLER = {"host": "127.0.0.1", "port": 8765}
PORTS = {"tts": 8095, "asr": 8097, "brain": 8098}

TTS_LANGUAGES = {
    "en": "English",
    "pl": "Polish",
    "de": "German",
}
DEFAULT_REPLY_LANGUAGE = "en"

MIC = {
    "sample_rate": 16000,
    "vad_threshold": 0.020,
    "vad_silence_ms": 700,
    "vad_min_speech_ms": 400,
    "pre_roll_ms": 300,
    "clone_reference_seconds": 15,
    "auto_send": True,
}

ASR_RUNTIME = {"threads": 4, "device": "Vulkan0", "response_format": "json"}
ASR_CHUNK = {"seconds": 20.0, "overlap": 1.0}

TTS_RUNTIME = {"gpu_layers": 99, "context": 512, "threads": 4}
TTS_SAMPLE = {
    "seed": 42, "max_tokens": 1000, "top_k": 1000, "top_p": 0.95,
    "min_p": 0.05, "temperature": 0.8, "repeat_penalty": 1.2, "cfm_steps": 10,
}
TTS_VOICE = {"cfg_weight": 0.5, "exaggeration": 0.5}
TTS_CHUNK = {"chars": 120}
TTS_LABEL = "CHATTERBOX TTS V3"
_V3_CKPT = (
    "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen_v3.pt",
    "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
)
TTS_MODELS = {
    "chatterbox-t3": {
        "label": "CHATTERBOX V3 T3", "repo": "ResembleAI/chatterbox",
        "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
        "file": "chatterbox-t3-mtl-v3-q4_0.gguf", "size": 344985408,
        "convert": {"script": "convert-t3-mtl-to-gguf.py", "quant": "q4_0", "files": _V3_CKPT},
    },
    "chatterbox-codec": {
        "label": "CHATTERBOX V3 S3GEN", "repo": "ResembleAI/chatterbox",
        "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
        "file": "chatterbox-s3gen-mtl-v3-f16.gguf", "size": 1056431360,
        "convert": {"script": "convert-s3gen-to-gguf.py", "quant": "f16", "variant": "mtl", "files": _V3_CKPT},
    },
}

BRAIN_MODEL = "gemma"
BRAIN_RUNTIME = {
    "device": "Vulkan0", "gpu_layers": "all", "context": 4096, "parallel": 1,
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
