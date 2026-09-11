import sys
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

def parse(path):
    d = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        k = parts[0]
        d[k] = [int(x) for x in parts[1:]] if parts[1:] else []
    return d

def longest_repeat(seq, minlen=6):
    """Return (start_idx, length) of the longest substring that repeats at least twice."""
    n = len(seq)
    best = (0, 0)
    # suffix array is overkill; do a simple double-loop on a shortened set for speed
    for L in range(minlen, min(n, 60) + 1):
        seen = {}
        for i in range(0, n - L + 1):
            tup = tuple(seq[i:i+L])
            if tup in seen:
                # count repeats
                return seen[tup], L, i
            seen[tup] = i
    return None, 0, None

SIL = 4299

for name in ("nano", "turbo", "v3"):
    d = parse(MODELS / f"{name}_t3_dump.txt")
    dropped = d.get("dropped", [])
    sil_pos = [i for i, t in enumerate(dropped) if t == SIL]
    print(f"\n===== {name.upper()} ===== dropped_count={len(dropped)}")
    print(f"  silence(4299) positions ({len(sil_pos)}): {sil_pos}")
    # segments between silences
    segs = []
    prev = 0
    for p in sil_pos:
        segs.append((prev, p))
        prev = p + 1
    segs.append((prev, len(dropped)))
    seg_lens = [e - s for s, e in segs if e > s]
    print(f"  segments={len(segs)}  seg_lens={seg_lens}")
    # distinct tokens
    c = Counter(dropped)
    print(f"  distinct_tokens={len(c)}  top5={c.most_common(5)}")
    # repeated n-gram detection (loop)
    s, L, s2 = longest_repeat(dropped, minlen=8)
    if s is not None:
        print(f"  LOOP: repeated {L}-gram at {s} and {s2} (={s*0.04:.2f}s)")
    else:
        print(f"  no long repeat found (len>=8)")
