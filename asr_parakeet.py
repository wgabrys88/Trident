"""Offline WAV transcription for TTS diagnosis via parakeet-cli + Nemotron 3.5 ASR (not chatterbox-server).

Default backend is CPU so batch ASR can run concurrently with Vulkan TTS on the same host.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
RUNTIME_BASE = TOOLS / "runtime"
DOWNLOADS = TOOLS / "downloads"
MODELS_DIR = ROOT / "models"

SCHEMA_VERSION = 2
PARAKEET_VERSION = "0.5.0"
PARAKEET_EXE = "parakeet-cli.exe"
PARAKEET_BACKEND = "cpu"
PARAKEET_ZIP_NAME = f"parakeet-v{PARAKEET_VERSION}-bin-win-cpu-x64.zip"
PARAKEET_ZIP_SHA = "df25af4095807d83957f6e135950120e7954fd2d4aca8ad0a5de248ada6287e0"
PARAKEET_ZIP_SIZE = 1421017
RUNTIME_DIR = RUNTIME_BASE / "parakeet-cpu"
DEFAULT_THREADS = max(1, (os.cpu_count() or 4) - 1)

NEMOTRON_HF_ID = "nvidia/nemotron-3.5-asr-streaming-0.6b"
NEMOTRON_GGUF_BASE = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/main"
NEMOTRON_QUANTS = {
    "q8_0": ("nemotron-3.5-asr-streaming-0.6b-q8_0.gguf", "ba2f13eccd4a5245be728f77e6149bd6a4fdcdd133ff2e08ac6005bcef7a99f1", 983696512),
    "q4_k": ("nemotron-3.5-asr-streaming-0.6b-q4_k.gguf", "5ad85eb3f3014c1a300d67b7ccbd23c38c4c952405cbe33a861e19fb2775e84b", 718102624),
}
DEFAULT_QUANT = "q8_0"

_EOU_RE = re.compile(r" \[(EOU|EOB) @ (\d+(?:\.\d+)?)s\]")
_STREAM_FINAL_RE = re.compile(r"^\[stream:final\] (.*)$")
_WORD_TS_RE = re.compile(
    r"^(?P<word>.+?)\s+\[(?P<start>\d+(?:\.\d+)?)-(?P<end>\d+(?:\.\d+)?)\]\s+\((?P<conf>\d+(?:\.\d+)?)\)\s*$"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path, expected_sha: str = "", expected_size: int = 0) -> Path:
    if dest.is_file():
        if expected_size and dest.stat().st_size != expected_size:
            raise RuntimeError(f"size mismatch for {dest.name}")
        if expected_sha and sha256_file(dest) != expected_sha:
            raise RuntimeError(f"sha256 mismatch for {dest.name}")
        return dest
    if dest.exists():
        raise RuntimeError(f"refusing to overwrite unverified artifact: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Trident-asr_parakeet/2"})
    with urllib.request.urlopen(req, timeout=3600) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    if expected_size and tmp.stat().st_size != expected_size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"download size mismatch for {dest.name}")
    if expected_sha and sha256_file(tmp) != expected_sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"download sha256 mismatch for {dest.name}")
    tmp.replace(dest)
    return dest


def parakeet_zip_url(zip_name: str) -> str:
    return (
        f"https://github.com/mudler/parakeet.cpp/releases/download/v{PARAKEET_VERSION}/{zip_name}"
    )


def install_runtime_and_model(quant: str = DEFAULT_QUANT) -> tuple[Path, Path]:
    if sys.platform != "win32":
        raise SystemExit("parakeet-cli requires Windows")
    if quant not in NEMOTRON_QUANTS:
        raise SystemExit(f"unknown quant {quant}")
    name, sha, size = NEMOTRON_QUANTS[quant]
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOADS / PARAKEET_ZIP_NAME
    fetch(
        parakeet_zip_url(PARAKEET_ZIP_NAME),
        zip_path,
        expected_sha=PARAKEET_ZIP_SHA,
        expected_size=PARAKEET_ZIP_SIZE,
    )
    runtime_dir = RUNTIME_DIR
    exe = runtime_dir / PARAKEET_EXE
    if not exe.is_file():
        found = list(runtime_dir.rglob(PARAKEET_EXE)) if runtime_dir.is_dir() else []
        if len(found) == 1:
            exe = found[0]
        else:
            if runtime_dir.exists():
                shutil.rmtree(runtime_dir)
            runtime_dir.mkdir(parents=True)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(runtime_dir)
            found = list(runtime_dir.rglob(PARAKEET_EXE))
            if len(found) != 1:
                raise SystemExit(f"{PARAKEET_EXE} missing after extract (cpu)")
            exe = found[0]
    model = fetch(f"{NEMOTRON_GGUF_BASE}/{name}", MODELS_DIR / name, expected_sha=sha, expected_size=size)
    return exe, model


def lang_from_run_dir(run_dir: Path) -> str:
    for prov in run_dir.glob("*.provenance.json"):
        argv = json.loads(prov.read_text(encoding="utf-8")).get("argv") or []
        if argv and argv[0] == "tts_v3.py" and len(argv) >= 2:
            return str(argv[-1]).strip().lower()
    return "en"


def run_cli(
    exe: Path, model: Path, wav: Path, lang: str, extra: list[str], threads: int = 0
) -> dict:
    thread_args = ["--threads", str(threads)] if threads > 0 else []
    args = [
        str(exe),
        "transcribe",
        "--model",
        str(model),
        "--input",
        str(wav),
        "--lang",
        lang,
        *thread_args,
        *extra,
    ]
    t0 = time.perf_counter()
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "argv": args,
        "exit_code": proc.returncode,
        "host_wall_s": round(time.perf_counter() - t0, 4),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def parse_stream_stdout(stdout: str) -> dict:
    lines = stdout.splitlines()
    final_text = ""
    stream_lines: list[str] = []
    events: list[dict] = []
    word_timestamps: list[dict] = []
    for line in lines:
        m_final = _STREAM_FINAL_RE.match(line)
        if m_final:
            final_text = m_final.group(1).strip()
            continue
        if line.startswith("[stream] "):
            stream_lines.append(line[len("[stream] ") :])
            continue
        for m in _EOU_RE.finditer(line):
            events.append({"type": m.group(1), "time_s": float(m.group(2)), "context_line": line})
        wm = _WORD_TS_RE.match(line.strip())
        if wm:
            word_timestamps.append(
                {
                    "word": wm.group("word").strip(),
                    "start_s": float(wm.group("start")),
                    "end_s": float(wm.group("end")),
                    "confidence": float(wm.group("conf")),
                }
            )
        elif line.strip() and not line.startswith("["):
            stream_lines.append(line)
    return {
        "final_text": final_text,
        "stream_text_joined": "".join(stream_lines),
        "stdout_lines": lines,
        "events": events,
        "word_timestamps": word_timestamps,
    }


def parse_nemotron_json(stdout: str) -> dict:
    text = stdout.strip()
    if not text:
        raise RuntimeError("parakeet-cli --json returned empty stdout")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"parakeet-cli --json invalid JSON: {exc}") from exc
    body = data[0] if isinstance(data, list) and data else data
    if not isinstance(body, dict):
        raise RuntimeError("parakeet-cli --json unexpected shape")
    transcript = (body.get("text") or "").strip()
    if not transcript:
        raise RuntimeError("parakeet-cli --json missing text")
    return body


def transcribe(exe: Path, model: Path, wav: Path, lang: str, threads: int = DEFAULT_THREADS) -> dict:
    json_inv = run_cli(exe, model, wav, lang, ["--json"], threads=threads)
    if json_inv["exit_code"] != 0:
        raise RuntimeError(f"parakeet-cli --json exit {json_inv['exit_code']}: {json_inv['stderr'][-500:]}")
    nemotron = parse_nemotron_json(json_inv["stdout"])

    stream_inv = run_cli(exe, model, wav, lang, ["--stream", "--timestamps"], threads=threads)
    if stream_inv["exit_code"] != 0:
        raise RuntimeError(f"parakeet-cli --stream exit {stream_inv['exit_code']}: {stream_inv['stderr'][-500:]}")
    stream = parse_stream_stdout(stream_inv["stdout"])

    return {
        "transcript": (nemotron.get("text") or "").strip(),
        "nemotron_json": nemotron,
        "stream": stream,
        "invocations": {"json": json_inv, "stream": stream_inv},
    }


def normalize_words(raw: list) -> list[dict]:
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = item.get("start", item.get("start_s"))
        end = item.get("end", item.get("end_s"))
        conf = item.get("confidence", item.get("prob"))
        word = item.get("word") or item.get("text") or item.get("w") or ""
        if conf is None:
            conf = item.get("conf")
        out.append(
            {
                "word": str(word),
                "start_s": float(start) if start is not None else None,
                "end_s": float(end) if end is not None else None,
                "confidence": float(conf) if conf is not None else None,
                "raw": item,
            }
        )
    return out


def build_payload(
    wav: Path,
    lang: str,
    result: dict,
    wall: float,
    exe: Path,
    model: Path,
    quant: str,
    threads: int,
    error: str | None = None,
) -> dict:
    nemotron = result.get("nemotron_json") or {}
    stream = result.get("stream") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "component": "asr_parakeet",
        "utc": utc_now(),
        "backend": PARAKEET_BACKEND,
        "threads": threads,
        "model_hf_id": NEMOTRON_HF_ID,
        "parakeet_cpp_version": PARAKEET_VERSION,
        "executable": str(exe),
        "executable_sha256": sha256_file(exe),
        "model_path": str(model),
        "model_sha256": sha256_file(model),
        "quant": quant,
        "lang": lang,
        "wav_path": str(wav),
        "wav_sha256": sha256_file(wav),
        "transcript": result.get("transcript") or "",
        "words": normalize_words(nemotron.get("words") or []),
        "tokens": nemotron.get("tokens"),
        "nemotron_json": nemotron,
        "stream_final_text": stream.get("final_text"),
        "stream_events": stream.get("events"),
        "stream_word_timestamps": stream.get("word_timestamps"),
        "stream_stdout_lines": stream.get("stdout_lines"),
        "invocations": result.get("invocations"),
        "error": error,
        "host_wall_s": round(wall, 3),
    }


def write_json(run_dir: Path, payload: dict) -> None:
    (run_dir / "asr_parakeet.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def process_wav(
    wav: Path,
    lang: str,
    run_dir: Path | None,
    model_override: Path | None,
    quant: str,
    threads: int,
) -> int:
    if not wav.is_file() or wav.stat().st_size <= 44:
        raise SystemExit(f"invalid wav: {wav}")
    exe, model = install_runtime_and_model(quant)
    if model_override and model_override.is_file():
        model = model_override
    t0 = time.perf_counter()
    try:
        result = transcribe(exe, model, wav, lang, threads=threads)
        payload = build_payload(
            wav, lang, result, time.perf_counter() - t0, exe, model, quant, threads
        )
        if run_dir:
            write_json(run_dir, payload)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["transcript"] else 1
    except Exception as exc:
        if run_dir:
            write_json(
                run_dir,
                build_payload(
                    wav,
                    lang,
                    {"transcript": "", "nemotron_json": {}, "stream": {}, "invocations": {}},
                    time.perf_counter() - t0,
                    exe,
                    model,
                    quant,
                    threads,
                    str(exc),
                ),
            )
        raise


def process_run_dir(run_dir: Path, model_override: Path | None, quant: str, threads: int) -> int:
    wavs = sorted(run_dir.glob("*.wav"))
    lang = lang_from_run_dir(run_dir)
    if not wavs:
        write_json(
            run_dir,
            {
                "schema_version": SCHEMA_VERSION,
                "component": "asr_parakeet",
                "utc": utc_now(),
                "lang": lang,
                "transcript": "",
                "error": "no wav in run directory",
                "host_wall_s": 0.0,
            },
        )
        print(f"skip {run_dir.name}: no wav")
        return 0
    try:
        return process_wav(wavs[0], lang, run_dir, model_override, quant, threads)
    except Exception:
        print(f"fail {run_dir.name}", file=sys.stderr)
        return 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    if argv and argv[0] == "install":
        pinst = argparse.ArgumentParser(prog="asr_parakeet.py install")
        pinst.add_argument("command")
        pinst.add_argument("--quant", choices=tuple(NEMOTRON_QUANTS), default=DEFAULT_QUANT)
        inst = pinst.parse_args(argv)
        exe, model = install_runtime_and_model(inst.quant)
        print(
            json.dumps(
                {
                    "backend": PARAKEET_BACKEND,
                    "executable": str(exe),
                    "model": str(model),
                    "model_sha256": sha256_file(model),
                },
                indent=2,
            )
        )
        return 0
    p = argparse.ArgumentParser(description="Nemotron 3.5 ASR for Trident run WAVs (parakeet-cli)")
    p.add_argument("wav", nargs="?", type=Path)
    p.add_argument("--run-dir", type=Path)
    p.add_argument("--runs-root", type=Path, action="append")
    p.add_argument("--model", type=Path, help="Override GGUF path")
    p.add_argument("--quant", choices=tuple(NEMOTRON_QUANTS), default=DEFAULT_QUANT)
    p.add_argument("--threads", type=int, default=DEFAULT_THREADS, help="ggml CPU threads for parakeet-cli")
    p.add_argument("--lang", default="en")
    args = p.parse_args(argv)
    if args.runs_root:
        code = 0
        for root in args.runs_root:
            for run_dir in sorted(root.iterdir()):
                if run_dir.is_dir():
                    code |= process_run_dir(
                        run_dir.resolve(), args.model, args.quant, args.threads
                    )
        return code
    if args.run_dir:
        return process_run_dir(args.run_dir.resolve(), args.model, args.quant, args.threads)
    if args.wav:
        return process_wav(
            args.wav.resolve(),
            args.lang.strip().lower(),
            None,
            args.model,
            args.quant,
            args.threads,
        )
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
