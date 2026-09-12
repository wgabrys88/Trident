"""Score dated family WAVs with .venv-eval libs. Fail hard. No pandas."""
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np
import parselmouth
import soundfile as sf
import torch
from audiobox_aesthetics.infer import initialize_predictor
from faster_whisper import WhisperModel
from funasr import AutoModel
from jiwer import cer, wer
from parselmouth.praat import call
from silero_vad import get_speech_timestamps, load_silero_vad
from utmos_pytorch import UTMOSScoreTorch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from tts_nano import CFG as NANO

os.environ.setdefault("PYTHONUTF8", "1")
FAMILIES = (NANO,)
HEADER = {
    "nano": "include/tts-cpp/chatterbox/nano.h",
    "turbo": "include/tts-cpp/chatterbox/turbo.h",
    "v3": "include/tts-cpp/chatterbox/v3.h",
}
CHATTERBOX = ROOT.parent / "chatterbox.cpp"
MODELS = ROOT / "models"
REF = ROOT / "reference.wav"
SRC = (ROOT / "eval_out" / "benchmark_text.txt").read_text(encoding="utf-8").strip()
OUT = ROOT / "eval_out" / "report.json"
ONES = "one two three four five six seven eight nine".split()
TEENS = "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
GARDEN = "the sun is shining over the garden"
TAIL = ["50", "ab-0042", "3.14159", "room 101"]
STREETS = [
    "baker",
    "pennsylvania",
    "fifth avenue",
    "downing",
    "marszalkowska",
    "infinite loop",
    "wall street",
    "princes",
    "wright",
    "right",
    "st john",
    "saint johns",
    "they re lane",
]
POSTALS = [
    "nw1 6xe",
    "sw1a 2aa",
    "sw1a 1aa",
    "ec1a 1bb",
    "g1 1aa",
    "w1a 0ax",
    "00-624",
    "00-001",
    "90210",
    "10001",
]
TIMES = ["00:00", "00:01", "12:00", "12:01", "07:05", "7:05", "13:00", "16:30", "23:59"]
INTS = [str(i) for i in range(1, 51)]


