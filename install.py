from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import venv
import zipfile
from pathlib import Path

from config import (CHATTERBOX, CHATTERBOX_REV, CHATTERBOX_URL, CODEC_QUANT, CONVERTER, DATA, GEMMA_FILE,
                    GEMMA_SHA256, GEMMA_SIZE, GEMMA_URL, GGML, GGML_GIT, HARDWARE, LLAMA_ZIP, MODELS,
                    PARAKEET_FILE, PARAKEET_SHA256, PARAKEET_SIZE, PARAKEET_URL, PARAKEET_ZIP, ROOT, RUNTIMES,
                    SMART_TURN_FILE, SMART_TURN_SHA256, SMART_TURN_SIZE,
                    SMART_TURN_URL, TOOLS, TTS, TTS_BACKEND, TTS_MODELS, TTS_WEIGHTS, VOICE_HF, VOICES, find_exe)
from journal import file_identity, git_identity

DOWNLOADS, RECEIPTS = TOOLS / "downloads", TOOLS / "receipts"
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
CONVERTER_PINS = {"torch": "2.6.0", "numpy": "1.26.4", "gguf": "0.19.0", "safetensors": "0.5.3", "scipy": "1.15.3",
                  "librosa": "0.11.0", "resampy": "0.4.3", "huggingface-hub": "0.34.4"}


def _sha(path: Path) -> str:
    return file_identity(path)["sha256"]


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _run(paths, cmd, cwd=None, env=None, role="exec") -> str:
    log = paths.journal.sidecar(role)
    paths.journal.emit("install", "exec.start", command=" ".join(map(str, cmd)), sidecar=log.name)
    t0 = time.perf_counter_ns()
    with log.open("ab") as out:
        proc = subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    elapsed = (time.perf_counter_ns() - t0) / 1e6
    if proc.returncode:
        tail = log.read_bytes()[-2400:].decode("utf-8", "replace").replace("\r", "\n")[-1800:]
        paths.journal.emit("install", "exec.failed", command=" ".join(map(str, cmd)), returncode=proc.returncode, elapsed_ms=round(elapsed, 3), tail=tail)
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    paths.journal.emit("install", "exec.completed", command=" ".join(map(str, cmd)), elapsed_ms=round(elapsed, 3))
    return log.read_text(encoding="utf-8", errors="replace")


def _download_receipt(url: str) -> Path:
    return RECEIPTS / "downloads" / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".json")


def _verified_file(path: Path, size=0, sha256="") -> bool:
    return path.is_file() and (not size or path.stat().st_size == size) and (not sha256 or _sha(path) == sha256)


def pull(paths, url: str, dest: Path, size: int = 0, sha256: str = "") -> Path:
    receipt_path = _download_receipt(url)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {}
    expected_size = int(size or receipt.get("size") or 0)
    expected_sha = sha256 or str(receipt.get("sha256") or "")
    if _verified_file(dest, expected_size, expected_sha):
        paths.journal.emit("install", "fetch.ready", name=dest.name, size=dest.stat().st_size, sha256=_sha(dest))
        return dest
    if dest.exists():
        raise RuntimeError(f"refusing unverified existing artifact: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part"); tmp.unlink(missing_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Trident/2"})
    paths.journal.emit("install", "fetch.start", name=dest.name, url=url)
    with urllib.request.urlopen(req, timeout=120) as response, tmp.open("wb") as out:
        header_size = int(response.headers.get("Content-Length") or 0)
        etag = (response.headers.get("ETag") or "").strip('"').split(":")[-1]
        shutil.copyfileobj(response, out, 4 << 20)
    got_size, got_sha = tmp.stat().st_size, _sha(tmp)
    authoritative_size = int(size or header_size or got_size)
    authoritative_sha = sha256 or (etag.lower() if HEX64.fullmatch(etag) else got_sha)
    if got_size != authoritative_size or got_sha != authoritative_sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"download identity mismatch for {dest.name}")
    tmp.replace(dest)
    _write_json(receipt_path, {"url": url, "size": got_size, "sha256": got_sha, "etag": etag})
    paths.journal.emit("install", "fetch.completed", name=dest.name, size=got_size, sha256=got_sha)
    return dest


def unzip(paths, archive: Path, dest: Path) -> None:
    if dest.exists(): raise RuntimeError(f"refusing to replace {dest}")
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z: z.extractall(dest)
    paths.journal.emit("install", "archive.completed", archive=archive.name, destination=str(dest))



