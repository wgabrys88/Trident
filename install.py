from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import venv
import zipfile
from pathlib import Path

from config import CHATTERBOX, CHATTERBOX_REV, CHATTERBOX_URL, CODEC_QUANT, CONVERTER, DATA, GEMMA_FILE, GEMMA_URL, GGML, GGML_GIT, HARDWARE, LLAMA_ZIP, MODELS, PARAKEET_FILE, PARAKEET_URL, PARAKEET_ZIP, ROOT, RUNTIMES, SMART_TURN_FILE, SMART_TURN_SHA256, SMART_TURN_SIZE, SMART_TURN_URL, TOOLS, TTS, TTS_MODELS, TTS_WEIGHTS, VOICE_HF, VOICES, emit, find_exe, sidecar

PIN = RUNTIMES / "tts" / ".pin"

def sh(cmd, cwd=None, env=None) -> None:
    emit("exec", cmd=" ".join(str(c) for c in cmd))
    with sidecar("install.log").open("ab") as output:
        subprocess.check_call(cmd, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)

def pull(url: str, dest: Path, size: int = 0, sha256: str = "") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size:
        if size and dest.stat().st_size != size:
            raise RuntimeError(f"{dest.name} size mismatch")
        if sha256 and hashlib.sha256(dest.read_bytes()).hexdigest() != sha256:
            raise RuntimeError(f"{dest.name} SHA256 mismatch")
        emit("fetch.have", name=dest.name, bytes=dest.stat().st_size)
        return dest
    emit("fetch.get", url=url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req) as response, tmp.open("wb") as out:
        shutil.copyfileobj(response, out, 1024 * 1024)
    tmp.replace(dest)
    if size and dest.stat().st_size != size:
        raise RuntimeError(f"{dest.name} size mismatch")
    if sha256 and hashlib.sha256(dest.read_bytes()).hexdigest() != sha256:
        raise RuntimeError(f"{dest.name} SHA256 mismatch")
    emit("fetch.got", name=dest.name, bytes=dest.stat().st_size, elapsed_ms=round((time.perf_counter() - t0) * 1000))
    return dest

def unzip(archive: Path, dest: Path) -> None:
    if dest.exists():
        raise RuntimeError(f"refusing to replace {dest}")
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    emit("unzip", archive=archive.name, dest=str(dest))

def pin(url: str, rev: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    created = not (dest / ".git").is_dir()
    if created:
        if dest.exists():
            raise RuntimeError(f"refusing to replace {dest}")
        sh(["git", "clone", "--filter=blob:none", "--no-checkout", url, str(dest)])
    if rev == "latest":
        sh(["git", "fetch", "--depth", "1", "origin", "HEAD"], dest)
        target = "FETCH_HEAD"
    else:
        sh(["git", "fetch", "--depth", "1", "origin", rev], dest)
        target = rev
    current = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=dest, text=True).strip()
    fetched = subprocess.check_output(["git", "rev-parse", target], cwd=dest, text=True).strip()
    if created or current != fetched:
        sh(["git", "checkout", "--detach", target], dest)

def patch_ggml() -> None:
    path = GGML / "src" / "ggml-vulkan" / "ggml-vulkan.cpp"
    text = path.read_text(encoding="utf-8")
    direct = "const bool force_disable_f16 = device->vendor_id == VK_VENDOR_ID_NVIDIA && device->architecture == vk_device_architecture::NVIDIA_PRE_TURING;"
    staged = "bool force_disable_f16 = physical_device.getProperties().vendorID == VK_VENDOR_ID_NVIDIA && device_architecture == vk_device_architecture::NVIDIA_PRE_TURING;"
    if direct not in text or staged not in text:
        text = text.replace('const bool force_disable_f16 = getenv("GGML_VK_DISABLE_F16") != nullptr;', direct)
        text = text.replace('const char* GGML_VK_DISABLE_F16 = getenv("GGML_VK_DISABLE_F16");\n    bool force_disable_f16 = GGML_VK_DISABLE_F16 != nullptr;', staged)
        if direct not in text or staged not in text:
            raise RuntimeError("GGML Pascal patch source mismatch")
        path.write_text(text, encoding="utf-8")
    emit("ggml.patch", hardware=HARDWARE)

def chatterbox_sha() -> str:
    return subprocess.check_output(["git", "-C", str(CHATTERBOX), "rev-parse", "HEAD"], text=True).strip()

def tts_pin() -> str:
    blob = (TTS / "src" / "server.cpp").read_bytes() + (TTS / "CMakeLists.txt").read_bytes()
    return f"{chatterbox_sha()} {HARDWARE} {GGML_GIT[1]} {hashlib.sha256(blob).hexdigest()[:16]}"

