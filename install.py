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
                    GEMMA_URL, GGML, GGML_GIT, HARDWARE, LLAMA_ZIP, MODELS, PARAKEET, PARAKEET_FILE,
                    PARAKEET_GIT_URL, PARAKEET_REV, PARAKEET_URL, ROOT, SMART_TURN_FILE, SMART_TURN_URL,
                    TOOLS, TTS_BACKEND, TTS_MODELS, TTS_WEIGHTS, VOICE_HF, VOICES, find_exe)
from journal import git_identity

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


def _identity(**fields) -> str:
    return "".join(f"{key}={fields[key]}\n" for key in sorted(fields))


def _stamp(path: Path, **fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_identity(**fields), encoding="utf-8")


def _ready(outputs: list[Path], stamp: Path, **fields) -> bool:
    expected = _identity(**fields)
    if not all(path.is_file() and path.stat().st_size for path in outputs):
        return False
    if stamp.is_file() and stamp.read_text(encoding="utf-8") == expected:
        return True
    if not stamp.is_file():
        _stamp(stamp, **fields)
        return True
    return False


def product_stamps(models: Path) -> dict[str, str]:
    root = Path(models) / "built-from"
    if not root.is_dir():
        return {}
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(root.glob("*.txt")) if path.is_file()}


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


def pull(paths, url: str, dest: Path) -> Path:
    if dest.is_file():
        paths.journal.emit("install", "fetch.ready", name=dest.name, size=dest.stat().st_size)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part"); tmp.unlink(missing_ok=True)
    paths.journal.emit("install", "fetch.start", name=dest.name, url=url)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Trident/2"}), timeout=120) as response, tmp.open("wb") as out:
        shutil.copyfileobj(response, out, 4 << 20)
    tmp.replace(dest)
    paths.journal.emit("install", "fetch.completed", name=dest.name, size=dest.stat().st_size)
    return dest


def fetch(paths, url: str, dest: Path, stamp: Path) -> Path:
    if _ready([dest], stamp, url=url):
        paths.journal.emit("install", "fetch.ready", name=dest.name, size=dest.stat().st_size)
        return dest
    dest.unlink(missing_ok=True)
    pull(paths, url, dest)
    _stamp(stamp, url=url)
    return dest


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


def _take(src: Path, dest: Path) -> None:
    if dest.exists() or not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest)
        (dest / "provenance.json").unlink(missing_ok=True)
    else:
        shutil.copy2(src, dest)


def _adopt_previous(models: Path) -> None:
    old = TOOLS / "runtime"
    for role in ("tts", "parakeet", "gemma"):
        _take(old / role, models / role)
    for _source, name in VOICES.values():
        _take(DATA / name, models / "voices" / name)


def _tts_server_fields() -> dict:
    return {"chatterbox": CHATTERBOX_REV, "ggml": GGML_GIT[1], "hardware": HARDWARE,
            "backend": TTS_BACKEND, "flags": " ".join(_BUILD_FLAGS)}


def _tts_weight_fields(family: str) -> dict:
    spec = TTS_WEIGHTS[family]
    t3, s3 = TTS_MODELS[family]
    return {"chatterbox": CHATTERBOX_REV, "family": family, "repo": spec["repo"], "rev": spec["rev"],
            "t3": t3, "s3": s3, "t3_quant": "q4_0", "s3_quant": CODEC_QUANT}


def _parakeet_fields() -> dict:
    return {"parakeet": PARAKEET_REV, "hardware": HARDWARE, "flags": " ".join(_PARAKEET_FLAGS)}


def build_tts(paths, dest: Path) -> None:
    pin(paths, *GGML_GIT, GGML, "ggml")
    build = TOOLS / "tts-build"
    _run(paths, ["cmake", "-S", str(CHATTERBOX), "-B", str(build), "-A", "x64", *_BUILD_FLAGS], role="cmake-configure")
    _run(paths, ["cmake", "--build", str(build), "--config", "Release", "--target", "chatterbox-server", "--parallel"], role="cmake-build")
    _clean_repo(GGML)
    built, server = build / "bin", build / "bin" / "chatterbox-server.exe"
    if not server.is_file(): raise RuntimeError("chatterbox-server.exe missing after build")
    if dest.exists(): shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(server, dest / server.name)
    for dll in built.glob("*.dll"): shutil.copy2(dll, dest / dll.name)
    paths.journal.emit("install", "tts.build.completed", artifact=str(dest / server.name))


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


