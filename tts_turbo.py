import sys

from tts_common import GPT2_KNOBS, Variant, launch_variant

CFG = Variant(
    name="turbo",
    t3_ckpt="t3_turbo_v1.safetensors",
    split_tokens=0,
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
    t3_name="chatterbox-t3-turbo-f16w.gguf",
    s3_name="chatterbox-s3gen-meanflow-q4_0.gguf",
    pid_name="turbo.pid",
    build_name="gpt2",
    venv_name=".venv-convert",
    ckpt_name=".ckpt",
    pipe_tag=b"turbo",
    other_pids=("server.pid", "v3.pid"),
    t3_script="convert-t3-gpt2-to-gguf.py",
    s3_script="convert-s3gen-to-gguf.py",
    knobs=GPT2_KNOBS,
)


def main():
    launch_variant(CFG, sys.argv)


if __name__ == "__main__":
    main()
