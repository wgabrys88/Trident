from __future__ import annotations
import hashlib, json, os, statistics, subprocess, sys, time, venv
from pathlib import Path

from main import ROOT, _download, jsonl

# Chonky modernbert-large: semantic cuts from token-class scores, not char caps.
MODELS = ROOT / "models/chonky-modernbert-large-1"
VENV = ROOT / "tools/runtime/chunker"
STAMP = VENV / "chonky.ok"
WEIGHTS = MODELS / "model.safetensors"
CONFIG = MODELS / "config.json"
TOKENIZER = MODELS / "tokenizer.json"
TOKENIZER_CONFIG = MODELS / "tokenizer_config.json"
SPECIAL_TOKENS = MODELS / "special_tokens_map.json"
WEIGHTS_SHA = "097fd24eeafee40df5654b3870ebf9a6b1762b395bcdea94746a35fa60ebf35b"
CONFIG_SHA = "a3cdad5d0fd67a9ecc4ea1a1f3b58793630711c24b1cab2f8271a7adb79a1470"
TOKENIZER_SHA = "c7a995f78d60cc3c253902f4b5becfe2f9d0b44f78e6e2f81a343a0cb71789e6"
TOKENIZER_CONFIG_SHA = "37542f45b201d188c1b4199c2e93078064224d4355d28479f6444cd86c586ea3"
SPECIAL_TOKENS_SHA = "ea97ecdbcc73713039d8d64dbb05e3689495c96657fbd9a18f5bed381be81049"
HUB = "https://huggingface.co/mirth/chonky_modernbert_large_1/resolve/main"
CHONKY_DEVICE = "cpu"
CHONKY_THREADS = 4
CHONKY_BATCH_SIZE = 1
# Trained at 1024; ModernBERT can go to 8192 but this checkpoint was not.
CHONKY_MAX_LENGTH = 1024
# Overlap in tokens. Transformers only applies stride when aggregation != none.
CHONKY_STRIDE = 512
# none: per-token softmax so threshold can add cuts. simple/first/average/max: argmax groups.
CHONKY_AGGREGATION = "none"
# P(separator). Argmax is ~0.5 and made one 1497-char piece. Lower = more native cuts.
CHONKY_THRESHOLD = 0.2
# Empty so O tokens stay visible and 1-score is P(separator). Default pipeline hides O.
CHONKY_IGNORE_LABELS = []
# Official Chonky demo uses paul_graham_essay_no_new_line. Newlines hid cuts.
CHONKY_NEWLINE_IS_SPACE = True
_splitter = None


def _python() -> Path:
    return VENV / "Scripts/python.exe"


def _ready() -> bool:
    return (_python().is_file() and STAMP.is_file() and WEIGHTS.is_file() and CONFIG.is_file()
            and TOKENIZER.is_file() and TOKENIZER_CONFIG.is_file() and SPECIAL_TOKENS.is_file())


def _knobs() -> dict:
    return {
        "model": "chonky_modernbert_large_1", "library": "chonky", "device": CHONKY_DEVICE,
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
        jsonl("chunk.install", model="chonky_modernbert_large_1", library="chonky")
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
        kwargs = dict(aggregation_strategy=CHONKY_AGGREGATION, ignore_labels=CHONKY_IGNORE_LABELS,
                      batch_size=CHONKY_BATCH_SIZE)
        if CHONKY_AGGREGATION != "none":
            kwargs["stride"] = CHONKY_STRIDE
        _splitter = pipeline("ner", model=model, tokenizer=tokenizer, device=CHONKY_DEVICE, **kwargs)
    return _splitter


def _p_sep(ner: dict) -> float:
    score = float(ner["score"])
    name = str(ner.get("entity") or ner.get("entity_group") or "")
    return score if name.endswith("separator") else 1.0 - score


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
    by_end = {}
    for ner in ners:
        end = int(ner["end"])
        if end <= 0 or end >= len(text):
            continue
        p = _p_sep(ner)
        if p >= CHONKY_THRESHOLD and p > by_end.get(end, 0.0):
            by_end[end] = p
    cuts = sorted(by_end)
    pieces, begin, scores = [], 0, []
    for end in cuts:
        chunk = text[begin:end].strip()
        if chunk:
            pieces.append(chunk)
            scores.append(round(by_end[end], 4))
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
