from main import ROOT, TTS as SharedTTS, run_tts

MODELS = ROOT / "models"
T3 = MODELS / "chatterbox-t3-mtl-q4_0.gguf"
S3 = MODELS / "chatterbox-s3gen-mtl-q4_0.gguf"
SPEC = {
    "family": "v3", "label": "v3", "models": (T3, S3), "language": "en",
    "multilingual": True, "port": 17936, "output": "v3",
    "knobs": {"n-gpu-layers": 99, "context": 2048, "threads": 6, "fastconv": 1, "seed": 42,
              "max-tokens": 1000, "top-k": 1000, "top-p": .95, "min-p": 0,
              "temperature": .8, "repeat-penalty": 1.2, "cfm-steps": 5,
              "cfg-weight": 0.5, "exaggeration": 0.5},
    "url": "https://huggingface.co/ResembleAI/chatterbox/resolve/ef85ce7bef2f3f1a74d0d837d379d2fcb68203cd",
    "checkpoints": ("t3_mtl23ls_v3.safetensors", "s3gen.pt", "conds.pt", "ve.pt",
                    "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json"),
    "card": "mtl-model-card.md",
    "conversions": (("convert-t3-mtl-to-gguf.py", (), "q4_0"),
                    ("convert-s3gen-to-gguf.py", ("--variant", "mtl"), "q4_0")),
}


class TTS(SharedTTS):
    def __init__(self) -> None:
        super().__init__(SPEC)


if __name__ == "__main__":
    run_tts(SPEC, TTS)
