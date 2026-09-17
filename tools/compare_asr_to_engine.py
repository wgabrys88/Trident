"""Word-level diff: asr_parakeet.json transcript vs engine text_prepared."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def norm_words(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^\w\s']+", " ", text)
    return [w for w in text.split() if w]


def prepared_from_engine(run_dir: Path) -> str | None:
    for path in run_dir.glob("*.engine.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "text_prepared":
                return ev.get("text")
    return None


def lcs_diff(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    # Simple greedy alignment for reporting (not full Myers)
    out = []
    i = j = 0
    while i < len(a) or j < len(b):
        if i < len(a) and j < len(b) and a[i] == b[j]:
            out.append(("=", a[i]))
            i += 1
            j += 1
        elif j < len(b) and (i >= len(a) or (i + 1 < len(a) and a[i + 1] == b[j])):
            out.append(("+asr", b[j]))
            j += 1
        elif i < len(a):
            out.append(("-ref", a[i]))
            i += 1
        else:
            out.append(("+asr", b[j]))
            j += 1
    return out


def analyze_run(run_dir: Path) -> dict:
    asr_path = run_dir / "asr_parakeet.json"
    if not asr_path.is_file():
        return {"run_id": run_dir.name, "error": "missing asr_parakeet.json"}
    asr = json.loads(asr_path.read_text(encoding="utf-8"))
    ref = prepared_from_engine(run_dir)
    if not ref:
        return {"run_id": run_dir.name, "error": "missing text_prepared in engine jsonl"}
    ref_w = norm_words(ref)
    hyp_w = norm_words(asr.get("transcript") or "")
    diff = lcs_diff(ref_w, hyp_w)
    subs = sum(1 for k, _ in diff if k != "=")
    return {
        "run_id": run_dir.name,
        "ref_words": len(ref_w),
        "asr_words": len(hyp_w),
        "mismatch_tokens": subs,
        "mismatch_ratio": round(subs / max(len(ref_w), 1), 4),
        "diff_sample": diff[:40],
        "lang_hint": asr.get("lang_hint"),
    }


def main() -> int:
    roots = [ROOT / "models" / "runs", ROOT / "models" / "runs-PASCAL"]
    rows = []
    for root in roots:
        if not root.is_dir():
            continue
        for run_dir in sorted(root.iterdir()):
            if run_dir.is_dir() and (run_dir / "asr_parakeet.json").is_file():
                rows.append(analyze_run(run_dir))
    out = ROOT / "working_report_asr_diff.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {len(rows)} comparisons to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
