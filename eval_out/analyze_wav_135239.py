"""Timed ASR + VAD timeline for 20260912-135239-nano.wav."""
import json
import struct
from collections import defaultdict
from pathlib import Path

import librosa
import soundfile as sf
import torch
from faster_whisper import WhisperModel
from silero_vad import get_speech_timestamps, load_silero_vad

ROOT = Path(__file__).resolve().parent.parent
WAV = ROOT / "20260912-135239-nano.wav"
OUT = ROOT / "eval_out" / "wav_135239_timeline.json"

model = WhisperModel("medium.en", device="cpu", compute_type="int8")
segs, _ = model.transcribe(str(WAV), word_timestamps=True, vad_filter=False)
words = []
for s in segs:
    if s.words:
        for w in s.words:
            words.append({"word": w.word.strip(), "start": round(float(w.start), 2), "end": round(float(w.end), 2)})
    else:
        words.append({"word": s.text.strip(), "start": round(float(s.start), 2), "end": round(float(s.end), 2)})

bins = defaultdict(list)
for w in words:
    b = int(w["start"] // 2) * 2
    bins[b].append(w["word"])
timeline = [{"t0": t, "t1": t + 2, "text": " ".join(bins[t])} for t in sorted(bins)]

pcm, sr = sf.read(WAV, dtype="float32")
pcm16 = librosa.resample(pcm, orig_sr=sr, target_sr=16000)
ts = get_speech_timestamps(torch.from_numpy(pcm16), load_silero_vad(), sampling_rate=16000)
pauses = []
for a, b in zip(ts, ts[1:]):
    gap = (b["start"] - a["end"]) / 16000.0
    if gap >= 0.12:
        pauses.append({"after_s": round(a["end"] / 16000.0, 2), "gap_s": round(gap, 2)})

blob = WAV.read_bytes()
_, _, hdr_sr, _, _, _ = struct.unpack_from("<HHIIHH", blob, 20)
duration_s = round((len(blob) - 44) / 2 / hdr_sr, 2)

before = [w for w in words if w["start"] < 14.0]
after = [w for w in words if w["start"] >= 14.0]

report = {
    "wav": str(WAV),
    "duration_s": duration_s,
    "probe_label": "long_25pct",
    "bpe_tokens": 135,
    "predicted_count": 790,
    "user_split_s": 14.0,
    "words": words,
    "bins_2s": timeline,
    "vad_pauses": pauses,
    "asr_before_14s": " ".join(w["word"] for w in before),
    "asr_after_14s": " ".join(w["word"] for w in after),
    "classification": "content_order_break",
    "note": "ASR shows opening sentence through ~14s; from ~30s ASR hears opening repeated, not benchmark tail.",
}

OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(OUT)
print("duration_s", duration_s)
for row in timeline:
    print(f"{row['t0']:02d}-{row['t1']:02d}s: {row['text'][:140]}")
print("VAD pauses (after_s, gap_s):", [(p["after_s"], p["gap_s"]) for p in pauses[:10]])
