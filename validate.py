from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import struct
import sys
sys.dont_write_bytecode = True
import tempfile
import zipfile
import time
import wave
from pathlib import Path

# Sequential validator: preserve failures, continue cases, return nonzero on failure.

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
COMMAND_LOGS = []
CHUNK_PYTHON = ROOT / "tools/runtime/chunker/Scripts/python.exe"
TTS_LOG = ROOT / "tts.log"
MAIN_LOG = ROOT / "main.log"
RUN_ID = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"
def artifact(name: str) -> Path:
    return ROOT / f"validation-{RUN_ID}-{name}"



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
    with subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE if input_text is not None else None,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace") as proc:
        if input_text is not None:
            proc.stdin.write(input_text)
            proc.stdin.close()
        lines = []
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        code = proc.wait()
    output = "".join(lines)
    COMMAND_LOGS.append({"label": label, "returncode": code, "output": output})
    if code:
        raise subprocess.CalledProcessError(code, cmd, output=output)
    return output


def run_json(label: str, wav: Path) -> dict:
    result = run(label, "parakeet.py", "--json", str(wav))
    asr = json.loads(result)
    if not isinstance(asr, dict) or not asr.get("words"):
        raise ValueError("ASR returned no timestamped words")
    return asr


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
    target = artifact(name)
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
    errors = []
    identities = set()
    for line in log.splitlines():
        fields = parse_fields(line)
        event = fields.get("event")
        if int(fields.get("response", "0")) == 0 or "piece" not in fields:
            continue
        try:
            piece = int(fields["piece"])
        except ValueError:
            continue
        identities.add((fields.get("conn"), fields.get("response")))
        item = pieces.setdefault(piece, {})
        slot = {"s3.end":"s3_end", "s3.audit":"audit", "s3.audit.local":"local", "s3.roundtrip":"roundtrip"}.get(event)
        if slot and slot in item:
            errors.append(f"piece {piece}: duplicate {event}")
        if event == "wire.pcm" and (piece, int(fields.get("chunk", "0"))) in native_wire:
            errors.append(f"piece {piece}: duplicate PCM chunk")
        if event == "synthesis.queued" and piece in native_req:
            errors.append(f"piece {piece}: duplicate request")
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
    if len(identities) != 1:
        errors.append(f"expected one connection/response, found {sorted(identities)}")
    return {"pieces": pieces, "wire": native_wire, "requests": native_req, "errors": errors}


def parse_python(output: str) -> dict:
    intervals = {}
    errors = []
    for match in PCM_LINE.finditer(output):
        piece, start, end, trim, sha = match.groups()
        if int(piece) in intervals:
            errors.append(f"piece {piece}: duplicate Python interval")
        intervals[int(piece)] = {
            "sample_start": int(start), "sample_end": int(end),
            "trimmed_leading_bytes": int(trim), "pcm_sha": sha,
        }
    wire = {(int(p), int(c)): (int(n), h) for p, c, n, h in WIRE_PY.findall(output)}
    requests = {int(p): h for p, h in REQ_PY.findall(output)}
    if len(wire) != len(WIRE_PY.findall(output)) or len(requests) != len(REQ_PY.findall(output)):
        errors.append("duplicate Python request or PCM event")
    return {"intervals": intervals, "wire": wire, "requests": requests, "errors": errors}


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


