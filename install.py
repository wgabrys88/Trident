from config import ensure_venv

ensure_venv()
if __name__ == "__main__":
    from main import main

    raise SystemExit(main("install"))


import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import venv
import zipfile
from pathlib import Path

from config import (
    CHATTERBOX, CHATTERBOX_REV, CHATTERBOX_URL, CODEC_FILE, DATA, GGML, GGML_GIT, HARDWARE, MODELS, ROOT,
    RUNTIMES, T3_FILE, TOOLS, TTS_BACKEND, TTS_MODELS, TTS_NANO_URLS, VOICE_FILE, VOICE_SHA256, VOICE_SIZE,
    VOICE_URL, find_exe,
)
from journal import file_identity, git_identity

DOWNLOADS = TOOLS / "downloads"
_BUILD_FLAGS = ["-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF", "-DTTS_CPP_BUILD_EXECUTABLES=ON"]


def _sha(path: Path) -> str:
    return file_identity(path)["sha256"]


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except Exception:
        return None


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
    if dest.exists():
        raise RuntimeError(f"refusing unverified existing artifact: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    paths.journal.emit("install", "fetch.start", name=dest.name, url=url)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Trident/2"}), timeout=120) as response, tmp.open("wb") as out:
        header_size = int(response.headers.get("Content-Length") or 0)
        shutil.copyfileobj(response, out, 4 << 20)
    got_size, got_sha = tmp.stat().st_size, _sha(tmp)
    if got_size != (size or header_size or got_size) or (sha256 and got_sha != sha256):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"download identity mismatch for {dest.name}")
    tmp.replace(dest)
    paths.journal.emit("install", "fetch.completed", name=dest.name, size=got_size, sha256=got_sha)
    return dest


def _git(dest: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(dest), *args], text=True).strip()


def _clean_repo(dest: Path) -> None:
    if _git(dest, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError(f"managed repository has tracked changes; refusing checkout: {dest}")


def pin(paths, url: str, rev: str, dest: Path, role: str) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").is_dir():
        if dest.exists():
            raise RuntimeError(f"refusing to replace {dest}")
        _run(paths, ["git", "clone", "--filter=blob:none", url, str(dest)], role=f"git-{role}")
    _clean_repo(dest)
    _run(paths, ["git", "fetch", "--depth", "1", "origin", rev], dest, role=f"git-{role}")
    fetched = _git(dest, "rev-parse", rev)
    if fetched != _git(dest, "rev-parse", "HEAD"):
        _run(paths, ["git", "checkout", "--detach", rev], dest, role=f"git-{role}")
    _clean_repo(dest)
    paths.journal.emit("install", "repository.ready", role=role, requested=rev, sha=fetched, dirty=False)
    return fetched


def _tool(command: list[str], first_line: bool = True) -> str:
    try:
        value = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=30).strip()
        return value.splitlines()[0] if first_line else value
    except Exception:
        return "unavailable"


def _cache_values(path: Path) -> dict:
    keys = ("CMAKE_CXX_COMPILER:", "CMAKE_CXX_COMPILER_VERSION:", "CMAKE_GENERATOR:")
    return {
        line.split("=", 1)[0].split(":", 1)[0]: line.split("=", 1)[1]
        for line in (path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else [])
        if line.startswith(keys)
    }


def build_tts(paths) -> None:
    ggml_sha = pin(paths, *GGML_GIT, GGML, "ggml")
    build = TOOLS / "tts-build"
    _run(paths, ["cmake", "-S", str(CHATTERBOX), "-B", str(build), "-A", "x64", *_BUILD_FLAGS], role="cmake-configure")
    _run(paths, ["cmake", "--build", str(build), "--config", "Release", "--target", "chatterbox-server", "--parallel"], role="cmake-build")
    _clean_repo(GGML)
    built, server = build / "bin", build / "bin" / "chatterbox-server.exe"
    if not server.is_file():
        raise RuntimeError("chatterbox-server.exe missing after build")
    dest = RUNTIMES / "tts"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(server, dest / server.name)
    for dll in built.glob("*.dll"):
        shutil.copy2(dll, dest / dll.name)
    receipt = {
        "hardware": HARDWARE, "backend": TTS_BACKEND, "chatterbox": git_identity(CHATTERBOX), "ggml_sha": ggml_sha,
        "build_flags": _BUILD_FLAGS,
        "cmake": _tool(["cmake", "--version"]), "compiler": _cache_values(build / "CMakeCache.txt"),
        "executable": file_identity(dest / server.name),
    }
    _write_json(dest / "provenance.json", receipt)
    paths.journal.emit("install", "tts.build.completed", **receipt)


