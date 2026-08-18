CONTROLLER = {"host": "127.0.0.1", "port": 8765}
PORTS = {"tts": 8095, "asr": 8097, "brain": 8098}

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
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "tr": "Turkish", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "no": "Norwegian", "el": "Greek", "ms": "Malay", "sw": "Swahili",
    "ar": "Arabic", "ko": "Korean",
}
DEFAULT_REPLY_LANGUAGE = "en"

MIC = {
    "sample_rate": 16000,
    "vad_threshold": 0.020,
    "vad_silence_ms": 700,
    "vad_min_speech_ms": 400,
    "pre_roll_ms": 300,
    "clone_reference_seconds": 10,
    "auto_send": True,
}

ASR_RUNTIME = {"threads": 4, "device": "Vulkan0", "response_format": "json"}
ASR_CHUNK = {"seconds": 20.0, "overlap": 1.0}

TTS_RUNTIME = {"gpu_layers": 99, "context": 512, "threads": 4}
TTS_SAMPLE = {
    "seed": 42, "max_tokens": 1000, "top_k": 1000, "top_p": 0.95,
    "min_p": 0.05, "temperature": 0.8, "repeat_penalty": 1.2, "cfm_steps": 5,
}
TTS_VOICE = {"cfg_weight": 0.5, "exaggeration": 0.5}
TTS_CHUNK = {"chars": 120}

BRAIN_MODEL = "gemma"
BRAIN_RUNTIME = {
    "device": "Vulkan0", "gpu_layers": "all", "context": 4096, "parallel": 1,
    "flash_attn": "on", "fit": "on", "fit_target": 3072, "fit_ctx": 4096,
}
BRAIN_GENERATION = {
    "temperature": 0.3, "top_p": 0.90, "top_k": 40, "min_p": 0.0,
    "repeat_penalty": 1.05, "seed": 42, "max_tokens": 1024,
}
BRAIN_FAMILY = {
    "reasoning_effort": "none",
    "reasoning_format": "none",
    "chat_template_kwargs": {"enable_thinking": False},
}
BRAIN_SYSTEM = (
    "Reply in {language_name} ({language}). Answer naturally and completely. "
    "Match the user's requested level of detail instead of forcing a fixed number "
    "of sentences. Use speech-friendly prose, avoid meta commentary, and do not "
    "mention transcription or internal reasoning."
)
