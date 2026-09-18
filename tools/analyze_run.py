from __future__ import annotations
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
import numpy as np
import pandas as pd
import soundfile as sf
from run_tables import HOP, F0_MIN, F0_MAX, wav_path


def append_jsonl(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def finite(value):
    return float(value) if math.isfinite(float(value)) else None


def align(values, n):
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    if values.size == n:
        return values
    if values.size == 0:
        return np.zeros(n, dtype=np.float64)
    return np.interp(np.linspace(0, values.size - 1, n), np.arange(values.size), values)


def fold_text(value):
    value = unicodedata.normalize("NFKD", str(value).lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).split())

def best_window(target, transcript, editdistance):
    target = fold_text(target)
    transcript = fold_text(transcript)
    target_words = target.split()
    words = transcript.split()
    if not target_words or not words:
        return {"target_folded":target,"best_transcript_window":"","character_edit_distance":len(target),"character_similarity":0.0,"exact_substring":False}
    best = None
    for width in range(max(1, len(target_words) - 2), min(len(words), len(target_words) + 2) + 1):
        for start in range(0, len(words) - width + 1):
            candidate = " ".join(words[start:start + width])
            distance = int(editdistance.eval(target, candidate))
            similarity = 1.0 - distance / max(1, len(target), len(candidate))
            item = (similarity, -distance, candidate)
            if best is None or item > best:
                best = item
    return {"target_folded":target,"best_transcript_window":best[2],"character_edit_distance":-best[1],"character_similarity":finite(best[0]),"exact_substring":target in transcript}

def stats(values):
    values = np.asarray(values, dtype=np.float64)
    voiced = values[values > 0]
    return {
        "frames": int(values.size),
        "voiced_frames": int(voiced.size),
        "voiced_fraction": finite(voiced.size / values.size) if values.size else 0.0,
        "median_hz": finite(np.median(voiced)) if voiced.size else None,
        "mean_hz": finite(np.mean(voiced)) if voiced.size else None,
        "p05_hz": finite(np.percentile(voiced, 5)) if voiced.size else None,
        "p95_hz": finite(np.percentile(voiced, 95)) if voiced.size else None,
    }


