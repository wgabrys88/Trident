"""Structure vs size experiments for nano/turbo. Appends eval_out/experiment_report.jsonl."""
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
REPORT = ROOT / "eval_out" / "experiment_report.jsonl"
SUMMARY = ROOT / "eval_out" / "experiment_summary.json"
DUMP_DIR = ROOT / "eval_out" / "dumps"

# How size is counted in this stack (from C++ dump + t3_nano.cpp):
#   bpe_tokens     = GPT-2 BPE ids in dump "text" line (input text only)
#   cond_prompt_len = baked speech cond tokens in GGUF (375 here)
#   prompt_slots   = 1 + cond_prompt_len + bpe_tokens + 1  (start + cond + text + start_speech)
#   n_ctx          = 8196 KV slots (hard throw if prompt_slots > n_ctx)
#   predicted_count = speech tokens sampled before stop_speech or cap
#   duration_s     = PCM length; ~0.04s per speech token at 24kHz (960 samples/token)

CASES = [
    (
        "count_list_63bpe_ref",
        "numbered",
        "And now we will be counting from ten to twenty-seven: ten, eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen, eighteen, nineteen, twenty, twenty-one, twenty-two, twenty-three, twenty-four, twenty-five, twenty-six, twenty-seven.",
    ),
    (
        "prose_matched_chars",
        "flowing",
        "And now we will walk slowly through the quiet garden beside the old stone wall, noticing how the morning light catches each leaf and how the winding path bends toward the wooden gate that opens onto the river bank where boats rest in the soft current.",
    ),
    (
        "numbered_short_~130bpe",
        "numbered",
        "Step one: confirm the route. Step two: note seven oh five. Step three: read SW1A 2AA. Step four: count ten, eleven, twelve. Step five: pass Baker Street. Step six: check thirteen hundred. Step seven: say room one oh one. Step eight: repeat the code. Step nine: verify zero zero zero zero. Step ten: finish at Downing Street. Step eleven: call the courier. Step twelve: log Pennsylvania Avenue.",
    ),
    (
        "prose_single_~130bpe",
        "flowing",
        "Please follow the delivery route as one continuous record while you pass Baker Street in the morning light, continue along Pennsylvania Avenue toward the river district, pause briefly near the old post office, keep speaking without breaking the thread of the journey, and describe each landmark in order until you reach the final gate where the courier signs the register and the walk ends at Downing Street before noon.",
    ),
    (
        "numbered_short_~300bpe",
        "numbered",
        "Item one: open the map. Item two: read NW1 6XE. Item three: count one, two, three, four, five. Item four: turn at Fifth Avenue. Item five: note twelve hundred. Item six: say EC1A 1BB. Item seven: count six, seven, eight, nine, ten. Item eight: pass Infinite Loop. Item nine: check twenty-three fifty-nine. Item ten: read nine zero two one zero. Item eleven: count eleven through twenty. Item twelve: confirm Marszalkowska. Item thirteen: time zero zero colon zero one. Item fourteen: count twenty-one to thirty. Item fifteen: pass Wall Street. Item sixteen: read G1 1AA. Item seventeen: count thirty-one to forty. Item eighteen: note Princes Street. Item nineteen: say Wright not Right. Item twenty: finish room one oh one.",
    ),
    (
        "prose_single_~300bpe",
        "flowing",
        "The courier describes the entire delivery route in one unbroken narrative, moving from Baker Street through Pennsylvania Avenue and Fifth Avenue without pausing for lists, carrying the listener past Infinite Loop and Wall Street while the clock moves from morning toward afternoon, mentioning Marszalkowska and Princes Street as natural landmarks in the story, distinguishing Wright Road from Right Road in passing, and ending at Downing Street with the science desk order, serial AB dash zero zero forty-two, the constant three point one four one five nine, and room one oh one, all woven into the same flowing sentence so the speech feels like a single thirty-second paragraph rather than a checklist of numbered commands.",
    ),
]


