"""Build run inventory from models/runs and models/runs-PASCAL (offline evidence)."""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_server_tail(path: Path) -> dict:
    if not path.is_file():
        return {"outcome": "no_server_log"}
    lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    if not lines:
        return {"outcome": "empty_server_log"}
    last = lines[-1]
    if "request failed" in last:
        err = last.split("error=", 1)[-1] if "error=" in last else last
        m = re.search(r"text_tokens=(\d+)", last)
        return {
            "outcome": "failed",
            "error": err,
            "text_tokens": int(m.group(1)) if m else None,
        }
    m = re.search(
        r"unit (\d+)/(\d+) text_tokens=(\d+) predicted=(\d+) dropped=(\d+) eos=(\d+)",
        last,
    )
    if m:
        return {
            "outcome": "complete",
            "units": f"{m.group(1)}/{m.group(2)}",
            "text_tokens": int(m.group(3)),
            "predicted": int(m.group(4)),
            "dropped": int(m.group(5)),
            "eos": int(m.group(6)),
        }
    return {"outcome": "unknown", "tail": last}


def load_provenance(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def catalog_run(host: str, run_dir: Path) -> dict:
    prov_files = sorted(run_dir.glob("*.provenance.json"))
    wavs = sorted(run_dir.glob("*.wav"))
    variant = prov_files[0].name.split(".")[0] if prov_files else None
    prov = load_provenance(prov_files[0]) if prov_files else {}
    sl = parse_server_tail(run_dir / "server.log")
    argv = prov.get("argv") or []
    lang = None
    if argv and argv[0] == "tts_v3.py" and len(argv) >= 2:
        lang = argv[-1]
    binaries = prov.get("binaries") or {}
    server = {}
    if isinstance(binaries, dict):
        server = binaries.get("chatterbox-server") or binaries.get("server") or {}
    elif isinstance(binaries, list):
        for item in binaries:
            if not isinstance(item, dict):
                continue
            name = (item.get("path") or "").lower()
            if name.endswith("chatterbox-server.exe"):
                server = item
                break
    sources = prov.get("sources") or {}
    engine = sources.get("engine") if isinstance(sources, dict) else {}
    if not engine and isinstance(prov.get("trident"), dict):
        engine = sources.get("engine", {})
    request = prov.get("request") or {}
    input_sha = request.get("input_sha256") or prov.get("input_sha256")
    wav_sha = None
    if wavs:
        wav_sha = prov.get("output", {}).get("wav_sha256") if isinstance(prov.get("output"), dict) else None
        if not wav_sha:
            wav_sha = sha256_file(wavs[0])
    return {
        "host": host,
        "run_id": run_dir.name,
        "variant": variant,
        "language": lang or request.get("language"),
        "has_wav": bool(wavs),
        "wav_path": str(wavs[0]) if wavs else None,
        "wav_sha256": wav_sha,
        "input_sha256": input_sha,
        "argv0": argv[0] if argv else None,
        "server": sl,
        "engine_head": (engine or {}).get("head"),
        "engine_pin": prov.get("engine_pin") or sources.get("engine_pin"),
        "server_exe_sha256": server.get("sha256"),
        "vulkan_device": (prov.get("host_inventory") or {}).get("selected_vulkan_device"),
    }


def main() -> int:
    out_path = ROOT / "working_report_runs.json"
    rows = []
    for host, sub in (("IrisXe", "runs"), ("Pascal", "runs-PASCAL")):
        root = ROOT / "models" / sub
        if not root.is_dir():
            continue
        for run_dir in sorted(root.iterdir()):
            if run_dir.is_dir():
                rows.append(catalog_run(host, run_dir))
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    pairs: dict[str, list] = {}
    for r in rows:
        key = f"{r.get('input_sha256')}|{r.get('variant')}|{r.get('language')}"
        pairs.setdefault(key, []).append(r["run_id"][:8])
    print(f"wrote {len(rows)} runs to {out_path}")
    print(f"cross-host pair groups: {sum(1 for v in pairs.values() if len(v) > 1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
