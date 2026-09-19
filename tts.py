import sys
from tts_common import Variant, launch

VARIANTS = {
    "nano": Variant(
        name="nano", t3_ckpt="t3_nano_v1.safetensors",
        hf="https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64",
        assets=("t3_nano_v1.safetensors","s3gen_meanflow.safetensors","conds.pt","ve.safetensors","vocab.json","merges.txt","added_tokens.json"),
        pid_name="server.pid", build_name="gpt2", ckpt_name=".ckpt", pipe_tag=b"",
        other_pids=("turbo.pid",), t3_script="convert-t3-gpt2-to-gguf.py", s3_script="convert-s3gen-to-gguf.py",
    ),
    "turbo": Variant(
        name="turbo", t3_ckpt="t3_turbo_v1.safetensors",
        hf="https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/749d1c1a46eb10492095d68fbcf55691ccf137cd",
        assets=("t3_turbo_v1.safetensors","s3gen_meanflow.safetensors","conds.pt","ve.safetensors","vocab.json","merges.txt","added_tokens.json"),
        pid_name="turbo.pid", build_name="gpt2", ckpt_name=".ckpt", pipe_tag=b"turbo",
        other_pids=("server.pid",), t3_script="convert-t3-gpt2-to-gguf.py", s3_script="convert-s3gen-to-gguf.py",
    ),
    "v3": Variant(
        name="v3", t3_ckpt="t3_mtl23ls_v3.safetensors",
        hf="https://huggingface.co/ResembleAI/chatterbox/resolve/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
        assets=("t3_mtl23ls_v3.safetensors","s3gen.safetensors","conds.pt","ve.safetensors","grapheme_mtl_merged_expanded_v1.json","Cangjie5_TC.json"),
        pid_name="v3.pid", build_name="v3", ckpt_name=".ckpt-v3", pipe_tag=b"v3", other_pids=(),
        t3_script="convert-t3-v3-to-gguf.py", s3_script="convert-s3gen-v3-to-gguf.py", needs_language=True,
        external_assets=(
            ("official_mtl_tokenizer.py", "https://raw.githubusercontent.com/resemble-ai/chatterbox/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/models/tokenizers/tokenizer.py"),
            ("official_mtl_tts.py", "https://raw.githubusercontent.com/resemble-ai/chatterbox/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/mtl_tts.py"),
            ("dicta-1.0.int8.onnx", "https://github.com/thewh1teagle/dicta-onnx/releases/download/model-files-v1.0/dicta-1.0.int8.onnx"),
        ),
    ),
}

def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2 or argv[1] in ("-h", "--help", "-?"):
        print("usage: python tts.py nano|turbo|v3 [knobs] [--t3-weight-type f32|f16|q4_0] [--s3-weight-type f32|f16|q4_0] [--reference wav] TEXT [language]")
        raise SystemExit(0)
    name = argv[1]
    if name not in VARIANTS:
        raise SystemExit("variant must be nano, turbo, or v3")
    launch(VARIANTS[name], [argv[0], *argv[2:]])

if __name__ == "__main__":
    main()
