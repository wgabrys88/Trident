"""Load and finalize Trident run directories as pandas tables."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf

MEL_BINS = 80
HOP = 240
F0_MIN = 50.0
F0_MAX = 400.0


def _load_jsonl(path: Path) -> pd.DataFrame:
    if not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_json(path, lines=True)


def _load_table(run_dir: Path, stem: str) -> pd.DataFrame:
    parquet = run_dir / f"{stem}.parquet"
    jsonl = run_dir / f"{stem}.jsonl"
    if parquet.is_file() and parquet.stat().st_size > 0:
        return pd.read_parquet(parquet)
    return _load_jsonl(jsonl)


def _parquet_ready(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype != object:
            continue
        nested = any(isinstance(v, (list, dict, tuple)) for v in out[col])
        if not nested:
            continue

        def conv(v):
            if v is None or (not isinstance(v, (list, dict, tuple)) and pd.isna(v)):
                return None
            if isinstance(v, (list, dict, tuple)):
                return json.dumps(v, ensure_ascii=True)
            return str(v)

        out[col] = out[col].map(conv)
    return out


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    _parquet_ready(df).to_parquet(path, index=False)


def extract_mel(pcm: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    x = pcm.astype(np.float32) / 32767.0
    return librosa.feature.melspectrogram(y=x, sr=sr, n_mels=MEL_BINS, hop_length=HOP)


def extract_f0(pcm: np.ndarray, sr: int) -> np.ndarray:
    x = pcm.astype(np.float64) / 32767.0
    hop_s = HOP / float(sr)
    try:
        import parselmouth

        snd = parselmouth.Sound(x, sampling_frequency=sr)
        pitch = snd.to_pitch(time_step=hop_s, pitch_floor=F0_MIN, pitch_ceiling=F0_MAX)
        f0 = np.asarray(pitch.selected_array["frequency"], dtype=np.float64)
        return np.nan_to_num(f0, nan=0.0)
    except Exception:
        import librosa

        f0, _, _ = librosa.pyin(
            x.astype(np.float32), fmin=F0_MIN, fmax=F0_MAX, sr=sr, hop_length=HOP
        )
        return np.nan_to_num(np.asarray(f0, dtype=np.float64), nan=0.0)


def features_frame(pcm: np.ndarray, sr: int) -> pd.DataFrame:
    mel = extract_mel(pcm, sr)
    f0 = extract_f0(pcm, sr)
    n = min(mel.shape[1], f0.size if f0.size else mel.shape[1])
    if f0.size == 0:
        f0 = np.zeros((n,), dtype=np.float64)
    f0 = f0[:n]
    rows = {
        "i": np.arange(n, dtype=np.int32),
        "t_s": np.arange(n, dtype=np.float64) * (HOP / float(sr)),
        "f0": f0,
    }
    for band in range(MEL_BINS):
        rows[f"mel_{band:02d}"] = mel[band, :n]
    return pd.DataFrame(rows)


def write_spectrogram_png(pcm: np.ndarray, sr: int, dest: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import librosa
    import librosa.display

    x = pcm.astype(np.float32) / 32767.0
    spec = librosa.power_to_db(extract_mel(pcm, sr), ref=np.max)
    fig, ax = plt.subplots(figsize=(10, 4))
    librosa.display.specshow(spec, sr=sr, hop_length=HOP, x_axis="time", y_axis="mel", ax=ax)
    ax.set_title(dest.stem)
    fig.tight_layout()
    fig.savefig(dest)
    plt.close(fig)


def finalize_run(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    for stem in ("events", "text_tokens", "t3_tokens", "s3_tokens"):
        df = _load_jsonl(run_dir / f"{stem}.jsonl")
        _write_parquet(df, run_dir / f"{stem}.parquet")
    wavs = list(run_dir.glob("*.wav"))
    if len(wavs) != 1:
        return
    pcm, sr = sf.read(wavs[0], dtype="int16", always_2d=False)
    pcm = np.asarray(pcm, dtype=np.int16)
    feats = features_frame(pcm, int(sr))
    _write_parquet(feats, run_dir / "features.parquet")
    write_spectrogram_png(pcm, int(sr), run_dir / "spectrogram.png")


def load_run(path: Path) -> dict:
    path = Path(path)
    meta_path = path / "meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"{path} missing meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    wavs = list(path.glob("*.wav"))
    if len(wavs) != 1:
        raise SystemExit(f"{path} must contain exactly one WAV")
    pcm, sr = sf.read(wavs[0], dtype="int16", always_2d=False)
    pcm = np.asarray(pcm, dtype=np.int16)
    features_path = path / "features.parquet"
    if features_path.is_file() and features_path.stat().st_size > 0:
        features = pd.read_parquet(features_path)
    else:
        features = features_frame(pcm, int(sr))
    import hashlib
    import xxhash

    wav_bytes = wavs[0].read_bytes()
    return {
        "meta": meta,
        "events": _load_table(path, "events"),
        "text_tokens": _load_table(path, "text_tokens"),
        "t3_tokens": _load_table(path, "t3_tokens"),
        "s3_tokens": _load_table(path, "s3_tokens"),
        "features": features,
        "wav_path": wavs[0],
        "pcm": pcm,
        "sr": int(sr),
        "wav_sha256": hashlib.sha256(wav_bytes).hexdigest(),
        "xxh64": xxhash.xxh64(wav_bytes).hexdigest(),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_tables.py RUN_DIR")
    finalize_run(Path(sys.argv[1]))
