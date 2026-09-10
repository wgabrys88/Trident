from __future__ import annotations
import json, subprocess, venv
from pathlib import Path

from main import ROOT, TTS_LOG, _run_logged

PACKAGES = ("pandas==2.2.3", "pyarrow==19.0.1")
_DUMP = (
    "import json,pandas as pd,sys; from pathlib import Path;"
    "d=json.load(sys.stdin); p=Path(sys.argv[1]);"
    "e,c=d.get('events') or [], d.get('pieces') or [];"
    "pd.DataFrame(e).to_parquet(p/'events.parquet', index=False) if e else None;"
    "pd.DataFrame(c).to_parquet(p/'pieces.parquet', index=False) if c else None"
)


def _python() -> Path:
    return ROOT / ".venv/Scripts/python.exe"


def install() -> None:
    py = _python()
    if py.is_file() and (ROOT / ".venv/Lib/site-packages/pyarrow").is_dir():
        return
    if not py.is_file():
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    _run_logged([str(py), "-m", "pip", "--isolated", "install", "--no-cache-dir",
                 "--disable-pip-version-check", "--progress-bar", "off", "--no-input", *PACKAGES],
                step="pip-analyze")


def _jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def finalize_run(run_dir: Path, wav_paths: list[Path], spec: dict, run_ctx: dict,
                 chatterbox_rev: str, sampler_fn) -> None:
    install()
    tts_rows = _jsonl(TTS_LOG)
    begins = [r for r in tts_rows if r.get("event") == "synthesis.begin"]
    n = int(begins[-1]["pieces"]) if begins else 0
    pieces = [r for r in tts_rows if "n_speech_tok" in r][-n:] if n else []
    subprocess.run([str(_python()), "-c", _DUMP, str(run_dir)],
                   input=json.dumps({"events": _jsonl(run_dir / "events.jsonl"), "pieces": pieces}),
                   text=True, encoding="utf-8", check=True)
    audit_dir = spec.get("audit_dir")
    if audit_dir:
        p = Path(audit_dir)
        audit_dir = str(p.relative_to(ROOT) if p.is_absolute() else p)
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_ctx["run_id"],
        "family": spec["family"],
        "wav_paths": [p.name for p in wav_paths],
        "audit_dir": audit_dir or None,
        "knobs": spec["knobs"],
        "sampler": sampler_fn(spec["knobs"]),
        "chatterbox_rev": chatterbox_rev,
        "n_pieces": n,
        "n_text_tok": sum(r.get("n_text_tok") or 0 for r in pieces) or None,
        "n_speech_tok": sum(r.get("n_speech_tok") or 0 for r in pieces) or None,
    }, indent=2), encoding="utf-8")
