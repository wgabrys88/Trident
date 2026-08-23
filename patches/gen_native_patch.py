"""Generate patches/native_patch.py from the applied diff in third_party/chatterbox.cpp.

The worktree there is upstream ddca05f + every trident patch, so its `git diff`
is the exact, complete transformation.  We convert each hunk into a literal
(old_block -> new_block) replacement verified unique against upstream content.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent / "third_party" / "chatterbox.cpp"
OUT = Path(__file__).resolve().parent / "native_patch.py"

diff = subprocess.run(["git", "diff", "-U6"], cwd=REPO, capture_output=True, check=True).stdout.decode("utf-8")
lines = diff.split("\n")

files = {}  # path -> list[(old,new)]
cur_path = None
hunks = []
i = 0
while i < len(lines):
    ln = lines[i]
    if ln.startswith("+++ b/"):
        cur_path = ln[6:]
        files.setdefault(cur_path, [])
    elif ln.startswith("@@") and cur_path:
        i += 1
        old_blk, new_blk = [], []
        while i < len(lines) and not lines[i].startswith(("@@", "diff --git")):
            b = lines[i]
            if b.startswith("-"):
                old_blk.append(b[1:])
            elif b.startswith("+"):
                new_blk.append(b[1:])
            else:
                if b.startswith(" ") or b == "":
                    t = b[1:] if b.startswith(" ") else ""
                    old_blk.append(t)
                    new_blk.append(t)
            i += 1
        continue
    i += 1

# Re-walk properly: hunks belong to the last seen +++ path.
cur_path = None
old_blk = new_blk = None
def flush():
    global old_blk, new_blk
    if cur_path and old_blk is not None:
        files[cur_path].append(("\n".join(old_blk), "\n".join(new_blk)))
    old_blk = new_blk = None

for ln in lines:
    if ln.startswith("+++ b/"):
        flush()
        cur_path = ln[6:]
        files.setdefault(cur_path, [])
    elif ln.startswith("@@") and cur_path:
        flush()
        old_blk, new_blk = [], []
    elif old_blk is not None:
        if ln.startswith("-"):
            old_blk.append(ln[1:])
        elif ln.startswith("+"):
            new_blk.append(ln[1:])
        elif ln.startswith(" ") or ln == "":
            t = ln[1:] if ln.startswith(" ") else ""
            old_blk.append(t)
            new_blk.append(t)
        else:
            flush()
flush()

# Verify each old block occurs exactly once in the pristine upstream blob.
def upstream_text(path: str) -> str:
    r = subprocess.run(["git", "show", f"ddca05fb69c2910b0d7b5eae420d360ed98c067b:{path}"],
                       cwd=REPO, capture_output=True, check=True)
    return r.stdout.decode("utf-8")

cache = {}
ops = {}
for path, pairs in files.items():
    text = cache.setdefault(path, upstream_text(path))
    checked = []
    for old, new in pairs:
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"anchor not unique ({n}) in {path}:\n---\n{old[:300]}\n---")
        checked.append((old, new))
    ops[path] = checked

def emit(s: str) -> str:
    return repr(s)

out_lines = [
    '"""Deterministic native source transforms for chatterbox.cpp @ ddca05f.',
    "",
    "Generated from the applied patch set; extended with the Trident fp2 fixes",
    "(VoiceEncoder reference cap, ct_stats distinct tokens, dxdt_step0 CFM",
    "fingerprint, parity-probe extension for the S3TokenizerV2 op set).",
    '"""',
    "",
    "OPS = {",
]
for path, pairs in ops.items():
    out_lines.append(f"    {path!r}: [")
    for old, new in pairs:
        out_lines.append(f"        ({emit(old)},")
        out_lines.append(f"         {emit(new)}),")
    out_lines.append("    ],")
out_lines.append("}")
out_lines.append("")
OUT.write_text("\n".join(out_lines), encoding="utf-8")
total = sum(len(v) for v in ops.values())
print(f"generated {OUT.name}: {len(ops)} files, {total} transforms")
