from __future__ import annotations
import hashlib, json, shutil, statistics, subprocess, sys, time, venv
from pathlib import Path

from main import ROOT, _download, jsonl

# sat-12l is the official non-SM 12-layer checkpoint. It scores newlines, not every sentence period.
MODELS = ROOT / "models/sat-12l"
VENV = ROOT / "tools/runtime/chunker"
ONNX = MODELS / "model_optimized.onnx"
CONFIG = MODELS / "config.json"
TOKENIZER = MODELS / "tokenizer.json"
ONNX_SHA = "c8704f695246a12449ede8aaa8ef7deb107ac77c982d63bef15d7ac4a5a68968"
CONFIG_SHA = "ee63fb740bd478f277e45a2beb6b011eeed797e7b8841786669721e0a53a8c84"
TOKENIZER_SHA = "a898ea75433890f6610f4e470b8ebeb0c21dce5c8dd61f892eb09eb5919d2e2c"
SAT_ONNX_URL = "https://huggingface.co/segment-any-text/sat-12l/resolve/main/model_optimized.onnx"
SAT_CONFIG_URL = "https://huggingface.co/segment-any-text/sat-12l/resolve/main/config.json"
TOKENIZER_URL = "https://huggingface.co/FacebookAI/xlm-roberta-base/resolve/main/tokenizer.json"
SAT_HUB_PREFIX = None
SAT_LORA_PATH = None
SAT_STYLE = None
SAT_LANGUAGE = None

# Native SaT knobs. Non-SM sat-12l scores newlines, not every sentence period.
SAT_THRESHOLD = 0.99
SAT_STRIDE = 64
SAT_BLOCK_SIZE = 512
SAT_BATCH_SIZE = 32
SAT_OUTER_BATCH_SIZE = 1000
SAT_PAD_LAST_BATCH = False
SAT_WEIGHTING = "uniform"
SAT_REMOVE_WHITESPACE = False
SAT_STRIP_WHITESPACE = False
SAT_NEWLINE_IS_SPACE = True
SAT_DO_PARAGRAPH = False
SAT_PARAGRAPH_THRESHOLD = 0.99
SAT_VERBOSE = False

# CPU ONNX. This 12-layer encoder is not a GGML Vulkan target.
ORT_PROVIDERS = ["CPUExecutionProvider"]
ORT_INTRA_THREADS = 1
ORT_INTER_THREADS = 1
ORT_SEQUENTIAL = True
ORT_GRAPH_OPT = "all"
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
        jsonl("chunk.install", model="sat-12l")
        _download(SAT_ONNX_URL, ONNX, ONNX_SHA)
    if not CONFIG.is_file():
        _download(SAT_CONFIG_URL, CONFIG, CONFIG_SHA)
    if not TOKENIZER.is_file():
        previous = ROOT / "models/sat-12l-sm/tokenizer.json"
        if not previous.is_file():
            previous = ROOT / "models/sat-3l-sm/tokenizer.json"
        if previous.is_file():
            TOKENIZER.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(previous, TOKENIZER)
        else:
            _download(TOKENIZER_URL, TOKENIZER, TOKENIZER_SHA)
    jsonl("chunk.install.done")


def _knobs() -> dict:
    return {
        "model": "sat-12l", "library": "wtpsplit-lite", "threshold": SAT_THRESHOLD,
        "stride": SAT_STRIDE, "block_size": SAT_BLOCK_SIZE, "batch_size": SAT_BATCH_SIZE,
        "outer_batch_size": SAT_OUTER_BATCH_SIZE, "pad_last_batch": SAT_PAD_LAST_BATCH,
        "weighting": SAT_WEIGHTING, "remove_whitespace": SAT_REMOVE_WHITESPACE,
        "strip_whitespace": SAT_STRIP_WHITESPACE, "newline_is_space": SAT_NEWLINE_IS_SPACE,
        "do_paragraph": SAT_DO_PARAGRAPH, "paragraph_threshold": SAT_PARAGRAPH_THRESHOLD,
        "verbose": SAT_VERBOSE, "hub_prefix": SAT_HUB_PREFIX, "lora_path": SAT_LORA_PATH,
        "style": SAT_STYLE, "language": SAT_LANGUAGE, "providers": ORT_PROVIDERS,
        "ort_intra_threads": ORT_INTRA_THREADS, "ort_inter_threads": ORT_INTER_THREADS,
        "ort_sequential": ORT_SEQUENTIAL, "ort_graph_opt": ORT_GRAPH_OPT,
    }


