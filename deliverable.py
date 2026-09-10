from __future__ import annotations
import hashlib, json, shutil, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DELIVERABLES = ROOT / ".runtime-deliverables"
PLATFORM, BUILD_RECIPE = "win-x64", ("vulkan", "mtl-off", "shared")


def resolve_source(override: str | Path | None, default: Path) -> Path:
    path = Path(override).resolve() if override else default
    if override and not (path / "CMakeLists.txt").is_file():
        raise FileNotFoundError(f"chatterbox source missing CMakeLists.txt: {path}")
    return path.resolve() if path.is_dir() and (path / "CMakeLists.txt").is_file() else default.resolve()


def source_rev(source: Path, fallback_rev: str) -> str:
    proc = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True)
    head = proc.stdout.strip() if proc.returncode == 0 else fallback_rev
    diff = subprocess.run(
        ["git", "-C", str(source), "diff", "HEAD", "--", "src", "include", "CMakeLists.txt"],
        capture_output=True)
    if diff.returncode == 0 and diff.stdout:
        return f"{head}+{hashlib.sha256(diff.stdout).hexdigest()[:12]}"
    return head


def fingerprint(source: Path, ggml_rev: str, patch_path: Path, fallback_rev: str) -> str:
    patch_hash = hashlib.sha256(patch_path.read_bytes()).hexdigest()[:12] if patch_path.is_file() else "no-patch"
    key = "|".join([source_rev(source, fallback_rev), ggml_rev, patch_hash, PLATFORM, *BUILD_RECIPE])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def deliverable_dir(fp: str) -> Path:
    return DELIVERABLES / fp


def is_complete(fp: str, runtime_files: tuple[str, ...]) -> bool:
    return all((deliverable_dir(fp) / "bin" / name).is_file() for name in runtime_files)


def install_from_deliverable(fp: str, runtime_dir: Path, runtime_files: tuple[str, ...]) -> None:
    src = deliverable_dir(fp)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for name in runtime_files:
        shutil.copy2(src / "bin" / name, runtime_dir / name)
    for lic in (src / "licenses").glob("*.txt"):
        shutil.copy2(lic, runtime_dir / lic.name)


def save_deliverable(fp: str, build_bin: Path, source: Path, runtime_files: tuple[str, ...], *,
                    chatterbox_rev: str, ggml_rev: str) -> None:
    dest = deliverable_dir(fp)
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "bin").mkdir(parents=True)
    (dest / "licenses").mkdir()
    for name in runtime_files:
        shutil.copy2(build_bin / name, dest / "bin" / name)
    shutil.copy2(source / "LICENSE", dest / "licenses" / "chatterbox-LICENSE.txt")
    shutil.copy2(source / "ggml/LICENSE", dest / "licenses" / "ggml-LICENSE.txt")
    (dest / "manifest.json").write_text(json.dumps({
        "fingerprint": fp, "source": str(source), "source_rev": source_rev(source, chatterbox_rev),
        "chatterbox_rev": chatterbox_rev, "ggml_rev": ggml_rev, "platform": PLATFORM,
        "recipe": list(BUILD_RECIPE), "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": list(runtime_files),
    }, indent=2), encoding="utf-8")


def purge_deliverable(fp: str) -> None:
    shutil.rmtree(deliverable_dir(fp), ignore_errors=True)


def server_exe(spec: dict, runtime_dir: Path) -> Path:
    if spec.get("chatterbox_exe"):
        path = Path(spec["chatterbox_exe"]).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"--chatterbox-exe not found: {path}")
        return path
    path = runtime_dir / "chatterbox-server.exe"
    if not path.is_file():
        raise RuntimeError("TTS runtime missing; run --install or pass --chatterbox-exe")
    return path


def print_status(runtime_files: tuple[str, ...]) -> None:
    if not DELIVERABLES.is_dir():
        print("No cached deliverables.")
        return
    for entry in sorted(DELIVERABLES.iterdir()):
        if not entry.is_dir():
            continue
        man = json.loads((entry / "manifest.json").read_text(encoding="utf-8")) if (entry / "manifest.json").is_file() else {}
        mark = "OK" if is_complete(entry.name, runtime_files) else "INCOMPLETE"
        print(f"  [{mark}] {entry.name}  rev={str(man.get('source_rev', '?'))[:12]}  built={man.get('built_at', '?')}")
