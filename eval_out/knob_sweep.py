"""Hyperparameter sweep for nano/turbo: literature baselines, hypothesis rounds, full eval metrics."""
import json
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MODELS = ROOT / "models"
DUMP_DIR = ROOT / "eval_out" / "dumps"
REPORT = ROOT / "eval_out" / "knob_sweep_report.jsonl"
SUMMARY = ROOT / "eval_out" / "knob_sweep_summary.json"
COMPARE = ROOT / "eval_out" / "knob_sweep_compare.json"

# PyTorch Chatterbox Turbo defaults (tts_turbo.py): temp=0.8 top_k=1000 top_p=0.95 repeat=1.2
# PyTorch Standard (tts.py / t3.py): temp=0.8 top_p=1.0 min_p=0.05 repeat=1.2 cfg=0.5
# C++ nano/turbo headers: top_p=0.95 repeat=1.2 temp=0.8 top_k=1000 silence_count=3 cfm_steps=2

PROSE_130 = (
    "Please follow the delivery route as one continuous record while you pass Baker Street in the "
    "morning light, continue along Pennsylvania Avenue toward the river district, pause briefly near "
    "the old post office, keep speaking without breaking the thread of the journey, and describe each "
    "landmark in order until you reach the final gate where the courier signs the register and the walk "
    "ends at Downing Street before noon."
)

