CONTROLLER = {"host": "127.0.0.1", "port": 8765}
PORTS = {"tts": 8095, "asr": 8097, "brain": 8098}

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

_EN = {"en": "English"}
_SAMPLE = {
    "seed": 42, "max_tokens": 1000, "top_k": 1000, "top_p": 0.95,
    "temperature": 0.8, "repeat_penalty": 1.2,
}
_V3_CKPT = (
    "ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen_v3.pt",
    "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json",
)
_TURBO_CKPT = (
    "t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
)
_NANO_CKPT = (
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


def _family(label, languages, context, chars, cfm, min_p, cfg, exaggeration, models):
    return {
        "TTS_LANGUAGES": languages,
        "DEFAULT_REPLY_LANGUAGE": "en",
        "TTS_LABEL": label,
        "TTS_RUNTIME": {"gpu_layers": 99, "context": context, "threads": 4},
        "TTS_SAMPLE": {**_SAMPLE, "min_p": min_p, "cfm_steps": cfm},
        "TTS_VOICE": {"cfg_weight": cfg, "exaggeration": exaggeration},
        "TTS_CHUNK": {"chars": chars},
        "TTS_MODELS": models,
    }


FAMILIES = {
    "v3": _family(
        "CHATTERBOX TTS V3", {"en": "English", "pl": "Polish", "de": "German"},
        2048, 120, 10, 0.05, 0.5, 0.5,
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX V3 T3", "ResembleAI/chatterbox",
                "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                "chatterbox-t3-mtl-v3-q4_0.gguf", 344985408,
                "convert-t3-mtl-to-gguf.py", _V3_CKPT,
                copy={"t3_mtl23ls_v3.safetensors": "t3_mtl23ls_v2.safetensors"},
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX V3 S3GEN", "ResembleAI/chatterbox",
                "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
                "chatterbox-s3gen-mtl-v3-f16.gguf", 1056431360,
                "convert-s3gen-to-gguf.py", _V3_CKPT, quant="f16", variant="mtl",
                copy={"s3gen_v3.pt": "s3gen.pt"},
            ),
        },
    ),
    "turbo": _family(
        "CHATTERBOX TTS TURBO", dict(_EN),
        2048, 120, 2, 0.0, 0.0, 0.0,
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX TURBO T3", "ResembleAI/chatterbox-turbo",
                "749d1c1a46eb10492095d68fbcf55691ccf137cd",
                "chatterbox-t3-turbo-q4_0.gguf", 333506240,
                "convert-t3-turbo-to-gguf.py", _TURBO_CKPT,
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX TURBO S3GEN", "ResembleAI/chatterbox-turbo",
                "749d1c1a46eb10492095d68fbcf55691ccf137cd",
                "chatterbox-s3gen-turbo-f16.gguf", 1064879936,
                "convert-s3gen-to-gguf.py", _TURBO_CKPT, quant="f16", variant="turbo",
            ),
        },
    ),
    "nano": _family(
        "CHATTERBOX TTS NANO", dict(_EN),
        2048, 180, 2, 0.0, 0.0, 0.0,
        {
            "chatterbox-t3": _gguf(
                "CHATTERBOX NANO T3", "ResembleAI/chatterbox-nano",
                "71ccd1d0081b430592cea481f4307e764e07bc64",
                "chatterbox-t3-nano-q4_0.gguf", 171901536,
                "convert-t3-turbo-to-gguf.py", _NANO_CKPT,
                copy={"t3_nano_v1.safetensors": "t3_turbo_v1.safetensors"},
            ),
            "chatterbox-codec": _gguf(
                "CHATTERBOX NANO S3GEN", "ResembleAI/chatterbox-nano",
                "71ccd1d0081b430592cea481f4307e764e07bc64",
                "chatterbox-s3gen-nano-f16.gguf", 1064879936,
                "convert-s3gen-to-gguf.py", _NANO_CKPT, quant="f16", variant="turbo",
            ),
        },
    ),
}