def install_runtime(paths, role: str, exe_name: str, spec: tuple[str, str, int, str]) -> None:
    url, archive_name, expected_size, expected_sha = spec
    dest, archive = RUNTIMES / role, DOWNLOADS / archive_name
    receipt_path = dest / "provenance.json"
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            exe = find_exe(dest, exe_name)
            if (exe is not None and receipt.get("archive", {}).get("sha256") == expected_sha
                    and receipt.get("executable", {}).get("size") == exe.stat().st_size):
                paths.journal.emit("install", "runtime.ready", role=role, executable=receipt["executable"], archive=receipt.get("archive"))
                return
        except Exception:
            pass
    if dest.exists():
        shutil.rmtree(dest)
    archive = pull(paths, url, archive, expected_size, expected_sha)
    unzip(paths, archive, dest)
    exe = find_exe(dest, exe_name)
    if exe is None:
        raise RuntimeError(f"{exe_name} missing from verified {archive_name}")
    receipt = {"role": role, "url": url, "archive": file_identity(archive),
               "executable": {"path": str(exe), "size": exe.stat().st_size}, "version": _tool([str(exe), "--version"])}
    _write_json(receipt_path, receipt)
    paths.journal.emit("install", "runtime.completed", **receipt)

def _clean_repo(dest: Path) -> None:
    if subprocess.check_output(["git", "-C", str(dest), "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        raise RuntimeError(f"managed repository has tracked changes; refusing checkout: {dest}")


def pin(paths, url: str, rev: str, dest: Path, role: str) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").is_dir():
        if dest.exists(): raise RuntimeError(f"refusing to replace {dest}")
        _run(paths, ["git", "clone", "--filter=blob:none", url, str(dest)], role=f"git-{role}")
    _clean_repo(dest)
    target = "FETCH_HEAD" if rev == "latest" else rev
    _run(paths, ["git", "fetch", "--depth", "1", "origin", "HEAD" if rev == "latest" else rev], dest, role=f"git-{role}")
    fetched = subprocess.check_output(["git", "-C", str(dest), "rev-parse", target], text=True).strip()
    current = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    if current != fetched: _run(paths, ["git", "checkout", "--detach", target], dest, role=f"git-{role}")
    _clean_repo(dest)
    paths.journal.emit("install", "repository.ready", role=role, requested=rev, sha=fetched, dirty=False)
    return fetched


def _tool(command: list[str], first_line: bool = True) -> str:
    try:
        value = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=30).strip()
        return value.splitlines()[0] if first_line else value
    except Exception: return "unavailable"


def _cache_values(path: Path) -> dict:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(("CMAKE_CXX_COMPILER:", "CMAKE_CXX_COMPILER_VERSION:", "CMAKE_GENERATOR:")):
                key, value = line.split("=", 1); values[key.split(":", 1)[0]] = value
    return values


def _server_source_identity() -> str:
    h = hashlib.sha256()
    for path in (TTS / "CMakeLists.txt", TTS / "src" / "server.cpp"):
        h.update(path.read_bytes())
    return h.hexdigest()


def _build_flags() -> list[str]:
    # North Star GPU path is Vulkan on both Intel Iris Xe and NVIDIA Pascal.
    # CUDA is intentionally not a build/runtime dependency.
    return ["-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF"]


def build_tts(paths) -> None:
    ggml_sha = pin(paths, *GGML_GIT, GGML, "ggml")
    build = TTS / "build"
    flags = _build_flags()
    _run(paths, ["cmake", "-S", ".", "-B", "build", "-A", "x64", f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}", *flags], TTS, role="cmake-configure")
    _run(paths, ["cmake", "--build", "build", "--config", "Release", "--target", "trident-tts-server", "--parallel"], TTS, role="cmake-build")
    _clean_repo(GGML)
    built = build / "bin"; server = built / "trident-tts-server.exe"
    if not server.is_file(): raise RuntimeError("trident-tts-server.exe missing after build")
    dest = RUNTIMES / "tts"; dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(server, dest / server.name)
    for dll in built.glob("*.dll"): shutil.copy2(dll, dest / dll.name)
    receipt = {
        "hardware": HARDWARE, "backend": TTS_BACKEND, "chatterbox": git_identity(CHATTERBOX), "ggml_sha": ggml_sha,
        "server_source_sha256": _server_source_identity(), "build_flags": flags,
        "cmake": _tool(["cmake", "--version"]), "compiler": _cache_values(build / "CMakeCache.txt"),
        "executable": file_identity(dest / server.name),
    }
    _write_json(dest / "provenance.json", receipt)
    paths.journal.emit("install", "tts.build.completed", **receipt)


def _build_valid() -> bool:
    receipt_path = RUNTIMES / "tts" / "provenance.json"; exe = RUNTIMES / "tts" / "trident-tts-server.exe"
    if not receipt_path.is_file() or not exe.is_file(): return False
    try: receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception: return False
    return (receipt.get("hardware") == HARDWARE and receipt.get("chatterbox", {}).get("sha") == git_identity(CHATTERBOX).get("sha")
            and receipt.get("chatterbox", {}).get("dirty") is False and receipt.get("ggml_sha") == GGML_GIT[1]
            and receipt.get("backend") == TTS_BACKEND and receipt.get("build_flags") == _build_flags()
            and receipt.get("server_source_sha256") == _server_source_identity()
            and receipt.get("executable", {}).get("sha256") == _sha(exe))


def _converter(paths) -> tuple[Path, dict]:
    py = CONVERTER / "Scripts" / "python.exe"
    if not py.is_file():
        _run(paths, [sys.executable, "-m", "venv", str(CONVERTER)], role="converter-venv")
    code = "import importlib.metadata,json; print(json.dumps({n:importlib.metadata.version(n) if True else '' for n in " + repr(tuple(CONVERTER_PINS)) + "}))"
    try: installed = json.loads(subprocess.check_output([str(py), "-c", code], text=True, stderr=subprocess.DEVNULL, timeout=30))
    except Exception: installed = {}
    if any(installed.get(name) != version for name, version in CONVERTER_PINS.items()):
        _run(paths, [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], role="converter-pip")
        packages = [f"{name}=={version}" for name, version in CONVERTER_PINS.items() if name != "torch"]
        _run(paths, [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", *packages], role="converter-pip")
    versions = _tool([str(py), "-m", "pip", "freeze"], first_line=False)
    return py, {"python": _tool([str(py), "--version"]), "required": CONVERTER_PINS, "packages": versions.splitlines()}


def _snapshot(paths, py: Path, env: dict, spec: dict, dest: Path) -> None:
    code = "from huggingface_hub import snapshot_download;" + f"snapshot_download(repo_id={spec['repo']!r},revision={spec['rev']!r},allow_patterns={list(spec['files'])!r},local_dir={str(dest)!r})"
    _run(paths, [str(py), "-c", code], ROOT, env, role="hf-snapshot")
    missing = [name for name in spec["files"] if not (dest / name).is_file()]
    if missing: raise RuntimeError(f"checkpoint snapshot incomplete: {missing}")


def _conversion_identity(family: str, models: Path, converter: dict) -> dict:
    spec = TTS_WEIGHTS[family]; t3, s3 = (models / name for name in TTS_MODELS[family])
    scripts = [CHATTERBOX / "scripts" / spec["t3"], CHATTERBOX / "scripts" / "convert-s3gen-to-gguf.py"]
    ckpt = CONVERTER / spec["ckpt"]
    return {"family": family, "checkpoint_repo": spec["repo"], "checkpoint_revision": spec["rev"],
            "checkpoint_files": {name: file_identity(ckpt / name) for name in spec["files"]},
            "converter_repository": git_identity(CHATTERBOX), "converter_scripts": {p.name: _sha(p) for p in scripts},
            "tool_versions": converter, "quantization": {"t3": "q4_0", "s3gen": CODEC_QUANT},
            "outputs": {"t3": file_identity(t3), "s3gen": file_identity(s3)}}


def _conversion_valid(family: str, models: Path, converter: dict | None = None) -> bool:
    receipt_path = models / f".{family}-provenance.json"; spec = TTS_WEIGHTS[family]
    if not receipt_path.is_file(): return False
    try: receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception: return False
    scripts = [CHATTERBOX / "scripts" / spec["t3"], CHATTERBOX / "scripts" / "convert-s3gen-to-gguf.py"]
    expected_scripts = {p.name: _sha(p) for p in scripts if p.is_file()}
    if (receipt.get("checkpoint_repo") != spec["repo"] or receipt.get("checkpoint_revision") != spec["rev"]
            or receipt.get("converter_repository", {}).get("sha") != git_identity(CHATTERBOX).get("sha")
            or receipt.get("converter_scripts") != expected_scripts
            or receipt.get("quantization") != {"t3": "q4_0", "s3gen": CODEC_QUANT}
            or (converter is not None and receipt.get("tool_versions") != converter)):
        return False
    ckpt = CONVERTER / spec["ckpt"]
    current_checkpoints = {name: file_identity(ckpt / name) for name in spec["files"]}
    if receipt.get("checkpoint_files") != current_checkpoints:
        return False
    for role, name in zip(("t3", "s3gen"), TTS_MODELS[family]):
        path = models / name; recorded = receipt.get("outputs", {}).get(role, {})
        if not path.is_file() or recorded.get("size") != path.stat().st_size or recorded.get("sha256") != _sha(path): return False
    return True


def convert_tts(paths, models: Path, families: list[str]) -> None:
    py, tools = _converter(paths); env = os.environ.copy(); env.update(HF_HOME=str(TOOLS / "huggingface"), HF_HUB_DISABLE_SYMLINKS="1")
    for family in families:
        if _conversion_valid(family, models, tools):
            paths.journal.emit("install", "tts.convert.ready", family=family); continue
        spec = TTS_WEIGHTS[family]; ckpt = CONVERTER / spec["ckpt"]
        _snapshot(paths, py, env, spec, ckpt)
        t3, s3 = (models / name for name in TTS_MODELS[family]); temps = [p.with_suffix(p.suffix + ".new") for p in (t3, s3)]
        for temp in temps: temp.unlink(missing_ok=True)
        cmd = [str(py), str(CHATTERBOX / "scripts" / spec["t3"])]
        if model := spec.get("model"): cmd += ["--model", model]
        _run(paths, cmd + ["--ckpt-dir", str(ckpt), "--out", str(temps[0]), "--quant", "q4_0"], ROOT, env, role=f"convert-{family}-t3")
        _run(paths, [str(py), str(CHATTERBOX / "scripts" / "convert-s3gen-to-gguf.py"), "--variant", spec["s3"], "--ckpt-dir", str(ckpt), "--out", str(temps[1]), "--quant", CODEC_QUANT], ROOT, env, role=f"convert-{family}-s3")
        if not all(p.is_file() and p.stat().st_size for p in temps): raise RuntimeError(f"{family} conversion produced incomplete output")
        for temp, final in zip(temps, (t3, s3)): os.replace(temp, final)
        receipt = _conversion_identity(family, models, tools); _write_json(models / f".{family}-provenance.json", receipt)
        paths.journal.emit("install", "tts.convert.completed", **receipt)


def install_python(paths) -> None:
    env = ROOT / ".venv"; python, req, marker = env / "Scripts" / "python.exe", ROOT / "requirements.txt", env / ".requirements.sha256"
    digest = _sha(req)
    if not python.is_file(): venv.EnvBuilder(with_pip=True).create(env)
    if not marker.is_file() or marker.read_text(encoding="ascii").strip() != digest:
        _run(paths, [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", "-r", str(req)], role="runtime-pip")
        marker.write_text(digest + "\n", encoding="ascii")
    freeze = subprocess.check_output([str(python), "-m", "pip", "freeze", "--all"], text=True, encoding="utf-8", errors="replace", timeout=60).splitlines()
    paths.journal.emit("install", "python.ready", interpreter=str(python), requirements_sha256=digest, packages=freeze)


def install(models_dir: Path | None, data_dir: Path | None, paths) -> None:
    if sys.version_info < (3, 11) or os.name != "nt": raise RuntimeError("Trident needs Python 3.11+ on Windows")
    models, data = Path(models_dir or MODELS), Path(data_dir or DATA); models.mkdir(parents=True, exist_ok=True); data.mkdir(parents=True, exist_ok=True)
    paths.journal.emit("install", "start", hardware=HARDWARE, chatterbox_revision=CHATTERBOX_REV, ggml_revision=GGML_GIT[1])
    pin(paths, CHATTERBOX_URL, CHATTERBOX_REV, CHATTERBOX, "chatterbox")
    if not _build_valid(): build_tts(paths)
    else: paths.journal.emit("install", "tts.build.ready", executable=file_identity(RUNTIMES / "tts" / "trident-tts-server.exe"))
    convert_tts(paths, models, list(TTS_MODELS))
    install_runtime(paths, "parakeet", "parakeet-server.exe", PARAKEET_ZIP)
    install_runtime(paths, "gemma", "llama-server.exe", LLAMA_ZIP)
    for source, name, size, sha256 in VOICES.values(): pull(paths, VOICE_HF + source, data / name, size, sha256)
    pull(paths, PARAKEET_URL, models / PARAKEET_FILE, PARAKEET_SIZE, PARAKEET_SHA256); pull(paths, GEMMA_URL, models / GEMMA_FILE, GEMMA_SIZE, GEMMA_SHA256)
    pull(paths, SMART_TURN_URL, models / SMART_TURN_FILE, SMART_TURN_SIZE, SMART_TURN_SHA256)
    install_python(paths)
    paths.journal.emit("install", "completed", models={family: list(names) for family, names in TTS_MODELS.items()}, chatterbox=git_identity(CHATTERBOX), ggml=git_identity(GGML))
