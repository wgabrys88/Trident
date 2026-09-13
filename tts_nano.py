import sys

from tts_common import GPT2_KNOBS, Variant, parse_variant_args, run_variant

CFG = Variant(
    name="nano",
    branch="experimental",
    chatterbox_rev="aaafdb6e1d83ecb8fd963ac2e76bc5e5718074e5",
    hf="https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64",
    assets=(
        "t3_nano_v1.safetensors",
        "s3gen_meanflow.safetensors",
        "conds.pt",
        "ve.safetensors",
        "vocab.json",
        "merges.txt",
        "added_tokens.json",
    ),
    t3_name="chatterbox-t3-nano-f16-mixed.gguf",
    s3_name="chatterbox-s3gen-nano-q4_0.gguf",
    pid_name="server.pid",
    build_name="nano",
    venv_name=".venv-convert",
    ckpt_name=".ckpt",
    pipe_tag=b"",
    other_pids=("turbo.pid", "v3.pid"),
    t3_script="convert-t3-nano-to-gguf.py",
    t3_convert_flags=("--f16",),
    s3_script="convert-s3gen-to-gguf.py",
    knobs=GPT2_KNOBS,
    policy="Nano: F16 T3 with text_emb/speech_emb/speech_head retained F32 by engine aaafdb6; Q4_0 S3Gen.",
)


def main():
    text, language, knobs = parse_variant_args(CFG, sys.argv)
    run_variant(CFG, text, language, knobs)


if __name__ == "__main__":
    main()
