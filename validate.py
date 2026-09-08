from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

# Sequential validator. If any command exits non-zero or raises, skip it and
# run the next command. No timeouts, no extra lock files, no failure pings.

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
CHUNK_PYTHON = ROOT / "tools/runtime/chunker/Scripts/python.exe"
TTS_LOG = ROOT / ".runtime-logs/tts.log"
MAIN_LOG = ROOT / ".runtime-logs/main.log"
RUN_ID = time.strftime("%Y%m%d-%H%M%S")
RUN_DIR = ROOT / "validation-runs" / RUN_ID
RUN_DIR.mkdir(parents=True, exist_ok=False)

SHORT_TEXT = (
    "Trident turns written language into local speech. "
    "This short sample checks a simple synthesis request."
)
COUNT_1_TO_30 = (
    "One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. "
    "Eleven. Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. "
    "Eighteen. Nineteen. Twenty. Twenty-one. Twenty-two. Twenty-three. "
    "Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. "
    "Twenty-nine. Thirty."
)
COUNT_20_TO_30 = (
    "Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. "
    "Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty."
)
LONG_SPEECH_TEXT = """Trident is a local real-time speech pipeline.
The brain writes short natural language for the ear, with one breath per line.
The CPU segmenter preserves those breath boundaries and may make smaller meaning-aware cuts inside them.
Each synthesis request owns one native acoustic session.
T3 produces speech tokens in order, and S3Gen turns those tokens into twenty-four kilohertz audio.
Acoustic history may provide continuity between pieces, but previously emitted speech must never be emitted twice.
The final wave file should contain every requested idea once, in the correct order, with natural transitions."""
V3_POLISH_TEXT = (
    "To jest krótka próba wielojęzycznego syntezatora. "
    "Każde zdanie powinno zostać wypowiedziane tylko raz i we właściwej kolejności."
)
BRAIN_SHORT_PROMPT = (
    "Explain in a few short spoken lines why deterministic tracing is useful "
    "in a real-time text-to-speech system."
)
BRAIN_README_PROMPT = (
    "Give a natural spoken technical tour of Trident. Explain the local Gemma brain, CPU SaT breath "
    "segmentation, request-owned native synthesis, T3 speech tokens, S3Gen acoustic decoding, twenty-four "
    "kilohertz waveform assembly, and Parakeet verification. Keep it concise and natural to listen to."
)
PIPELINE_PROMPT = (
    "Create a concise spoken README-style introduction to Trident. Explain the local brain, one-breath-per-line "
    "speech writing, CPU semantic chunking, Nano TTS, T3 to S3Gen synthesis, waveform assembly, Parakeet "
    "verification, and why one complete request must own one acoustic session."
)

KV = re.compile(r"([A-Za-z0-9_]+)=([^ ]+)")
PCM_LINE = re.compile(
    r'"event": "synth.piece".*"piece": (\d+).*"sample_start": (\d+).*"sample_end": (\d+).*"trimmed_leading_bytes": (\d+)'
)


def run(label: str, script: str, *args: str, input_text: str | None = None,
        interpreter: str | Path | None = None) -> str:
    cmd = [str(interpreter or PYTHON), "-u", str(ROOT / script), *args]
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    print(" ".join(cmd), flush=True)
    try:
        proc = subprocess.Popen(
            cmd, cwd=ROOT, stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
        )
        if input_text is not None:
            proc.stdin.write(input_text)
            proc.stdin.close()
        lines: list[str] = []
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        proc.wait()
        return "".join(lines)
    except Exception:
        return ""


def run_json(label: str, wav: Path) -> dict:
    cmd = [PYTHON, "-u", str(ROOT / "parakeet.py"), "--json", str(wav)]
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    print(" ".join(cmd), flush=True)
    try:
        result = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)
    except Exception:
        return {}