def build_tts() -> None:
    t0 = time.perf_counter()
    pin(*GGML_GIT, GGML)
    if HARDWARE == "pascal":
        patch_ggml()
    sh(["cmake", "-S", ".", "-B", "build", "-A", "x64", "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF", "-DTTS_CPP_BUILD_EXECUTABLES=OFF", "-DTTS_CPP_BUILD_TESTS=OFF"], CHATTERBOX)
    sh(["cmake", "--build", "build", "--config", "Release", "--target", "tts-cpp", "mtl_tokenizer", "--parallel"], CHATTERBOX)
    sh(["cmake", "-S", ".", "-B", "build", "-A", "x64", f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}"], TTS)
    sh(["cmake", "--build", "build", "--config", "Release", "--target", "trident-tts-server", "--parallel"], TTS)
    built, server = TTS / "build" / "Release", TTS / "build" / "Release" / "trident-tts-server.exe"
    if not server.is_file():
        raise RuntimeError("trident-tts-server.exe missing after build")
    dest = RUNTIMES / "tts"; dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(server, dest / server.name)
    for dll in built.glob("*.dll"):
        shutil.copy2(dll, dest / dll.name)
    PIN.write_text(tts_pin() + "\n", encoding="ascii")
    emit("tts.build", hardware=HARDWARE, sha=chatterbox_sha(), elapsed_ms=round((time.perf_counter() - t0) * 1000))

def snapshot(py: Path, env: dict, repo: str, rev: str, files: tuple[str, ...], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if all((dest / name).is_file() for name in files):
        return
    code = "from huggingface_hub import snapshot_download; " + f"snapshot_download(repo_id={repo!r}, revision={rev!r}, allow_patterns={list(files)!r}, local_dir={str(dest)!r})"
    sh([str(py), "-c", code], ROOT, env)

def convert_tts(models: Path) -> None:
    t0 = time.perf_counter()
    py = CONVERTER / "Scripts" / "python.exe"
    if not py.is_file():
        sh([sys.executable, "-m", "venv", str(CONVERTER)])
        sh([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"])
        sh([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3", "scipy==1.15.3", "librosa==0.11.0", "resampy==0.4.3", "huggingface-hub==0.34.4"])
    env = os.environ.copy()
    env["HF_HOME"] = str(TOOLS / "huggingface")
    env["HF_HUB_DISABLE_SYMLINKS"] = "1"
    models.mkdir(parents=True, exist_ok=True)
    for family, spec in TTS_WEIGHTS.items():
        t3, codec = (models / name for name in TTS_MODELS[family])
        if t3.is_file() and codec.is_file():
            continue
        ckpt = CONVERTER / spec["ckpt"]
        snapshot(py, env, spec["repo"], spec["rev"], spec["files"], ckpt)
        if not t3.is_file():
            cmd = [str(py), str(CHATTERBOX / "scripts" / spec["t3"])]
            if model := spec.get("model"):
                cmd += ["--model", model]
            cmd += ["--ckpt-dir", str(ckpt), "--out", str(t3), "--quant", "q4_0"]
            sh(cmd, ROOT, env)
        if not codec.is_file():
            sh([str(py), str(CHATTERBOX / "scripts" / "convert-s3gen-to-gguf.py"), "--variant", spec["s3"], "--ckpt-dir", str(ckpt), "--out", str(codec), "--quant", CODEC_QUANT], ROOT, env)
    emit("tts.gguf", hardware=HARDWARE, elapsed_ms=round((time.perf_counter() - t0) * 1000))

def install_python() -> None:
    env = ROOT / ".venv"
    python, req, marker = env / "Scripts" / "python.exe", ROOT / "requirements.txt", env / ".req"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    if not python.is_file() or not marker.is_file() or marker.read_text(encoding="ascii").strip() != digest:
        if not python.is_file():
            venv.EnvBuilder(with_pip=True).create(env)
        sh([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", "-r", str(req)])
        marker.write_text(digest + "\n", encoding="ascii")
    emit("python.venv", python=str(python))

def install(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    if sys.version_info < (3, 11) or os.name != "nt":
        raise RuntimeError("Trident needs Python 3.11+ on Windows")
    models, data = Path(models_dir or MODELS), Path(data_dir or DATA)
    models.mkdir(parents=True, exist_ok=True); data.mkdir(parents=True, exist_ok=True)
    emit("install", hardware=HARDWARE, chatterbox_rev=CHATTERBOX_REV)
    server = RUNTIMES / "tts" / "trident-tts-server.exe"
    need_gguf = any(not (models / name).is_file() for pair in TTS_MODELS.values() for name in pair)
    pin(CHATTERBOX_URL, CHATTERBOX_REV, CHATTERBOX)
    if not server.is_file() or not PIN.is_file() or PIN.read_text(encoding="ascii").strip() != tts_pin():
        build_tts()
    if need_gguf:
        convert_tts(models)
    if find_exe(RUNTIMES / "parakeet", "parakeet-server.exe") is None:
        unzip(pull(PARAKEET_ZIP[0], TOOLS / "downloads" / PARAKEET_ZIP[1]), RUNTIMES / "parakeet")
    if find_exe(RUNTIMES / "gemma", "llama-server.exe") is None:
        unzip(pull(LLAMA_ZIP[0], TOOLS / "downloads" / LLAMA_ZIP[1]), RUNTIMES / "gemma")
    for source, name in VOICES.values():
        pull(VOICE_HF + source, data / name)
    pull(PARAKEET_URL, models / PARAKEET_FILE)
    pull(GEMMA_URL, models / GEMMA_FILE)
    pull(SMART_TURN_URL, models / SMART_TURN_FILE, SMART_TURN_SIZE, SMART_TURN_SHA256)
    install_python()
    emit("install.done")
