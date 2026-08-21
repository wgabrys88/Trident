FAMILY = {
    "name": "v3",
    "TTS_LANGUAGES": {
        "en": "English", "es": "Spanish", "fr": "French", "de": "German",
        "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
        "tr": "Turkish", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
        "no": "Norwegian", "el": "Greek", "ms": "Malay", "sw": "Swahili",
        "ar": "Arabic", "ko": "Korean",
    },
    "DEFAULT_REPLY_LANGUAGE": "en",
    "TTS_RUNTIME": {"gpu_layers": 99, "context": 2048, "threads": 4},
    "TTS_SAMPLE": {
        "seed": 42, "max_tokens": 768, "top_k": 0, "top_p": 1.0,
        "temperature": 0.8, "repeat_penalty": 1.2,
        "min_p": 0.05, "cfm_steps": 7,
    },
    "TTS_VOICE": {"cfg_weight": 0.5, "exaggeration": 0.3},
    "TTS_CHUNK": {"first_chars": 180, "chars": 300},
    "TTS_MODELS": {
        "chatterbox-t3": {
            "label": "CHATTERBOX V3 T3", "repo": "ResembleAI/chatterbox",
            "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
            "file": "chatterbox-t3-mtl-v3-q4_0.gguf", "size": 344985408,
            "convert": {
                "script": "convert-t3-mtl-to-gguf.py", "quant": "q4_0",
                "files": (
                    "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen_v3.pt",
                    "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
                ),
                "copy": {"t3_mtl23ls_v3.safetensors": "t3_mtl23ls_v2.safetensors"},
            },
        },
        "chatterbox-codec": {
            "label": "CHATTERBOX V3 S3GEN", "repo": "ResembleAI/chatterbox",
            "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
            "file": "chatterbox-s3gen-mtl-v3-f16.gguf", "size": 1056431360,
            "convert": {
                "script": "convert-s3gen-to-gguf.py", "quant": "f16", "variant": "mtl",
                "files": (
                    "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen_v3.pt",
                    "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
                ),
                "copy": {"s3gen_v3.pt": "s3gen.pt"},
            },
        },
    },
}
