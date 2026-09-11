import sys
from pathlib import Path
import numpy as np
import soundfile as sf
import librosa

SR = 24000
ROOT = Path(__file__).resolve().parent.parent
FILES = [("nano", "20260911-193837-nano.wav"),
         ("turbo", "20260911-200024-turbo.wav"),
         ("v3", "20260911-200048-v3.wav")]

for name, fn in FILES:
    y, sr = sf.read(str(ROOT / fn))
    y = y.astype(np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=240, win_length=1024, window="hann"))
    rms = librosa.feature.rms(S=S, frame_length=1024, hop_length=240)[0]
    centroid = librosa.feature.spectral_centroid(S=S, sr=SR, n_fft=1024, hop_length=240)[0]
    flatness = librosa.feature.spectral_flatness(S=S, n_fft=1024, hop_length=240)[0]
    zcr = librosa.feature.zero_crossing_rate(y, frame_length=1024, hop_length=240)[0]
    nfr = len(rms)
    print(f"\n===== {name.upper()} ===== (per 1s bin: rms_db, voiced-ish=flatness, centroid_Hz, zcr)")
    for b in range(0, int(np.ceil(nfr / 100))):
        sl = slice(b * 100, min((b + 1) * 100, nfr))
        r = 20 * np.log10(np.maximum(rms[sl], 1e-8))
        print(f"  t={b:2d}-{b+1:2d}s  rms={np.mean(r):5.1f}dB  flat={np.mean(flatness[sl]):.4f}  cent={np.mean(centroid[sl]):6.0f}Hz  zcr={np.mean(zcr[sl]):.4f}")
