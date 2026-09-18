from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from perth import PerthImplicitWatermarker


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fit_length(audio: np.ndarray, samples: int) -> np.ndarray:
    if audio.size > samples:
        return audio[:samples]
    if audio.size < samples:
        return np.pad(audio, (0, samples - audio.size))
    return audio


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("wav"); a = p.parse_args()
    path = Path(a.wav).resolve()
    audio, sr = sf.read(path, dtype="float32", always_2d=False)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim != 1 or sr != 24000:
        raise SystemExit("watermark expects mono 24000 Hz WAV")
    before = sha256(path)
    watermarker = PerthImplicitWatermarker(device="cpu")
    raw_marked = np.asarray(watermarker.apply_watermark(audio, watermark=None, sample_rate=sr), dtype=np.float32).reshape(-1)
    watermarked = fit_length(raw_marked, audio.size).astype(np.float32, copy=False)
    tmp = path.with_suffix(path.suffix + ".watermarking")
    sf.write(tmp, watermarked, sr, subtype="PCM_16", format="WAV")
    tmp.replace(path)
    final_audio, final_sr = sf.read(path, dtype="float32", always_2d=False)
    final_audio = np.asarray(final_audio, dtype=np.float32)
    if final_audio.ndim != 1 or final_sr != sr or final_audio.size != audio.size:
        raise RuntimeError("watermarked WAV format or length changed")
    extracted = np.asarray(watermarker.get_watermark(final_audio, sample_rate=sr, round=False), dtype=np.float32)
    error = final_audio.astype(np.float64) - audio.astype(np.float64)
    mse = float(np.mean(error * error)) if error.size else 0.0
    signal_power = float(np.mean(audio.astype(np.float64) ** 2)) if audio.size else 0.0
    snr = None if mse == 0.0 else float(10.0 * np.log10(max(signal_power, np.finfo(np.float64).tiny) / mse))
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    psnr = None if mse == 0.0 else float(10.0 * np.log10(max(peak * peak, np.finfo(np.float64).tiny) / mse))
    result = {
        "enabled": True, "provider": "resemble-perth", "provider_version": importlib.metadata.version("resemble-perth"),
        "before_sha256": before, "after_sha256": sha256(path), "input_samples": int(audio.size),
        "watermarker_output_samples": int(raw_marked.size), "output_samples": int(final_audio.size),
        "length_adjustment_samples": int(audio.size - raw_marked.size), "sample_rate": int(sr),
        "verification_mean": float(np.mean(extracted)), "verification_min": float(np.min(extracted)),
        "verification_max": float(np.max(extracted)), "mse": mse, "snr_db": snr, "psnr_db": psnr,
    }
    print(json.dumps(result, sort_keys=True, allow_nan=False)); return 0

if __name__ == "__main__": raise SystemExit(main())
