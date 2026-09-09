from __future__ import annotations
import hashlib, json, shutil, subprocess, sys, time, venv
from pathlib import Path

from main import ROOT, _download, jsonl

MODELS = ROOT / "models/sat-12l-sm"
VENV = ROOT / "tools/runtime/chunker"
ONNX = MODELS / "model_optimized.onnx"
CONFIG = MODELS / "config.json"
TOKENIZER = MODELS / "tokenizer.json"
ONNX_SHA = "0bb8cf275f98e1c337138bcecdf007876fb2a2b0eb4b5756ce627b2b0510c2c7"
CONFIG_SHA = "ed9094a56926e0ca1302f6c7ca9b11cf2ea0eced84d0f330ad938cba0fcc6209"
TOKENIZER_SHA = "a898ea75433890f6610f4e470b8ebeb0c21dce5c8dd61f892eb09eb5919d2e2c"
SAT_ONNX_URL = "https://huggingface.co/segment-any-text/sat-12l-sm/resolve/main/model_optimized.onnx"
SAT_CONFIG_URL = "https://huggingface.co/segment-any-text/sat-12l-sm/resolve/main/config.json"
TOKENIZER_URL = "https://huggingface.co/FacebookAI/xlm-roberta-base/resolve/main/tokenizer.json"
# sat-12l-sm SM default. 0.025 forced one piece per counted word (64 pieces
# on the probe): tiny pieces ran native RTF ~1.08, prose ~0.37. Longer SaT
# pieces are the RTF and cadence lever. Newlines from Gemma still break.
SAT_THRESHOLD = 0.25
# CPU only. Dml/CUDA would steal the GPU from Nano/Gemma/Parakeet.
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
        subprocess.run([*pip, "numpy==1.26.4", "onnxruntime==1.20.1", "tokenizers==0.21.4",
                        "huggingface-hub==0.34.4", "wtpsplit-lite==0.2.0"], check=True)
    if not ONNX.is_file():
        jsonl("chunk.install", model="sat-12l-sm")
        _download(SAT_ONNX_URL, ONNX, ONNX_SHA)
    if not CONFIG.is_file():
        _download(SAT_CONFIG_URL, CONFIG, CONFIG_SHA)
    if not TOKENIZER.is_file():
        previous = ROOT / "models/sat-3l-sm/tokenizer.json"
        if previous.is_file():
            TOKENIZER.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(previous, TOKENIZER)
        else:
            _download(TOKENIZER_URL, TOKENIZER, TOKENIZER_SHA)
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


def split(text: str) -> list:
    # Gemma puts one breath per line. Keep those breaks. SaT still meaning-cuts inside a line.
    source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    t0 = time.perf_counter()
    lines = [line for line in (" ".join(part.split()) for part in
             text.replace("\r\n", "\n").replace("\r", "\n").split("\n")) if line]
    text = "\n".join(lines)
    prep_ms = int((time.perf_counter() - t0) * 1000)
    if not text:
        raise ValueError("TTS input is empty")
    t0 = time.perf_counter()
    model = _model()
    load_ms = int((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    pieces = [p.strip() for p in model.split(
        text, threshold=SAT_THRESHOLD, treat_newline_as_space=False) if p and p.strip()]
    infer_ms = int((time.perf_counter() - t0) * 1000)
    if not pieces:
        raise ValueError("TTS input is empty")
    jsonl("chunk.done", model="sat-12l-sm", threshold=SAT_THRESHOLD, newline_is_space=False,
          providers=ORT_PROVIDERS, source_sha=source_sha, lines=len(lines), pieces=len(pieces),
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
    pieces = split(sys.stdin.read())
    json.dump(pieces, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