def tts_provenance() -> dict:
    exe = RUNTIMES / "tts" / "chatterbox-server.exe"
    receipt = _read_json(RUNTIMES / "tts" / "provenance.json")
    source, actual = git_identity(CHATTERBOX), file_identity(exe)
    checks = {
        "receipt": isinstance(receipt, dict),
        "configured source": source.get("sha") == CHATTERBOX_REV and source.get("dirty") is False,
        "built source": isinstance(receipt, dict) and receipt.get("chatterbox", {}).get("sha") == CHATTERBOX_REV and receipt.get("chatterbox", {}).get("dirty") is False,
        "hardware": isinstance(receipt, dict) and receipt.get("hardware") == HARDWARE,
        "backend": isinstance(receipt, dict) and receipt.get("backend") == TTS_BACKEND,
        "ggml": isinstance(receipt, dict) and receipt.get("ggml_sha") == GGML_GIT[1],
        "build flags": isinstance(receipt, dict) and receipt.get("build_flags") == _BUILD_FLAGS,
        "executable": isinstance(receipt, dict) and receipt.get("executable", {}).get("size") == actual.get("size") and receipt.get("executable", {}).get("sha256") == actual.get("sha256"),
    }
    if failed := [name for name, valid in checks.items() if not valid]:
        raise RuntimeError(f"installed TTS provenance mismatch ({', '.join(failed)}); run python main.py install")
    return {**receipt, "configured_pin": CHATTERBOX_REV, "executable": actual}


def _build_valid() -> bool:
    try:
        tts_provenance()
        return True
    except RuntimeError:
        return False


def install_python(paths) -> None:
    env = ROOT / ".venv"
    python, req, marker = env / "Scripts" / "python.exe", ROOT / "requirements.txt", env / ".requirements.sha256"
    digest = _sha(req)
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(env)
    if not marker.is_file() or marker.read_text(encoding="ascii").strip() != digest:
        if digest.strip():
            _run(paths, [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", "-r", str(req)], role="runtime-pip")
        marker.write_text(digest + "\n", encoding="ascii")
    if python.is_file():
        freeze = subprocess.check_output([str(python), "-m", "pip", "freeze", "--all"], text=True, encoding="utf-8", errors="replace", timeout=60).splitlines()
        paths.journal.emit("install", "python.ready", interpreter=str(python), requirements_sha256=digest, packages=freeze)
    else:
        paths.journal.emit("install", "python.ready", interpreter=str(python), requirements_sha256=digest, packages=[])


def install(models_dir: Path | None, data_dir: Path | None, paths) -> None:
    if sys.version_info < (3, 11) or os.name != "nt":
        raise RuntimeError("Trident needs Python 3.11+ on Windows")
    models, data = Path(models_dir or MODELS), Path(data_dir or DATA)
    models.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    paths.journal.emit("install", "start", hardware=HARDWARE, chatterbox_revision=CHATTERBOX_REV, ggml_revision=GGML_GIT[1])
    pin(paths, CHATTERBOX_URL, CHATTERBOX_REV, CHATTERBOX, "chatterbox")
    if not _build_valid():
        build_tts(paths)
    else:
        paths.journal.emit("install", "tts.build.ready", executable=file_identity(RUNTIMES / "tts" / "chatterbox-server.exe"))
    t3_file, codec_file = TTS_MODELS["nano"]
    pull(paths, TTS_NANO_URLS[t3_file][0], models / t3_file)
    pull(paths, TTS_NANO_URLS[codec_file][0], models / codec_file)
    pull(paths, VOICE_URL, data / VOICE_FILE, VOICE_SIZE, VOICE_SHA256)
    install_python(paths)
    paths.journal.emit("install", "completed", models={family: list(names) for family, names in TTS_MODELS.items()}, chatterbox=git_identity(CHATTERBOX), ggml=git_identity(GGML))
