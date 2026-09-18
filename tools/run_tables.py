from __future__ import annotations
import hashlib
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
    return pd.read_parquet(parquet) if parquet.is_file() and parquet.stat().st_size else _load_jsonl(run_dir / f"{stem}.jsonl")


def _parquet_ready(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype != object or not any(isinstance(v, (list, dict, tuple)) for v in out[col]):
            continue
        out[col] = out[col].map(lambda v: None if v is None else json.dumps(v, ensure_ascii=True) if isinstance(v, (list, dict, tuple)) else str(v))
    return out


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    _parquet_ready(df).to_parquet(path, index=False)


def wav_path(run_dir: Path, meta: dict | None = None) -> Path:
    meta = meta or json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    output = meta.get("output_path") or meta.get("output", {}).get("path")
    if output:
        path = Path(output)
        if not path.is_absolute():
            path = (run_dir.parent / path).resolve()
        if path.is_file():
            return path
    wavs = sorted(run_dir.glob("*.wav"))
    if len(wavs) != 1:
        raise SystemExit(f"{run_dir} does not resolve to exactly one WAV")
    return wavs[0]


def extract_mel(pcm: np.ndarray, sr: int) -> np.ndarray:
    import librosa
    x = pcm.astype(np.float32) / 32768.0
    return librosa.feature.melspectrogram(y=x, sr=sr, n_fft=1024, hop_length=HOP, n_mels=MEL_BINS, power=2.0)


def extract_f0(pcm: np.ndarray, sr: int) -> np.ndarray:
    import parselmouth
    x = pcm.astype(np.float64) / 32768.0
    pitch = parselmouth.Sound(x, sampling_frequency=sr).to_pitch(time_step=HOP / float(sr), pitch_floor=F0_MIN, pitch_ceiling=F0_MAX)
    return np.nan_to_num(np.asarray(pitch.selected_array["frequency"], dtype=np.float64), nan=0.0)


def features_frame(pcm: np.ndarray, sr: int) -> pd.DataFrame:
    mel = extract_mel(pcm, sr)
    f0 = extract_f0(pcm, sr)
    n = min(mel.shape[1], f0.size)
    rows = {"i": np.arange(n, dtype=np.int32), "t_s": np.arange(n, dtype=np.float64) * HOP / float(sr), "f0": f0[:n]}
    for band in range(MEL_BINS):
        rows[f"mel_{band:02d}"] = mel[band, :n]
    return pd.DataFrame(rows)


def write_spectrogram_png(pcm: np.ndarray, sr: int, dest: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import librosa
    import librosa.display
    spec = librosa.power_to_db(extract_mel(pcm, sr), ref=np.max)
    fig, ax = plt.subplots(figsize=(12, 4))
    librosa.display.specshow(spec, sr=sr, hop_length=HOP, x_axis="time", y_axis="mel", ax=ax)
    fig.tight_layout()
    fig.savefig(dest, dpi=140)
    plt.close(fig)


def finalize_run(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    for stem in ("events", "text_tokens", "t3_tokens", "s3_tokens"):
        _write_parquet(_load_jsonl(run_dir / f"{stem}.jsonl"), run_dir / f"{stem}.parquet")
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    wav = wav_path(run_dir, meta)
    pcm, sr = sf.read(wav, dtype="int16", always_2d=False)
    pcm = np.asarray(pcm, dtype=np.int16)
    _write_parquet(features_frame(pcm, int(sr)), run_dir / "features.parquet")
    write_spectrogram_png(pcm, int(sr), run_dir / "spectrogram.png")


def load_run(path: Path) -> dict:
    path = Path(path)
    meta_path = path / "meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"{path} missing meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    wav = wav_path(path, meta)
    pcm, sr = sf.read(wav, dtype="int16", always_2d=False)
    pcm = np.asarray(pcm, dtype=np.int16)
    features_path = path / "features.parquet"
    features = pd.read_parquet(features_path) if features_path.is_file() and features_path.stat().st_size else features_frame(pcm, int(sr))
    import xxhash
    wav_bytes = wav.read_bytes()
    return {
        "meta": meta,
        "events": _load_table(path, "events"),
        "text_tokens": _load_table(path, "text_tokens"),
        "t3_tokens": _load_table(path, "t3_tokens"),
        "s3_tokens": _load_table(path, "s3_tokens"),
        "features": features,
        "wav_path": wav,
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
