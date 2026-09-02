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

from config import (CHATTERBOX, CHATTERBOX_REV, CHATTERBOX_URL, CODEC_QUANT, CONVERTER, DATA, GEMMA_FILE,
                    GEMMA_SHA256, GEMMA_SIZE, GEMMA_URL, GGML, GGML_GIT, HARDWARE, LLAMA_ZIP, MODELS,
                    PARAKEET, PARAKEET_FILE, PARAKEET_GIT_URL, PARAKEET_REV, PARAKEET_SHA256, PARAKEET_SIZE,
                    PARAKEET_URL, ROOT, RUNTIMES, SMART_TURN_FILE, SMART_TURN_SHA256, SMART_TURN_SIZE,
                    SMART_TURN_URL, TOOLS, TTS_BACKEND, TTS_MODELS, TTS_WEIGHTS, VOICE_HF, VOICES, find_exe)
from journal import file_identity, git_identity

DOWNLOADS = TOOLS / "downloads"
CONVERTER_PINS = {"torch": "2.6.0", "numpy": "1.26.4", "gguf": "0.19.0", "safetensors": "0.5.3", "scipy": "1.15.3",
                  "librosa": "0.11.0", "resampy": "0.4.3", "huggingface-hub": "0.34.4"}
_PIP = ("-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input")
_BUILD_FLAGS = ["-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF", "-DTTS_CPP_BUILD_EXECUTABLES=ON"]
_PARAKEET_FLAGS = [
    "-DPARAKEET_SHARED=ON", "-DPARAKEET_GGML_VULKAN=ON", "-DPARAKEET_GGML_CUDA=OFF",
    "-DPARAKEET_BUILD_CLI=OFF", "-DPARAKEET_BUILD_SERVER=OFF", "-DPARAKEET_BUILD_TESTS=OFF",
    "-DBUILD_SHARED_LIBS=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF", "-DPARAKEET_VERSION=0.5.0",
]


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


def install_runtime(paths, role: str, artifact_name: str, spec: tuple[str, str, int, str], version_name: str | None = None) -> None:
    url, archive_name, expected_size, expected_sha = spec
    dest, archive, receipt_path = RUNTIMES / role, DOWNLOADS / archive_name, RUNTIMES / role / "provenance.json"
    receipt, artifact = _read_json(receipt_path), find_exe(dest, artifact_name)
    if (isinstance(receipt, dict) and artifact is not None and receipt.get("archive", {}).get("sha256") == expected_sha
            and receipt.get("artifact", {}).get("sha256") == _sha(artifact)):
        paths.journal.emit("install", "runtime.ready", role=role, artifact=receipt["artifact"], archive=receipt.get("archive"))
        return
    if dest.exists(): shutil.rmtree(dest)
    archive = pull(paths, url, archive, expected_size, expected_sha)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z: z.extractall(dest)
    paths.journal.emit("install", "archive.completed", archive=archive.name, destination=str(dest))
    if (artifact := find_exe(dest, artifact_name)) is None: raise RuntimeError(f"{artifact_name} missing from verified {archive_name}")
    version_tool = find_exe(dest, version_name) if version_name else artifact
    receipt = {"role": role, "url": url, "archive": file_identity(archive), "artifact": file_identity(artifact),
               "version": _tool([str(version_tool), "--version"]) if version_tool else "unavailable"}
    _write_json(receipt_path, receipt)
    paths.journal.emit("install", "runtime.completed", **receipt)


def _git(dest: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(dest), *args], text=True).strip()


