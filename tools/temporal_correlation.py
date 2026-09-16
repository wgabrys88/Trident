"""Temporal ASR error analysis: when mismatches occur and cross-run correlation."""
import json
import re
import wave
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 24000


def wav_duration_s(wav: Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def norm_words(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s']+", " ", text)
    return [x for x in text.split() if x]


def prepared_text(run_dir: Path) -> str | None:
    for path in run_dir.glob("*.engine.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "text_prepared":
                return ev.get("text")
    return None


def preparation_edits(run_dir: Path) -> list[dict]:
    for path in run_dir.glob("*.engine.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "text_prepared":
                return ev.get("edits") or []
    return []


def unit_timeline(run_dir: Path) -> list[dict]:
    units = []
    for path in run_dir.glob("*.engine.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "unit_complete":
                begin = ev.get("output_begin_sample", 0)
                end = ev.get("output_end_sample", 0)
                units.append(
                    {
                        "index": ev.get("index"),
                        "t_begin_s": begin / SAMPLE_RATE,
                        "t_end_s": end / SAMPLE_RATE,
                        "cropped_samples": ev.get("cropped_samples"),
                        "onset_zero_samples": ev.get("onset_zero_samples"),
                    }
                )
    return units


def char_to_time(prepared: str, char_index: int, duration_s: float) -> float:
    if not prepared:
        return 0.0
    frac = min(max(char_index / max(len(prepared), 1), 0.0), 1.0)
    return frac * duration_s


def asr_timed_words(asr: dict) -> list[dict]:
    words = asr.get("words") or []
    out = []
    for w in words:
        if not isinstance(w, dict):
            continue
        raw = w.get("raw") if isinstance(w.get("raw"), dict) else w
        text = w.get("word") or raw.get("w") or raw.get("text") or ""
        if not str(text).strip():
            continue
        start = w.get("start_s")
        if start is None:
            start = raw.get("start")
        end = w.get("end_s")
        if end is None:
            end = raw.get("end")
        normed = norm_words(str(text))
        out.append(
            {
                "word": normed[0] if normed else str(text).lower(),
                "start_s": float(start) if start is not None else None,
                "end_s": float(end) if end is not None else None,
                "confidence": w.get("confidence") if w.get("confidence") is not None else raw.get("conf"),
            }
        )
    return out


def word_align_errors(ref_words: list[str], hyp_words: list[str]) -> list[dict]:
    sm = SequenceMatcher(a=ref_words, b=hyp_words)
    errors = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            for k, wi in enumerate(range(i1, i2)):
                hj = j1 + k if j1 + k < j2 else None
                errors.append(
                    {
                        "kind": "replace",
                        "ref_index": wi,
                        "hyp_index": hj,
                        "ref_word": ref_words[wi],
                        "asr_word": hyp_words[hj] if hj is not None else None,
                    }
                )
        elif tag == "delete":
            for wi in range(i1, i2):
                errors.append({"kind": "omit", "ref_index": wi, "hyp_index": None, "ref_word": ref_words[wi]})
        elif tag == "insert":
            for hj in range(j1, j2):
                errors.append(
                    {
                        "kind": "insert",
                        "ref_index": i1,
                        "hyp_index": hj,
                        "asr_word": hyp_words[hj],
                    }
                )
    return errors


def word_char_offset(prepared: str, words: list[str], word_index: int) -> int:
    pos = 0
    for i, w in enumerate(words):
        idx = prepared.lower().find(w, pos)
        if idx < 0:
            return int(len(prepared) * i / max(len(words), 1))
        if i == word_index:
            return idx
        pos = idx + len(w)
    return len(prepared) - 1


def analyze_run(run_dir: Path) -> dict | None:
    asr_path = run_dir / "asr_parakeet.json"
    if not asr_path.is_file():
        return None
    asr = json.loads(asr_path.read_text(encoding="utf-8"))
    if asr.get("error"):
        return {"run_id": run_dir.name, "error": asr["error"]}
    wavs = list(run_dir.glob("*.wav"))
    if not wavs:
        return None
    prepared = prepared_text(run_dir)
    if not prepared:
        return {"run_id": run_dir.name, "error": "no text_prepared"}
    ref_w = norm_words(prepared)
    hyp_w = norm_words(asr.get("transcript") or "")
    dur = wav_duration_s(wavs[0])
    errs = word_align_errors(ref_w, hyp_w)
    timed_words = asr_timed_words(asr)
    stream_events = asr.get("stream_events") or []
    timed = []
    for e in errs:
        wi = e.get("ref_index", 0)
        ci = word_char_offset(prepared, ref_w, wi)
        t_est = char_to_time(prepared, ci, dur)
        e2 = dict(e)
        e2["est_time_s"] = round(t_est, 3)
        e2["est_rel"] = round(t_est / max(dur, 1e-9), 4)
        hi = e.get("hyp_index")
        tw = timed_words[hi] if hi is not None and hi < len(timed_words) else None
        if tw and tw.get("start_s") is not None:
            e2["asr_time_s"] = tw["start_s"]
            e2["asr_time_end_s"] = tw.get("end_s")
            e2["time_source"] = "nemotron_json_word"
            e2["time_s"] = tw["start_s"]
            e2["rel"] = round(float(tw["start_s"]) / max(dur, 1e-9), 4)
        else:
            e2["time_source"] = "proportional_text"
            e2["time_s"] = e2["est_time_s"]
            e2["rel"] = e2["est_rel"]
        timed.append(e2)
    prov = next(run_dir.glob("*.provenance.json"), None)
    variant = prov.name.split(".")[0] if prov else None
    argv = []
    if prov:
        argv = json.loads(prov.read_text(encoding="utf-8")).get("argv") or []
    lang = argv[-1] if argv and argv[0] == "tts_v3.py" else None
    input_sha = None
    if prov:
        data = json.loads(prov.read_text(encoding="utf-8"))
        req = data.get("request") or {}
        input_sha = req.get("input_sha256") or data.get("input_sha256")
    return {
        "run_id": run_dir.name,
        "variant": variant,
        "language": lang,
        "input_sha256": input_sha,
        "duration_s": round(dur, 3),
        "ref_words": len(ref_w),
        "asr_words": len(hyp_w),
        "errors": timed,
        "asr_word_timeline": timed_words,
        "asr_stream_events": stream_events,
        "asr_stream_word_timestamps": asr.get("stream_word_timestamps") or [],
        "units": unit_timeline(run_dir),
        "edits": preparation_edits(run_dir),
    }


def correlate(runs: list[dict]) -> dict:
    """Find relative-time buckets where multiple runs share error kinds."""
    rel_bucket = 0.05
    abs_bucket_s = 2.0
    by_input: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        if r.get("input_sha256"):
            by_input[r["input_sha256"]].append(r)
    clusters = []
    for input_sha, group in by_input.items():
        if len(group) < 2:
            continue
        rel_buckets: dict[int, list[dict]] = defaultdict(list)
        abs_buckets: dict[int, list[dict]] = defaultdict(list)
        for r in group:
            for e in r.get("errors") or []:
                rel = e.get("rel") if e.get("rel") is not None else e.get("est_rel") or 0
                t_s = e.get("time_s") if e.get("time_s") is not None else e.get("est_time_s") or 0
                rel_b = int(float(rel) / rel_bucket)
                abs_b = int(float(t_s) / abs_bucket_s)
                payload = {
                    "run_id": r["run_id"],
                    "variant": r.get("variant"),
                    "host": r.get("host"),
                    **e,
                }
                rel_buckets[rel_b].append(payload)
                abs_buckets[abs_b].append(payload)
        hot_rel = []
        for b, items in rel_buckets.items():
            runs_here = {x["run_id"] for x in items}
            if len(runs_here) >= 2:
                hot_rel.append(
                    {
                        "bucket_rel_start": round(b * rel_bucket, 3),
                        "bucket_rel_end": round((b + 1) * rel_bucket, 3),
                        "run_count": len(runs_here),
                        "events": items[:12],
                    }
                )
        hot_abs = []
        for b, items in abs_buckets.items():
            runs_here = {x["run_id"] for x in items}
            if len(runs_here) >= 2:
                hot_abs.append(
                    {
                        "bucket_time_start_s": round(b * abs_bucket_s, 3),
                        "bucket_time_end_s": round((b + 1) * abs_bucket_s, 3),
                        "run_count": len(runs_here),
                        "events": items[:12],
                    }
                )
        if hot_rel or hot_abs:
            clusters.append(
                {
                    "input_sha256": input_sha,
                    "hot_buckets_relative": sorted(hot_rel, key=lambda x: -x["run_count"]),
                    "hot_buckets_absolute_s": sorted(hot_abs, key=lambda x: -x["run_count"]),
                }
            )
    return {
        "bucket_width_rel": rel_bucket,
        "bucket_width_s": abs_bucket_s,
        "input_clusters": clusters,
    }


def main() -> int:
    rows = []
    for sub, host in (("runs", "IrisXe"), ("runs-PASCAL", "Pascal")):
        root = ROOT / "models" / sub
        if not root.is_dir():
            continue
        for run_dir in sorted(root.iterdir()):
            if not run_dir.is_dir():
                continue
            row = analyze_run(run_dir)
            if row:
                row["host"] = host
                rows.append(row)
    out = ROOT / "working_report_temporal.json"
    payload = {"runs": rows, "correlation": correlate(rows)}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote temporal report: {len(rows)} runs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
