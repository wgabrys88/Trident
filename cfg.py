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

# Live copy is built from the dicts above. KNOBS is ranges/apply only.
KNOBS = {
    "mic": {
        "vad_threshold": {"min": 0.005, "max": 0.08, "step": 0.005},
        "vad_silence_ms": {"min": 200, "max": 2000, "step": 50},
        "vad_min_speech_ms": {"min": 150, "max": 1500, "step": 50},
        "pre_roll_ms": {"min": 0, "max": 800, "step": 50},
        "clone_reference_seconds": {"min": 5, "max": 30, "step": 1},
        "auto_send": {},
    },
    "asr_runtime": {
        "threads": {"min": 1, "max": 16, "step": 1, "apply": "load"},
        "device": {"choices": ["Vulkan0", "cpu"], "apply": "load"},
        "response_format": {"choices": ["json", "verbose_json"]},
    },
    "asr_chunk": {
        "seconds": {"min": 5, "max": 40, "step": 1},
        "overlap": {"min": 0.2, "max": 5, "step": 0.2},
    },
    "tts_runtime": {
        "gpu_layers": {"min": 1, "max": 99, "step": 1, "apply": "load"},
        "context": {"min": 256, "max": 2048, "step": 64, "apply": "load"},
        "threads": {"min": 1, "max": 16, "step": 1, "apply": "load"},
    },
    "tts_sample": {
        "seed": {"min": 0, "max": 999999, "step": 1},
        "max_tokens": {"min": 64, "max": 2000, "step": 16},
        "top_k": {"min": 1, "max": 1000, "step": 1},
        "top_p": {"min": 0.05, "max": 1, "step": 0.01},
        "min_p": {"min": 0, "max": 0.5, "step": 0.01},
        "temperature": {"min": 0.05, "max": 2, "step": 0.05},
        "repeat_penalty": {"min": 1, "max": 2, "step": 0.05},
        "cfm_steps": {"min": 5, "max": 20, "step": 1},
    },
    "tts_voice": {
        "cfg_weight": {"min": 0, "max": 1, "step": 0.05},
        "exaggeration": {"min": 0.25, "max": 2, "step": 0.05},
    },
    "tts_chunk": {"chars": {"min": 40, "max": 400, "step": 10}},
    "brain_runtime": {
        "device": {"choices": ["Vulkan0", "cpu"], "apply": "load"},
        "gpu_layers": {"choices": ["all", "99", "40", "20", "0"], "apply": "load"},
        "context": {"min": 512, "max": 8192, "step": 256, "apply": "load"},
        "parallel": {"min": 1, "max": 4, "step": 1, "apply": "load"},
        "flash_attn": {"choices": ["on", "off"], "apply": "load"},
        "fit": {"choices": ["on", "off"], "apply": "load"},
        "fit_target": {"min": 256, "max": 4096, "step": 128, "apply": "load"},
        "fit_ctx": {"min": 512, "max": 8192, "step": 256, "apply": "load"},
    },
    "brain_generation": {
        "temperature": {"min": 0, "max": 2, "step": 0.05},
        "top_p": {"min": 0.05, "max": 1, "step": 0.01},
        "top_k": {"min": 1, "max": 128, "step": 1},
        "min_p": {"min": 0, "max": 0.5, "step": 0.01},
        "repeat_penalty": {"min": 1, "max": 2, "step": 0.05},
        "seed": {"min": 0, "max": 999999, "step": 1},
        "max_tokens": {"min": 32, "max": 2048, "step": 32},
    },
    "brain_thinking": {},
    "brain_system": {},
}

KNOB_ENGINE = {
    "asr_runtime": "asr",
    "tts_runtime": "tts",
    "brain_runtime": "brain",
}
