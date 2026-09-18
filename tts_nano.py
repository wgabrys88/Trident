import sys
from tts_common import Variant, launch_variant
CFG = Variant(
    name="nano", t3_ckpt="t3_nano_v1.safetensors",
    hf="https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64",
    assets=("t3_nano_v1.safetensors","s3gen_meanflow.safetensors","conds.pt","ve.safetensors","vocab.json","merges.txt","added_tokens.json"),
    pid_name="server.pid", build_name="gpt2", venv_name=".venv-convert", ckpt_name=".ckpt", pipe_tag=b"",
    other_pids=("turbo.pid",), t3_script="convert-t3-gpt2-to-gguf.py", s3_script="convert-s3gen-to-gguf.py",
)
def main(): launch_variant(CFG, sys.argv)
if __name__ == "__main__": main()
