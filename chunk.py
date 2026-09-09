from __future__ import annotations
import json, sys, time, venv
from pathlib import Path

from main import ROOT, _download, _run_logged, jsonl

MODELS = ROOT / "models/sat-12l-sm"
VENV = ROOT / "tools/runtime/chunker"
ONNX = MODELS / "model_optimized.onnx"
CONFIG = MODELS / "config.json"
TOKENIZER = MODELS / "tokenizer.json"
SAT_ONNX_URL = "https://huggingface.co/segment-any-text/sat-12l-sm/resolve/main/model_optimized.onnx"
SAT_CONFIG_URL = "https://huggingface.co/segment-any-text/sat-12l-sm/resolve/main/config.json"
TOKENIZER_URL = "https://huggingface.co/FacebookAI/xlm-roberta-base/resolve/main/tokenizer.json"
SAT_THRESHOLD = 0.25
PACK_CHARS = 240
ORT_PROVIDERS = ["CPUExecutionProvider"]
_sat = None


def _python() -> Path:
    return VENV / "Scripts/python.exe"


def _ready() -> bool:
    return _python().is_file() and ONNX.is_file() and CONFIG.is_file() and TOKENIZER.is_file()


def install() -> None:
    if _ready():
        return
    py = _python()
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(VENV)
        pip = [str(py), "-m", "pip", "--isolated", "install", "--no-cache-dir",
               "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
        _run_logged([*pip, "numpy==1.26.4", "onnxruntime==1.20.1", "tokenizers==0.21.4",
                     "huggingface-hub==0.34.4", "wtpsplit-lite==0.2.0"], step="pip-chunker")
    if not ONNX.is_file():
        jsonl("chunk.install", model="sat-12l-sm")
        _download(SAT_ONNX_URL, ONNX)
    if not CONFIG.is_file():
        _download(SAT_CONFIG_URL, CONFIG)
    if not TOKENIZER.is_file():
        _download(TOKENIZER_URL, TOKENIZER)
    jsonl("chunk.install.done")


def _model():
    global _sat
    if _sat is None:
        install()
        import onnxruntime as ort
        from wtpsplit_lite import SaT
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _sat = SaT(str(MODELS), tokenizer_name_or_path=TOKENIZER,
                   ort_providers=ORT_PROVIDERS, ort_kwargs={"sess_options": so})
    return _sat


def _spoken_lines(text: str) -> list:
    lines = []
    for part in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(part.split())
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _pack(pieces: list) -> list:
    packed, buf = [], ""
    for piece in pieces:
        if not buf:
            buf = piece
        elif len(buf) + 1 + len(piece) <= PACK_CHARS:
            buf = f"{buf} {piece}"
        else:
            packed.append(buf)
            buf = piece
    if buf:
        packed.append(buf)
    return packed


def split(text: str) -> list:
    t0 = time.perf_counter()
    lines = _spoken_lines(text)
    text = "\n".join(lines)
    prep_ms = int((time.perf_counter() - t0) * 1000)
    if not text:
        raise ValueError("TTS input is empty")
    t0 = time.perf_counter()
    model = _model()
    load_ms = int((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    cuts = [p.strip() for p in model.split(
        text, threshold=SAT_THRESHOLD, treat_newline_as_space=False) if p and p.strip()]
    pieces = _pack(cuts)
    infer_ms = int((time.perf_counter() - t0) * 1000)
    if not pieces:
        raise ValueError("TTS input is empty")
    jsonl("chunk.done", model="sat-12l-sm", threshold=SAT_THRESHOLD, pack_chars=PACK_CHARS,
          newline_is_space=False, providers=ORT_PROVIDERS,
          lines=len(lines), sat_pieces=len(cuts), pieces=len(pieces),
          chars=sum(len(p) for p in pieces), prep_ms=prep_ms, load_ms=load_ms, infer_ms=infer_ms,
          ms=prep_ms + load_ms + infer_ms)
    for i, piece in enumerate(pieces):
        jsonl("chunk.piece", i=i, chars=len(piece), text=piece)
    return pieces


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if "--install" in sys.argv:
        install()
        sys.exit(0)
    json.dump(split(sys.stdin.read()), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
