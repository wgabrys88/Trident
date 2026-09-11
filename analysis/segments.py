import sys
from pathlib import Path
import numpy as np
import soundfile as sf
import librosa
import pyworld as pw

SR = 24000
ROOT = Path(__file__).resolve().parent.parent
FILES = [("nano", "20260911-193837-nano.wav"),
         ("turbo", "20260911-200024-turbo.wav"),
         ("v3", "20260911-200048-v3.wav")]

def voiced_segments(f0, ap, t):
    """Return list of (start_s, end_s, mean_f0) for voiced runs."""
    voiced = f0 > 1.0
    segs = []
    i = 0
    n = len(voiced)
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            if (j - i) >= 2:  # >= 20ms
                segs.append((t[i], t[j-1], float(np.mean(f0[i:j]))))
            i = j
        else:
            i += 1
    return segs

for name, fn in FILES:
    y, sr = sf.read(str(ROOT / fn))
    y = y.astype(np.float64)
    if y.ndim > 1:
        y = y.mean(axis=1)
    f0, sp, ap = pw.wav2world(y, SR, frame_period=10)
    ap = ap.mean(axis=1)
    t = np.arange(len(f0)) * 0.01
    segs = voiced_segments(f0, ap, t)
    # merge segments separated by < 0.12s (same word)
    merged = []
    for s, e, mf in segs:
        if merged and s - merged[-1][1] < 0.12:
            ms, me, _ = merged[-1]
            merged[-1] = (ms, e, (merged[-1][2] + mf) / 2)
        else:
            merged.append((s, e, mf))
    print(f"\n===== {name.upper()} ===== dur={len(y)/SR:.2f}s  voiced_segments(merged<0.12s)={len(merged)}")
    # print each word with time + duration + pitch
    for i, (s, e, mf) in enumerate(merged):
        d = e - s
        print(f"  w{i:02d}  {s:6.2f}s  dur={d:4.2f}s  f0={mf:5.1f}Hz")
