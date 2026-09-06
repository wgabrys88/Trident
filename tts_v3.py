from _runtime import Chatterbox, TTS_MODELS, tts_cli

CHATTERBOX_REV = "c4e051e82f086b80c5379e4219e2693c15db90f8"
GGML_REV = "58c3805840b516b2a88ff867ccf7bb41dba79951"
NATIVE_PIN = f"{CHATTERBOX_REV} {GGML_REV}"
MTL_REV = "ef85ce7bef2f3f1a74d0d837d379d2fcb68203cd"
MTL_URL = f"https://huggingface.co/ResembleAI/chatterbox/resolve/{MTL_REV}"
T3 = TTS_MODELS / "chatterbox-t3-mtl-q4_0.gguf"
S3 = TTS_MODELS / "chatterbox-s3gen-mtl-q5_0.gguf"
LANGUAGES = tuple("ar da de el en es fi fr he hi it ja ko ms nl no pl pt ru sv sw tr zh".split())


class TTS(Chatterbox):
    family, language, hf_folder, output_tag = "v3", "en", "v3", "v3"
    port, t3, s3 = 17936, T3, S3
    model_url = MTL_URL
    model_card = TTS_MODELS / "mtl-model-card.md"
    chatterbox_rev, ggml_rev, native_pin = CHATTERBOX_REV, GGML_REV, NATIVE_PIN
    extra_scripts = ("quant_policy.py",)
    conversions = (
        ("convert-t3-mtl-to-gguf.py", (), T3, "q4_0",
         ("t3_mtl23ls_v3.safetensors", "conds.pt", "ve.pt",
          "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json")),
        ("convert-s3gen-to-gguf.py", ("--variant", "mtl"), S3, "q5_0", ("s3gen.pt", "conds.pt")))
    knobs = {"n-gpu-layers": 99, "context": 512, "threads": 4, "fastconv": 1, "seed": 42,
             "max-tokens": 1000, "top-k": 1000, "top-p": .95, "min-p": 0,
             "temperature": .8, "repeat-penalty": 1.2, "cfm-steps": 10,
             "cfg-weight": .7, "exaggeration": .5}


if __name__ == "__main__":
    tts_cli(TTS(), LANGUAGES)
