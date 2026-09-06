from __future__ import annotations
import argparse, json, subprocess, sys, venv
from pathlib import Path

from _runtime import ROOT, _download

MODELS = ROOT / "models/sat-3l-sm"
VENV = ROOT / "tools/runtime/chunker"
ONNX = MODELS / "model_optimized.onnx"
CONFIG = MODELS / "config.json"
TOKENIZER = MODELS / "tokenizer.json"
ONNX_SHA = "8573277b4dbea9c5fb1b4cfd8c21e5aa628069ac8258d1342ba664e1b64ada6d"
CONFIG_SHA = "d61498bc239f773126ee794446cdcad88822967e0b02ae7bd070f725c98be791"
TOKENIZER_SHA = "a898ea75433890f6610f4e470b8ebeb0c21dce5c8dd61f892eb09eb5919d2e2c"
SAT_ONNX_URL = "https://huggingface.co/segment-any-text/sat-3l-sm/resolve/main/model_optimized.onnx"
SAT_CONFIG_URL = "https://huggingface.co/segment-any-text/sat-3l-sm/resolve/main/config.json"
TOKENIZER_URL = "https://huggingface.co/FacebookAI/xlm-roberta-base/resolve/main/tokenizer.json"
ORT_PROVIDERS = ["CPUExecutionProvider"]
_sat = None


def _python() -> Path:
    return VENV / "Scripts/python.exe"


def install() -> None:
    changed = False
    py = _python()
    if not py.is_file():
        changed = True
        venv.EnvBuilder(with_pip=True).create(VENV)
        pip = [str(py), "-m", "pip", "--isolated", "install", "--no-cache-dir",
               "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
        subprocess.run([*pip, "numpy==1.26.4", "onnxruntime==1.20.1", "tokenizers==0.21.4",
                        "huggingface-hub==0.34.4", "wtpsplit-lite==0.2.0"], check=True)
    if not ONNX.is_file():
        changed = True
        print("[chunk] install | sat-3l-sm onnx", flush=True)
        _download(SAT_ONNX_URL, ONNX, ONNX_SHA)
    if not CONFIG.is_file():
        changed = True
        _download(SAT_CONFIG_URL, CONFIG, CONFIG_SHA)
    if not TOKENIZER.is_file():
        changed = True
        _download(TOKENIZER_URL, TOKENIZER, TOKENIZER_SHA)
    if changed:
        print("[chunk] install | done", flush=True)


def _model():
    global _sat
    if _sat is None:
        import onnxruntime as ort
        from wtpsplit_lite import SaT
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _sat = SaT(str(MODELS), tokenizer_name_or_path=str(TOKENIZER),
                   ort_providers=ORT_PROVIDERS, ort_kwargs={"sess_options": so})
    return _sat


def split(text: str, min_chars: int = 200) -> list:
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    text = "\n".join(line for line in lines if line)
    if not text:
        raise ValueError("TTS input is empty")
    pieces = [p.strip() for p in _model().split(text, treat_newline_as_space=False) if p and p.strip()]
    if not pieces:
        raise ValueError("TTS input is empty")
    merged = []
    for p in pieces:
        if merged and len(merged[-1]) < min_chars:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--install", action="store_true")
    args = p.parse_args()
    if args.install:
        install()
        sys.exit(0)
    pieces = split(sys.stdin.read())
    print(f"[chunk] n={len(pieces)}", file=sys.stderr)
    json.dump(pieces, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
