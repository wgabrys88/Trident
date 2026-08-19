# Edit this file and restart the controller. English only.

TTS_LANGUAGES = {"en": "English"}
DEFAULT_REPLY_LANGUAGE = "en"
TTS_LABEL = "CHATTERBOX TTS TURBO"
TTS_RUNTIME = {"gpu_layers": 99, "context": 512, "threads": 4}
TTS_SAMPLE = {
    "seed": 42, "max_tokens": 1000, "top_k": 1000, "top_p": 0.95,
    "min_p": 0.0, "temperature": 0.8, "repeat_penalty": 1.2, "cfm_steps": 2,
}
TTS_VOICE = {"cfg_weight": 0.0, "exaggeration": 0.5}
TTS_CHUNK = {"chars": 120}
_CKPT = (
    "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)
TTS_MODELS = {
    "chatterbox-t3": {
        "label": "CHATTERBOX TURBO T3", "repo": "ResembleAI/chatterbox-turbo",
        "revision": "749d1c1a46eb10492095d68fbcf55691ccf137cd",
        "file": "chatterbox-t3-turbo-q4_0.gguf", "size": 333506240,
        "convert": {"script": "convert-t3-turbo-to-gguf.py", "quant": "q4_0", "files": _CKPT},
    },
    "chatterbox-codec": {
        "label": "CHATTERBOX TURBO S3GEN", "repo": "ResembleAI/chatterbox-turbo",
        "revision": "749d1c1a46eb10492095d68fbcf55691ccf137cd",
        "file": "chatterbox-s3gen-turbo-f16.gguf", "size": 1064879936,
        "convert": {"script": "convert-s3gen-to-gguf.py", "quant": "f16", "variant": "turbo", "files": _CKPT},
    },
}
