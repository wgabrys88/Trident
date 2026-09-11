import sys, json
from pathlib import Path
from gguf import GGUFReader

MODELS = Path(__file__).resolve().parent.parent / "models"
OUT = Path(__file__).resolve().parent / "gguf_metadata.txt"

def dump(name, path):
    lines = []
    lines.append("=" * 70)
    lines.append(f"MODEL {name}: {path.name}")
    lines.append("=" * 70)
    r = GGUFReader(str(path))
    keys = {}
    for f in r.fields.values():
        try:
            keys[f.name] = f.contents()
        except Exception as e:
            keys[f.name] = f"<err {e}>"
    for k in sorted(keys):
        v = keys[k]
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        lines.append(f"KV {k} = {v}")
    lines.append("-" * 70)
    lines.append("TENSORS")
    for t in r.tensors:
        lines.append(f"  {t.name}: type={t.tensor_type.name} shape={list(t.shape)} nbytes={t.n_bytes}")
    return "\n".join(lines)

out = []
for name, fn in [("NANO", "chatterbox-t3-nano-q8_0.gguf"),
                 ("TURBO", "chatterbox-t3-turbo-q8_0.gguf"),
                 ("V3", "chatterbox-t3-v3-q8_0.gguf")]:
    out.append(dump(name, MODELS / fn))
for name, fn in [("NANO-S3", "chatterbox-s3gen-nano-q4_0.gguf"),
                 ("TURBO-S3", "chatterbox-s3gen-turbo-q4_0.gguf"),
                 ("V3-S3", "chatterbox-s3gen-v3-q4_0.gguf")]:
    out.append(dump(name, MODELS / fn))
OUT.write_text("\n".join(out), encoding="ascii", errors="replace")
print("wrote", OUT, len("\n".join(out)), "chars")