def _clean_repo(dest: Path) -> None:
    if _git(dest, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError(f"managed repository has tracked changes; refusing checkout: {dest}")


def pin(paths, url: str, rev: str, dest: Path, role: str, submodules: bool = False) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").is_dir():
        if dest.exists(): raise RuntimeError(f"refusing to replace {dest}")
        _run(paths, ["git", "clone", "--filter=blob:none", url, str(dest)], role=f"git-{role}")
    elif submodules:
        _run(paths, ["git", "reset", "--hard", "HEAD"], dest, role=f"git-{role}")
    else:
        _clean_repo(dest)
    _run(paths, ["git", "fetch", "--depth", "1", "origin", rev], dest, role=f"git-{role}")
    fetched = _git(dest, "rev-parse", rev)
    if fetched != _git(dest, "rev-parse", "HEAD"): _run(paths, ["git", "checkout", "--detach", rev], dest, role=f"git-{role}")
    if submodules:
        _run(paths, ["git", "submodule", "sync", "--recursive"], dest, role=f"git-{role}")
        _run(paths, ["git", "submodule", "update", "--init", "--recursive", "--force"], dest, role=f"git-{role}")
    else:
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
    return {line.split("=", 1)[0].split(":", 1)[0]: line.split("=", 1)[1]
            for line in (path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else [])
            if line.startswith(keys)}


def build_tts(paths) -> None:
    ggml_sha = pin(paths, *GGML_GIT, GGML, "ggml")
    build = TOOLS / "tts-build"
    _run(paths, ["cmake", "-S", str(CHATTERBOX), "-B", str(build), "-A", "x64", *_BUILD_FLAGS], role="cmake-configure")
    _run(paths, ["cmake", "--build", str(build), "--config", "Release", "--target", "chatterbox-server", "--parallel"], role="cmake-build")
    _clean_repo(GGML)
    built, server = build / "bin", build / "bin" / "chatterbox-server.exe"
    if not server.is_file(): raise RuntimeError("chatterbox-server.exe missing after build")
    dest = RUNTIMES / "tts"; dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(server, dest / server.name)
    for dll in built.glob("*.dll"): shutil.copy2(dll, dest / dll.name)
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
    try: tts_provenance(); return True
    except RuntimeError: return False


def _git_apply(repo: Path, patch: Path, check: bool = False, reverse: bool = False) -> bool:
    cmd = ["git", "-C", str(repo), "apply"]
    if check: cmd.append("--check")
    if reverse: cmd.append("--reverse")
    cmd.append(str(patch))
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def _apply_parakeet_patches(paths) -> None:
    ggml, patch_dir = PARAKEET / "third_party" / "ggml", PARAKEET / "third_party" / "ggml-patches"
    if not (ggml / ".git").exists(): raise RuntimeError("parakeet ggml submodule is missing")
    patches = sorted(patch_dir.glob("*.patch"))
    if not patches: raise RuntimeError("parakeet ggml patches are missing")
    applied = skipped = 0
    for patch in patches:
        if _git_apply(ggml, patch, check=True, reverse=True):
            skipped += 1; continue
        if not _git_apply(ggml, patch, check=True) or not _git_apply(ggml, patch):
            raise RuntimeError(f"cannot apply ggml patch {patch.name}")
        applied += 1
    paths.journal.emit("install", "parakeet.patches", applied=applied, skipped=skipped)


def build_parakeet(paths) -> None:
    pin(paths, PARAKEET_GIT_URL, PARAKEET_REV, PARAKEET, "parakeet", submodules=True)
    _apply_parakeet_patches(paths)
    build = TOOLS / "parakeet-build"
    _run(paths, ["cmake", "-S", str(PARAKEET), "-B", str(build), "-A", "x64", *_PARAKEET_FLAGS], role="parakeet-configure")
    _run(paths, ["cmake", "--build", str(build), "--config", "Release", "--target", "parakeet", "--parallel"], role="parakeet-build")
    dll = build / "Release" / "parakeet.dll"
    if not dll.is_file(): dll = find_exe(build, "parakeet.dll")
    if dll is None or not dll.is_file(): raise RuntimeError("parakeet.dll missing after build")
    dest = RUNTIMES / "parakeet"
    if dest.exists(): shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(dll, dest / "parakeet.dll")
    for extra in dll.parent.glob("*.dll"):
        if extra.name.casefold() != "parakeet.dll": shutil.copy2(extra, dest / extra.name)
    paths.journal.emit("install", "parakeet.build.completed", artifact=str(dest / "parakeet.dll"))


def _converter(paths) -> tuple[Path, dict]:
    py = CONVERTER / "Scripts" / "python.exe"
    if not py.is_file(): _run(paths, [sys.executable, "-m", "venv", str(CONVERTER)], role="converter-venv")
    code = "import importlib.metadata,json; print(json.dumps({n:importlib.metadata.version(n) for n in " + repr(tuple(CONVERTER_PINS)) + "}))"
    try: installed = json.loads(subprocess.check_output([str(py), "-c", code], text=True, stderr=subprocess.DEVNULL, timeout=30))
    except Exception: installed = {}
    if any(installed.get(name) != version for name, version in CONVERTER_PINS.items()):
        _run(paths, [str(py), *_PIP, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], role="converter-pip")
        _run(paths, [str(py), *_PIP, *[f"{n}=={v}" for n, v in CONVERTER_PINS.items() if n != "torch"]], role="converter-pip")
    return py, {"python": _tool([str(py), "--version"]), "required": CONVERTER_PINS, "packages": _tool([str(py), "-m", "pip", "freeze"], first_line=False).splitlines()}


def _snapshot(paths, py: Path, env: dict, spec: dict, dest: Path) -> None:
    code = "from huggingface_hub import snapshot_download;" + f"snapshot_download(repo_id={spec['repo']!r},revision={spec['rev']!r},allow_patterns={list(spec['files'])!r},local_dir={str(dest)!r})"
    _run(paths, [str(py), "-c", code], ROOT, env, role="hf-snapshot")
    missing = [name for name in spec["files"] if not (dest / name).is_file()]
    if missing: raise RuntimeError(f"checkpoint snapshot incomplete: {missing}")


def _scripts(spec: dict) -> tuple[Path, Path]:
    return CHATTERBOX / "scripts" / spec["t3"], CHATTERBOX / "scripts" / "convert-s3gen-to-gguf.py"


def _conversion_identity(family: str, models: Path, converter: dict) -> dict:
    spec = TTS_WEIGHTS[family]; t3, s3 = (models / name for name in TTS_MODELS[family])
    ckpt = CONVERTER / spec["ckpt"]
    return {"family": family, "checkpoint_repo": spec["repo"], "checkpoint_revision": spec["rev"],
            "checkpoint_files": {name: file_identity(ckpt / name) for name in spec["files"]},
            "converter_repository": git_identity(CHATTERBOX), "converter_scripts": {p.name: _sha(p) for p in _scripts(spec)},
            "tool_versions": converter, "quantization": {"t3": "q4_0", "s3gen": CODEC_QUANT},
            "outputs": {"t3": file_identity(t3), "s3gen": file_identity(s3)}}


def _conversion_valid(family: str, models: Path, converter: dict | None = None) -> bool:
    spec, receipt = TTS_WEIGHTS[family], _read_json(models / f".{family}-provenance.json")
    if not receipt: return False
    ckpt = CONVERTER / spec["ckpt"]
    if (receipt.get("checkpoint_repo") != spec["repo"] or receipt.get("checkpoint_revision") != spec["rev"]
            or receipt.get("converter_repository", {}).get("sha") != git_identity(CHATTERBOX).get("sha")
            or receipt.get("converter_scripts") != {p.name: _sha(p) for p in _scripts(spec) if p.is_file()}
            or receipt.get("quantization") != {"t3": "q4_0", "s3gen": CODEC_QUANT}
            or (converter is not None and receipt.get("tool_versions") != converter)
            or receipt.get("checkpoint_files") != {name: file_identity(ckpt / name) for name in spec["files"]}):
        return False
    return all((p := models / name).is_file() and (r := receipt.get("outputs", {}).get(role, {})).get("size") == p.stat().st_size and r.get("sha256") == _sha(p)
               for role, name in zip(("t3", "s3gen"), TTS_MODELS[family]))


def convert_tts(paths, models: Path, families: list[str]) -> None:
    py, tools = _converter(paths); env = os.environ.copy(); env.update(HF_HOME=str(TOOLS / "huggingface"), HF_HUB_DISABLE_SYMLINKS="1")
    for family in families:
        if _conversion_valid(family, models, tools):
            paths.journal.emit("install", "tts.convert.ready", family=family); continue
        spec, ckpt = TTS_WEIGHTS[family], CONVERTER / TTS_WEIGHTS[family]["ckpt"]
        _snapshot(paths, py, env, spec, ckpt)
        t3, s3 = (models / name for name in TTS_MODELS[family]); temps = [p.with_suffix(p.suffix + ".new") for p in (t3, s3)]
        for temp in temps: temp.unlink(missing_ok=True)
        t3_py, s3_py = _scripts(spec)
        _run(paths, [str(py), str(t3_py), *(["--model", spec["model"]] if spec.get("model") else []), "--ckpt-dir", str(ckpt), "--out", str(temps[0]), "--quant", "q4_0"], ROOT, env, role=f"convert-{family}-t3")
        _run(paths, [str(py), str(s3_py), "--variant", spec["s3"], "--ckpt-dir", str(ckpt), "--out", str(temps[1]), "--quant", CODEC_QUANT], ROOT, env, role=f"convert-{family}-s3")
        if not all(p.is_file() and p.stat().st_size for p in temps): raise RuntimeError(f"{family} conversion produced incomplete output")
        for temp, final in zip(temps, (t3, s3)): os.replace(temp, final)
        receipt = _conversion_identity(family, models, tools); _write_json(models / f".{family}-provenance.json", receipt)
        paths.journal.emit("install", "tts.convert.completed", **receipt)


def install_python(paths) -> None:
    env = ROOT / ".venv"; python, req, marker = env / "Scripts" / "python.exe", ROOT / "requirements.txt", env / ".requirements.sha256"
    digest = _sha(req)
    if not python.is_file(): venv.EnvBuilder(with_pip=True).create(env)
    if not marker.is_file() or marker.read_text(encoding="ascii").strip() != digest:
        _run(paths, [str(python), *_PIP, "-r", str(req)], role="runtime-pip")
        marker.write_text(digest + "\n", encoding="ascii")
    freeze = subprocess.check_output([str(python), "-m", "pip", "freeze", "--all"], text=True, encoding="utf-8", errors="replace", timeout=60).splitlines()
    paths.journal.emit("install", "python.ready", interpreter=str(python), requirements_sha256=digest, packages=freeze)


def install(models_dir: Path | None, data_dir: Path | None, paths) -> None:
    if sys.version_info < (3, 11) or os.name != "nt": raise RuntimeError("Trident needs Python 3.11+ on Windows")
    models, data = Path(models_dir or MODELS), Path(data_dir or DATA); models.mkdir(parents=True, exist_ok=True); data.mkdir(parents=True, exist_ok=True)
    paths.journal.emit("install", "start", hardware=HARDWARE, chatterbox_revision=CHATTERBOX_REV, ggml_revision=GGML_GIT[1], parakeet_revision=PARAKEET_REV)
    pin(paths, CHATTERBOX_URL, CHATTERBOX_REV, CHATTERBOX, "chatterbox")
    if not _build_valid(): build_tts(paths)
    else: paths.journal.emit("install", "tts.build.ready", executable=file_identity(RUNTIMES / "tts" / "chatterbox-server.exe"))
    convert_tts(paths, models, list(TTS_MODELS))
    build_parakeet(paths)
    install_runtime(paths, "gemma", "llama-server.exe", LLAMA_ZIP)
    for source, name, size, sha256 in VOICES.values(): pull(paths, VOICE_HF + source, data / name, size, sha256)
    pull(paths, PARAKEET_URL, models / PARAKEET_FILE, PARAKEET_SIZE, PARAKEET_SHA256)
    pull(paths, GEMMA_URL, models / GEMMA_FILE, GEMMA_SIZE, GEMMA_SHA256)
    pull(paths, SMART_TURN_URL, models / SMART_TURN_FILE, SMART_TURN_SIZE, SMART_TURN_SHA256)
    install_python(paths)
    paths.journal.emit("install", "completed", models={family: list(names) for family, names in TTS_MODELS.items()}, chatterbox=git_identity(CHATTERBOX), ggml=git_identity(GGML))
