import sys

from tts_common import Variant, run_variant, usage

CFG = Variant(
    name="nano",
    branch="nano",
    chatterbox_rev="4e43b9ae6c0d15ff13cd730f0c6adbe08cbb8433",
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
    t3_name="chatterbox-t3-nano-q8_0.gguf",
    s3_name="chatterbox-s3gen-nano-q4_0.gguf",
    stamp_name="voice.sha256",
    rev_name="rev",
    pid_name="server.pid",
    build_name="nano",
    venv_name=".venv-convert",
    ckpt_name=".ckpt",
    pipe_tag=b"",
    other_pids=("turbo.pid", "v3.pid"),
    t3_script="convert-t3-nano-to-gguf.py",
    s3_script="convert-s3gen-to-gguf.py",
)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(usage(CFG))
    run_variant(CFG, sys.argv[1])


if __name__ == "__main__":
    main()
