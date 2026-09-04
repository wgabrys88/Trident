import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from config import (
    MODELS, PARAKEET_FILE, PARAKEET_SHA256, PARAKEET_SIZE, PARAKEET_URL, PARAKEET_ZIP,
    ROOT, RUNTIMES, TOOLS, find_exe,
)
from journal import file_identity, git_identity

DOWNLOADS = TOOLS / "downloads"


def _sha(path: Path) -> str:
    return file_identity(path)["sha256"]


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    try: return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except Exception: return None


def _run(paths, cmd, cwd=None, env=None, role="exec") -> str:
    log, joined, t0 = paths.journal.sidecar(role), " ".join(map(str, cmd)), time.perf_counter_ns()
    paths.journal.emit("install", "exec.start", command=joined, sidecar=log.name)
    with log.open("ab") as out:
        proc = subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    elapsed = (time.perf_counter_ns() - t0) / 1e6
    if proc.returncode:
        tail = log.read_bytes()[-2400:].decode("utf-8", "replace").replace("\r", "\n")[-1800:]
        paths.journal.emit("install", "exec.failed", command=joined, returncode=proc.returncode, elapsed_ms=round(elapsed, 3), tail=tail)
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    paths.journal.emit("install", "exec.completed", command=joined, elapsed_ms=round(elapsed, 3))
    return log.read_text(encoding="utf-8", errors="replace")


def pull(paths, url: str, dest: Path, size: int = 0, sha256: str = "") -> Path:
    if dest.is_file() and (not size or dest.stat().st_size == size) and (not sha256 or _sha(dest) == sha256):
        paths.journal.emit("install", "fetch.ready", name=dest.name, size=dest.stat().st_size, sha256=_sha(dest))
        return dest
    if dest.exists(): raise RuntimeError(f"refusing unverified existing artifact: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part"); tmp.unlink(missing_ok=True)
    paths.journal.emit("install", "fetch.start", name=dest.name, url=url)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Trident/2"}), timeout=120) as response, tmp.open("wb") as out:
        header_size = int(response.headers.get("Content-Length") or 0)
        shutil.copyfileobj(response, out, 4 << 20)
    got_size, got_sha = tmp.stat().st_size, _sha(tmp)
    if got_size != (size or header_size or got_size) or (sha256 and got_sha != sha256):
        tmp.unlink(missing_ok=True); raise RuntimeError(f"download identity mismatch for {dest.name}")
    tmp.replace(dest)
    paths.journal.emit("install", "fetch.completed", name=dest.name, size=got_size, sha256=got_sha)
    return dest


def install_runtime(paths, role: str, exe_name: str, spec: tuple[str, str, int, str]) -> None:
    url, archive_name, expected_size, expected_sha = spec
    dest, archive, receipt_path = RUNTIMES / role, DOWNLOADS / archive_name, RUNTIMES / role / "provenance.json"
    receipt, exe = _read_json(receipt_path), find_exe(dest, exe_name)
    if (isinstance(receipt, dict) and exe is not None and receipt.get("archive", {}).get("sha256") == expected_sha
            and receipt.get("executable", {}).get("size") == exe.stat().st_size):
        paths.journal.emit("install", "runtime.ready", role=role, executable=receipt["executable"], archive=receipt.get("archive"))
        return
    if dest.exists(): shutil.rmtree(dest)
    archive = pull(paths, url, archive, expected_size, expected_sha)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z: z.extractall(dest)
    paths.journal.emit("install", "archive.completed", archive=archive.name, destination=str(dest))
    if (exe := find_exe(dest, exe_name)) is None: raise RuntimeError(f"{exe_name} missing from verified {archive_name}")
    receipt = {"role": role, "url": url, "archive": file_identity(archive),
               "executable": {"path": str(exe), "size": exe.stat().st_size}, "version": _tool([str(exe), "--version"])}
    _write_json(receipt_path, receipt)
    paths.journal.emit("install", "runtime.completed", **receipt)


def _tool(command: list[str], first_line: bool = True) -> str:
    try:
        value = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=30).strip()
        return value.splitlines()[0] if first_line else value
    except Exception:
        return "unavailable"


def install(models_dir: Path | None, data_dir: Path | None, paths) -> None:
    if sys.version_info < (3, 11): raise RuntimeError("Trident needs Python 3.11+")
    if not os.name == "nt": raise RuntimeError("Trident requires Windows")
    models = Path(models_dir or MODELS); models.mkdir(parents=True, exist_ok=True)
    paths.journal.emit("install", "start")
    install_runtime(paths, "parakeet", "parakeet-server.exe", PARAKEET_ZIP)
    pull(paths, PARAKEET_URL, models / PARAKEET_FILE, PARAKEET_SIZE, PARAKEET_SHA256)
    paths.journal.emit("install", "completed", models={"asr": [PARAKEET_FILE]})