def synth(launcher: str, text: str) -> Path:
    out = subprocess.run(
        ["python", launcher, text],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
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
        if raw.startswith("predicted "):
            pred = [int(x) for x in raw.split()[1:]]
            d["last_token"] = pred[-1] if pred else None
    sil = 0
    n = 0
    if csv.is_file():
        with csv.open(encoding="ascii", errors="replace") as f:
            next(f, None)
            for raw in f:
                n += 1
                cols = raw.split(",")
                if len(cols) > 1 and cols[1].strip() == "4299":
                    sil += 1
    d["steps"] = n
    d["chosen4299"] = sil
    d["prompt_slots"] = 1 + d.get("cond_prompt_len", 0) + d.get("bpe_tokens", 0) + 1
    return d


def wav_meta(path: Path) -> dict:
    blob = path.read_bytes()
    if len(blob) <= 44:
        return {"Length": len(blob), "duration_s": 0.0}
    _, _, sr, _, _, _ = struct.unpack_from("<HHIIHH", blob, 20)
    ns = (len(blob) - 44) // 2
    return {"Length": len(blob), "duration_s": round(ns / sr, 2), "sr": sr}


def asr_quick(path: Path) -> dict:
    from faster_whisper import WhisperModel

    model = WhisperModel("medium.en", device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=False)
    words = []
    for s in segs:
        if s.words:
            for w in s.words:
                words.append({"w": w.word.strip(), "t": round(float(w.start), 2)})
        elif s.text.strip():
            words.append({"w": s.text.strip(), "t": round(float(s.start), 2)})
    hyp = " ".join(x["w"] for x in words)
    # repetition: first 8 words reappear after t>=10
    head = " ".join(x["w"] for x in words[:8]).lower()
    tail_words = [x for x in words if x["t"] >= 10.0]
    repeat_at = next((x["t"] for x in tail_words if x["w"].lower() in head.split()), None)
    return {"hyp": hyp, "repeat_after_10s": repeat_at, "word_count": len(words)}


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


def run_family(family: str, launcher: str):
    for case_id, structure, text in CASES:
        wav = synth(launcher, text)
        dump = read_dump(family)
        meta = wav_meta(wav)
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        stem = wav.stem
        shutil.copy2(MODELS / f"{family}_t3_dump.txt", DUMP_DIR / f"{stem}_{family}_t3_dump.txt")
        shutil.copy2(MODELS / f"{family}_sample_dump.csv", DUMP_DIR / f"{stem}_{family}_sample_dump.csv")
        asr = asr_quick(wav)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "family": family,
            "case_id": case_id,
            "structure": structure,
            "text_chars": len(text),
            "bpe_tokens": dump.get("bpe_tokens"),
            "prompt_slots": dump.get("prompt_slots"),
            "predicted_count": dump.get("predicted_count"),
            "eos": dump.get("eos"),
            "duration_s": meta["duration_s"],
            "Length": meta["Length"],
            "chosen4299": dump.get("chosen4299"),
            "failure_class": classify(dump, meta),
            "asr_hyp": asr["hyp"][:500],
            "asr_repeat_after_10s": asr["repeat_after_10s"],
            "wav": str(wav),
            "dump": str(DUMP_DIR / f"{stem}_{family}_t3_dump.txt"),
        }
        with REPORT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(
            family,
            case_id,
            structure,
            "bpe",
            row["bpe_tokens"],
            "pred",
            row["predicted_count"],
            "dur",
            row["duration_s"],
            row["failure_class"],
        )


def summarize():
    rows = [json.loads(ln) for ln in REPORT.read_text(encoding="utf-8").splitlines() if ln.strip()]
    session = [r for r in rows if r.get("ts", "").startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d"))]
    if not session:
        session = rows[-24:]

    def by_family(fam):
        return [r for r in session if r["family"] == fam]

    patterns = []
    for fam in ("nano", "turbo"):
        fam_rows = by_family(fam)
        cliffs = [r for r in fam_rows if r["failure_class"] == "length_cliff"]
        ok = [r for r in fam_rows if r["failure_class"] in ("full_generation", "partial", "early_stop_or_content")]
        max_ok_bpe = max((r["bpe_tokens"] for r in ok), default=0)
        min_cliff_bpe = min((r["bpe_tokens"] for r in cliffs), default=None)
        patterns.append({"family": fam, "max_ok_bpe": max_ok_bpe, "min_cliff_bpe": min_cliff_bpe})

    # structure pairs at similar bpe
    pairs = []
    for fam in ("nano", "turbo"):
        fam_rows = by_family(fam)
        for numbered in [r for r in fam_rows if r["structure"] == "numbered"]:
            prose = next((r for r in fam_rows if r["structure"] == "flowing" and abs(r["bpe_tokens"] - numbered["bpe_tokens"]) <= 40 and numbered["case_id"].split("_")[0] == r["case_id"].split("_")[0]), None)
            if prose is None:
                # match by case prefix like numbered_short vs prose_single
                key = numbered["case_id"].replace("numbered", "").replace("short", "")
                prose = next((r for r in fam_rows if r["structure"] == "flowing" and key[:10] in r["case_id"]), None)
            if prose:
                pairs.append(
                    {
                        "family": fam,
                        "numbered": {k: numbered[k] for k in ("case_id", "bpe_tokens", "duration_s", "predicted_count", "failure_class")},
                        "flowing": {k: prose[k] for k in ("case_id", "bpe_tokens", "duration_s", "predicted_count", "failure_class")},
                        "duration_delta_s": round(prose["duration_s"] - numbered["duration_s"], 2),
                        "bpe_delta": prose["bpe_tokens"] - numbered["bpe_tokens"],
                    }
                )

    summary = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "size_counted_as": {
            "primary": "GPT-2 BPE tokens on input text (dump text line token count)",
            "secondary": "prompt_slots = 1 + cond_prompt_len + bpe + 1",
            "hard_ctx": "n_ctx=8196; prompt_slots must be <= n_ctx or C++ throws",
            "output_cap": "N_PREDICT=1000 speech tokens (~40s audio at 960 samples/token)",
            "community_guidance": "~300 chars per chunk (resemble-ai/chatterbox#181); max_new_tokens=1000 in reference PyTorch",
        },
        "thresholds_from_session": patterns,
        "structure_pairs": pairs,
        "rows": session,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(SUMMARY)


def main():
    REPORT.write_text("", encoding="utf-8")
    run_family("nano", "tts_nano.py")
    run_family("turbo", "tts_turbo.py")
    summarize()


if __name__ == "__main__":
    main()
