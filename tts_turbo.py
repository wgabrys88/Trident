from _runtime import Chatterbox, TTS_MODELS, tts_cli

CHATTERBOX_REV = "c4e051e82f086b80c5379e4219e2693c15db90f8"
GGML_REV = "58c3805840b516b2a88ff867ccf7bb41dba79951"
NATIVE_PIN = f"{CHATTERBOX_REV} {GGML_REV}"
TURBO_URL = "https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/main"
T3 = TTS_MODELS / "chatterbox-t3-turbo-q4_0.gguf"
S3 = TTS_MODELS / "chatterbox-s3gen-turbo-q4_0-rawf32-v1.gguf"
T3_FILES = ("t3_turbo_v1.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt",
            "added_tokens.json")


class TTS(Chatterbox):
    family, language, hf_folder, output_tag = "turbo", "en", "turbo", "tts"
    port, t3, s3 = 17935, T3, S3
    model_url = TURBO_URL
    model_card = TTS_MODELS / "turbo-model-card.md"
    chatterbox_rev, ggml_rev, native_pin = CHATTERBOX_REV, GGML_REV, NATIVE_PIN
    conversions = (
        ("convert-t3-turbo-to-gguf.py", ("--model", "turbo"), T3, "q4_0", T3_FILES),
        ("convert-s3gen-to-gguf.py", ("--variant", "turbo"), S3, "q4_0",
         ("s3gen_meanflow.safetensors", "conds.pt")))
    knobs = {"n-gpu-layers": 99, "context": 8196, "threads": 4, "fastconv": 1, "seed": 42,
             "max-tokens": 1000, "top-k": 1000, "top-p": .95, "min-p": 0,
             "temperature": .8, "repeat-penalty": 1.2, "cfm-steps": 2,
             "cfg-weight": 0, "exaggeration": 0}


if __name__ == "__main__":
    tts_cli(TTS())