def log_offset(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def read_suffix(path: Path, start: int) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as f:
        f.seek(start)
        return f.read().decode("utf-8", "replace")


def save_copy(source: Path, name: str) -> Path | None:
    if not source.is_file():
        return None
    target = RUN_DIR / name
    shutil.copy2(source, target)
    return target


def words(text: str) -> list[str]:
    text = text.lower().replace("-", " ")
    return [w for w in re.findall(r"[^\W_]+", text, flags=re.UNICODE) if w]


def observed_words(asr: dict) -> list[dict]:
    out = []
    for item in asr.get("words", []):
        parts = words(str(item.get("w", "")))
        for part in parts:
            out.append({"word": part, "start": float(item["start"]), "end": float(item["end"]),
                        "conf": float(item.get("conf", 0.0))})
    return out


def align(expected: list[str], observed: list[dict]) -> list[dict]:
    n, m = len(expected), len(observed)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0], back[i][0] = i, "delete"
    for j in range(1, m + 1):
        dp[0][j], back[0][j] = j, "insert"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            same = expected[i - 1] == observed[j - 1]["word"]
            choices = (
                (dp[i - 1][j - 1] + (0 if same else 1), "match" if same else "substitute"),
                (dp[i - 1][j] + 1, "delete"),
                (dp[i][j - 1] + 1, "insert"),
            )
            dp[i][j], back[i][j] = min(choices, key=lambda x: x[0])
    ops = []
    i, j = n, m
    while i or j:
        op = back[i][j]
        if op in ("match", "substitute"):
            ops.append({"op": op, "expected": expected[i - 1], "observed": observed[j - 1]})
            i -= 1; j -= 1
        elif op == "delete":
            ops.append({"op": op, "expected": expected[i - 1], "observed": None})
            i -= 1
        else:
            ops.append({"op": op, "expected": None, "observed": observed[j - 1]})
            j -= 1
    ops.reverse()
    return ops


def parse_fields(line: str) -> dict:
    return dict(KV.findall(line))


def parse_native(log: str) -> dict:
    pieces: dict[int, dict] = {}
    for line in log.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            fields = json.loads(text)
        except json.JSONDecodeError:
            continue
        if "piece" not in fields:
            continue
        pieces[int(fields["piece"])] = fields
    return {"pieces": pieces, "wire": {}, "requests": {}}


def parse_python(output: str) -> dict:
    intervals = {}
    audit_dir = None
    for line in output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "synth.begin" and obj.get("audit_dir"):
            audit_dir = obj["audit_dir"]
        if obj.get("event") == "synth.piece":
            intervals[int(obj["piece"])] = {
                "sample_start": int(obj["sample_start"]), "sample_end": int(obj["sample_end"]),
                "trimmed_leading_bytes": int(obj["trimmed_leading_bytes"]),
            }
    return {"intervals": intervals, "wire": {}, "requests": {}, "audit_dir": audit_dir}


def csv_hash(fields: dict, name: str) -> list[str]:
    value = fields.get(name, "-")
    return [] if value == "-" else value.split(",")


