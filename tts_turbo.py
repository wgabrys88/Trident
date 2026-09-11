import sys

from tts_common import Variant, run_variant, usage

CFG = Variant(
    name="turbo",
    branch="turbo",
    chatterbox_rev="7327ac41384b88926196325f48de1d6697c4b1d2",
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
    t3_name="chatterbox-t3-turbo-q8_0.gguf",
    s3_name="chatterbox-s3gen-turbo-q4_0.gguf",
    stamp_name="turbo.voice.sha256",
    rev_name="turbo.rev",
    pid_name="turbo.pid",
    build_name="turbo",
    venv_name=".venv-convert-turbo",
    ckpt_name=".ckpt-turbo",
    pipe_tag=b"turbo",
    other_pids=("server.pid", "v3.pid"),
    t3_script="convert-t3-turbo-to-gguf.py",
    s3_script="convert-s3gen-to-gguf.py",
)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(usage(CFG))
    run_variant(CFG, sys.argv[1])


if __name__ == "__main__":
    main()