COUNT_10_27 = (ROOT / "eval_out" / "count_10_27.txt").read_text(encoding="utf-8").strip()
BENCHMARK = (ROOT / "eval_out" / "benchmark_text.txt").read_text(encoding="utf-8").strip()
BENCHMARK_50 = BENCHMARK[: len(BENCHMARK) // 2]

TEXTS = {
    "count_10_27": (COUNT_10_27, list(range(10, 28))),
    "prose_130": (PROSE_130, None),
    "benchmark_50pct": (BENCHMARK_50, None),
}

ROUND1 = [
    {
        "id": "r1_header",
        "knobs": {},
        "source": "C++ header defaults (match PyTorch turbo: top_p=0.95 repeat=1.2 temp=0.8)",
        "hypothesis": "Baseline reference for all deltas.",
    },
    {
        "id": "r1_top_p_1",
        "knobs": {"top-p": "1.0"},
        "source": "PyTorch standard Chatterbox uses top_p=1.0 not 0.95",
        "hypothesis": "Wider nucleus sampling may extend generation and reduce early stop_speech.",
    },
    {
        "id": "r1_repeat_1",
        "knobs": {"repeat-penalty": "1.0"},
        "source": "llama.cpp: repeat_penalty=1.0 disables penalty; mintlify default 1.2",
        "hypothesis": "Lower penalty on counting lists should raise predicted_count and reduce dropped numbers.",
    },
    {
        "id": "r1_repeat_15",
        "knobs": {"repeat-penalty": "1.5"},
        "source": "mintlify range 1.0-2.5; stronger anti-repeat",
        "hypothesis": "Higher penalty shortens output and reduces phrase loops.",
    },
    {
        "id": "r1_temp_06",
        "knobs": {"temperature": "0.6"},
        "source": "mintlify: lower temp = more consistency",
        "hypothesis": "Steadier tone, better number fidelity, possibly shorter generation.",
    },
    {
        "id": "r1_temp_10",
        "knobs": {"temperature": "1.0"},
        "source": "mintlify: higher temp = more variation",
        "hypothesis": "More prosodic variation, worse counting WER, higher f0_std.",
    },
    {
        "id": "r1_silence_0",
        "knobs": {"silence-count": "0"},
        "source": "Turbo PyTorch appends 3 silence tokens; C++ SILENCE_COUNT=3",
        "hypothesis": "No trailing silence changes tail PCM and chosen4299 timeline only.",
    },
    {
        "id": "r1_cfm_4",
        "knobs": {"cfm-steps": "4"},
        "source": "Turbo uses n_cfm_timesteps=2; more steps may improve PQ/CE",
        "hypothesis": "S3Gen quality up (UTMOS/PQ/CE); T3 predicted_count unchanged.",
    },
]


def synth(launcher: str, text: str, knobs: dict | None = None) -> Path:
    cmd = ["python", launcher]
    for k, v in sorted((knobs or {}).items()):
        cmd.extend([f"--{k}", str(v)])
    cmd.append(text)
    out = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
    for line in reversed((out.stdout or "").splitlines()):
        p = Path(line.strip())
        if p.suffix.lower() == ".wav" and p.is_file():
            return p
    raise RuntimeError((out.stdout or "") + (out.stderr or ""))


def read_dump(family: str) -> dict:
    txt = MODELS / f"{family}_t3_dump.txt"
    csv = MODELS / f"{family}_sample_dump.csv"
    d = {}
    for raw in txt.read_text(encoding="ascii", errors="replace").splitlines():
        for k in ("predicted_count", "dropped_count", "eos", "n_ctx", "cond_prompt_len"):
            if raw.startswith(k + " "):
                d[k] = int(raw.split(" ", 1)[1])
        if raw.startswith("text "):
            d["bpe_tokens"] = len(raw.split()) - 1
    sil = steps = 0
    if csv.is_file():
        with csv.open(encoding="ascii", errors="replace") as f:
            next(f, None)
            for raw in f:
                steps += 1
                cols = raw.split(",")
                if len(cols) > 1 and cols[1].strip() == "4299":
                    sil += 1
    d["steps"] = steps
    d["chosen4299"] = sil
    d["prompt_slots"] = 1 + d.get("cond_prompt_len", 0) + d.get("bpe_tokens", 0) + 1
    return d


def wav_meta(path: Path) -> dict:
    blob = path.read_bytes()
    if len(blob) <= 44:
        return {"Length": len(blob), "duration_s": 0.0, "sr": 0}
    _, _, sr, _, _, _ = struct.unpack_from("<HHIIHH", blob, 20)
    ns = (len(blob) - 44) // 2
    return {"Length": len(blob), "duration_s": round(ns / sr, 2), "sr": sr}


def count_coverage(hyp: str, need: list[int] | None) -> dict:
    if not need:
        return {"count_ok": None, "count_missing": []}
    h = hyp.lower()
    h = re.sub(r"[^a-z0-9 ]+", " ", h)
    h = re.sub(r"\s+", " ", h).strip()
    missing = []
    for n in need:
        if str(n) not in h.split():
            missing.append(n)
    return {"count_ok": not missing, "count_missing": missing}


class Scorer:
    def __init__(self):
        import librosa
        import numpy as np
        import parselmouth
        import soundfile as sf
        import torch
        from audiobox_aesthetics.infer import initialize_predictor
        from faster_whisper import WhisperModel
        from funasr import AutoModel
        from parselmouth.praat import call
        from silero_vad import get_speech_timestamps, load_silero_vad
        from utmos_pytorch import UTMOSScoreTorch

        self.librosa = librosa
        self.np = np
        self.sf = sf
        self.torch = torch
        self.parselmouth = parselmouth
        self.call = call
        self.get_speech_timestamps = get_speech_timestamps
        self.device = "cpu"
        self.asr = WhisperModel("medium.en", device=self.device, compute_type="int8")
        self.vad = load_silero_vad()
        self.utmos = UTMOSScoreTorch(device=self.device)
        self.aes = initialize_predictor()
        self.emo = AutoModel(model="iic/emotion2vec_plus_base")
        ref_pcm, ref_sr = sf.read(ROOT / "reference.wav", dtype="float32")
        if ref_pcm.ndim > 1:
            ref_pcm = ref_pcm.mean(axis=1)
        ref16 = librosa.resample(ref_pcm, orig_sr=ref_sr, target_sr=16000)
        tmp = ROOT / "eval_out" / "_knob_ref16.wav"
        sf.write(tmp, ref16, 16000)
        self.ref_feats = np.asarray(
            self.emo.generate(str(tmp), granularity="utterance", extract_embedding=True)[0]["feats"],
            dtype=np.float64,
        )
        self.ref_pcm = ref_pcm
        self.ref_sr = ref_sr

    def score(self, wav: Path, count_need: list[int] | None) -> dict:
        pcm, sr = self.sf.read(wav, dtype="float32")
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1)
        pcm16 = self.librosa.resample(pcm, orig_sr=sr, target_sr=16000)
        ts = self.get_speech_timestamps(self.torch.from_numpy(pcm16), self.vad, sampling_rate=16000)
        pauses = []
        for a, b in zip(ts, ts[1:]):
            gap = (b["start"] - a["end"]) / 16000.0
            if gap >= 0.12:
                pauses.append(round(gap, 3))
        snd = self.parselmouth.Sound(str(wav))
        pitch = snd.to_pitch(pitch_floor=75, pitch_ceiling=400)
        vals = pitch.selected_array["frequency"]
        voiced = vals[vals > 0]
        pp = self.call(snd, "To PointProcess (periodic, cc)", 75, 400)
        jitter = float(self.call(pp, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3))
        shimmer = float(self.call([snd, pp], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6))
        hnr = float(self.call(snd.to_harmonicity_cc(), "Get mean", 0, 0))
        wav16t = self.torch.from_numpy(pcm16).float().unsqueeze(0)
        utmos_s = float(self.utmos.score(wav16t.to(self.device))[0])
        aes_s = self.aes.forward([{"path": self.torch.from_numpy(pcm).float().unsqueeze(0), "sample_rate": sr}])[0]
        tmp = ROOT / "eval_out" / "_knob_tmp16.wav"
        self.sf.write(tmp, pcm16, 16000)
        feats = self.np.asarray(
            self.emo.generate(str(tmp), granularity="utterance", extract_embedding=True)[0]["feats"],
            dtype=self.np.float64,
        )
        emo_cos = float(self.np.dot(self.ref_feats, feats) / (self.np.linalg.norm(self.ref_feats) * self.np.linalg.norm(feats)))
        ma = self.librosa.feature.mfcc(y=pcm, sr=sr, n_mfcc=20).mean(axis=1)
        mb = self.librosa.feature.mfcc(y=self.ref_pcm, sr=self.ref_sr, n_mfcc=20).mean(axis=1)
        mfcc_cos = float(self.np.dot(ma, mb) / (self.np.linalg.norm(ma) * self.np.linalg.norm(mb)))
        segs, _ = self.asr.transcribe(str(wav), word_timestamps=False, vad_filter=False)
        hyp = " ".join(s.text for s in segs).strip()
        cov = count_coverage(hyp, count_need)
        speech_rate = len(hyp.split()) / max(wav_meta(wav)["duration_s"], 0.01)
        return {
            "asr_hyp": hyp[:400],
            "count_coverage": cov,
            "vad_n_pause": len(pauses),
            "vad_mean_pause_s": round(float(sum(pauses) / len(pauses)), 3) if pauses else 0.0,
            "f0_mean": float(self.np.mean(voiced)) if voiced.size else 0.0,
            "f0_std": float(self.np.std(voiced)) if voiced.size else 0.0,
            "f0_voiced_frac": float(voiced.size / max(vals.size, 1)),
            "jitter": jitter,
            "shimmer": shimmer,
            "hnr": hnr,
            "utmos": utmos_s,
            "audiobox_pq": float(aes_s["PQ"]),
            "audiobox_ce": float(aes_s["CE"]),
            "emotion2vec_cosine": emo_cos,
            "speaker_mfcc_cosine": mfcc_cos,
            "words_per_sec": round(speech_rate, 2),
        }


