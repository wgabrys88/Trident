"""Compare two Trident run directories. Numeric gate only."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf


def load_run(path: Path) -> dict:
    path = Path(path)
    meta = pd.json_normalize(json.loads((path / "meta.json").read_text(encoding="utf-8")))
    wavs = list(path.glob("*.wav"))
    if len(wavs) != 1:
        raise SystemExit(f"{path} must contain exactly one WAV")
    pcm, sr = sf.read(wavs[0], dtype="int16", always_2d=False)
    return {
        "meta": meta,
        "events": pd.read_json(path / "events.jsonl", lines=True),
        "text_tokens": pd.read_json(path / "text_tokens.jsonl", lines=True),
        "t3_tokens": pd.read_json(path / "t3_tokens.jsonl", lines=True),
        "s3_tokens": pd.read_json(path / "s3_tokens.jsonl", lines=True),
        "wav_path": wavs[0],
        "pcm": np.asarray(pcm, dtype=np.int16),
        "sr": int(sr),
        "wav_sha256": hashlib.sha256(wavs[0].read_bytes()).hexdigest(),
    }


def _ids(df: pd.DataFrame) -> np.ndarray:
    if df is None or df.empty:
        return np.zeros((0,), dtype=np.int64)
    return df["id"].to_numpy(dtype=np.int64)


def _edit(a: np.ndarray, b: np.ndarray) -> int:
    import editdistance

    return int(editdistance.eval(a.tolist(), b.tolist()))


def _dtw(a: np.ndarray, b: np.ndarray) -> float:
    import dtw

    if a.size == 0 and b.size == 0:
        return 0.0
    aa = a.reshape(-1, 1).astype(np.float64)
    bb = b.reshape(-1, 1).astype(np.float64)
    return float(dtw.dtw(aa, bb, keep_internals=False).distance)


def _mel(pcm: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    x = pcm.astype(np.float32) / 32767.0
    return librosa.feature.melspectrogram(y=x, sr=sr, n_mels=80, hop_length=240)


def _f0(pcm: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    x = pcm.astype(np.float32) / 32767.0
    f0, _, _ = librosa.pyin(x, fmin=50, fmax=400, sr=sr)
    return np.nan_to_num(f0, nan=0.0)


def _pesq_stoi_sisdr(a: np.ndarray, b: np.ndarray, sr: int) -> tuple[float, float, float]:
    from pesq import pesq
    from pystoi import stoi
    import fast_bss_eval

    xa = a.astype(np.float64) / 32767.0
    xb = b.astype(np.float64) / 32767.0
    n = min(xa.size, xb.size)
    xa, xb = xa[:n], xb[:n]
    p = float(pesq(sr, xa, xb, "wb")) if sr in (8000, 16000) else float("nan")
    if sr == 24000:
        import soxr

        xa16 = soxr.resample(xa, sr, 16000)
        xb16 = soxr.resample(xb, sr, 16000)
        p = float(pesq(16000, xa16, xb16, "wb"))
    s = float(stoi(xa, xb, sr, extended=False))
    sisdr = float(np.asarray(fast_bss_eval.si_sdr(xb[None, :], xa[None, :])).reshape(-1)[0])
    return p, s, sisdr


def _lufs(pcm: np.ndarray, sr: int) -> float:
    import pyloudnorm as pyln

    x = pcm.astype(np.float64) / 32767.0
    meter = pyln.Meter(sr)
    return float(meter.integrated_loudness(x))


def _speaker(a: np.ndarray, b: np.ndarray, sr: int) -> float:
    from resemblyzer import VoiceEncoder, preprocess_wav

    enc = VoiceEncoder()
    wa = preprocess_wav(a.astype(np.float32) / 32767.0, source_sr=sr)
    wb = preprocess_wav(b.astype(np.float32) / 32767.0, source_sr=sr)
    ea, eb = enc.embed_utterance(wa), enc.embed_utterance(wb)
    return float(np.dot(ea, eb) / (np.linalg.norm(ea) * np.linalg.norm(eb)))


def gate(a: dict, b: dict) -> dict:
    ta, tb = _ids(a["text_tokens"]), _ids(b["text_tokens"])
    t3a, t3b = _ids(a["t3_tokens"]), _ids(b["t3_tokens"])
    s3a, s3b = _ids(a["s3_tokens"]), _ids(b["s3_tokens"])
    same = a["wav_sha256"] == b["wav_sha256"]
    out = {
        "wav_sha_equal": same,
        "wav_sha_a": a["wav_sha256"],
        "wav_sha_b": b["wav_sha256"],
        "text_edit": _edit(ta, tb),
        "t3_edit": _edit(t3a, t3b),
        "s3_edit": _edit(s3a, s3b),
        "text_dtw": _dtw(ta, tb),
        "t3_dtw": _dtw(t3a, t3b),
        "s3_dtw": _dtw(s3a, s3b),
        "duration_a_s": a["pcm"].size / a["sr"],
        "duration_b_s": b["pcm"].size / b["sr"],
        "sr_a": a["sr"],
        "sr_b": b["sr"],
    }
    ma, mb = _mel(a["pcm"], a["sr"]), _mel(b["pcm"], b["sr"])
    t = min(ma.shape[1], mb.shape[1])
    out["mel_l2"] = float(np.linalg.norm(ma[:, :t] - mb[:, :t]))
    fa, fb = _f0(a["pcm"], a["sr"]), _f0(b["pcm"], b["sr"])
    n = min(fa.size, fb.size)
    out["f0_l2"] = float(np.linalg.norm(fa[:n] - fb[:n]))
    pesq_v, stoi_v, sisdr_v = _pesq_stoi_sisdr(a["pcm"], b["pcm"], a["sr"])
    out["pesq"] = pesq_v
    out["stoi"] = stoi_v
    out["si_sdr"] = sisdr_v
    out["lufs_a"] = _lufs(a["pcm"], a["sr"])
    out["lufs_b"] = _lufs(b["pcm"], b["sr"])
    out["speaker_cosine"] = _speaker(a["pcm"], b["pcm"], a["sr"])
    out["exact_match"] = bool(
        same
        and out["text_edit"] == 0
        and out["t3_edit"] == 0
        and out["s3_edit"] == 0
        and out["mel_l2"] == 0.0
        and out["f0_l2"] == 0.0
        and a["pcm"].tobytes() == b["pcm"].tobytes()
    )
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: compare_runs.py A B")
    a = load_run(Path(argv[1]))
    b = load_run(Path(argv[2]))
    result = gate(a, b)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["exact_match"] or argv[1] != argv[2] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