def verify_structural(py: dict, native: dict, audit: bool = True) -> list[str]:
    errors = list(native.get("errors", [])) + list(py.get("errors", []))
    for side, data in (("python", py), ("native", native)):
        for kind in ("requests", "wire"):
            if not data[kind]:
                errors.append(f"{side}: missing {kind}")
    expected = set(range(len(py["requests"])))
    if set(py["requests"]) != expected or set(py["intervals"]) != expected or set(native["pieces"]) != expected:
        errors.append("missing or non-contiguous piece evidence")
    if py["requests"] != native["requests"]:
        errors.append(f"request wire mismatch python={py['requests']} native={native['requests']}")
    if py["wire"] != native["wire"]:
        errors.append(f"PCM wire mismatch python={py['wire']} native={native['wire']}")
    pieces = native["pieces"]

    # Audit mode preserves both sides of the socket. Compare exact bytes, not only fingerprints.
    audit_dirs = {
        Path(item["audit"]["dir"].rsplit(".r", 1)[0])
        for item in pieces.values() if item.get("audit", {}).get("dir")
    }
    if audit and len(audit_dirs) != 1:
        errors.append("expected exactly one audit filename prefix")
    if audit and len(audit_dirs) == 1:
        audit_dir = next(iter(audit_dirs))
        for piece, request_hash in py["requests"].items():
            response = pieces.get(piece, {}).get("response", 0)
            native_path = Path(str(audit_dir) + f".native-r{response}_p{piece}.request.utf8")
            python_path = Path(str(audit_dir) + f".python-r{response}_p{piece}.request.utf8")
            if not native_path.is_file() or not python_path.is_file():
                errors.append(f"piece {piece}: missing exact request wire artifact")
            elif native_path.read_bytes() != python_path.read_bytes():
                errors.append(f"piece {piece}: exact request wire bytes differ")
        for (piece, chunk), _ in py["wire"].items():
            response = pieces.get(piece, {}).get("response", 0)
            native_path = Path(str(audit_dir) + f".native-r{response}_p{piece}_c{chunk}.pcm16")
            python_path = Path(str(audit_dir) + f".python-r{response}_p{piece}_c{chunk}.pcm16")
            if not native_path.is_file() or not python_path.is_file():
                errors.append(f"piece {piece} chunk {chunk}: missing exact PCM wire artifact")
            elif native_path.read_bytes() != python_path.read_bytes():
                errors.append(f"piece {piece} chunk {chunk}: exact PCM wire bytes differ")
    previous_end = 0
    for piece, interval in sorted(py["intervals"].items()):
        n = pieces.get(piece, {})
        end = n.get("s3_end", {})
        if not end:
            errors.append(f"piece {piece}: missing native s3.end")
            continue
        if interval["sample_start"] != previous_end or interval["sample_end"] <= interval["sample_start"]:
            errors.append(f"piece {piece}: invalid/non-contiguous WAV interval")
        previous_end = interval["sample_end"]
        begin, finish, hold = (int(end[k]) for k in ("emit_begin", "emit_end", "hold"))
        native_emitted = int(end["emitted"])
        if not 0 <= begin < finish or finish-begin != native_emitted or hold not in (0,480):
            errors.append(f"piece {piece}: invalid native emission range")
        if audit and not n.get("audit"):
            errors.append(f"piece {piece}: missing S3 audit")
        python_samples = interval["sample_end"] - interval["sample_start"]
        trimmed = interval["trimmed_leading_bytes"] // 2
        if native_emitted - trimmed != python_samples:
            errors.append(
                f"piece {piece}: native emitted {native_emitted} - trim {trimmed} != python {python_samples}"
            )
    ordered = sorted(pieces) if audit else []
    for a, b in zip(ordered, ordered[1:]):
        out = pieces[a].get("audit", {})
        inn = pieces[b].get("audit", {})
        for state in ("mel_cache", "source_cache", "phase", "pending"):
            if not out.get(f"{state}_out") or not inn.get(f"{state}_in") or out[f"{state}_out"] != inn[f"{state}_in"]:
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


def clear_outputs(*names: str) -> None:
    # Remove the exact output before its producer runs; timestamps cannot prove freshness.
    for name in names:
        (ROOT / name).unlink(missing_ok=True)