def make_plots(run_dir: Path, x: np.ndarray, sr: int, frame: pd.DataFrame, mel_db: np.ndarray, s3_count: int, t3_count: int, crop_samples: int, trim_fade: int) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import librosa.display
    duration = len(x) / sr
    token_s = 0.04
    fig, ax = plt.subplots(figsize=(14, 4))
    t = np.arange(len(x)) / sr
    ax.plot(t, x, linewidth=0.5)
    for value in np.arange(token_s, min(duration, s3_count * token_s) + token_s / 2, token_s):
        ax.axvline(value, linewidth=0.15, alpha=0.22)
    ax.axvspan(0, trim_fade / sr, alpha=0.12)
    ax.set(xlabel="seconds", ylabel="amplitude", xlim=(0, max(duration, token_s)))
    fig.tight_layout()
    fig.savefig(run_dir / "waveform_tokens.png", dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(14, 5))
    librosa.display.specshow(mel_db, sr=sr, hop_length=HOP, x_axis="time", y_axis="mel", ax=ax)
    for value in np.arange(token_s, min(duration + crop_samples / sr, s3_count * token_s) + token_s / 2, token_s):
        ax.axvline(value, linewidth=0.12, alpha=0.2)
    ax.set_title(f"T3={t3_count} S3={s3_count} crop={crop_samples} fade={trim_fade}")
    fig.tight_layout()
    fig.savefig(run_dir / "spectrogram_tokens.png", dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(frame["t_s"], frame["f0_praat_hz"], label="Praat", linewidth=0.8)
    ax.plot(frame["t_s"], frame["f0_pyworld_hz"], label="WORLD", linewidth=0.8)
    ax.plot(frame["t_s"], frame["f0_torchcrepe_hz"], label="CREPE", linewidth=0.8)
    ax.set(xlabel="seconds", ylabel="Hz", ylim=(0, F0_MAX * 1.1))
    ax.legend()
    fig.tight_layout()
    fig.savefig(run_dir / "f0_estimators.png", dpi=150)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(frame["t_s"], frame["rms"], label="RMS")
    ax.plot(frame["t_s"], frame["spectral_centroid_hz"] / sr, label="centroid/sr")
    ax.plot(frame["t_s"], frame["spectral_rolloff_hz"] / sr, label="rolloff/sr")
    ax.legend()
    ax.set_xlabel("seconds")
    fig.tight_layout()
    fig.savefig(run_dir / "energy_spectral.png", dpi=150)
    plt.close(fig)


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: analyze_run.py RUN_DIR REFERENCE_WAV")
    run_dir = Path(sys.argv[1]).resolve()
    ref_path = Path(sys.argv[2]).resolve()
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    wav = wav_path(run_dir, meta)
    pcm, sr = sf.read(wav, dtype="float32", always_2d=False)
    x = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if sr != 24000:
        raise SystemExit(f"unexpected sample rate {sr}")
    import librosa
    import parselmouth
    import pyworld
    import pyloudnorm as pyln
    import torch
    import torchaudio
    import torchcrepe
    import editdistance
    from scipy.signal import welch
    from scipy.stats import entropy
    from resemblyzer import VoiceEncoder, preprocess_wav
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    n = 1 + len(x) // HOP
    t_s = np.arange(n, dtype=np.float64) * HOP / sr
    rms = align(librosa.feature.rms(y=x, frame_length=1024, hop_length=HOP, center=True)[0], n)
    zcr = align(librosa.feature.zero_crossing_rate(y=x, frame_length=1024, hop_length=HOP, center=True)[0], n)
    centroid = align(librosa.feature.spectral_centroid(y=x, sr=sr, n_fft=1024, hop_length=HOP)[0], n)
    bandwidth = align(librosa.feature.spectral_bandwidth(y=x, sr=sr, n_fft=1024, hop_length=HOP)[0], n)
    rolloff = align(librosa.feature.spectral_rolloff(y=x, sr=sr, n_fft=1024, hop_length=HOP, roll_percent=0.85)[0], n)
    mel = librosa.feature.melspectrogram(y=x, sr=sr, n_fft=1024, hop_length=HOP, n_mels=80, power=2.0)
    mel_db = librosa.power_to_db(mel, ref=np.max)
    append_jsonl(run_dir / "librosa.jsonl", {"tool": "librosa", "frames": n, "rms_mean": finite(np.mean(rms)), "rms_p95": finite(np.percentile(rms, 95)), "zcr_mean": finite(np.mean(zcr)), "centroid_mean_hz": finite(np.mean(centroid)), "bandwidth_mean_hz": finite(np.mean(bandwidth)), "rolloff_mean_hz": finite(np.mean(rolloff))})
    praat = parselmouth.Sound(x.astype(np.float64), sampling_frequency=sr).to_pitch(time_step=HOP / sr, pitch_floor=F0_MIN, pitch_ceiling=F0_MAX)
    f0_praat = align(np.nan_to_num(np.asarray(praat.selected_array["frequency"], dtype=np.float64), nan=0.0), n)
    append_jsonl(run_dir / "parselmouth.jsonl", {"tool": "praat-parselmouth", **stats(f0_praat)})
    f0_dio, world_t = pyworld.dio(x.astype(np.float64), sr, f0_floor=F0_MIN, f0_ceil=F0_MAX, frame_period=1000.0 * HOP / sr)
    f0_world = align(pyworld.stonemask(x.astype(np.float64), f0_dio, world_t, sr), n)
    append_jsonl(run_dir / "pyworld.jsonl", {"tool": "pyworld", **stats(f0_world)})
    audio = torch.from_numpy(x).unsqueeze(0)
    mfcc = torchaudio.transforms.MFCC(sample_rate=sr, n_mfcc=20, melkwargs={"n_fft": 1024, "hop_length": HOP, "n_mels": 80})(audio)
    append_jsonl(run_dir / "torchaudio.jsonl", {"tool": "torchaudio", "mfcc_frames": int(mfcc.shape[-1]), "mfcc_mean_abs": finite(mfcc.abs().mean().item()), "mfcc_std": finite(mfcc.std().item())})
    pitch, periodicity = torchcrepe.predict(audio, sr, HOP, F0_MIN, F0_MAX, "full", batch_size=1024, device="cpu", return_periodicity=True)
    f0_crepe = align(pitch.squeeze().detach().cpu().numpy(), n)
    crepe_periodicity = align(periodicity.squeeze().detach().cpu().numpy(), n)
    f0_crepe[crepe_periodicity < 0.21] = 0.0
    append_jsonl(run_dir / "torchcrepe.jsonl", {"tool": "torchcrepe", "periodicity_mean": finite(np.mean(crepe_periodicity)), **stats(f0_crepe)})
    loudness = pyln.Meter(sr).integrated_loudness(x.astype(np.float64))
    append_jsonl(run_dir / "pyloudnorm.jsonl", {"tool":"pyloudnorm","integrated_lufs":finite(loudness)})
    frequencies, psd = welch(x.astype(np.float64), fs=sr, nperseg=min(4096, len(x)))
    psd_norm = psd / max(float(np.sum(psd)), np.finfo(np.float64).tiny)
    spectral_entropy = float(entropy(psd_norm) / math.log(len(psd_norm))) if len(psd_norm) > 1 else 0.0
    dominant_hz = float(frequencies[int(np.argmax(psd))]) if len(psd) else 0.0
    append_jsonl(run_dir / "scipy.jsonl", {"tool":"scipy","welch_bins":int(len(psd)),"spectral_entropy":finite(spectral_entropy),"dominant_frequency_hz":finite(dominant_hz)})
    encoder = VoiceEncoder(device="cpu")
    generated_embed = encoder.embed_utterance(preprocess_wav(wav))
    reference_embed = encoder.embed_utterance(preprocess_wav(ref_path))
    speaker_cosine = float(np.dot(generated_embed, reference_embed) / (np.linalg.norm(generated_embed) * np.linalg.norm(reference_embed)))
    append_jsonl(run_dir / "resemblyzer.jsonl", {"tool": "resemblyzer", "reference_speaker_cosine": finite(speaker_cosine)})
    asr = json.loads((run_dir / "asr_parakeet.json").read_text(encoding="utf-8"))
    expected_raw = str(meta.get("transport_text") or "")
    transcript_raw = str(asr.get("transcript") or "")
    expected = fold_text(expected_raw)
    transcript = fold_text(transcript_raw)
    asr_edit = int(editdistance.eval(expected, transcript))
    asr_ratio = 1.0 - asr_edit / max(1, len(expected), len(transcript))
    append_jsonl(run_dir / "editdistance.jsonl", {"tool":"editdistance","expected_characters":len(expected),"transcript_characters":len(transcript),"character_edit_distance":asr_edit,"character_similarity":finite(asr_ratio)})
    normalization = json.loads((run_dir / "normalization.json").read_text(encoding="utf-8"))
    number_alignment = []
    for change in normalization.get("changes") or []:
        item = {"kind":change.get("kind"),"source":change.get("source"),"target":change.get("target"),**best_window(change.get("target") or "", transcript_raw, editdistance)}
        number_alignment.append(item)
    (run_dir / "normalization_alignment.json").write_text(json.dumps({"language":normalization.get("language"),"transcript":transcript_raw,"items":number_alignment}, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    frame = pd.DataFrame({
        "i": np.arange(n, dtype=np.int32), "t_s": t_s, "rms": rms, "zcr": zcr,
        "spectral_centroid_hz": centroid, "spectral_bandwidth_hz": bandwidth, "spectral_rolloff_hz": rolloff,
        "f0_praat_hz": f0_praat, "f0_pyworld_hz": f0_world, "f0_torchcrepe_hz": f0_crepe, "torchcrepe_periodicity": crepe_periodicity,
    })
    frame.to_parquet(run_dir / "acoustic_frames.parquet", index=False)
    events = pd.read_parquet(run_dir / "events.parquet") if (run_dir / "events.parquet").is_file() else pd.DataFrame()
    def event_int(name, key, default):
        if events.empty or "event" not in events:
            return default
        rows = events[events["event"] == name]
        return int(rows.iloc[-1][key]) if not rows.empty and key in rows.columns and pd.notna(rows.iloc[-1][key]) else default
    s3_count = len(pd.read_parquet(run_dir / "s3_tokens.parquet")) if (run_dir / "s3_tokens.parquet").is_file() else 0
    t3_count = len(pd.read_parquet(run_dir / "t3_tokens.parquet")) if (run_dir / "t3_tokens.parquet").is_file() else 0
    crop = event_int("unit_complete", "cropped_samples", 0)
    fade = event_int("unit_complete", "onset_zero_samples", 0)
    make_plots(run_dir, x, sr, frame, mel_db, s3_count, t3_count, crop, fade)
    thirds = np.array_split(x, 3)
    ref_pcm, ref_sr = sf.read(ref_path, dtype="float32", always_2d=False)
    ref_x = np.asarray(ref_pcm, dtype=np.float32).reshape(-1)
    overall_rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    peak = float(np.max(np.abs(x)))
    align_scores = [float(item["character_similarity"]) for item in number_alignment]
    voiced_pair = (f0_praat > 0) & (f0_world > 0)
    f0_praat_world_mae = float(np.mean(np.abs(f0_praat[voiced_pair] - f0_world[voiced_pair]))) if np.any(voiced_pair) else None
    metrics = {
        "schema_version": 1,
        "wav_path": str(wav),
        "sample_rate": sr,
        "samples": int(len(x)),
        "duration_s": finite(len(x) / sr),
        "peak":finite(peak),
        "rms":finite(overall_rms),
        "dc_offset":finite(np.mean(x, dtype=np.float64)),
        "crest_factor":finite(peak / max(overall_rms, np.finfo(np.float64).tiny)),
        "clipped_fraction":finite(np.mean(np.abs(x) >= 32767.0 / 32768.0)),
        "silence_frame_fraction":finite(np.mean(rms < 0.001)),
        "spectral_entropy":finite(spectral_entropy),
        "dominant_frequency_hz":finite(dominant_hz),
        "first_third_rms": finite(np.sqrt(np.mean(np.square(thirds[0], dtype=np.float64)))),
        "last_third_rms": finite(np.sqrt(np.mean(np.square(thirds[-1], dtype=np.float64)))),
        "integrated_lufs": finite(loudness),
        "speaker_cosine_to_reference":finite(speaker_cosine),
        "reference":{"sample_rate":int(ref_sr),"samples":int(len(ref_x)),"duration_s":finite(len(ref_x) / ref_sr),"t3_conditioning_seconds":finite(min(len(ref_x) / ref_sr, 6.0)),"s3_conditioning_seconds":finite(min(len(ref_x) / ref_sr, 10.0))},
        "asr_character_edit_distance": asr_edit,
        "asr_character_similarity":finite(asr_ratio),
        "number_alignment":{"count":len(number_alignment),"minimum_similarity":finite(min(align_scores)) if align_scores else None,"mean_similarity":finite(np.mean(align_scores)) if align_scores else None},
        "torchaudio_mfcc_mean_abs": finite(mfcc.abs().mean().item()),
        "f0":{"praat":stats(f0_praat),"pyworld":stats(f0_world),"torchcrepe":stats(f0_crepe),"praat_world_mae_hz":finite(f0_praat_world_mae) if f0_praat_world_mae is not None else None},
        "t3_tokens": t3_count,
        "s3_tokens": s3_count,
        "token_period_s": 0.04,
        "cropped_samples": crop,
        "trim_fade_samples": fade,
    }
    (run_dir / "analysis_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    append_jsonl(run_dir / "analysis.jsonl", metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
