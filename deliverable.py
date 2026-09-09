"""Persistent native TTS deliverables keyed by source fingerprint.

Builds are cached under .runtime-deliverables/<fingerprint>/ so repeated
install/probe runs skip cmake/msbuild unless the source or build recipe changes.
Use --chatterbox-exe to point at any pre-built chatterbox-server without install.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DELIVERABLES = ROOT / ".runtime-deliverables"
MANIFEST = "manifest.json"
BIN = "bin"
LICENSES = "licenses"
PLATFORM = "win-x64"
BUILD_RECIPE = ("vulkan", "mtl-off", "shared")


def resolve_source(override: str | Path | None, default: Path) -> Path:
    if override:
        path = Path(override).resolve()
        if not (path / "CMakeLists.txt").is_file():
            raise FileNotFoundError(f"chatterbox source missing CMakeLists.txt: {path}")
        return path
    if default.is_dir() and (default / "CMakeLists.txt").is_file():
        return default.resolve()
    return default.resolve()


def source_rev(source: Path, fallback_rev: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return proc.stdout.strip()
    return fallback_rev


def fingerprint(source: Path, ggml_rev: str, patch_path: Path, fallback_rev: str) -> str:
    rev = source_rev(source, fallback_rev)
    patch_hash = (
        hashlib.sha256(patch_path.read_bytes()).hexdigest()[:12]
        if patch_path.is_file() else "no-patch"
    )
    key = "|".join([rev, ggml_rev, patch_hash, PLATFORM, *BUILD_RECIPE])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def deliverable_dir(fp: str) -> Path:
    return DELIVERABLES / fp


def manifest_path(fp: str) -> Path:
    return deliverable_dir(fp) / MANIFEST


def is_complete(fp: str, runtime_files: tuple[str, ...]) -> bool:
    base = deliverable_dir(fp) / BIN
    return all((base / name).is_file() for name in runtime_files)


def load_manifest(fp: str) -> dict:
    path = manifest_path(fp)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_manifest(fp: str, data: dict) -> None:
    path = manifest_path(fp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def install_from_deliverable(fp: str, runtime_dir: Path, runtime_files: tuple[str, ...]) -> None:
    src_bin = deliverable_dir(fp) / BIN
    src_lic = deliverable_dir(fp) / LICENSES
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for name in runtime_files:
        shutil.copy2(src_bin / name, runtime_dir / name)
    for lic in src_lic.glob("*.txt"):
        shutil.copy2(lic, runtime_dir / lic.name)


def save_from_runtime(
    fp: str,
    runtime_dir: Path,
    source: Path,
    runtime_files: tuple[str, ...],
    *,
    chatterbox_rev: str,
    ggml_rev: str,
) -> None:
    dest = deliverable_dir(fp)
    bin_dir = dest / BIN
    lic_dir = dest / LICENSES
    if dest.exists():
        shutil.rmtree(dest)
    bin_dir.mkdir(parents=True)
    lic_dir.mkdir(parents=True)
    for name in runtime_files:
        shutil.copy2(runtime_dir / name, bin_dir / name)
    for lic_name in ("chatterbox-LICENSE.txt", "ggml-LICENSE.txt"):
        src = runtime_dir / lic_name
        if src.is_file():
            shutil.copy2(src, lic_dir / lic_name)
    save_manifest(fp, {
        "fingerprint": fp,
        "source": str(source),
        "source_rev": source_rev(source, chatterbox_rev),
        "chatterbox_rev": chatterbox_rev,
        "ggml_rev": ggml_rev,
        "platform": PLATFORM,
        "recipe": list(BUILD_RECIPE),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": list(runtime_files),
        "seeded_from_runtime": True,
    })


def save_deliverable(
    fp: str,
    build_bin: Path,
    source: Path,
    runtime_files: tuple[str, ...],
    *,
    chatterbox_rev: str,
    ggml_rev: str,
) -> None:
    dest = deliverable_dir(fp)
    bin_dir = dest / BIN
    lic_dir = dest / LICENSES
    if dest.exists():
        shutil.rmtree(dest)
    bin_dir.mkdir(parents=True)
    lic_dir.mkdir(parents=True)
    for name in runtime_files:
        shutil.copy2(build_bin / name, bin_dir / name)
    shutil.copy2(source / "LICENSE", lic_dir / "chatterbox-LICENSE.txt")
    shutil.copy2(source / "ggml/LICENSE", lic_dir / "ggml-LICENSE.txt")
    save_manifest(fp, {
        "fingerprint": fp,
        "source": str(source),
        "source_rev": source_rev(source, chatterbox_rev),
        "chatterbox_rev": chatterbox_rev,
        "ggml_rev": ggml_rev,
        "platform": PLATFORM,
        "recipe": list(BUILD_RECIPE),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": list(runtime_files),
    })


def purge_deliverable(fp: str) -> None:
    shutil.rmtree(deliverable_dir(fp), ignore_errors=True)


def server_exe(spec: dict, runtime_dir: Path) -> Path:
    override = spec.get("chatterbox_exe")
    if override:
        path = Path(override).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"--chatterbox-exe not found: {path}")
        return path
    path = runtime_dir / "chatterbox-server.exe"
    if not path.is_file():
        raise RuntimeError("TTS runtime missing; run --install or pass --chatterbox-exe")
    return path


def status(runtime_files: tuple[str, ...]) -> list[dict]:
    rows = []
    if not DELIVERABLES.is_dir():
        return rows
    for entry in sorted(DELIVERABLES.iterdir()):
        if not entry.is_dir():
            continue
        fp = entry.name
        manifest = load_manifest(fp)
        rows.append({
            "fingerprint": fp,
            "complete": is_complete(fp, runtime_files),
            "source_rev": manifest.get("source_rev"),
            "built_at": manifest.get("built_at"),
            "source": manifest.get("source"),
        })
    return rows


def print_status(runtime_files: tuple[str, ...]) -> None:
    rows = status(runtime_files)
    if not rows:
        print("No cached deliverables.")
        return
    for row in rows:
        mark = "OK" if row["complete"] else "INCOMPLETE"
        print(f"  [{mark}] {row['fingerprint']}  rev={row.get('source_rev', '?')[:12]}  built={row.get('built_at', '?')}")
        if row.get("source"):
            print(f"         source={row['source']}")
