from main import ROOT, TTS as SharedTTS, run_tts

MODELS = ROOT / "models"
T3 = MODELS / "chatterbox-t3-nano-q4_0.gguf"
S3 = MODELS / "chatterbox-s3gen-nano-q4_0.gguf"
SPEC = {
    "family": "nano", "label": "tts", "models": (T3, S3), "language": "en",
    "multilingual": False, "port": 17933, "output": "tts",
    "knobs": {"n-gpu-layers": 99, "context": 2048, "threads": 4, "fastconv": 1, "seed": 42,
              "max-tokens": 1000, "top-k": 1000, "top-p": .95, "min-p": 0,
              "temperature": .8, "repeat-penalty": 1.2, "cfm-steps": 1,
              "cfg-weight": 0, "exaggeration": 0},
    "url": "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64",
    "checkpoints": ("t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
                    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json"),
    "card": "nano-model-card.md",
    "conversions": (("convert-t3-turbo-to-gguf.py", ("--model", "nano"), "q4_0"),
                    ("convert-s3gen-to-gguf.py", ("--variant", "turbo"), "q4_0")),
}


class TTS(SharedTTS):
    def __init__(self) -> None:
        super().__init__(SPEC)


if __name__ == "__main__":
    run_tts(SPEC, TTS)
