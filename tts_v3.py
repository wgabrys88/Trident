import sys

from tts_common import Variant, run_variant, usage

CFG = Variant(
    name="v3",
    branch="v3",
    chatterbox_rev="ead5032095c0f4c2b95f8453057ba9cbce296e27",
    hf="https://huggingface.co/ResembleAI/chatterbox/resolve/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
    assets=(
        "t3_mtl23ls_v3.safetensors",
        "s3gen.safetensors",
        "conds.pt",
        "ve.safetensors",
        "grapheme_mtl_merged_expanded_v1.json",
        "Cangjie5_TC.json",
    ),
    t3_name="chatterbox-t3-v3-q8_0.gguf",
    s3_name="chatterbox-s3gen-v3-q4_0.gguf",
    stamp_name="v3.voice.sha256",
    rev_name="v3.rev",
    pid_name="v3.pid",
    build_name="v3",
    venv_name=".venv-convert-v3",
    ckpt_name=".ckpt-v3",
    pipe_tag=b"v3",
    other_pids=("server.pid", "turbo.pid"),
    t3_script="convert-t3-v3-to-gguf.py",
    s3_script="convert-s3gen-v3-to-gguf.py",
    needs_language=True,
)


def main():
    if len(sys.argv) < 3:
        raise SystemExit(usage(CFG))
    run_variant(CFG, sys.argv[1], sys.argv[2].lower())


if __name__ == "__main__":
    main()
