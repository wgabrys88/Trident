from __future__ import annotations
import hashlib, json, os, statistics, subprocess, sys, time, venv
from pathlib import Path

from main import ROOT, _download, jsonl

# Chonky DistilBERT uncased: official ParagraphSplitter default. Replaces modernbert-large.
MODELS = ROOT / "models/chonky-distilbert-base-uncased-1"
VENV = ROOT / "tools/runtime/chunker"
STAMP = VENV / "chonky.ok"
WEIGHTS = MODELS / "model.safetensors"
CONFIG = MODELS / "config.json"
TOKENIZER = MODELS / "tokenizer.json"
TOKENIZER_CONFIG = MODELS / "tokenizer_config.json"
SPECIAL_TOKENS = MODELS / "special_tokens_map.json"
VOCAB = MODELS / "vocab.txt"
WEIGHTS_SHA = "9a2cd8b8d81b29612d5045430353e213df61f2e896fb834ce5eb7a8b6efd99ab"
CONFIG_SHA = "70eeab02faefceae57ea6355a4d9aafd2c3f88fa8a1dc88012e6b0e0c9c365df"
TOKENIZER_SHA = "435667fab0c06c165b1283ecb422497c37124f2d6a35b2ac73dc876332fc9518"
TOKENIZER_CONFIG_SHA = "769fd5b8e7e5d12eca596adb3d90eae5003fcaf1e77926a61d1694dd49f90c62"
SPECIAL_TOKENS_SHA = "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a"
VOCAB_SHA = "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3"
HUB = "https://huggingface.co/mirth/chonky_distilbert_base_uncased_1/resolve/main"
CHONKY_DEVICE = "cpu"
CHONKY_THREADS = 4
CHONKY_BATCH_SIZE = 1
CHONKY_MAX_LENGTH = 512
CHONKY_STRIDE = 256
CHONKY_AGGREGATION = "simple"
CHONKY_THRESHOLD = 0.0
CHONKY_IGNORE_LABELS = ["O"]
CHONKY_NEWLINE_IS_SPACE = True
_splitter = None


def _python() -> Path:
    return VENV / "Scripts/python.exe"


def _ready() -> bool:
    return (_python().is_file() and STAMP.is_file() and WEIGHTS.is_file() and CONFIG.is_file()
            and TOKENIZER.is_file() and TOKENIZER_CONFIG.is_file() and SPECIAL_TOKENS.is_file()
            and VOCAB.is_file())


def _knobs() -> dict:
    return {
        "model": "chonky_distilbert_base_uncased_1", "library": "chonky", "device": CHONKY_DEVICE,
        "max_length": CHONKY_MAX_LENGTH, "stride": CHONKY_STRIDE,
        "aggregation": CHONKY_AGGREGATION, "threshold": CHONKY_THRESHOLD,
        "ignore_labels": CHONKY_IGNORE_LABELS, "newline_is_space": CHONKY_NEWLINE_IS_SPACE,
        "batch_size": CHONKY_BATCH_SIZE, "threads": CHONKY_THREADS,
    }


def install() -> None:
    if _ready():
        return
    py = _python()
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(VENV)
    if not STAMP.is_file():
        jsonl("chunk.install", model="chonky_distilbert_base_uncased_1", library="chonky")
        pip = [str(py), "-m", "pip", "--isolated", "install", "--no-cache-dir",
               "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
        subprocess.run([*pip, "--index-url", "https://download.pytorch.org/whl/cpu", "torch"], check=True)
        subprocess.run([*pip, "transformers==4.51.3", "chonky==0.1.7"], check=True)
        STAMP.write_text("chonky==0.1.7\n", encoding="utf-8")
    files = (
        (WEIGHTS, f"{HUB}/model.safetensors", WEIGHTS_SHA),
        (CONFIG, f"{HUB}/config.json", CONFIG_SHA),
        (TOKENIZER, f"{HUB}/tokenizer.json", TOKENIZER_SHA),
        (TOKENIZER_CONFIG, f"{HUB}/tokenizer_config.json", TOKENIZER_CONFIG_SHA),
        (SPECIAL_TOKENS, f"{HUB}/special_tokens_map.json", SPECIAL_TOKENS_SHA),
        (VOCAB, f"{HUB}/vocab.txt", VOCAB_SHA),
    )
    for path, url, sha in files:
        if not path.is_file():
            jsonl("chunk.install.file", path=str(path.relative_to(ROOT)))
            _download(url, path, sha)
    jsonl("chunk.install.done", **_knobs())


def _model():
    global _splitter
    if _splitter is None:
        install()
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_VERBOSITY"] = "error"
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline
        torch.set_num_threads(CHONKY_THREADS)
        tokenizer = AutoTokenizer.from_pretrained(MODELS, model_max_length=CHONKY_MAX_LENGTH, local_files_only=True)
        model = AutoModelForTokenClassification.from_pretrained(
            MODELS, num_labels=2, id2label={0: "O", 1: "separator"},
            label2id={"O": 0, "separator": 1}, local_files_only=True)
        model.to(CHONKY_DEVICE)
        _splitter = pipeline(
            "ner", model=model, tokenizer=tokenizer, device=CHONKY_DEVICE,
            aggregation_strategy=CHONKY_AGGREGATION, ignore_labels=CHONKY_IGNORE_LABELS,
            stride=CHONKY_STRIDE, batch_size=CHONKY_BATCH_SIZE)
    return _splitter


def split(text: str) -> list:
    source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    t0 = time.perf_counter()
    lines = [line for line in (" ".join(part.split()) for part in
             text.replace("\r\n", "\n").replace("\r", "\n").split("\n")) if line]
    text = (" " if CHONKY_NEWLINE_IS_SPACE else "\n").join(lines)
    prep_ms = int((time.perf_counter() - t0) * 1000)
    if not text:
        raise ValueError("TTS input is empty")
    t0 = time.perf_counter()
    pipe = _model()
    load_ms = int((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    ners = pipe(text)
    cuts, scores = [], []
    begin = 0
    pieces = []
    for ner in ners:
        end = int(ner["end"])
        p = float(ner["score"])
        if end <= begin or end > len(text) or p < CHONKY_THRESHOLD:
            continue
        chunk = text[begin:end].strip()
        if chunk:
            pieces.append(chunk)
            scores.append(round(p, 4))
            cuts.append(end)
        begin = end
    tail = text[begin:].strip()
    if tail:
        pieces.append(tail)
    infer_ms = int((time.perf_counter() - t0) * 1000)
    if not pieces:
        raise ValueError("TTS input is empty")
    lengths = [len(p) for p in pieces]
    jsonl("chunk.done", **_knobs(), source_sha=source_sha, lines=len(lines), pieces=len(pieces),
          chars=sum(lengths), chars_min=min(lengths), chars_max=max(lengths),
          chars_mean=round(statistics.mean(lengths), 1),
          chars_median=statistics.median(lengths), n_lt_50=sum(n < 50 for n in lengths),
          n_ge_200=sum(n >= 200 for n in lengths), n_ge_400=sum(n >= 400 for n in lengths),
          n_cuts=len(cuts), cut_scores=scores[:32],
          cut_score_min=min(scores) if scores else None,
          cut_score_max=max(scores) if scores else None,
          prep_ms=prep_ms, load_ms=load_ms, infer_ms=infer_ms, ms=prep_ms + load_ms + infer_ms)
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
