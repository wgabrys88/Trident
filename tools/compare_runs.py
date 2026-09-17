"""Compare two Trident run directories. Numeric gate only."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

from run_tables import extract_f0, extract_mel, load_run, write_spectrogram_png

IDENTICAL_PESQ_WB_MIN = 4.5
SDR_CLIP = 100.0


def _ids(df) -> np.ndarray:
    if df is None or df.empty or "id" not in df.columns:
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


def _clip_sdr(value: float) -> float:
    if not math.isfinite(value):
        return SDR_CLIP
    return float(min(max(value, -SDR_CLIP), SDR_CLIP))


def _pesq_stoi_sisdr(a: np.ndarray, b: np.ndarray, sr: int) -> tuple[float, float, float]:
    from pesq import pesq
    from pystoi import stoi
    import fast_bss_eval

    xa = a.astype(np.float64) / 32767.0
    xb = b.astype(np.float64) / 32767.0
    n = min(xa.size, xb.size)
    xa, xb = xa[:n], xb[:n]
    p = float("nan")
    if sr in (8000, 16000):
        p = float(pesq(sr, xa, xb, "wb"))
    elif sr == 24000:
        import soxr

        xa16 = soxr.resample(xa, sr, 16000)
        xb16 = soxr.resample(xb, sr, 16000)
        p = float(pesq(16000, xa16, xb16, "wb"))
    s = float(stoi(xa, xb, sr, extended=False))
    sisdr = float(np.asarray(fast_bss_eval.si_sdr(xb[None, :], xa[None, :])).reshape(-1)[0])
    return p, s, _clip_sdr(sisdr)


def _lufs(pcm: np.ndarray, sr: int) -> float:
    import pyloudnorm as pyln

    x = pcm.astype(np.float64) / 32767.0
    meter = pyln.Meter(sr)
    value = float(meter.integrated_loudness(x))
    if not math.isfinite(value):
        raise RuntimeError("non-finite LUFS")
    return value


def _speaker(a: np.ndarray, b: np.ndarray, sr: int) -> float:
    from resemblyzer import VoiceEncoder, preprocess_wav

    enc = VoiceEncoder()
    wa = preprocess_wav(a.astype(np.float32) / 32767.0, source_sr=sr)
    wb = preprocess_wav(b.astype(np.float32) / 32767.0, source_sr=sr)
    ea, eb = enc.embed_utterance(wa), enc.embed_utterance(wb)
    denom = float(np.linalg.norm(ea) * np.linalg.norm(eb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(ea, eb) / denom)


def _close(a: float, b: float, abs_tol: float = 1e-9) -> bool:
    if not math.isfinite(a) or not math.isfinite(b):
        return False
    return math.isclose(a, b, rel_tol=0.0, abs_tol=abs_tol)


def gate(a: dict, b: dict) -> dict:
    from scipy.linalg import norm

    ta, tb = _ids(a["text_tokens"]), _ids(b["text_tokens"])
    t3a, t3b = _ids(a["t3_tokens"]), _ids(b["t3_tokens"])
    s3a, s3b = _ids(a["s3_tokens"]), _ids(b["s3_tokens"])
    same = a["wav_sha256"] == b["wav_sha256"]
    out = {
        "wav_sha_equal": same,
        "wav_sha_a": a["wav_sha256"],
        "wav_sha_b": b["wav_sha256"],
        "xxh64_equal": a["xxh64"] == b["xxh64"],
        "xxh64_a": a["xxh64"],
        "xxh64_b": b["xxh64"],
        "text_edit": _edit(ta, tb),
        "t3_edit": _edit(t3a, t3b),
        "s3_edit": _edit(s3a, s3b),
        "text_dtw": _dtw(ta, tb),
        "t3_dtw": _dtw(t3a, t3b),
        "s3_dtw": _dtw(s3a, s3b),
        "duration_a_s": float(a["pcm"].size / a["sr"]),
        "duration_b_s": float(b["pcm"].size / b["sr"]),
        "sr_a": a["sr"],
        "sr_b": b["sr"],
    }
    ma, mb = extract_mel(a["pcm"], a["sr"]), extract_mel(b["pcm"], b["sr"])
    t = min(ma.shape[1], mb.shape[1])
    out["mel_l2"] = float(norm((ma[:, :t] - mb[:, :t]).reshape(-1)))
    fa, fb = extract_f0(a["pcm"], a["sr"]), extract_f0(b["pcm"], b["sr"])
    n = min(fa.size, fb.size)
    out["f0_l2"] = float(norm(fa[:n] - fb[:n])) if n else 0.0
    pesq_v, stoi_v, sisdr_v = _pesq_stoi_sisdr(a["pcm"], b["pcm"], a["sr"])
    out["pesq"] = pesq_v
    out["stoi"] = stoi_v
    out["si_sdr"] = sisdr_v
    out["lufs_a"] = _lufs(a["pcm"], a["sr"])
    out["lufs_b"] = _lufs(b["pcm"], b["sr"])
    out["speaker_cosine"] = _speaker(a["pcm"], b["pcm"], a["sr"])
    pcm_equal = a["pcm"].tobytes() == b["pcm"].tobytes()
    out["exact_match"] = bool(
        same
        and out["xxh64_equal"]
        and out["text_edit"] == 0
        and out["t3_edit"] == 0
        and out["s3_edit"] == 0
        and out["text_dtw"] == 0.0
        and out["t3_dtw"] == 0.0
        and out["s3_dtw"] == 0.0
        and out["mel_l2"] == 0.0
        and out["f0_l2"] == 0.0
        and pcm_equal
        and a["sr"] == b["sr"]
        and _close(out["duration_a_s"], out["duration_b_s"], 1e-12)
        and _close(out["lufs_a"], out["lufs_b"], 1e-6)
        and _close(out["stoi"], 1.0, 1e-6)
        and out["speaker_cosine"] >= 0.999
        and math.isfinite(out["pesq"])
        and out["pesq"] >= IDENTICAL_PESQ_WB_MIN
        and out["si_sdr"] >= SDR_CLIP - 1e-6
    )
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: compare_runs.py A B")
    a_path = Path(argv[1]).resolve()
    b_path = Path(argv[2]).resolve()
    a = load_run(a_path)
    b = load_run(b_path)
    if a_path != b_path:
        write_spectrogram_png(a["pcm"], a["sr"], Path.cwd() / "spectrogram_a.png")
        write_spectrogram_png(b["pcm"], b["sr"], Path.cwd() / "spectrogram_b.png")
    result = gate(a, b)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result["exact_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