def build_parakeet(paths, dest: Path) -> None:
    pin(paths, PARAKEET_GIT_URL, PARAKEET_REV, PARAKEET, "parakeet", submodules=True)
    _apply_parakeet_patches(paths)
    build = TOOLS / "parakeet-build"
    _run(paths, ["cmake", "-S", str(PARAKEET), "-B", str(build), "-A", "x64", *_PARAKEET_FLAGS], role="parakeet-configure")
    _run(paths, ["cmake", "--build", str(build), "--config", "Release", "--target", "parakeet", "--parallel"], role="parakeet-build")
    dll = build / "Release" / "parakeet.dll"
    if not dll.is_file(): dll = find_exe(build, "parakeet.dll")
    if dll is None or not dll.is_file(): raise RuntimeError("parakeet.dll missing after build")
    if dest.exists(): shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(dll, dest / "parakeet.dll")
    for extra in dll.parent.glob("*.dll"):
        if extra.name.casefold() != "parakeet.dll": shutil.copy2(extra, dest / extra.name)
    paths.journal.emit("install", "parakeet.build.completed", artifact=str(dest / "parakeet.dll"))


def install_llama(paths, dest: Path) -> None:
    url, archive_name = LLAMA_ZIP
    archive = pull(paths, url, DOWNLOADS / archive_name)
    scratch = DOWNLOADS / "llama-unpack"
    if scratch.exists(): shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z: z.extractall(scratch)
    paths.journal.emit("install", "archive.completed", archive=archive.name, destination=str(scratch))
    exe = find_exe(scratch, "llama-server.exe")
    if exe is None: raise RuntimeError(f"llama-server.exe missing from {archive_name}")
    if dest.exists(): shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(exe, dest / exe.name)
    for dll in exe.parent.glob("*.dll"): shutil.copy2(dll, dest / dll.name)
    shutil.rmtree(scratch, ignore_errors=True)
    paths.journal.emit("install", "runtime.completed", role="gemma", artifact=str(dest / exe.name))


def _converter(paths) -> Path:
    py = CONVERTER / "Scripts" / "python.exe"
    if not py.is_file(): _run(paths, [sys.executable, "-m", "venv", str(CONVERTER)], role="converter-venv")
    code = "import importlib.metadata,json; print(json.dumps({n:importlib.metadata.version(n) for n in " + repr(tuple(CONVERTER_PINS)) + "}))"
    try: installed = json.loads(subprocess.check_output([str(py), "-c", code], text=True, stderr=subprocess.DEVNULL, timeout=30))
    except Exception: installed = {}
    if any(installed.get(name) != version for name, version in CONVERTER_PINS.items()):
        _run(paths, [str(py), *_PIP, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], role="converter-pip")
        _run(paths, [str(py), *_PIP, *[f"{n}=={v}" for n, v in CONVERTER_PINS.items() if n != "torch"]], role="converter-pip")
    return py


def _snapshot(paths, py: Path, env: dict, spec: dict, dest: Path) -> None:
    code = "from huggingface_hub import snapshot_download;" + f"snapshot_download(repo_id={spec['repo']!r},revision={spec['rev']!r},allow_patterns={list(spec['files'])!r},local_dir={str(dest)!r})"
    _run(paths, [str(py), "-c", code], ROOT, env, role="hf-snapshot")
    missing = [name for name in spec["files"] if not (dest / name).is_file()]
    if missing: raise RuntimeError(f"checkpoint snapshot incomplete: {missing}")


def _scripts(spec: dict) -> tuple[Path, Path]:
    return CHATTERBOX / "scripts" / spec["t3"], CHATTERBOX / "scripts" / "convert-s3gen-to-gguf.py"


def convert_tts(paths, models: Path, families: list[str]) -> None:
    py = env = None
    for family in families:
        spec = TTS_WEIGHTS[family]
        if py is None:
            py = _converter(paths)
            env = os.environ.copy(); env.update(HF_HOME=str(TOOLS / "huggingface"), HF_HUB_DISABLE_SYMLINKS="1")
        ckpt = CONVERTER / spec["ckpt"]
        _snapshot(paths, py, env, spec, ckpt)
        t3, s3 = (models / name for name in TTS_MODELS[family]); temps = [p.with_suffix(p.suffix + ".new") for p in (t3, s3)]
        for temp in temps: temp.unlink(missing_ok=True)
        t3_py, s3_py = _scripts(spec)
        _run(paths, [str(py), str(t3_py), *(["--model", spec["model"]] if spec.get("model") else []), "--ckpt-dir", str(ckpt), "--out", str(temps[0]), "--quant", "q4_0"], ROOT, env, role=f"convert-{family}-t3")
        _run(paths, [str(py), str(s3_py), "--variant", spec["s3"], "--ckpt-dir", str(ckpt), "--out", str(temps[1]), "--quant", CODEC_QUANT], ROOT, env, role=f"convert-{family}-s3")
        if not all(p.is_file() and p.stat().st_size for p in temps): raise RuntimeError(f"{family} conversion produced incomplete output")
        for temp, final in zip(temps, (t3, s3)): os.replace(temp, final)
        _stamp(models / "built-from" / f"tts-{family}.txt", **_tts_weight_fields(family))
        paths.journal.emit("install", "tts.convert.completed", family=family, t3=t3.name, s3=s3.name)