def classify(dump: dict, meta: dict) -> str:
    if dump.get("predicted_count", 0) <= 20 and dump.get("eos") == 1:
        return "length_cliff"
    if meta["duration_s"] >= 25 and dump.get("predicted_count", 0) >= 500:
        return "full_generation"
    if meta["duration_s"] >= 5 and dump.get("predicted_count", 0) < 200:
        return "early_stop_or_content"
    if meta["duration_s"] < 3:
        return "truncated"
    return "partial"


def run_round(round_id: str, profiles: list[dict], families: tuple[tuple[str, str], ...], texts: dict, scorer: Scorer) -> list[dict]:
    rows = []
    for fam, launcher in families:
        for text_id, (text, count_need) in texts.items():
            for prof in profiles:
                wav = synth(launcher, text, prof["knobs"])
                dump = read_dump(fam)
                meta = wav_meta(wav)
                DUMP_DIR.mkdir(parents=True, exist_ok=True)
                stem = wav.stem
                shutil.copy2(MODELS / f"{fam}_t3_dump.txt", DUMP_DIR / f"{stem}_{fam}_t3_dump.txt")
                shutil.copy2(MODELS / f"{fam}_sample_dump.csv", DUMP_DIR / f"{stem}_{fam}_sample_dump.csv")
                metrics = scorer.score(wav, count_need)
                row = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "round": round_id,
                    "profile_id": prof["id"],
                    "knobs": prof["knobs"],
                    "hypothesis": prof.get("hypothesis", ""),
                    "source": prof.get("source", ""),
                    "family": fam,
                    "text_id": text_id,
                    "bpe_tokens": dump.get("bpe_tokens"),
                    "prompt_slots": dump.get("prompt_slots"),
                    "predicted_count": dump.get("predicted_count"),
                    "eos": dump.get("eos"),
                    "chosen4299": dump.get("chosen4299"),
                    "failure_class": classify(dump, meta),
                    "duration_s": meta["duration_s"],
                    "Length": meta["Length"],
                    "wav": str(wav),
                    **metrics,
                }
                rows.append(row)
                with REPORT.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                print(
                    round_id,
                    fam,
                    prof["id"],
                    text_id,
                    "pred",
                    row["predicted_count"],
                    "dur",
                    row["duration_s"],
                    "utmos",
                    round(row["utmos"], 2),
                    row["failure_class"],
                )
    return rows


