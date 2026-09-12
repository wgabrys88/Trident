"""Bisect nano/turbo input threshold via dump stats. Untracked."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tts_nano import CFG as NANO
from tts_turbo import CFG as TURBO

MODELS = ROOT / "models"
OUT = ROOT / "eval_out" / "threshold_report.json"


def bpe_count(dump_path: Path) -> int:
    for line in dump_path.read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("text "):
            return len(line.split()) - 1
    return 0


def dump_stats(name: str) -> dict:
    txt = MODELS / f"{name}_t3_dump.txt"
    keys = {}
    for raw in txt.read_text(encoding="ascii", errors="replace").splitlines():
        for k in ("predicted_count", "dropped_count", "eos", "n_ctx", "cond_prompt_len"):
            if raw.startswith(k + " "):
                keys[k] = int(raw.split(" ", 1)[1])
    pred = []
    for raw in txt.read_text(encoding="ascii", errors="replace").splitlines():
        if raw.startswith("predicted "):
            pred = [int(x) for x in raw.split()[1:]]
    keys["bpe_tokens"] = bpe_count(txt)
    keys["last_token"] = pred[-1] if pred else None
    keys["stop_speech"] = 6562
    return keys


def synth(launcher: str, text: str, knobs: list[str] | None = None) -> Path:
    cmd = ["python", launcher]
    if knobs:
        cmd.extend(knobs)
    cmd.append(text)
    out = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True)
    wav = None
    for line in reversed((out.stdout or "").splitlines()):
        p = Path(line.strip())
        if p.suffix.lower() == ".wav" and p.is_file():
            wav = p
            break
    if wav is None:
        raise RuntimeError((out.stdout or "") + (out.stderr or ""))
    return wav


def wav_meta(path: Path) -> dict:
    blob = path.read_bytes()
    length = len(blob)
    if length <= 44:
        return {"path": str(path), "Length": length, "duration_s": 0.0, "sr": 0}
    import struct

    _, _, sr, _, _, _ = struct.unpack_from("<HHIIHH", blob, 20)
    n_samples = (length - 44) // 2
    return {
        "path": str(path),
        "Length": length,
        "duration_s": float(n_samples / sr),
        "sr": int(sr),
    }


def probe_family(name: str, launcher: str, texts: list[tuple[str, str]]) -> list[dict]:
    rows = []
    for label, text in texts:
        wav = synth(launcher, text)
        dump = dump_stats(name)
        meta = wav_meta(wav)
        row = {
            "label": label,
            "text_chars": len(text),
            "bpe_tokens": dump["bpe_tokens"],
            "predicted_count": dump["predicted_count"],
            "eos": dump["eos"],
            "last_token_is_stop": dump["last_token"] == dump["stop_speech"],
            "n_ctx": dump.get("n_ctx"),
            "cond_prompt_len": dump.get("cond_prompt_len"),
            "prompt_slots": 1 + dump.get("cond_prompt_len", 0) + dump["bpe_tokens"] + 1,
            **meta,
        }
        row["usable"] = meta["Length"] > 44 and dump["predicted_count"] > 50 and meta["duration_s"] > 3.0
        rows.append(row)
        print(name, label, "bpe", row["bpe_tokens"], "pred", row["predicted_count"], "dur", round(row["duration_s"], 2), "usable", row["usable"])
    return rows


def main():
    short = "Hello."
    count = (ROOT / "eval_out" / "count_10_27.txt").read_text(encoding="utf-8").strip()
    baseline = "The courier left the package at 10 Downing Street, London SW1A 2AA this morning. Count seventeen to twenty-five: seventeen, eighteen, nineteen, twenty, twenty-one, twenty-two, twenty-three, twenty-four, twenty-five."
    long = (ROOT / "eval_out" / "benchmark_text.txt").read_text(encoding="utf-8").strip()
    # progressive slices of long benchmark by char length
    slices = []
    for pct in (25, 50, 75, 100):
        n = max(20, int(len(long) * pct / 100))
        slices.append((f"long_{pct}pct", long[:n]))

    texts = [("hello", short), ("count_10_27", count), ("short_baseline", baseline)] + slices

    report = {
        "git_note": "No chatterbox C++ commits in prior session; local untracked eval files only.",
        "knob_guidance": {
            "repeat_penalty": "1.0 disables penalty (llama.cpp default); nano header uses 1.2",
            "temperature": "0.8 default; try 0.9-1.0 for diversity on repetitive counts",
            "top_p": "0.95 default; lower only if trailing garbage",
            "n_predict": "does not override eos=1 stop_speech early exit",
        },
        "nano": probe_family("nano", "tts_nano.py", texts),
        "turbo": probe_family("turbo", "tts_turbo.py", texts),
    }

    def threshold(rows):
        usable = [r for r in rows if r["usable"]]
        fail = [r for r in rows if not r["usable"]]
        max_ok = max((r["bpe_tokens"] for r in usable), default=0)
        min_bad = min((r["bpe_tokens"] for r in fail), default=99999)
        return {"max_ok_bpe": max_ok, "min_fail_bpe": min_bad if fail else None, "threshold_between": f"{max_ok}-{min_bad}"}

    report["nano_threshold"] = threshold(report["nano"])
    report["turbo_threshold"] = threshold(report["turbo"])
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(OUT)
    print("nano", report["nano_threshold"])
    print("turbo", report["turbo_threshold"])


if __name__ == "__main__":
    main()