def direct_tts(case: str, script: str, source: str, *extra: str, audit: bool = True,
               pipeline: bool = False) -> dict:
    from audit_log import unpack, pack, append_report
    args = [*extra, *(["--audit"] if audit else [])]
    args += [source] if pipeline else ["--text", source]
    output, asr, errors, structural_errors = "", {}, [], []
    clear_outputs("tts_out.wav", "tts_out.log.zip", *(["brain_out.txt"] if pipeline else []))
    wav_path, log_path = artifact(case+".wav"), artifact(case+".log.zip")
    try:
        output = run(f"TTS — {case}", script, *args)
        fresh_wav = ROOT / "tts_out.wav"
        if not fresh_wav.is_file():
            raise RuntimeError("successful command produced no fresh WAV")
        fresh_wav.replace(wav_path)
        with wave.open(str(wav_path)) as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 24000) or audio.getnframes() <= 0:
                raise RuntimeError("WAV must be nonempty mono PCM16 at 24000 Hz")
        if pipeline:
            source = (ROOT / "brain_out.txt").read_text(encoding="utf-8")
        asr = run_json(f"ASR JSON — {case}", wav_path)
    except Exception as exc:
        if isinstance(exc, subprocess.CalledProcessError) and not output:
            output = exc.output or ""
        errors.append(f"{type(exc).__name__}: {exc}")
    source_log = ROOT / "tts_out.log.zip"
    if source_log.is_file():
        source_log.replace(log_path)
    else:
        errors.append("missing synthesis log bundle")
        pack(log_path, {"schema":1,"status":"FAIL","error":"synthesis produced no log"}, "", None)
    py, native = parse_python(output), {"pieces":{},"requests":{},"wire":{}}
    tools = ROOT / "tools"
    tools.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="audit-read-", dir=tools) as temp:
            scratch = Path(temp)
            metadata = unpack(log_path, scratch)
            native = parse_native((scratch/'native.log').read_text(encoding='utf-8'))
            for item in native['pieces'].values():
                if item.get('audit',{}).get('dir'):
                    item['audit']['dir'] = str(scratch / Path(item['audit']['dir']).name)
            structural_errors += verify_structural(py, native, audit)
            if audit:
                structural_errors += verify_content(source, native)
                structural_errors += verify_audit(native)
            semantic = semantic_report(source, asr, py, native)
            # Locations remain useful after temporary extraction is removed.
            for anomaly in semantic['anomalies']:
                location = anomaly.get('native_location')
                if location and location.get('audit_dir'):
                    location['artifact_prefix'] = Path(location.pop('audit_dir')).name
                    location['log_bundle'] = log_path.name
    except Exception as exc:
        structural_errors.append(f"diagnostic validation failed: {type(exc).__name__}: {exc}")
        semantic = semantic_report(source, asr, py, native)
    report = {"case":case,"audit":audit,"source":source,"errors":errors,
              "structural_errors":structural_errors,"semantic":semantic,
              "reference_status":"INCONCLUSIVE","cause_identified":False}
    report['status'] = 'FAIL' if errors or structural_errors or semantic['edit_distance'] else 'PASS'
    report['speech_status'] = 'ASR_MISMATCH' if semantic['edit_distance'] else 'ASR_MATCH'
    append_report(log_path, report, output, asr)
    print(f"[validator] {case}: {report['status']} semantic_edit_distance={semantic['edit_distance']} log={log_path.name}")
    return report


def verify_content(source: str, native: dict) -> list[str]:
    texts = []
    for piece, item in sorted(native["pieces"].items()):
        prefix = item.get("audit", {}).get("dir")
        if not prefix:
            return ["content conservation: missing request artifacts"]
        path = Path(prefix.rsplit(".r", 1)[0] + f".native-r{item['response']}_p{piece}.request.utf8")
        if not path.is_file():
            return [f"content conservation: missing {path.name}"]
        texts.append(path.read_text(encoding="utf-8"))
    normalize = lambda text: "".join(text.split())
    return [] if texts and normalize(source) == normalize("".join(texts)) else ["chunked request content differs from source"]


