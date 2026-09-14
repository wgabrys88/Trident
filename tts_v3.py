import sys

from tts_common import V3_KNOBS, Variant, parse_variant_args, run_variant

CFG = Variant(
    name="v3",
    branch="experimental",
    chatterbox_rev="3ca5487da54ff10443d09d2dfb7f1fc203bdce86",
    hf="https://huggingface.co/ResembleAI/chatterbox/resolve/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
    assets=(
        "t3_mtl23ls_v3.safetensors",
        "s3gen.safetensors",
        "conds.pt",
        "ve.safetensors",
        "grapheme_mtl_merged_expanded_v1.json",
        "Cangjie5_TC.json",
    ),
    t3_name="chatterbox-t3-v3-f16-mixed.gguf",
    s3_name="chatterbox-s3gen-v3-q4_0.gguf",
    pid_name="v3.pid",
    build_name="v3",
    venv_name=".venv-convert-v3",
    ckpt_name=".ckpt-v3",
    pipe_tag=b"v3",
    other_pids=("server.pid", "turbo.pid"),
    t3_script="convert-t3-v3-to-gguf.py",
    t3_convert_flags=("--f16",),
    s3_script="convert-s3gen-v3-to-gguf.py",
    knobs=V3_KNOBS,
    needs_language=True,
    policy="V3 Llama analog of Nano delta: T3 F32 under --f16 (not Q8/F16), N_PREDICT=4096 from speech_pos/max_speech_tokens (n_ctx formula, not a GPT-2 8196 copy), Q4_0 vanilla S3Gen. Keep penalty-first sampler, ENC 6s, CFM 10, CFG 0.5.",
)


def main():
    text, language, knobs, play = parse_variant_args(CFG, sys.argv)
    run_variant(CFG, text, language, knobs, play=play)


if __name__ == "__main__":
    main()