def words_to_digits(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[\s-]+(one|two|three|four|five|six|seven|eight|nine)\b", lambda m: str(20 + 10 * TENS.index(m.group(1)) + ONES.index(m.group(2)) + 1), t)
    for i, w in enumerate(TENS):
        t = re.sub(rf"\b{w}\b", str(20 + 10 * i), t)
    for i, w in enumerate(TEENS):
        t = re.sub(rf"\b{w}\b", str(10 + i), t)
    for i, w in enumerate(ONES):
        t = re.sub(rf"\b{w}\b", str(i + 1), t)
    t = re.sub(r"\bzero\b", "0", t)
    return t


def norm(text: str) -> str:
    t = words_to_digits(text)
    t = t.replace("p.m.", "pm").replace("a.m.", "am")
    t = re.sub(r"[^a-z0-9:.\- ]+", " ", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def in_order(needles, hay):
    pos = 0
    missing = []
    for n in needles:
        i = hay.find(n, pos)
        if i < 0:
            missing.append(n)
        else:
            pos = i + len(n)
    return missing


def dump_bind(name: str):
    txt = MODELS / f"{name}_t3_dump.txt"
    csv = MODELS / f"{name}_sample_dump.csv"
    keys = {}
    for raw in txt.read_text(encoding="ascii", errors="replace").splitlines():
        for k in ("predicted_count", "dropped_count", "eos"):
            if raw.startswith(k + " "):
                keys[k] = int(raw.split(" ", 1)[1])
    n = sil = 0
    with csv.open(encoding="ascii", errors="replace") as f:
        next(f)
        for raw in f:
            n += 1
            cols = raw.split(",")
            if cols[1].strip() == "4299":
                sil += 1
    keys["steps"] = n
    keys["chosen4299"] = sil
    return keys


def header_n_predict(branch: str, rel: str) -> int:
    raw = subprocess.run(["git", "-C", str(CHATTERBOX), "show", f"{branch}:{rel}"], check=True, capture_output=True, text=True).stdout
    m = re.search(r"N_PREDICT\s*=\s*(\d+)", raw)
    return int(m.group(1))


def wav_meta(path: Path):
    blob = path.read_bytes()
    length = len(blob)
    fmt, ch, sr, br, ba, bps = struct.unpack_from("<HHIIHH", blob, 20)
    pcm, file_sr = sf.read(path, dtype="float32")
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1)
    peak = float(np.max(np.abs(pcm)))
    rms = float(np.sqrt(np.mean(pcm * pcm)))
    clip = float(np.mean(np.abs(pcm) >= 0.999))
    return {
        "path": str(path),
        "Length": length,
        "header_fmt": fmt,
        "header_ch": ch,
        "header_sr": sr,
        "header_br": br,
        "header_ba": ba,
        "header_bits": bps,
        "sr": int(file_sr),
        "n_samples": int(pcm.shape[0]),
        "duration_s": float(pcm.shape[0] / file_sr),
        "peak": peak,
        "rms": rms,
        "clip_frac": clip,
    }, pcm, int(file_sr)


def f0_stats(path: Path):
    snd = parselmouth.Sound(str(path))
    pitch = snd.to_pitch(pitch_floor=75, pitch_ceiling=400)
    vals = pitch.selected_array["frequency"]
    voiced = vals[vals > 0]
    pp = call(snd, "To PointProcess (periodic, cc)", 75, 400)
    jitter = call(pp, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
    shimmer = call([snd, pp], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)
    hnr = call(snd.to_harmonicity_cc(), "Get mean", 0, 0)
    return {
        "f0_mean": float(np.mean(voiced)) if voiced.size else 0.0,
        "f0_std": float(np.std(voiced)) if voiced.size else 0.0,
        "f0_voiced_frac": float(voiced.size / max(vals.size, 1)),
        "jitter": float(jitter),
        "shimmer": float(shimmer),
        "hnr": float(hnr),
    }


def tone_class(pauses, f0):
    n_pause = len(pauses)
    mean_gap = float(np.mean(pauses)) if pauses else 0.0
    if f0["f0_voiced_frac"] < 0.15 or f0["f0_std"] < 5:
        return "flat"
    if n_pause >= 8 and mean_gap > 0.35:
        return "listed" if f0["f0_std"] < 40 else "mixed"
    if n_pause >= 12:
        return "broken"
    if n_pause <= 3 and f0["f0_std"] >= 15:
        return "flowing"
    if n_pause >= 4:
        return "mixed"
    return "flowing"


def mfcc_cosine(a, sr_a, b, sr_b):
    ma = librosa.feature.mfcc(y=a, sr=sr_a, n_mfcc=20).mean(axis=1)
    mb = librosa.feature.mfcc(y=b, sr=sr_b, n_mfcc=20).mean(axis=1)
    return float(np.dot(ma, mb) / (np.linalg.norm(ma) * np.linalg.norm(mb)))


def newest_wav(name: str) -> Path:
    wavs = sorted(ROOT.glob(f"20??????-??????-{name}.wav"))
    if not wavs:
        raise FileNotFoundError(name)
    return wavs[-1]


def knobs_of(name: str):
    blob = (MODELS / f"{name}.knobs").read_text(encoding="ascii")
    out = {}
    for raw in blob.splitlines():
        k, v = raw.split("=", 1)
        out[k] = v
    return out


def classify_failure(dump, n_predict, fails, f0):
    modes = []
    if dump["eos"] == 1 and dump["predicted_count"] < n_predict * 0.5:
        modes.append("length")
    if dump["eos"] == 0 and dump["predicted_count"] >= n_predict:
        modes.append("budget")
    content_fails = {
        "dropped_integers",
        "dropped_garden_repeats",
        "dropped_street_postal_time",
        "missing_tail",
        "trailing_garbage",
    }
    if any(f in content_fails for f in fails):
        modes.append("content")
    sil_frac = dump["chosen4299"] / max(dump["steps"], 1)
    if sil_frac > 0.15 or f0["f0_voiced_frac"] < 0.15:
        modes.append("collapse")
    if not modes:
        return "none"
    if len(modes) == 1:
        return modes[0]
    return "mix"


def main():
    device = "cpu"
    asr = WhisperModel("medium.en", device=device, compute_type="int8")
    vad = load_silero_vad()
    utmos = UTMOSScoreTorch(device=device)
    aes = initialize_predictor()
    emo = AutoModel(model="iic/emotion2vec_plus_base")
    ref_pcm, ref_sr = sf.read(REF, dtype="float32")
    if ref_pcm.ndim > 1:
        ref_pcm = ref_pcm.mean(axis=1)
    ref16 = librosa.resample(ref_pcm, orig_sr=ref_sr, target_sr=16000)
    sf.write(ROOT / "eval_out" / "_ref16.wav", ref16, 16000)
    ref_feats = np.asarray(emo.generate(str(ROOT / "eval_out" / "_ref16.wav"), granularity="utterance", extract_embedding=True)[0]["feats"], dtype=np.float64)
    src_n = norm(SRC)
    rows = []
    for fam in FAMILIES:
        wav = newest_wav(fam.name)
        meta, pcm, sr = wav_meta(wav)
        if meta["Length"] <= 44:
            raise RuntimeError(f"no pcm {wav}")
        dump = dump_bind(fam.name)
        n_predict = header_n_predict(fam.branch, HEADER[fam.name])
        lang = ""
        if fam.needs_language:
            lang = (MODELS / f"{fam.name}.language").read_text(encoding="ascii").strip()
        pcm16 = librosa.resample(pcm, orig_sr=sr, target_sr=16000)
        ts = get_speech_timestamps(torch.from_numpy(pcm16), vad, sampling_rate=16000)
        pauses = []
        for a, b in zip(ts, ts[1:]):
            gap = (b["start"] - a["end"]) / 16000.0
            if gap >= 0.12:
                pauses.append(gap)
        f0 = f0_stats(wav)
        wav16 = torch.from_numpy(pcm16).float().unsqueeze(0)
        utmos_s = float(utmos.score(wav16.to(device))[0])
        aes_s = aes.forward([{"path": torch.from_numpy(pcm).float().unsqueeze(0), "sample_rate": sr}])[0]
        sf.write(ROOT / "eval_out" / f"_{fam.name}16.wav", pcm16, 16000)
        feats = np.asarray(emo.generate(str(ROOT / "eval_out" / f"_{fam.name}16.wav"), granularity="utterance", extract_embedding=True)[0]["feats"], dtype=np.float64)
        emo_cos = float(np.dot(ref_feats, feats) / (np.linalg.norm(ref_feats) * np.linalg.norm(feats)))
        segs, _info = asr.transcribe(str(wav), word_timestamps=True, vad_filter=False)
        hyp = " ".join(s.text for s in segs).strip()
        hyp_n = norm(hyp)
        fails = []
        if in_order(TAIL, hyp_n):
            fails.append("missing_tail")
        dropped_i = in_order(INTS, hyp_n)
        if dropped_i:
            fails.append("dropped_integers")
        if GARDEN and hyp_n.count(GARDEN) < 10:
            fails.append("dropped_garden_repeats")
        miss_s = [x for x in STREETS if x not in hyp_n]
        miss_p = [x for x in POSTALS if x not in hyp_n]
        miss_t = [x for x in TIMES if x not in hyp_n]
        if miss_s or miss_p or miss_t:
            fails.append("dropped_street_postal_time")
        if len(hyp_n) > len(src_n) * 1.15:
            fails.append("trailing_garbage")
        if dump["eos"] == 0 and dump["predicted_count"] < n_predict:
            fails.append("eos0_short_predicted_count")
        w = float(wer(src_n, hyp_n))
        c = float(cer(src_n, hyp_n))
        verdict = "PASS" if (not fails and w <= 0.12) else "FAIL"
        failure_class = classify_failure(dump, n_predict, fails, f0)
        rows.append(
            {
                "family": fam.name,
                "verdict": verdict,
                "failure_class": failure_class,
                "fails": fails,
                "wav": meta,
                "pin": fam.chatterbox_rev,
                "header_n_predict": n_predict,
                "knobs": knobs_of(fam.name),
                "language": lang,
                "dump": dump,
                "dsp": {k: meta[k] for k in ("sr", "n_samples", "duration_s", "peak", "rms", "clip_frac")},
                "vad_pauses": pauses,
                "vad_n_pause": len(pauses),
                "f0": f0,
                "tone": tone_class(pauses, f0),
                "utmos": utmos_s,
                "audiobox_pq": float(aes_s["PQ"]),
                "audiobox_ce": float(aes_s["CE"]),
                "speaker_mfcc_cosine": mfcc_cosine(pcm, sr, ref_pcm, ref_sr),
                "emotion2vec_cosine": emo_cos,
                "asr": hyp,
                "wer": w,
                "cer": c,
                "dropped_integers": dropped_i,
                "garden_count": hyp_n.count(GARDEN) if GARDEN else 0,
                "missing_streets": miss_s,
                "missing_postals": miss_p,
                "missing_times": miss_t,
                "coverage_order_tail": in_order(TAIL, hyp_n),
            }
        )
    report = {
        "source": SRC,
        "families": rows,
        "rank": "not ranked",
        "session": "nano only",
        "differ": [
            "nano is GPT-2 T3 with silence-count; turbo/v3 not scored this session",
        ],
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(OUT)
    for r in rows:
        print(r["family"], r["verdict"], r["failure_class"], r["wav"]["path"], "Length", r["wav"]["Length"], "wer", round(r["wer"], 4), "eos", r["dump"]["eos"], "steps", r["dump"]["steps"], "sil", r["dump"]["chosen4299"])


if __name__ == "__main__":
    main()