def plan_round2(r1_rows: list[dict]) -> list[dict]:
    """Build round-2 profiles from round-1 deltas vs header on count_10_27 + prose_130."""
    profiles = []
    base = {}
    for fam in ("nano", "turbo"):
        for tid in ("count_10_27", "prose_130"):
            b = next(r for r in r1_rows if r["profile_id"] == "r1_header" and r["family"] == fam and r["text_id"] == tid)
            base[(fam, tid)] = b

    def delta(profile_id, key, fam, tid):
        r = next(x for x in r1_rows if x["profile_id"] == profile_id and x["family"] == fam and x["text_id"] == tid)
        b = base[(fam, tid)]
        return r[key] - b[key]

    # repeat_1.0 effect on length
    repeat_pred_nano = delta("r1_repeat_1", "predicted_count", "nano", "count_10_27")
    repeat_pred_turbo = delta("r1_repeat_1", "predicted_count", "turbo", "count_10_27")
    if repeat_pred_nano > 0 or repeat_pred_turbo > 0:
        profiles.append(
            {
                "id": "r2_repeat1_temp06",
                "knobs": {"repeat-penalty": "1.0", "temperature": "0.6"},
                "hypothesis": f"Round1 repeat=1.0 raised count pred (+nano {repeat_pred_nano}, +turbo {repeat_pred_turbo}); add low temp for number fidelity.",
                "source": "round1 confirmed partial",
            }
        )
    else:
        profiles.append(
            {
                "id": "r2_repeat1_top_p1",
                "knobs": {"repeat-penalty": "1.0", "top-p": "1.0"},
                "hypothesis": "repeat=1.0 did not extend count run; combine with PyTorch standard top_p=1.0.",
                "source": "round1 falsified repeat-only length hypothesis",
            }
        )

    repeat15_dur = (delta("r1_repeat_15", "duration_s", "nano", "prose_130") + delta("r1_repeat_15", "duration_s", "turbo", "prose_130")) / 2
    profiles.append(
        {
            "id": "r2_repeat15_temp06",
            "knobs": {"repeat-penalty": "1.5", "temperature": "0.6"},
            "hypothesis": f"repeat=1.5 changed prose duration avg {repeat15_dur:+.2f}s; test with low temp for cleaner lists.",
            "source": "round1 repeat_penalty=1.5 observation",
        }
    )

    cfm_utmos_nano = delta("r1_cfm_4", "utmos", "nano", "prose_130")
    profiles.append(
        {
            "id": "r2_cfm4_repeat1",
            "knobs": {"cfm-steps": "4", "repeat-penalty": "1.0"},
            "hypothesis": f"cfm_steps=4 shifted nano UTMOS {cfm_utmos_nano:+.3f}; pair with repeat=1.0 for quality+length.",
            "source": "round1 cfm_steps observation",
        }
    )

    sil4299_nano = delta("r1_silence_0", "chosen4299", "nano", "count_10_27")
    profiles.append(
        {
            "id": "r2_silence0_repeat1",
            "knobs": {"silence-count": "0", "repeat-penalty": "1.0"},
            "hypothesis": f"silence_count=0 changed chosen4299 by {sil4299_nano}; combine with repeat=1.0 on counting text.",
            "source": "round1 silence_count observation",
        }
    )

    top_p_pred = delta("r1_top_p_1", "predicted_count", "nano", "prose_130")
    profiles.append(
        {
            "id": "r2_top_p1_temp08",
            "knobs": {"top-p": "1.0", "temperature": "0.8"},
            "hypothesis": f"top_p=1.0 on prose changed nano pred {top_p_pred:+d}; confirm with PyTorch-standard nucleus on flowing text.",
            "source": "round1 top_p observation on prose_130",
        }
    )

    return profiles


