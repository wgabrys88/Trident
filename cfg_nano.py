# Edit this file and restart the controller. English only.

TTS_LANGUAGES = {"en": "English"}
DEFAULT_REPLY_LANGUAGE = "en"
TTS_LABEL = "CHATTERBOX TTS NANO"
# Official Nano generate(): cfg/exaggeration unused, cfm_steps=2, temp=0.8.
# GGUF n_ctx=8196; 512 leaves almost no room after the 375-token cond prompt.
# 2048 lets a 300-char pack finish. Official CLI packs at 180 (~5-8s);
# 300 is the longer community pack. Turbo T3 drifts after ~15s of speech.
TTS_RUNTIME = {"gpu_layers": 99, "context": 2048, "threads": 4}
TTS_SAMPLE = {
    "seed": 42, "max_tokens": 1000, "top_k": 1000, "top_p": 0.95,
    "min_p": 0.0, "temperature": 0.8, "repeat_penalty": 1.2, "cfm_steps": 2,
}
TTS_VOICE = {"cfg_weight": 0.0, "exaggeration": 0.0}
TTS_CHUNK = {"chars": 300}
_CKPT = (
    "t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)
TTS_MODELS = {
    "chatterbox-t3": {
        "label": "CHATTERBOX NANO T3", "repo": "ResembleAI/chatterbox-nano",
        "revision": "71ccd1d0081b430592cea481f4307e764e07bc64",
        "file": "chatterbox-t3-nano-q4_0.gguf", "size": 171901536,
        "convert": {
            "script": "convert-t3-turbo-to-gguf.py", "quant": "q4_0", "files": _CKPT,
            "copy": {"t3_nano_v1.safetensors": "t3_turbo_v1.safetensors"},
        },
    },
    "chatterbox-codec": {
        "label": "CHATTERBOX NANO S3GEN", "repo": "ResembleAI/chatterbox-nano",
        "revision": "71ccd1d0081b430592cea481f4307e764e07bc64",
        "file": "chatterbox-s3gen-nano-f16.gguf", "size": 1064879936,
        "convert": {"script": "convert-s3gen-to-gguf.py", "quant": "f16", "variant": "turbo", "files": _CKPT},
    },
}
