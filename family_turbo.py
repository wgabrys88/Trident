FAMILY = {
    "name": "turbo",
    "TTS_LANGUAGES": {"en": "English"},
    "DEFAULT_REPLY_LANGUAGE": "en",
    "TTS_RUNTIME": {"gpu_layers": 99, "context": 2048, "threads": 4},
    "TTS_SAMPLE": {
        "seed": 42, "max_tokens": 768, "top_k": 1000, "top_p": 0.99,
        "min_p": 0.0, "temperature": 0.6, "repeat_penalty": 1.3,
        "cfm_steps": 2,
    },
    "TTS_VOICE": {"cfg_weight": 0.0, "exaggeration": 0.0},
    "TTS_CHUNK": {"first_chars": 120, "chars": 280},
    "TTS_MODELS": {
        "chatterbox-t3": {
            "label": "CHATTERBOX TURBO T3", "repo": "ResembleAI/chatterbox-turbo",
            "revision": "749d1c1a46eb10492095d68fbcf55691ccf137cd",
            "file": "chatterbox-t3-turbo-q4_0.gguf", "size": 333506240,
            "convert": {
                "script": "convert-t3-turbo-to-gguf.py", "quant": "q4_0",
                "files": (
                    "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
                    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
                ),
            },
        },
        "chatterbox-codec": {
            "label": "CHATTERBOX TURBO S3GEN", "repo": "ResembleAI/chatterbox-turbo",
            "revision": "749d1c1a46eb10492095d68fbcf55691ccf137cd",
            "file": "chatterbox-s3gen-turbo-f16.gguf", "size": 1064879936,
            "convert": {
                "script": "convert-s3gen-to-gguf.py", "quant": "f16", "variant": "turbo",
                "files": (
                    "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
                    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
                ),
            },
        },
    },
}