def summarize(all_rows: list[dict], r2_profiles: list[dict]):
    by_profile = {}
    for r in all_rows:
        key = (r["family"], r["text_id"], r["profile_id"])
        by_profile[key] = r

    confirmations = []
    for prof in ROUND1:
        if prof["id"] == "r1_header":
            continue
        pid = prof["id"]
        obs = []
        for fam in ("nano", "turbo"):
            for tid in ("count_10_27", "prose_130"):
                b = by_profile.get((fam, tid, "r1_header"))
                t = by_profile.get((fam, tid, pid))
                if not b or not t:
                    continue
                obs.append(
                    {
                        "family": fam,
                        "text_id": tid,
                        "delta_predicted": t["predicted_count"] - b["predicted_count"],
                        "delta_duration_s": round(t["duration_s"] - b["duration_s"], 2),
                        "delta_utmos": round(t["utmos"] - b["utmos"], 3),
                        "delta_chosen4299": t["chosen4299"] - b["chosen4299"],
                        "delta_f0_std": round(t["f0_std"] - b["f0_std"], 2),
                        "count_ok": t["count_coverage"].get("count_ok"),
                    }
                )
        confirmations.append({"profile_id": pid, "knobs": prof["knobs"], "hypothesis": prof["hypothesis"], "observations": obs})

    summary = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "literature_defaults": {
            "pytorch_turbo": {"temperature": 0.8, "top_k": 1000, "top_p": 0.95, "repetition_penalty": 1.2, "cfm_steps": 2},
            "pytorch_standard": {"temperature": 0.8, "top_p": 1.0, "min_p": 0.05, "repetition_penalty": 1.2, "cfg_weight": 0.5},
            "cpp_nano_turbo_headers": {"temperature": 0.8, "top_k": 1000, "top_p": 0.95, "repeat_penalty": 1.2, "silence_count": 3, "cfm_steps": 2},
            "legal_cli_knobs": list(
                {
                    "repeat-penalty",
                    "temperature",
                    "top-k",
                    "top-p",
                    "repeat-last-n",
                    "seed",
                    "n-predict",
                    "cfm-steps",
                    "silence-token",
                    "silence-count",
                }
            ),
            "not_wired_nano_turbo": ["min-p", "cfg-weight", "cfm-cfg", "exaggeration"],
        },
        "round2_profiles": r2_profiles,
        "round1_hypothesis_check": confirmations,
        "rows": all_rows,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    COMPARE.write_text(json.dumps({"baseline_profile": "r1_header", "confirmations": confirmations}, indent=2), encoding="utf-8")
    print(SUMMARY)
    print(COMPARE)


def kill_servers():
    from tts_common import kill

    for pid_name in ("server.pid", "turbo.pid", "v3.pid"):
        kill(MODELS / pid_name)


def load_rows() -> list[dict]:
    if not REPORT.is_file():
        return []
    return [json.loads(ln) for ln in REPORT.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main():
    existing = load_rows()
    if existing:
        print(f"resume: {len(existing)} rows in {REPORT.name}")
    else:
        REPORT.write_text("", encoding="utf-8")
    kill_servers()
    families = (("nano", "tts_nano.py"), ("turbo", "tts_turbo.py"))
    scorer = Scorer()
    r1_texts = {k: TEXTS[k] for k in ("count_10_27", "prose_130")}
    r1_done = {r["round"] for r in existing}
    if "round1" in r1_done:
        r1_rows = [r for r in existing if r["round"] == "round1"]
        print(f"skip round1 ({len(r1_rows)} rows)")
    else:
        r1_rows = run_round("round1", ROUND1, families, r1_texts, scorer)
    r2_profiles = plan_round2(r1_rows)
    r2_texts = TEXTS
    if "round2" in r1_done:
        r2_rows = [r for r in existing if r["round"] == "round2"]
        print(f"skip round2 ({len(r2_rows)} rows)")
    else:
        r2_rows = run_round("round2", r2_profiles, families, r2_texts, scorer)
    summarize(r1_rows + r2_rows, r2_profiles)


if __name__ == "__main__":
    main()