def _model():
    global _sat
    if _sat is None:
        install()
        import onnxruntime as ort
        from wtpsplit_lite import SaT
        so = ort.SessionOptions()
        so.intra_op_num_threads = ORT_INTRA_THREADS
        so.inter_op_num_threads = ORT_INTER_THREADS
        so.execution_mode = (ort.ExecutionMode.ORT_SEQUENTIAL if ORT_SEQUENTIAL
                             else ort.ExecutionMode.ORT_PARALLEL)
        so.graph_optimization_level = {
            "off": ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
            "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
            "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
            "all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
        }[ORT_GRAPH_OPT]
        ctor = {"model_name_or_model": str(MODELS), "tokenizer_name_or_path": TOKENIZER,
                "ort_providers": ORT_PROVIDERS, "ort_kwargs": {"sess_options": so},
                "hub_prefix": SAT_HUB_PREFIX}
        if SAT_STYLE:
            ctor["style_or_domain"] = SAT_STYLE
        if SAT_LANGUAGE:
            ctor["language"] = SAT_LANGUAGE
        if SAT_LORA_PATH:
            ctor["lora_path"] = SAT_LORA_PATH
        _sat = SaT(**ctor)
    return _sat


def _flatten(raw) -> list:
    if SAT_DO_PARAGRAPH:
        out = []
        for para in raw:
            joined = " ".join(s.strip() for s in para if s and str(s).strip()) if isinstance(para, (list, tuple)) else str(para).strip()
            if joined:
                out.append(joined)
        return out
    return [p.strip() for p in raw if p and p.strip()]


def _cut_scores(probs, cut_idx) -> dict:
    if len(cut_idx) == 0:
        return {"n_cuts": 0, "cut_min": None, "cut_max": None, "cut_mean": None, "cut_median": None}
    scores = [float(probs[int(i)]) for i in cut_idx]
    return {
        "n_cuts": len(scores), "cut_min": round(min(scores), 5), "cut_max": round(max(scores), 5),
        "cut_mean": round(statistics.mean(scores), 5),
        "cut_median": round(float(statistics.median(scores)), 5),
    }


def split(text: str) -> list:
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
    import numpy as np
    from wtpsplit_lite._utils import indices_to_sentences
    t0 = time.perf_counter()
    probs = model.predict_proba(
        text, stride=SAT_STRIDE, block_size=SAT_BLOCK_SIZE, batch_size=SAT_BATCH_SIZE,
        pad_last_batch=SAT_PAD_LAST_BATCH, weighting=SAT_WEIGHTING,
        remove_whitespace_before_inference=SAT_REMOVE_WHITESPACE,
        outer_batch_size=SAT_OUTER_BATCH_SIZE,
        return_paragraph_probabilities=False, verbose=SAT_VERBOSE)
    cut_idx = np.where(probs > SAT_THRESHOLD)[0]
    raw = list(indices_to_sentences(text, cut_idx, strip_whitespace=SAT_STRIP_WHITESPACE))
    if not SAT_NEWLINE_IS_SPACE:
        raw = [part for sentence in raw for part in sentence.split("\n")]
    pieces = _flatten(raw)
    score = _cut_scores(probs, cut_idx)
    infer_ms = int((time.perf_counter() - t0) * 1000)
    if not pieces:
        raise ValueError("TTS input is empty")
    lengths = [len(p) for p in pieces]
    jsonl("chunk.done", **_knobs(), source_sha=source_sha, lines=len(lines), pieces=len(pieces),
          chars=sum(lengths), chars_min=min(lengths), chars_max=max(lengths),
          chars_mean=round(statistics.mean(lengths), 1),
          chars_median=statistics.median(lengths), n_lt_50=sum(n < 50 for n in lengths),
          n_ge_200=sum(n >= 200 for n in lengths), **score, prep_ms=prep_ms, load_ms=load_ms,
          infer_ms=infer_ms, ms=prep_ms + load_ms + infer_ms)
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
