"""Trident runtime configuration.

Edit this file for engine/sampling/VAD behavior. The browser intentionally exposes
only conversation controls; advanced parameters live here so there is one source
of truth and no duplicated UI schema.
"""

CONTROLLER = {"host": "127.0.0.1", "port": 8765}
PORTS = {"tts": 8095, "asr": 8097, "brain": 8098}

# NVIDIA Parakeet-TDT 0.6B v3 auto-detects these 25 languages. Trident does not
# force an input language because this Parakeet model/runtime does not expose a
# per-request language selector.
ASR_LANGUAGES = {
    "bg": "Bulgarian", "hr": "Croatian", "cs": "Czech", "da": "Danish",
    "nl": "Dutch", "en": "English", "et": "Estonian", "fi": "Finnish",
    "fr": "French", "de": "German", "el": "Greek", "hu": "Hungarian",
    "it": "Italian", "lv": "Latvian", "lt": "Lithuanian", "mt": "Maltese",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "sk": "Slovak",
    "sl": "Slovenian", "es": "Spanish", "sv": "Swedish", "ru": "Russian",
    "uk": "Ukrainian",
}

TTS_LANGUAGES = {
    "en": "English", "pt": "Portuguese", "ar": "Arabic", "zh": "Chinese",
    "da": "Danish", "nl": "Dutch", "fi": "Finnish", "fr": "French",
    "de": "German", "el": "Greek", "he": "Hebrew", "hi": "Hindi",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "ms": "Malay",
    "no": "Norwegian", "pl": "Polish", "ru": "Russian", "es": "Spanish",
    "sw": "Swahili", "sv": "Swedish", "tr": "Turkish",
}
DEFAULT_REPLY_LANGUAGE = "en"

# Browser microphone / buffered-live ASR. Live partials repeatedly transcribe
# the growing utterance because parakeet-server v0.5 accepts WAV uploads rather
# than exposing a streaming socket for this model.
MIC = {
    "sample_rate": 16000,
    "vad_threshold": 0.020,
    "vad_silence_ms": 700,
    "vad_min_speech_ms": 400,
    "pre_roll_ms": 300,
    "partial_asr_ms": 1500,
    "partial_min_ms": 700,
    "clone_reference_seconds": 10,
    "auto_send": True,
}

ASR_RUNTIME = {
    "threads": 4,
    "device": "Vulkan0",
    "response_format": "json",
}

# Certified MILESTONE native Chatterbox profile.
TTS_RUNTIME = {
    "gpu_layers": 99,
    "context": 512,
    "sessions": 2,
    "threads": 4,
}
TTS_SAMPLE = {
    "seed": 42,
    "max_tokens": 1000,
    "top_k": 1000,
    "top_p": 0.95,
    "min_p": 0.05,
    "temperature": 0.8,
    "repeat_penalty": 1.2,
    "cfm_steps": 7,
}
TTS_STREAM = {
    "first_chunk_tokens": 12,
    "chunk_tokens": 25,
    "max_sentence_chars": 180,
}
TTS_VOICE = {
    "cfg_weight": 0.5,
    "exaggeration": 0.5,
}
TTS_SAMPLE_RATE = 24000

# Brain. MILESTONE-3 forced replies to one/two sentences and max_tokens=160;
# both constraints are removed here. The model can answer normally while still
# producing speech-friendly prose.
BRAIN_MODEL = "gemma"
BRAIN_RUNTIME = {
    "device": "Vulkan0",
    "gpu_layers": "all",
    "context": 4096,
    "parallel": 1,
    "flash_attn": "on",
    "fit": "on",
    "fit_target": 3072,
    "fit_ctx": 4096,
}
BRAIN_GENERATION = {
    "temperature": 0.3,
    "top_p": 0.90,
    "top_k": 40,
    "min_p": 0.0,
    "repeat_penalty": 1.05,
    "seed": 42,
    "max_tokens": 1024,
}
BRAIN_FAMILIES = {
    "gemma4": {"reasoning_effort": "none", "reasoning_format": "none", "chat_template_kwargs": {"enable_thinking": False}},
    "qwen35": {"reasoning_effort": "none", "reasoning_format": "none", "chat_template_kwargs": {"enable_thinking": False}},
    "generic": {},
}
BRAIN_SYSTEM = (
    "Reply in {language_name} ({language}). Answer naturally and completely. "
    "Match the user's requested level of detail instead of forcing a fixed number "
    "of sentences. Use speech-friendly prose, avoid meta commentary, and do not "
    "mention transcription or internal reasoning."
)
