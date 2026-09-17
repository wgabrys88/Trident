"""Compare T3 raw_ids between paired runs (same input_sha256)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def raw_ids(run_dir: Path) -> list[int] | None:
    for path in run_dir.glob("*.engine.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "t3_result" and ev.get("unit_index") == 0:
                return ev.get("raw_ids")
    return None


def input_sha(run_dir: Path) -> str | None:
    for path in run_dir.glob("*.provenance.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        req = data.get("request") or {}
        return req.get("input_sha256") or data.get("input_sha256")
    return None


def main() -> int:
    by_key: dict[str, dict[str, Path]] = {}
    for sub in ("runs", "runs-PASCAL"):
        root = ROOT / "models" / sub
        if not root.is_dir():
            continue
        host = "IrisXe" if sub == "runs" else "Pascal"
        for run_dir in root.iterdir():
            if not run_dir.is_dir() or not list(run_dir.glob("*.wav")):
                continue
            sha = input_sha(run_dir)
            if not sha:
                continue
            by_key.setdefault(sha, {})[host] = run_dir

    results = []
    for sha, hosts in sorted(by_key.items()):
        if len(hosts) < 2:
            continue
        iris = raw_ids(hosts["IrisXe"])
        pas = raw_ids(hosts["Pascal"])
        if iris is None or pas is None:
            continue
        first = None
        for i, (a, b) in enumerate(zip(iris, pas)):
            if a != b:
                first = i
                break
        results.append(
            {
                "input_sha256": sha,
                "iris_run": hosts["IrisXe"].name,
                "pascal_run": hosts["Pascal"].name,
                "len_iris": len(iris),
                "len_pascal": len(pas),
                "first_divergence_index": first,
                "ids_match": first is None and len(iris) == len(pas),
            }
        )
    out = ROOT / "working_report_cross_host_t3.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"paired comparisons: {len(results)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
