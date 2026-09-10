from __future__ import annotations
import json, subprocess, venv
from pathlib import Path

from main import _run_logged

ROOT = Path(__file__).resolve().parent
PACKAGES = ("pandas==2.2.3", "pyarrow==19.0.1")


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


def finalize_run(run_dir: Path, wav_paths: list[Path], spec: dict, run_ctx: dict,
                 chatterbox_rev: str, sampler_fn) -> None:
    install()
    events_path = run_dir / "events.jsonl"
    if events_path.is_file():
        subprocess.run([str(_python()), "-c",
                        f"import pandas as pd; "
                        f"pd.read_json(r'{events_path}', lines=True)"
                        f".to_parquet(r'{run_dir / 'events.parquet'}', index=False)"],
                       check=True)
    audit_dir = spec.get("audit_dir")
    if audit_dir:
        p = Path(audit_dir)
        audit_dir = str(p.relative_to(ROOT) if p.is_absolute() else p)
    manifest = {
        "run_id": run_ctx["run_id"],
        "family": spec["family"],
        "wav_paths": [p.name for p in wav_paths],
        "audit_dir": audit_dir or None,
        "knobs": spec["knobs"],
        "sampler": sampler_fn(spec["knobs"]),
        "chatterbox_rev": chatterbox_rev,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
