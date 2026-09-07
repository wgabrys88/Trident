from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

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
    r"\[synth\] pcm .*piece=(\d+)/\d+ .*sample_start=(\d+) sample_end=(\d+) "
    r"trimmed_leading_bytes=(\d+) pcm_sha=([0-9a-f]+)"
)
WIRE_PY = re.compile(r"\[synth\] wire .*piece=(\d+)/\d+ chunk=(\d+) bytes=(\d+) fnv64=([0-9a-f]+)")
REQ_PY = re.compile(r"\[synth\] request .*piece=(\d+)/\d+ .*wire_fnv64=([0-9a-f]+)")


def run(label: str, script: str, *args: str, input_text: str | None = None,
        interpreter: str | Path | None = None) -> str:
    cmd = [str(interpreter or PYTHON), "-u", str(ROOT / script), *args]
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    print(" ".join(cmd), flush=True)
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
    code = proc.wait()
    output = "".join(lines)
    if code:
        raise subprocess.CalledProcessError(code, cmd, output=output)
    return output


def run_json(label: str, wav: Path) -> dict:
    cmd = [PYTHON, "-u", str(ROOT / "parakeet.py"), "--json", str(wav)]
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    print(" ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", check=True)
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    return json.loads(result.stdout)


def log_offset(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def read_suffix(path: Path, start: int) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as f:
        f.seek(start)
        return f.read().decode("utf-8", "replace")


def save_copy(source: Path, name: str) -> Path:
    if not source.is_file():
        raise FileNotFoundError(source)
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
    native_wire = {}
    native_req = {}
    for line in log.splitlines():
        fields = parse_fields(line)
        event = fields.get("event")
        if "piece" not in fields:
            continue
        try:
            piece = int(fields["piece"])
        except ValueError:
            continue
        item = pieces.setdefault(piece, {})
        item.setdefault("response", int(fields.get("response", "0")))
        if event == "s3.end":
            item["s3_end"] = fields
        elif event == "s3.audit":
            item["audit"] = fields
        elif event == "s3.audit.local":
            item["local"] = fields
        elif event == "s3.roundtrip":
            item["roundtrip"] = fields
        elif event == "wire.pcm":
            key = (piece, int(fields.get("chunk", "0")))
            native_wire[key] = (int(fields["bytes"]), fields["fnv64"])
        elif event == "synthesis.queued":
            native_req[piece] = fields.get("fnv64")
    return {"pieces": pieces, "wire": native_wire, "requests": native_req}


def parse_python(output: str) -> dict:
    intervals = {}
    for match in PCM_LINE.finditer(output):
        piece, start, end, trim, sha = match.groups()
        intervals[int(piece)] = {
            "sample_start": int(start), "sample_end": int(end),
            "trimmed_leading_bytes": int(trim), "pcm_sha": sha,
        }
    wire = {(int(p), int(c)): (int(n), h) for p, c, n, h in WIRE_PY.findall(output)}
    requests = {int(p): h for p, h in REQ_PY.findall(output)}
    return {"intervals": intervals, "wire": wire, "requests": requests}


def csv_hash(fields: dict, name: str) -> list[str]:
    value = fields.get(name, "-")
    return [] if value == "-" else value.split(",")


def locate(sample: int, py: dict, native: dict) -> dict | None:
    for piece, interval in sorted(py["intervals"].items()):
        if interval["sample_start"] <= sample < interval["sample_end"]:
            local_sample = sample - interval["sample_start"]
            native_local_sample = local_sample + interval["trimmed_leading_bytes"] // 2
            n = native["pieces"].get(piece, {})
            s3_end = n.get("s3_end", {})
            history = int(s3_end.get("history_tokens", "0"))
            pending_in = int(s3_end.get("pending_in", "0"))
            emit_begin = int(s3_end.get("emit_begin", "0"))
            raw_hift_sample = emit_begin + native_local_sample
            raw_window_token = max(0, raw_hift_sample // 960)
            local_token = raw_window_token - history
            local = n.get("local", {})
            encoder = csv_hash(local, "encoder")
            cfm0 = csv_hash(local, "cfm0")
            cfm = csv_hash(local, "cfm")
            mel = csv_hash(local, "mel")
            f0 = csv_hash(local, "f0")
            source = csv_hash(local, "source")
            hift = csv_hash(local, "hift")
            def pick(values: list[str], index: int):
                return values[index] if 0 <= index < len(values) else None
            return {
                "piece": piece,
                "wav_sample": sample,
                "piece_sample": local_sample,
                "native_piece_sample": native_local_sample,
                "raw_hift_sample": raw_hift_sample,
                "raw_window_token": raw_window_token,
                "approx_new_s3_token": local_token,
                "history_tokens": history,
                "boundary_crossfade": pending_in > 0 and native_local_sample < pending_in,
                "encoder_hash": pick(encoder, raw_window_token),
                "cfm0_hash": pick(cfm0, raw_window_token),
                "cfm_hash": pick(cfm, raw_window_token),
                "mel_hash": pick(mel, raw_window_token),
                "f0_hash": pick(f0, raw_window_token),
                "source_hash": pick(source, raw_window_token),
                "hift_hash": pick(hift, raw_window_token),
                "roundtrip": n.get("roundtrip", {}),
                "audit_dir": n.get("audit", {}).get("dir"),
            }
    return None


def verify_structural(py: dict, native: dict) -> list[str]:
    errors = []
    if py["requests"] != native["requests"]:
        errors.append(f"request wire mismatch python={py['requests']} native={native['requests']}")
    if py["wire"] != native["wire"]:
        errors.append(f"PCM wire mismatch python={py['wire']} native={native['wire']}")
    pieces = native["pieces"]

    # Audit mode preserves both sides of the socket. Compare exact bytes, not only fingerprints.
    audit_dirs = {
        Path(item["audit"]["dir"]).parent
        for item in pieces.values() if item.get("audit", {}).get("dir")
    }
    if len(audit_dirs) == 1:
        audit_dir = next(iter(audit_dirs))
        for piece, request_hash in py["requests"].items():
            response = pieces.get(piece, {}).get("response", 0)
            native_path = audit_dir / f"native-r{response}_p{piece}.request.utf8"
            python_path = audit_dir / f"python-r{response}_p{piece}.request.utf8"
            if not native_path.is_file() or not python_path.is_file():
                errors.append(f"piece {piece}: missing exact request wire artifact")
            elif native_path.read_bytes() != python_path.read_bytes():
                errors.append(f"piece {piece}: exact request wire bytes differ")
        for (piece, chunk), _ in py["wire"].items():
            response = pieces.get(piece, {}).get("response", 0)
            native_path = audit_dir / f"native-r{response}_p{piece}_c{chunk}.pcm16"
            python_path = audit_dir / f"python-r{response}_p{piece}_c{chunk}.pcm16"
            if not native_path.is_file() or not python_path.is_file():
                errors.append(f"piece {piece} chunk {chunk}: missing exact PCM wire artifact")
            elif native_path.read_bytes() != python_path.read_bytes():
                errors.append(f"piece {piece} chunk {chunk}: exact PCM wire bytes differ")
    for piece, interval in py["intervals"].items():
        n = pieces.get(piece, {})
        end = n.get("s3_end", {})
        if not end:
            errors.append(f"piece {piece}: missing native s3.end")
            continue
        native_emitted = int(end["emitted"])
        python_samples = interval["sample_end"] - interval["sample_start"]
        trimmed = interval["trimmed_leading_bytes"] // 2
        if native_emitted - trimmed != python_samples:
            errors.append(
                f"piece {piece}: native emitted {native_emitted} - trim {trimmed} != python {python_samples}"
            )
    ordered = sorted(pieces)
    for a, b in zip(ordered, ordered[1:]):
        out = pieces[a].get("audit", {})
        inn = pieces[b].get("audit", {})
        for state in ("mel_cache", "source_cache", "phase", "pending"):
            if out.get(f"{state}_out") != inn.get(f"{state}_in"):
                errors.append(
                    f"continuity {a}->{b} {state}: {out.get(f'{state}_out')} != {inn.get(f'{state}_in')}"
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
    asr = run_json(f"ASR JSON — {case}", wav)
    (RUN_DIR / f"{case}.asr.json").write_text(json.dumps(asr, ensure_ascii=False, indent=2), encoding="utf-8")
    py = parse_python(output)
    native = parse_native(native_log)
    structural_errors = verify_structural(py, native)
    semantic = semantic_report(source, asr, py, native)
    report = {
        "case": case, "audit": audit, "source": source,
        "structural_errors": structural_errors, "semantic": semantic,
    }
    (RUN_DIR / f"{case}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if structural_errors:
        raise RuntimeError(f"{case}: structural audit failed: {'; '.join(structural_errors)}")
    print(f"[validator] {case}: semantic_edit_distance={semantic['edit_distance']}")
    for anomaly in semantic["anomalies"]:
        print(f"[validator] semantic anomaly: {json.dumps(anomaly, ensure_ascii=False)}")
    return report


def provenance() -> None:
    print(f"[validator] Trident HEAD:")
    subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True)
    sibling = ROOT.parent / "chatterbox.cpp"
    if sibling.is_dir():
        print("[validator] chatterbox HEAD:")
        subprocess.run(["git", "-C", str(sibling), "rev-parse", "HEAD"], check=True)
    run("Nano provenance", "tts_nano.py", "--provenance")
    run("Turbo provenance", "tts_turbo.py", "--provenance")
    run("V3 provenance", "tts_v3.py", "--provenance")


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

        run("Brain — short", "brain.py", "--request", BRAIN_SHORT_PROMPT)
        save_copy(ROOT / "brain_out.txt", "brain-short.txt")
        run("Brain — architecture", "brain.py", "--request", BRAIN_README_PROMPT)
        save_copy(ROOT / "brain_out.txt", "brain-architecture.txt")
        run("Unload Brain", "brain.py", "--unload")

        sat = run("CPU SaT — long speech", "chunk.py", input_text=LONG_SPEECH_TEXT,
                  interpreter=CHUNK_PYTHON)
        (RUN_DIR / "sat-long.json").write_text(sat, encoding="utf-8")

        # Current Nano production parity: CFM=2. The CFM=1/2 cause branch is already closed.
        for case, text in (
            ("nano-short", SHORT_TEXT),
            ("nano-count-1-30", COUNT_1_TO_30),
            ("nano-count-20-30", COUNT_20_TO_30),
            ("nano-long", LONG_SPEECH_TEXT),
        ):
            summary["cases"].append(direct_tts(case, "tts_nano.py", text, "--seed", "42", "--cfm-steps", "2"))
            run(f"Unload after {case}", "tts_nano.py", "--unload")

        summary["cases"].append(direct_tts(
            "turbo-count-1-30", "tts_turbo.py", COUNT_1_TO_30, "--seed", "42", "--cfm-steps", "2"
        ))
        run("Unload Turbo", "tts_turbo.py", "--unload")

        summary["cases"].append(direct_tts(
            "v3-en-count-1-30", "tts_v3.py", COUNT_1_TO_30, "--language", "en", "--seed", "42"
        ))
        run("Unload V3 English", "tts_v3.py", "--unload")
        summary["cases"].append(direct_tts(
            "v3-pl-short", "tts_v3.py", V3_POLISH_TEXT, "--language", "pl", "--seed", "42"
        ))
        run("Unload V3", "tts_v3.py", "--unload")

        # Full application audit.
        start = log_offset(TTS_LOG)
        pipeline_output = run("Full pipeline audit", "main.py", "--audit", PIPELINE_PROMPT)
        (RUN_DIR / "pipeline.command.log").write_text(pipeline_output, encoding="utf-8")
        (RUN_DIR / "pipeline.tts.log").write_text(read_suffix(TTS_LOG, start), encoding="utf-8")
        save_copy(ROOT / "brain_out.txt", "pipeline.brain.txt")
        save_copy(ROOT / "tts_out.wav", "pipeline.wav")
        pipeline_asr = run_json("Full pipeline ASR JSON", RUN_DIR / "pipeline.wav")
        (RUN_DIR / "pipeline.asr.json").write_text(
            json.dumps(pipeline_asr, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run("Unload full pipeline", "main.py", "--unload")

        # Performance truth: audit disabled. Keep separate from diagnostic runs.
        perf = direct_tts(
            "nano-count-1-30-performance", "tts_nano.py", COUNT_1_TO_30,
            "--seed", "42", "--cfm-steps", "2", audit=False
        )
        summary["cases"].append(perf)
        run("Final unload", "main.py", "--unload")

        summary["status"] = "complete"
    finally:
        (RUN_DIR / "tts-all.log").write_text(read_suffix(TTS_LOG, tts_start), encoding="utf-8")
        (RUN_DIR / "main-all.log").write_text(read_suffix(MAIN_LOG, main_start), encoding="utf-8")
        (RUN_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[validator] COMPLETE {RUN_DIR}")


if __name__ == "__main__":
    main()
