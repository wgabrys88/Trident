from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

from main import PIPELINE_LOG, ROOT, jsonl

PYTHON = sys.executable
SHORT = "Trident turns written language into local speech. This short sample checks a simple synthesis request."
COUNT = "One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten."
BOUNDARY = "Ask him when you are unsure. The wave file is the product."


def run(label: str, *args: str) -> tuple[int, str]:
    cmd = [PYTHON, "-u", str(ROOT / "tts_nano.py"), *args]
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}\n{' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n", flush=True)
    return proc.returncode, proc.stdout


def native_pieces(log: str) -> dict[int, dict]:
    out = {}
    for line in log.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "tts.piece" or ("piece" in obj and "n_speech_tok" in obj):
            out[int(obj["piece"])] = obj
    return out


def synth_pieces(output: str) -> dict[int, dict]:
    out = {}
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "synth.piece":
            out[int(obj["piece"])] = obj
    return out


def verify(case: str, source: str, log_tail: str, output: str) -> list[str]:
    errors = []
    py = synth_pieces(output)
    nat = native_pieces(log_tail)
    if set(py) != set(nat):
        errors.append(f"{case}: piece set python={sorted(py)} native={sorted(nat)}")
    wav = ROOT / "tts_out.wav"
    if not wav.is_file():
        errors.append(f"{case}: missing tts_out.wav")
        return errors
    with wave.open(str(wav)) as w:
        samples = w.getnframes()
    for piece, interval in py.items():
        n = nat.get(piece, {})
        emitted = int(n.get("emitted_samples", 0))
        trimmed = int(interval.get("trimmed_leading_bytes", 0)) // 2
        got = interval["sample_end"] - interval["sample_start"]
        if emitted - trimmed != got:
            errors.append(f"{case}: piece {piece} emitted {emitted}-trim {trimmed} != python {got}")
        ratio = int(n.get("n_speech_tok", 0)) / max(1, int(n.get("n_text_tok", 1)))
        if ratio < 2:
            errors.append(f"{case}: piece {piece} collapsed ratio={ratio:.2f}")
    jsonl("validate.case", case=case, pieces=len(py), wav_samples=samples, errors=len(errors))
    return errors


def main() -> None:
    offset = PIPELINE_LOG.stat().st_size if PIPELINE_LOG.is_file() else 0
    subprocess.run([PYTHON, "-u", str(ROOT / "main.py"), "--unload"], cwd=ROOT)
    failures = []
    for case, text in (("short", SHORT), ("count", COUNT)):
        subprocess.run([PYTHON, "-u", str(ROOT / "tts_nano.py"), "--unload"], cwd=ROOT)
        code, out = run(case, "--seed", "42", "--cfm-steps", "1", "--text", text)
        tail = PIPELINE_LOG.read_text(encoding="utf-8", errors="replace")[offset:] if PIPELINE_LOG.is_file() else ""
        offset = PIPELINE_LOG.stat().st_size if PIPELINE_LOG.is_file() else 0
        if code:
            failures.append(f"{case}: exit {code}")
        failures.extend(verify(case, text, tail, out))
    subprocess.run([PYTHON, "-u", str(ROOT / "main.py"), "--unload"], cwd=ROOT)
    if failures:
        for item in failures:
            print(f"[validate] FAIL {item}", file=sys.stderr)
        raise SystemExit(1)
    print("[validate] OK")


if __name__ == "__main__":
    main()
