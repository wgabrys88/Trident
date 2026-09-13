import sys

from tts_common import V3_KNOBS, Variant, parse_variant_args, run_variant

CFG = Variant(
    name="v3",
    branch="experimental",
    chatterbox_rev="aaafdb6e1d83ecb8fd963ac2e76bc5e5718074e5",
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
    pid_name="v3.pid",
    build_name="v3",
    venv_name=".venv-convert-v3",
    ckpt_name=".ckpt-v3",
    pipe_tag=b"v3",
    other_pids=("server.pid", "turbo.pid"),
    t3_script="convert-t3-v3-to-gguf.py",
    t3_convert_flags=(),
    s3_script="convert-s3gen-v3-to-gguf.py",
    knobs=V3_KNOBS,
    needs_language=True,
    policy="V3: Q8_0 T3 selected because the strongest recorded V3 state passed the 1-30 ladder; Q4_0 V3 S3Gen. V3 remains a separate architecture/pipeline and keeps its own header defaults.",
)


def main():
    text, language, knobs, play = parse_variant_args(CFG, sys.argv)
    run_variant(CFG, text, language, knobs, play=play)


if __name__ == "__main__":
    main()