def install_python(paths) -> None:
    env = ROOT / ".venv"; python, req, marker = env / "Scripts" / "python.exe", ROOT / "requirements.txt", env / "requirements.txt"
    text = req.read_text(encoding="utf-8")
    if not python.is_file(): venv.EnvBuilder(with_pip=True).create(env)
    if not marker.is_file() or marker.read_text(encoding="utf-8") != text:
        _run(paths, [str(python), *_PIP, "-r", str(req)], role="runtime-pip")
        marker.write_text(text, encoding="utf-8")
    paths.journal.emit("install", "python.ready", interpreter=str(python))


def install(models_dir: Path | None, data_dir: Path | None, paths) -> None:
    if sys.version_info < (3, 11) or os.name != "nt": raise RuntimeError("Trident needs Python 3.11+ on Windows")
    models, data = Path(models_dir or MODELS), Path(data_dir or DATA)
    models.mkdir(parents=True, exist_ok=True); data.mkdir(parents=True, exist_ok=True)
    stamps = models / "built-from"; stamps.mkdir(exist_ok=True)
    _adopt_previous(models)
    paths.journal.emit("install", "start", hardware=HARDWARE, chatterbox_revision=CHATTERBOX_REV, ggml_revision=GGML_GIT[1], parakeet_revision=PARAKEET_REV)

    tts_exe = models / "tts" / "chatterbox-server.exe"
    need_tts = not _ready([tts_exe], stamps / "tts.txt", **_tts_server_fields())
    need_convert = [family for family in TTS_MODELS
                    if not _ready([models / name for name in TTS_MODELS[family]], stamps / f"tts-{family}.txt", **_tts_weight_fields(family))]
    if need_tts or need_convert:
        pin(paths, CHATTERBOX_URL, CHATTERBOX_REV, CHATTERBOX, "chatterbox")
    if need_tts:
        build_tts(paths, models / "tts")
        _stamp(stamps / "tts.txt", **_tts_server_fields())
    else:
        paths.journal.emit("install", "tts.build.ready", artifact=str(tts_exe))
    for family in list(TTS_MODELS):
        if family not in need_convert:
            paths.journal.emit("install", "tts.convert.ready", family=family)
    if need_convert:
        convert_tts(paths, models, need_convert)

    dll = models / "parakeet" / "parakeet.dll"
    if _ready([dll], stamps / "parakeet.txt", **_parakeet_fields()):
        paths.journal.emit("install", "parakeet.build.ready", artifact=str(dll))
    else:
        build_parakeet(paths, models / "parakeet")
        _stamp(stamps / "parakeet.txt", **_parakeet_fields())

    url, archive_name = LLAMA_ZIP
    llama = find_exe(models / "gemma", "llama-server.exe") or models / "gemma" / "llama-server.exe"
    if _ready([llama], stamps / "gemma-server.txt", url=url, archive=archive_name):
        paths.journal.emit("install", "runtime.ready", role="gemma", artifact=str(llama))
    else:
        install_llama(paths, models / "gemma")
        _stamp(stamps / "gemma-server.txt", url=url, archive=archive_name)

    for source, name in VOICES.values():
        fetch(paths, VOICE_HF + source, models / "voices" / name, stamps / f"voice-{Path(name).stem}.txt")
    fetch(paths, PARAKEET_URL, models / PARAKEET_FILE, stamps / "parakeet-gguf.txt")
    fetch(paths, GEMMA_URL, models / GEMMA_FILE, stamps / "gemma.txt")
    fetch(paths, SMART_TURN_URL, models / SMART_TURN_FILE, stamps / "smart-turn.txt")
    install_python(paths)
    paths.journal.emit("install", "completed", models={family: list(names) for family, names in TTS_MODELS.items()},
                       chatterbox=git_identity(CHATTERBOX), ggml=git_identity(GGML), products=sorted(p.stem for p in stamps.glob("*.txt")))
