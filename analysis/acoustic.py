import sys
from pathlib import Path
import numpy as np
import soundfile as sf
import librosa
import pyworld as pw

SR = 24000
FRAME_MS = 10
HOP = int(SR * FRAME_MS / 1000)
NFFT = 1024
ROOT = Path(__file__).resolve().parent.parent

def frames_feature(y):
    n = len(y)
    nfr = 1 + (n - NFFT) // HOP
    if nfr < 1:
        return None
    S = np.abs(librosa.stft(y, n_fft=NFFT, hop_length=HOP, win_length=NFFT, window="hann"))
    # S shape (1+NFFT/2, nframes)
    mag = S[:, :nfr]
    power = mag ** 2
    rms = librosa.feature.rms(S=mag, frame_length=NFFT, hop_length=HOP)[0, :nfr]
    centroid = librosa.feature.spectral_centroid(S=mag, sr=SR, n_fft=NFFT, hop_length=HOP)[0, :nfr]
    flatness = librosa.feature.spectral_flatness(S=mag, n_fft=NFFT, hop_length=HOP)[0, :nfr]
    flux = librosa.onset.onset_strength(S=mag, sr=SR, hop_length=HOP, center=False)
    flux = flux[:nfr]
    zcr = librosa.feature.zero_crossing_rate(y, frame_length=NFFT, hop_length=HOP)[0, :nfr]
    t = np.arange(nfr) * HOP / SR
    return dict(t=t, rms=rms, centroid=centroid, flatness=flatness, flux=flux, zcr=zcr, mag=mag)

def f0_feature(y):
    y64 = y.astype(np.float64)
    f0, sp, ap = pw.wav2world(y64, SR, frame_period=FRAME_MS)
    nfr = len(f0)
    ap = ap.mean(axis=1)[:nfr]
    return f0[:nfr], ap[:nfr]

def report_model(name, path):
    y, sr = sf.read(str(path))
    y = y.astype(np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    L = []
    L.append(f"\n{'='*70}\nMODEL {name}: {path.name}  frames={len(y)} sr={sr} dur={len(y)/sr:.3f}s")
    feat = frames_feature(y)
    f0, ap = f0_feature(y)
    nfr = feat["t"].shape[0]
    f0 = f0[:nfr]; ap = ap[:nfr]
    voiced = (f0 > 1.0).astype(float)
    rms_db = 20 * np.log10(np.maximum(feat["rms"], 1e-8))
    med_rms = np.median(rms_db)
    L.append(f"  med_rms {med_rms:.1f} dB  voiced_frac {voiced.mean()*100:.1f}%  nframes {nfr}")

    # Detector (a) repetition: low spectral flux sustained >1s
    # flux smoothed, threshold low
    flx = feat["flux"]
    flx_med = np.median(flx)
    low_flux = flx < 0.3 * flx_med
    # Detector (b) energy collapse: rms_db < med - 30 sustained
    energy_collapse = rms_db < (med_rms - 30)
    # Detector (c) voicing collapse: aperiodicity high (>0.9) sustained
    noise_collapse = ap > 0.9
    # Detector (d) pitch collapse: flat f0 over >0.5s among voiced
    # count of consecutive voiced frames with near-constant f0
    flat = np.zeros(nfr, bool)
    for i in range(1, nfr - 1):
        if voiced[i] and abs(f0[i] - f0[i-1]) < 0.5:
            flat[i] = True

    def first_run(mask, min_len):
        # min_len in frames (10ms each)
        run = 0
        start = -1
        for i, m in enumerate(mask):
            if m:
                if run == 0:
                    start = i
                run += 1
                if run >= min_len:
                    return start, run
            else:
                run = 0
        return None, 0

    min_1s = int(1.0 / (FRAME_MS / 1000))
    min_halfs = int(0.5 / (FRAME_MS / 1000))
    a_s, a_r = first_run(low_flux, min_1s)
    b_s, b_r = first_run(energy_collapse, min_halfs)
    c_s, c_r = first_run(noise_collapse, min_halfs)
    d_s, d_r = first_run(flat, min_halfs)
    L.append(f"  (a) repetition(low flux>1s): start={a_s} ({a_s*0.01:.2f}s) run={a_r}" if a_s is not None else "  (a) repetition(low flux>1s): none")
    L.append(f"  (b) energy collapse(<-30dB,>0.5s): start={b_s} ({b_s*0.01:.2f}s) run={b_r}" if b_s is not None else "  (b) energy collapse(<-30dB,>0.5s): none")
    L.append(f"  (c) noise takeover(ap>0.9,>0.5s): start={c_s} ({c_s*0.01:.2f}s) run={c_r}" if c_s is not None else "  (c) noise takeover(ap>0.9,>0.5s): none")
    L.append(f"  (d) pitch flat(>0.5s): start={d_s} ({d_s*0.01:.2f}s) run={d_r}" if d_s is not None else "  (d) pitch flat(>0.5s): none")

    # voiced segment count (approximate words)
    seg = (voiced[1:] > voiced[:-1])
    n_words = int(seg.sum())
    L.append(f"  approx_voiced_onsets {n_words}")

    # Save CSV
    csv = ROOT / "analysis" / f"{name}_frames.csv"
    header = "time_s,rms_db,f0,voiced,aperiodicity,centroid,flatness,flux,zcr"
    rows = []
    for i in range(nfr):
        rows.append(f"{feat['t'][i]:.3f},{rms_db[i]:.2f},{f0[i]:.2f},{voiced[i]:.0f},{ap[i]:.3f},{feat['centroid'][i]:.1f},{feat['flatness'][i]:.4f},{feat['flux'][i]:.3f},{feat['zcr'][i]:.4f}")
    csv.write_text(header + "\n" + "\n".join(rows), encoding="ascii")
    L.append(f"  wrote {csv.name} ({nfr} frames)")
    return "\n".join(L)

out = []
out.append("ACOUSTIC ANALYSIS (deterministic: librosa + pyworld, 10ms frames, no ASR)")
out.append("SR 24000, NFFT 1024, hop 240")
for name, fn in [("nano", "20260911-193837-nano.wav"),
                 ("turbo", "20260911-200024-turbo.wav"),
                 ("v3", "20260911-200048-v3.wav")]:
    out.append(report_model(name, ROOT / fn))
rep = ROOT / "analysis" / "degradation_report.txt"
rep.write_text("\n".join(out), encoding="ascii", errors="replace")
print("\n".join(out))
