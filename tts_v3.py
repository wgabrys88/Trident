from main import ROOT, run_tts, tts_knobs

SPEC = {
    "family": "v3", "label": "v3", "language": "en", "multilingual": True, "port": 17936, "output": "v3",
    "models": (ROOT / "models/chatterbox-t3-mtl-q4_0.gguf", ROOT / "models/chatterbox-s3gen-mtl-q4_0.gguf"),
    "knobs": tts_knobs(2048, 6, 5, 1.5, .3, .5),
    "url": "https://huggingface.co/ResembleAI/chatterbox/resolve/ef85ce7bef2f3f1a74d0d837d379d2fcb68203cd",
    "card": "mtl-model-card.md",
    "conversions": (
        ("convert-t3-mtl-to-gguf.py", (), "q4_0",
         ("t3_mtl23ls_v3.safetensors", "conds.pt", "ve.pt", "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json")),
        ("convert-s3gen-to-gguf.py", ("--variant", "mtl"), "q4_0", ("s3gen.pt", "conds.pt"))),
}

if __name__ == "__main__":
    run_tts(SPEC)
