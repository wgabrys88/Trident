from main import ROOT, TTS as SharedTTS, run_tts

MODELS = ROOT / "models"
T3 = MODELS / "chatterbox-t3-turbo-q4_0.gguf"
S3 = MODELS / "chatterbox-s3gen-turbo-q4_0.gguf"
SPEC = {
    "family": "turbo", "label": "tts", "models": (T3, S3), "language": "en",
    "multilingual": False, "port": 17935, "output": "tts",
    "knobs": {"n-gpu-layers": 99, "context": 8196, "threads": 4, "fastconv": 1, "seed": 42,
              "max-tokens": 1000, "top-k": 1000, "top-p": .95, "min-p": 0.0,
              "temperature": .8, "repeat-penalty": 1.2, "cfm-steps": 2,
              "cfg-weight": 0.0, "exaggeration": 0.0},
    "url": "https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/main",
    "checkpoints": ("t3_turbo_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
                    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json",
                    "tokenizer_config.json", "special_tokens_map.json"),
    "card": "turbo-model-card.md",
    "conversions": (("convert-t3-turbo-to-gguf.py", ("--model", "turbo"), "q4_0"),
                    ("convert-s3gen-to-gguf.py", ("--variant", "turbo"), "q4_0")),
}


class TTS(SharedTTS):
    def __init__(self, spec=SPEC) -> None:
        super().__init__(spec)


if __name__ == "__main__":
    run_tts(SPEC, TTS)
