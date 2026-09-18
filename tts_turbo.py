import sys
from tts_common import Variant, launch_variant
CFG = Variant(
    name="turbo", t3_ckpt="t3_turbo_v1.safetensors",
    hf="https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/749d1c1a46eb10492095d68fbcf55691ccf137cd",
    assets=("t3_turbo_v1.safetensors","s3gen_meanflow.safetensors","conds.pt","ve.safetensors","vocab.json","merges.txt","added_tokens.json"),
    pid_name="turbo.pid", build_name="gpt2", venv_name=".venv-convert", ckpt_name=".ckpt", pipe_tag=b"turbo",
    other_pids=("server.pid",), t3_script="convert-t3-gpt2-to-gguf.py", s3_script="convert-s3gen-to-gguf.py",
)
def main(): launch_variant(CFG, sys.argv)
if __name__ == "__main__": main()
