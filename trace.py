from __future__ import annotations
import json, os, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPELINE_LOG = ROOT / ".runtime-logs/pipeline.log"
DEBUG_LOG = ROOT.parent / "debug-d5d316.log"
_mu = threading.Lock()
_native: dict[int, dict] = {}


def dbg(hypothesis_id: str, location: str, message: str, **data) -> None:
    # #region agent log
    rec = {"sessionId": "d5d316", "hypothesisId": hypothesis_id, "location": location,
           "message": message, "data": data, "timestamp": int(time.time() * 1000)}
    try:
        with DEBUG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def reset_native() -> None:
    with _mu:
        _native.clear()


def native_piece(piece_id: int) -> dict:
    with _mu:
        return dict(_native.get(piece_id) or {})


def write(text: str) -> None:
    if not text:
        return
    if not text.endswith("\n"):
        text += "\n"
    PIPELINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _mu:
        with PIPELINE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(text)
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "tts.piece" and "piece" in obj:
                _native[int(obj["piece"])] = obj


def trace(event: str, *, file=None, **fields) -> None:
    line = json.dumps({"event": event, **fields}, ensure_ascii=False)
    print(line, file=file or sys.stderr, flush=True)
    if os.environ.get("TRIDENT_CHILD"):
        return
    write(line)
