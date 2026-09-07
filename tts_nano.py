from main import ROOT, run_tts, tts_knobs

SPEC = {
    "family": "nano", "label": "tts", "language": "en", "multilingual": False, "port": 17933, "output": "tts",
    "models": (ROOT / "models/chatterbox-t3-nano-q4_0.gguf", ROOT / "models/chatterbox-s3gen-nano-q4_0.gguf"),
    "knobs": tts_knobs(2048, 4, 2),
    "url": "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64",
    "card": "nano-model-card.md",
    "conversions": (
        ("convert-t3-turbo-to-gguf.py", ("--model", "nano"), "q4_0",
         ("t3_nano_v1.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")),
        ("convert-s3gen-to-gguf.py", ("--variant", "turbo"), "q4_0",
         ("s3gen_meanflow.safetensors", "conds.pt"))),
}

if __name__ == "__main__":
    run_tts(SPEC)
