from main import ROOT, run_tts, tts_knobs

SPEC = {
    "family": "turbo", "label": "tts", "language": "en", "multilingual": False, "port": 17935, "output": "tts",
    "models": (ROOT / "models/chatterbox-t3-turbo-q4_0.gguf", ROOT / "models/chatterbox-s3gen-turbo-q4_0.gguf"),
    "knobs": tts_knobs(8196, 4, 2),
    "url": "https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/main",
    "card": "turbo-model-card.md",
    "conversions": (
        ("convert-t3-turbo-to-gguf.py", ("--model", "turbo"), "q4_0",
         ("t3_turbo_v1.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")),
        ("convert-s3gen-to-gguf.py", ("--variant", "turbo"), "q4_0",
         ("s3gen_meanflow.safetensors", "conds.pt"))),
}

if __name__ == "__main__":
    run_tts(SPEC)