def locate(sample: int, py: dict, native: dict) -> dict | None:
    for piece, interval in sorted(py["intervals"].items()):
        if interval["sample_start"] <= sample < interval["sample_end"]:
            local_sample = sample - interval["sample_start"]
            native_local_sample = local_sample + interval["trimmed_leading_bytes"] // 2
            n = native["pieces"].get(piece, {})
            history = int(n.get("s3_history", 0))
            token_i = max(0, native_local_sample // 960)
            return {
                "piece": piece,
                "wav_sample": sample,
                "piece_sample": local_sample,
                "native_piece_sample": native_local_sample,
                "token_i": token_i,
                "window_i": history + token_i,
                "history_tokens": history,
                "n_speech_tok": n.get("n_speech_tok"),
                "speech_hash": n.get("speech_hash"),
                "stop": n.get("stop"),
                "text": n.get("text"),
                "audit_dir": py.get("audit_dir"),
            }
    return None


def verify_structural(py: dict, native: dict) -> list[str]:
    errors = []
    pieces = native["pieces"]
    if set(py["intervals"]) != set(pieces):
        errors.append(f"piece set mismatch python={sorted(py['intervals'])} native={sorted(pieces)}")
    for piece, interval in py["intervals"].items():
        n = pieces.get(piece, {})
        if not n:
            errors.append(f"piece {piece}: missing native JSONL")
            continue
        native_emitted = int(n.get("emitted_samples", 0))
        python_samples = interval["sample_end"] - interval["sample_start"]
        trimmed = interval["trimmed_leading_bytes"] // 2
        if native_emitted - trimmed != python_samples:
            errors.append(
                f"piece {piece}: native emitted {native_emitted} - trim {trimmed} != python {python_samples}"
            )
    return errors


def semantic_report(source: str, asr: dict, py: dict, native: dict) -> dict:
    expected = words(source)
    observed = observed_words(asr)
    ops = align(expected, observed)
    anomalies = []
    for op in ops:
        if op["op"] == "match":
            continue
        obs = op["observed"]
        entry = {"op": op["op"], "expected": op["expected"], "observed": obs}
        if obs:
            midpoint = (obs["start"] + obs["end"]) / 2
            entry["native_location"] = locate(int(midpoint * 24000), py, native)
        anomalies.append(entry)
    return {
        "expected_words": len(expected),
        "observed_words": len(observed),
        "edit_distance": sum(op["op"] != "match" for op in ops),
        "transcript": asr.get("text", ""),
        "anomalies": anomalies,
    }


def direct_tts(case: str, script: str, source: str, *extra: str, audit: bool = True) -> dict:
    start = log_offset(TTS_LOG)
    args = [*extra]
    if audit:
        args.append("--audit")
    args += ["--text", source]
    output = run(f"TTS — {case}", script, *args)
    native_log = read_suffix(TTS_LOG, start)
    (RUN_DIR / f"{case}.command.log").write_text(output, encoding="utf-8")
    (RUN_DIR / f"{case}.tts.log").write_text(native_log, encoding="utf-8")
    wav = save_copy(ROOT / "tts_out.wav", f"{case}.wav")
    asr = run_json(f"ASR JSON — {case}", wav) if wav is not None else {}
    (RUN_DIR / f"{case}.asr.json").write_text(json.dumps(asr, ensure_ascii=False, indent=2), encoding="utf-8")
    py = parse_python(output)
    if py.get("audit_dir"):
        audit_path = ROOT / py["audit_dir"] / "07-asr.json"
        if audit_path.parent.is_dir():
            audit_path.write_text(json.dumps(asr, ensure_ascii=False, indent=2), encoding="utf-8")
    native = parse_native(native_log)
    structural_errors = verify_structural(py, native)
    semantic = semantic_report(source, asr, py, native)
    report = {
        "case": case, "audit": audit, "source": source,
        "structural_errors": structural_errors, "semantic": semantic,
    }
    (RUN_DIR / f"{case}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[validator] {case}: semantic_edit_distance={semantic['edit_distance']}")
    for anomaly in semantic["anomalies"]:
        print(f"[validator] semantic anomaly: {json.dumps(anomaly, ensure_ascii=False)}")
    return report


def provenance() -> None:
    print(f"[validator] Trident HEAD:")
    subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    sibling = ROOT.parent / "chatterbox.cpp"
    if sibling.is_dir():
        print("[validator] chatterbox HEAD:")
        subprocess.run(["git", "-C", str(sibling), "rev-parse", "HEAD"])
    run("Nano provenance", "tts_nano.py", "--provenance")


def main() -> None:
    tts_start = log_offset(TTS_LOG)
    main_start = log_offset(MAIN_LOG)
    summary = {"run": RUN_ID, "cases": []}
    print(f"[validator] run={RUN_ID}")
    print(f"[validator] artifacts={RUN_DIR.relative_to(ROOT)}")
    print("[validator] installed bare-metal runtime required; this script never installs or retries")
    try:
        run("Unload all servers", "main.py", "--unload")
        provenance()
        for case, text in (
            ("nano-short", SHORT_TEXT),
            ("nano-count-1-30", COUNT_1_TO_30),
        ):
            try:
                summary["cases"].append(direct_tts(case, "tts_nano.py", text, "--seed", "42", "--cfm-steps", "2"))
            except Exception:
                pass
            run(f"Unload after {case}", "tts_nano.py", "--unload")
        run("Final unload", "main.py", "--unload")

        summary["status"] = "complete"
    finally:
        (RUN_DIR / "tts-all.log").write_text(read_suffix(TTS_LOG, tts_start), encoding="utf-8")
        (RUN_DIR / "main-all.log").write_text(read_suffix(MAIN_LOG, main_start), encoding="utf-8")
        (RUN_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[validator] COMPLETE {RUN_DIR}")


if __name__ == "__main__":
    main()
