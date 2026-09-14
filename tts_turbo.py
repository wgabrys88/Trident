import sys

from tts_common import GPT2_KNOBS, Variant, parse_variant_args, run_variant

CFG = Variant(
    name="turbo",
    branch="experimental",
    chatterbox_rev="dc0e83d2666d6f0ce5560466b9da737467cf47fa",
    hf="https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/749d1c1a46eb10492095d68fbcf55691ccf137cd",
    assets=(
        "t3_turbo_v1.safetensors",
        "s3gen_meanflow.safetensors",
        "conds.pt",
        "ve.safetensors",
        "vocab.json",
        "merges.txt",
        "added_tokens.json",
    ),
    t3_name="chatterbox-t3-turbo-f16-mixed.gguf",
    s3_name="chatterbox-s3gen-turbo-q4_0.gguf",
    pid_name="turbo.pid",
    build_name="turbo",
    venv_name=".venv-convert-turbo",
    ckpt_name=".ckpt-turbo",
    pipe_tag=b"turbo",
    other_pids=("server.pid", "v3.pid"),
    t3_script="convert-t3-turbo-to-gguf.py",
    t3_convert_flags=("--f16",),
    s3_script="convert-s3gen-to-gguf.py",
    knobs=GPT2_KNOBS,
    policy="Turbo: T3 F32 under --f16 (attn/mlp no longer F16) by engine 4fa717d; Q4_0 S3Gen.",
)


def main():
    text, language, knobs, play = parse_variant_args(CFG, sys.argv)
    run_variant(CFG, text, language, knobs, play=play)


if __name__ == "__main__":
    main()
