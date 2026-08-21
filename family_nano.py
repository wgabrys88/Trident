FAMILY = {
    "name": "nano",
    "TTS_LABEL": "CHATTERBOX TTS NANO",
    "TTS_EXE": "trident-tts-nano.exe",
    "TTS_MULTILINGUAL": False,
    "TTS_LANGUAGES": {"en": "English"},
    "DEFAULT_REPLY_LANGUAGE": "en",
    "TTS_RUNTIME": {"gpu_layers": 99, "context": 2048, "threads": 4},
    "TTS_SAMPLE": {
        "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.95,
        "min_p": 0.0, "temperature": 0.8, "repeat_penalty": 1.2,
        "cfm_steps": 2,
    },
    "TTS_VOICE": {"cfg_weight": 0.0, "exaggeration": 0.0},
    "TTS_CHUNK": {"first_chars": 180, "chars": 280},
    "TTS_MODELS": {
        "chatterbox-t3": {
            "label": "CHATTERBOX NANO T3", "repo": "ResembleAI/chatterbox-nano",
            "revision": "71ccd1d0081b430592cea481f4307e764e07bc64",
            "file": "chatterbox-t3-nano-q4_0.gguf", "size": 171901536,
            "convert": {
                "script": "convert-t3-turbo-to-gguf.py", "quant": "q4_0",
                "files": (
                    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
                    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
                ),
                "copy": {"t3_nano_v1.safetensors": "t3_turbo_v1.safetensors"},
            },
        },
        "chatterbox-codec": {
            "label": "CHATTERBOX NANO S3GEN", "repo": "ResembleAI/chatterbox-nano",
            "revision": "71ccd1d0081b430592cea481f4307e764e07bc64",
            "file": "chatterbox-s3gen-nano-f16.gguf", "size": 1064879936,
            "convert": {
                "script": "convert-s3gen-to-gguf.py", "quant": "f16", "variant": "turbo",
                "files": (
                    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
                    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
                ),
            },
        },
    },
}
