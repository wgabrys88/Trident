"""Deterministic librosa/scipy report for Rainbow WAV files. Run, do not import as a test."""
from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parent / "runs"
GOLD = "nano-en-iso-p.wav"


def load(path: Path):
    y, sr = sf.read(str(path), always_2d=False)
    if getattr(y, "ndim", 1) > 1:
        y = y.mean(axis=1)
    y = np.asarray(y, dtype=np.float32)
    return y, int(sr)


def longest_run(mask: np.ndarray, sr: int) -> float:
    if mask.size == 0:
        return 0.0
    padded = np.concatenate([[False], mask, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.where(edges == 1)[0]
    ends = np.where(edges == -1)[0]
    if starts.size == 0:
        return 0.0
    return float(np.max(ends - starts) / sr)


def analyze(path: Path) -> dict:
    y, sr = load(path)
    n = y.size
    duration = n / sr
    peak = float(np.max(np.abs(y)))
    rms = float(np.sqrt(np.mean(y * y)))
    clip = float(np.mean(np.abs(y) >= 0.99))
    intervals = librosa.effects.split(y, top_db=30, frame_length=2048, hop_length=512)
    speech = float(sum((e - s) for s, e in intervals) / sr) if len(intervals) else 0.0
    gaps = []
    prev = 0
    for s, e in intervals:
        if s > prev:
            gaps.append((s - prev) / sr)
        prev = e
    if prev < n:
        gaps.append((n - prev) / sr)
    hop = 512
    cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop)[0]
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop, units="time")
    f, t, Sxx = signal.spectrogram(y, fs=sr, nperseg=1024, noverlap=768, mode="magnitude")
    band = (f >= 300) & (f <= 3400)
    voice_band = float(np.mean(Sxx[band])) if np.any(band) else 0.0
    hi = (f >= 6000)
    hiss = float(np.mean(Sxx[hi])) if np.any(hi) else 0.0
    silent = np.abs(y) < 0.02
    return {
        "name": path.name,
        "sr": sr,
        "dur": duration,
        "peak": peak,
        "rms": rms,
        "clip": clip,
        "speech_s": speech,
        "speech_frac": speech / duration if duration else 0.0,
        "n_gaps": len(gaps),
        "gap_max": max(gaps) if gaps else 0.0,
        "gap_mean": float(np.mean(gaps)) if gaps else 0.0,
        "sil_frac": float(np.mean(silent)),
        "sil_max": longest_run(silent, sr),
        "centroid": float(np.mean(cent)),
        "zcr": float(np.mean(zcr)),
        "onsets": int(onsets.size),
        "onset_rate": float(onsets.size / duration) if duration else 0.0,
        "voice_band": voice_band,
        "hiss": hiss,
        "hiss_ratio": hiss / voice_band if voice_band else 0.0,
    }


def main() -> int:
    names = sys.argv[1:] or [
        "nano-en-iso-p.wav",
        "nano-en-fixed-p.wav",
        "turbo-en-iso-p.wav",
        "turbo-en-fixed-p.wav",
        "v3-en-iso-p.wav",
        "v3-en-fixed-p.wav",
        "v3-de-iso-p.wav",
        "v3-de-def-p.wav",
        "v3-de-cfg0-exag03-chunk180-p.wav",
        "v3-pl-fixed-p.wav",
    ]
    rows = []
    for name in names:
        path = ROOT / name
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        rows.append(analyze(path))
    if not rows:
        return 1
    gold = next((r for r in rows if r["name"] == GOLD), rows[0])
    hdr = (
        f"{'file':42s} {'dur':7s} {'rms':6s} {'peak':5s} {'clip%':6s} "
        f"{'speech%':7s} {'gaps':4s} {'gapMax':6s} {'silMax':6s} "
        f"{'cent':6s} {'onsets':6s} {'dDur':7s}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['name']:42s} {r['dur']:7.2f} {r['rms']:6.3f} {r['peak']:5.3f} {r['clip']*100:6.2f} "
            f"{r['speech_frac']*100:7.1f} {r['n_gaps']:4d} {r['gap_max']:6.2f} {r['sil_max']:6.2f} "
            f"{r['centroid']:6.0f} {r['onsets']:6d} {r['dur']-gold['dur']:+7.2f}"
        )
    print()
    print(f"gold={gold['name']} dur={gold['dur']:.2f}s rms={gold['rms']:.3f} speech={gold['speech_frac']*100:.1f}%")
    print("clip% = fraction of samples at |x|>=0.99")
    print("speech% = librosa.effects.split(top_db=30) voiced duration / file duration")
    print("dDur = duration minus nano ISO (extra seconds usually hallucination or language expansion)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