def verify_audit(native: dict) -> list[str]:
    errors = []
    for piece, item in sorted(native["pieces"].items()):
        prefix = item.get("audit", {}).get("dir")
        if not prefix:
            errors.append(f"piece {piece}: missing audit prefix")
            continue
        try:
            events = audit_events(Path(prefix + ".audit.jsonl"))
            for kind in ("t3.begin", "t3.end", "s3.begin", "s3.end"):
                if sum(e["event"] == kind for e in events) != 1:
                    raise ValueError(f"expected exactly one {kind}")
            tensors = {e["name"] for e in events if e["event"] == "tensor"}
            required = {"t3-text", "t3-speech", "speech-window", "input-embedding", "encoder-full",
                        "encoder-mu", "cfm-z0", "cfm-conditioning", "cfm-speaker", "cfm-time-span",
                        "mel-generated", "mel-conditioned", "f0", "source", "stft", "hift-wav",
                        "pcm-emitted-f32"}
            required |= {f"state-{state}-{side}" for state in ("mel", "source", "phase", "pending") for side in ("in", "out")}
            if required - tensors:
                raise ValueError(f"missing tensor events: {sorted(required-tensors)}")
            end = next(e for e in events if e["event"] == "t3.end")
            begin = next(e for e in events if e["event"] == "t3.begin")
            s3_end = next(e for e in events if e["event"] == "s3.end")
            if not 0 <= s3_end["emit_begin"] < s3_end["emit_end"] <= s3_end["raw_samples"] or s3_end["emit_end"]-s3_end["emit_begin"] != s3_end["emitted"] or s3_end["emit_end"]+s3_end["hold"] != s3_end["raw_samples"]:
                raise ValueError("invalid S3 audit emission range")
            if Path(prefix+".hift-wav.f32").stat().st_size != s3_end["raw_samples"]*4 or Path(prefix+".pcm-emitted-f32.f32").stat().st_size != s3_end["emitted"]*4:
                raise ValueError("emission range differs from waveform artifacts")
            speech = Path(prefix+".t3-speech.i32").read_bytes()
            window = Path(prefix+".speech-window.i32").read_bytes()
            if not speech or not window.endswith(speech):
                raise ValueError("T3 speech differs from new S3 window tokens")
            decisions = [e for e in events if e["event"] == "t3.decision"]
            if [e["step"] for e in decisions] != list(range(end["steps"])):
                raise ValueError("missing, reordered, or duplicate T3 decision")
            emitted = []
            for decision in decisions:
                step_prefix = prefix + f".t3-s{decision['step']}"
                sample_events = audit_events(Path(step_prefix + ".audit.jsonl"))
                kinds = [e["event"] for e in sample_events]
                if kinds.count("sampling.begin") != 1 or kinds.count("sample") != 1:
                    raise ValueError("incomplete sampling events")
                stages = [e["name"] for e in sample_events if e["event"] == "tensor"]
                if stages != ["prefix", "raw", "temperature", "top_k", "top_p", "repeat_penalty", "probabilities"]:
                    raise ValueError("incomplete sampling filter sequence")
                sampled = next(e for e in sample_events if e["event"] == "sample")
                if sampled["selected"] != decision["selected"]:
                    raise ValueError("sampler/engine selected token mismatch")
                if not sampled.get("rng_after") or not next(e for e in sample_events if e["event"] == "sampling.begin").get("rng_before"):
                    raise ValueError("missing RNG state")
                if decision["termination"] == "forced_repeat_eos":
                    data = Path(step_prefix + ".prefix.i32").read_bytes()
                    previous = [v[0] for v in struct.iter_unpack("<i", data)]
                    if len(previous) < 4 or previous[-4:] != [decision["selected"]]*4:
                        raise ValueError("forced EOS lacks five-consecutive-token evidence")
                elif decision["selected"] != decision["effective"]:
                    raise ValueError("unreported sampled token substitution")
                if decision["termination"] != "none" and (decision["effective"] != begin["stop_token"] or decision["published"] is not None):
                    raise ValueError("termination token was not suppressed EOS")
                if decision["published"] is not None:
                    emitted.append(decision["published"])
            if decisions and Path(prefix + ".t3-speech.i32").read_bytes() != struct.pack(f"<{len(emitted)}i", *emitted):
                raise ValueError("published tokens differ from T3 speech artifact")
        except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
            errors.append(f"piece {piece}: audit contract violation: {exc}")
    return errors


def audit_events(path: Path) -> list[dict]:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    names = set()
    for event in events:
        if event.get("schema") != 1 or not event.get("event"):
            raise ValueError("unsupported or missing audit schema/event")
        if event["event"] != "tensor":
            continue
        name = event["file"]
        if Path(name).name != name or name in names:
            raise ValueError("duplicate or non-flat artifact path")
        names.add(name)
        data = (path.parent / name).read_bytes()
        size, fmt = {"f32": (4,"f"), "f64": (8,"d"), "i32": (4,"i")}[event["dtype"]]
        shape = event["shape"]
        if not shape or any(not isinstance(d, int) or d < 0 for d in shape):
            raise ValueError(f"{name}: invalid shape")
        if event["bytes"] != len(data) or math.prod(shape)*size != len(data):
            raise ValueError(f"{name}: truncated or invalid tensor layout")
        if event["endian"] != "little" or event["layout"] != "C":
            raise ValueError(f"{name}: unsupported layout")
        hash_value = 1469598103934665603
        for byte in data:
            hash_value = ((hash_value ^ byte)*1099511628211) & 0xffffffffffffffff
        if f"{hash_value:016x}" != event["fnv64"]:
            raise ValueError(f"{name}: artifact checksum mismatch")
        if fmt != "i":
            filtered = event["name"] in ("temperature", "top_k", "top_p", "repeat_penalty")
            for (value,) in struct.iter_unpack("<"+fmt, data):
                if not math.isfinite(value) and not (filtered and value == -math.inf):
                    raise ValueError(f"{name}: invalid numeric value")
    return events


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


