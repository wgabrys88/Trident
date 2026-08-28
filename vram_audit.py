from __future__ import annotations

import csv
import re
import subprocess
import time
from pathlib import Path

from config import DEFAULT_DATA_DIR, DEFAULT_MODELS_DIR, FAMILIES, SHARED_MODELS
from log import note
from main import Paths, boot_residents, start_run
from resident import stop_all, status as resident_status

_GGUF = {
    "chatterbox-t3": ("chatterbox-t3-nano-q4_0.gguf", 171_901_536),
    "chatterbox-codec": ("chatterbox-s3gen-nano-f16.gguf", 1_064_879_936),
    "gemma": ("gemma-4-E2B_q4_0-it.gguf", 3_349_516_256),
    "parakeet": ("tdt-0.6b-v3-q4_k.gguf", 675_200_864),
}


def _vram_mib() -> int:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        text=True, encoding="utf-8", errors="replace", timeout=10,
    ).strip()
    return int(out)


def _gguf_sum_mib() -> int:
    return sum(size for _, size in _GGUF.values()) // (1024 * 1024)


def run(models_dir: Path | None = None, data_dir: Path | None = None) -> int:
    paths = Paths(models_dir, data_dir, "vram-audit")
    note(f"component=vram-audit event=start idle_mib={_vram_mib()}")
    rows: list[list] = [["ts", "step", "vram_mib", "note"]]
    rows.append([time.strftime("%Y-%m-%dT%H:%M:%S%z"), "idle", _vram_mib(), "before any residents"])
    boot_residents(paths.models_dir, paths.data_dir, "nano", "en", "trump")
    for step, names in (
        ("chatterbox-ready", ("chatterbox",)),
        ("gemma-ready", ("chatterbox", "gemma")),
        ("all-ready", ("chatterbox", "gemma", "parakeet")),
    ):
        ready = {row["name"] for row in resident_status() if row["ready"]}
        if not set(names).issubset(ready):
            note(f"component=vram-audit event=skip step={step} ready={sorted(ready)}")
            continue
        time.sleep(2)
        vram = _vram_mib()
        rows.append([time.strftime("%Y-%m-%dT%H:%M:%S%z"), step, vram, ",".join(sorted(ready))])
        note(f"component=vram-audit event=sample step={step} vram_mib={vram} ready={sorted(ready)}")
    stop_all()
    time.sleep(2)
    rows.append([time.strftime("%Y-%m-%dT%H:%M:%S%z"), "after-stop", _vram_mib(), "after stop_all()"])
    audit_path = paths.data_dir / "vram-audit.csv"
    with audit_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    gguf_sum = _gguf_sum_mib()
    all_ready = next((int(r[2]) for r in reversed(rows) if r[1] == "all-ready"), 0)
    idle = int(rows[1][2])
    delta = all_ready - idle
    if delta >= int(gguf_sum * 0.5):
        verdict = "ok"
    elif delta >= int(gguf_sum * 0.3):
        verdict = "below_expected"
    else:
        verdict = "cap_suspect"
    note(f"component=vram-audit event=done delta_mib={delta} gguf_sum_mib={gguf_sum} verdict={verdict}")
    print(f"vram_audit: delta_mib={delta} gguf_sum_mib={gguf_sum} verdict={verdict} csv={audit_path}")
    return 0 if verdict == "ok" else 1