def main() -> int:
    summary = {"run": RUN_ID, "cases": [], "errors": []}
    print(f"[validator] artifacts={ROOT} (validation-{RUN_ID}-*)")
    print("[validator] installed runtime required; cleaning is a separate clean.ps1 command")

    def attempt(label, fn):
        try:
            return fn()
        except Exception as exc:
            error = {"step": label, "error": f"{type(exc).__name__}: {exc}"}
            if isinstance(exc, subprocess.CalledProcessError):
                error.update(returncode=exc.returncode, output=exc.output)
            summary["errors"].append(error)
            print(f"[validator] FAIL {label}: {exc}")
            return None

    def command(label, script, *args):
        return attempt(label, lambda: run(label, script, *args))

    def brain_case(label, prompt):
        clear_outputs("brain_out.txt")
        run(label, "brain.py", "--request", prompt)
        if not (ROOT / "brain_out.txt").is_file():
            raise RuntimeError("missing fresh brain output")

    try:
        command("Unload all servers", "main.py", "--unload")
        attempt("provenance", provenance)
        attempt("brain-short", lambda: brain_case("brain-short", BRAIN_SHORT_PROMPT))
        attempt("brain-architecture", lambda: brain_case("brain-architecture", BRAIN_README_PROMPT))
        command("Unload Brain", "brain.py", "--unload")
        sat = attempt("CPU SaT", lambda: run("CPU SaT", "chunk.py", input_text=LONG_SPEECH_TEXT, interpreter=CHUNK_PYTHON))
        cases = [
            ("nano-short", "tts_nano.py", SHORT_TEXT, ["--seed","42","--cfm-steps","2"], True, False),
            ("nano-count-1-30", "tts_nano.py", COUNT_1_TO_30, ["--seed","42","--cfm-steps","2"], True, False),
            ("nano-count-1-30-repeat", "tts_nano.py", COUNT_1_TO_30, ["--seed","42","--cfm-steps","2"], True, False),
            ("nano-count-20-30", "tts_nano.py", COUNT_20_TO_30, ["--seed","42","--cfm-steps","2"], True, False),
            ("nano-long", "tts_nano.py", LONG_SPEECH_TEXT, ["--seed","42","--cfm-steps","2"], True, False),
            ("turbo-count-1-30", "tts_turbo.py", COUNT_1_TO_30, ["--seed","42","--cfm-steps","2"], True, False),
            ("v3-en-count-1-30", "tts_v3.py", COUNT_1_TO_30, ["--language","en","--seed","42"], True, False),
            ("v3-pl-short", "tts_v3.py", V3_POLISH_TEXT, ["--language","pl","--seed","42"], True, False),
            ("pipeline", "main.py", PIPELINE_PROMPT, [], True, True),
            ("nano-count-1-30-performance", "tts_nano.py", COUNT_1_TO_30, ["--seed","42","--cfm-steps","2"], False, False),
        ]
        for case, script, source, args, audit, pipeline in cases:
            report = attempt(case, lambda: direct_tts(case, script, source, *args, audit=audit, pipeline=pipeline))
            summary["cases"].append(report or {"case": case, "status": "FAIL", "errors": ["case raised; see suite errors"]})
            command(f"Unload after {case}", script, "--unload")
    finally:
        from audit_log import compare_runs
        first, second = artifact('nano-count-1-30.log.zip'), artifact('nano-count-1-30-repeat.log.zip')
        if first.is_file() and second.is_file():
            summary['nano_repeatability'] = attempt('Nano repeatability', lambda: compare_runs(first, second))
            if summary['nano_repeatability'] and summary['nano_repeatability']['status'] != 'EXACT_MATCH':
                summary['errors'].append({'step':'Nano repeatability','error':summary['nano_repeatability']})
        summary["status"] = "FAIL" if summary["errors"] or any(c["status"] != "PASS" for c in summary["cases"]) else "PASS"
        with zipfile.ZipFile(artifact("suite.log.zip"), 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('report.json', json.dumps(summary, ensure_ascii=False, indent=2))
            bundle.writestr('commands.json', json.dumps(COMMAND_LOGS, ensure_ascii=False))
    print(f"[validator] {summary['status']} {ROOT} (validation-{RUN_ID}-*)")
    return 1 if summary["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
