from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile
from pathlib import Path

from config import (
    CHATTERBOX, CHATTERBOX_GIT, CODEC_FILE, CODEC_QUANT, CONVERTER, DATA, GEMMA_FILE, GEMMA_URL,
    GGML, GGML_GIT, HARDWARE, LLAMA_ZIP, MODELS, NANO_FILES, NANO_REPO, NANO_REV, PARAKEET_FILE,
    PARAKEET_URL, PARAKEET_ZIP, ROOT, RUNTIMES, T3_FILE, TOOLS, TTS, VOICE_HF, VOICES, find_exe, log,
)


def sh(cmd, cwd=None, env=None) -> None:
    log("exec " + " ".join(str(c) for c in cmd))
    subprocess.check_call(cmd, cwd=cwd, env=env)


def pull(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size:
        log(f"have {dest.name}")
        return dest
    log(f"get {dest.name}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    log(f"got {dest.name} {dest.stat().st_size} bytes")
    return dest


def unzip(archive: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)


def pin(url: str, rev: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").is_dir():
        sh(["git", "clone", "--filter=blob:none", "--no-checkout", url, str(dest)], dest.parent)
    sh(["git", "fetch", "--depth", "1", "origin", rev], dest)
    sh(["git", "checkout", "--detach", rev], dest)


def patch_ggml() -> None:
    path = GGML / "src" / "ggml-vulkan" / "ggml-vulkan.cpp"
    lines = path.read_text(encoding="utf-8").splitlines()
    nvidia = "NVIDIA_PRE_TURING"
    direct = next(i for i, line in enumerate(lines) if "force_disable_f16" in line and "getenv(" in line)
    staged = next(i for i, line in enumerate(lines[:-1]) if "getenv(" in line and "force_disable_f16" in lines[i + 1])
    lines[direct] = (
        "    const bool force_disable_f16 = device->vendor_id == VK_VENDOR_ID_NVIDIA "
        f"&& device->architecture == vk_device_architecture::{nvidia};"
    )
    lines[staged] = ""
    lines[staged + 1] = (
        "    bool force_disable_f16 = physical_device.getProperties().vendorID == VK_VENDOR_ID_NVIDIA "
        f"&& device_architecture == vk_device_architecture::{nvidia};"
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("ggml vulkan: Pascal pre-Turing FP16 off")


def has_exe(root: Path, name: str) -> bool:
    return root.is_dir() and any(p.is_file() and p.name.lower() == name.lower() for p in root.rglob("*"))


def tts_pin() -> str:
    blob = (TTS / "src" / "server.cpp").read_bytes() + (TTS / "CMakeLists.txt").read_bytes()
    return f"{CHATTERBOX_GIT[1]} {GGML_GIT[1]} {hashlib.sha256(blob).hexdigest()[:16]}"


def build_tts() -> None:
    pin(*CHATTERBOX_GIT, CHATTERBOX)
    pin(*GGML_GIT, GGML)
    patch_ggml()
    cmake = "cmake"
    sh([cmake, "-S", ".", "-B", "build", "-A", "x64",
        "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF",
        "-DTTS_CPP_BUILD_EXECUTABLES=OFF", "-DTTS_CPP_BUILD_TESTS=OFF"], CHATTERBOX)
    sh([cmake, "--build", "build", "--config", "Release", "--target", "tts-cpp", "mtl_tokenizer", "--parallel"], CHATTERBOX)
    sh([cmake, "-S", ".", "-B", "build", "-A", "x64", f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}"], TTS)
    sh([cmake, "--build", "build", "--config", "Release", "--target", "trident-tts-server", "--parallel"], TTS)
    built = TTS / "build" / "Release"
    server = built / "trident-tts-server.exe"
    if not server.is_file():
        raise RuntimeError("trident-tts-server.exe missing after build")
    dest = RUNTIMES / "tts"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(server, dest / server.name)
    for dll in built.glob("*.dll"):
        shutil.copy2(dll, dest / dll.name)
    (CHATTERBOX / "build" / ".pin").write_text(tts_pin() + "\n", encoding="ascii")
    log("tts server ready")


def convert_nano() -> None:
    pin(*CHATTERBOX_GIT, CHATTERBOX)
    py = CONVERTER / "Scripts" / "python.exe"
    if not py.is_file():
        sh([sys.executable, "-m", "venv", str(CONVERTER)])
        sh([str(py), "-m", "pip", "install", "--disable-pip-version-check", "torch==2.6.0",
            "--index-url", "https://download.pytorch.org/whl/cpu"])
        sh([str(py), "-m", "pip", "install", "--disable-pip-version-check",
            "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3", "scipy==1.15.3",
            "librosa==0.11.0", "resampy==0.4.3", "huggingface-hub==0.34.4"])
    ckpt = CONVERTER / "ckpt"
    ckpt.mkdir(parents=True, exist_ok=True)
    missing = [f for f in NANO_FILES if not (ckpt / f).is_file()]
    if missing:
        env = os.environ.copy()
        env["HF_HOME"] = str(TOOLS / "huggingface")
        code = (
            "from huggingface_hub import snapshot_download; "
            f"snapshot_download(repo_id={NANO_REPO!r}, revision={NANO_REV!r}, "
            f"allow_patterns={list(NANO_FILES)!r}, local_dir={str(ckpt)!r})"
        )
        sh([str(py), "-c", code], ROOT, env)
    shutil.copyfile(ckpt / "t3_nano_v1.safetensors", ckpt / "t3_turbo_v1.safetensors")
    MODELS.mkdir(parents=True, exist_ok=True)
    t3, codec = MODELS / T3_FILE, MODELS / CODEC_FILE
    env = os.environ.copy()
    env["HF_HOME"] = str(TOOLS / "huggingface")
    if not t3.is_file():
        sh([str(py), str(CHATTERBOX / "scripts" / "convert-t3-turbo-to-gguf.py"),
            "--ckpt-dir", str(ckpt), "--out", str(t3), "--quant", "q4_0"], ROOT, env)
    if not codec.is_file():
        sh([str(py), str(CHATTERBOX / "scripts" / "convert-s3gen-to-gguf.py"),
            "--variant", "turbo", "--ckpt-dir", str(ckpt), "--out", str(codec), "--quant", CODEC_QUANT], ROOT, env)
    log(f"nano gguf ready t3={t3.name} codec={codec.name}")


def install_ui() -> Path:
    env = ROOT / ".venv"
    python = env / "Scripts" / "python.exe"
    req = ROOT / "requirements-ui.txt"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    marker = env / ".req"
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(env)
    if not marker.is_file() or marker.read_text(encoding="ascii").strip() != digest:
        sh([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)])
        marker.write_text(digest + "\n", encoding="ascii")
    return python


def install(models_dir: Path | None = None, data_dir: Path | None = None) -> Path:
    if sys.version_info < (3, 11) or os.name != "nt":
        raise RuntimeError("Trident needs Python 3.11+ on Windows")
    models = Path(models_dir or MODELS)
    data = Path(data_dir or DATA)
    models.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    log(f"install hardware={HARDWARE}")

    server = RUNTIMES / "tts" / "trident-tts-server.exe"
    pin_file = CHATTERBOX / "build" / ".pin"
    if not server.is_file() or not pin_file.is_file() or pin_file.read_text(encoding="ascii").strip() != tts_pin():
        build_tts()

    if not (models / T3_FILE).is_file() or not (models / CODEC_FILE).is_file():
        convert_nano()

    parakeet_zip = pull(PARAKEET_ZIP[0], TOOLS / "downloads" / PARAKEET_ZIP[1])
    if not has_exe(RUNTIMES / "parakeet", "parakeet-server.exe"):
        unzip(parakeet_zip, RUNTIMES / "parakeet")
    llama_zip = pull(LLAMA_ZIP[0], TOOLS / "downloads" / LLAMA_ZIP[1])
    if not has_exe(RUNTIMES / "gemma", "llama-server.exe"):
        unzip(llama_zip, RUNTIMES / "gemma")
    find_exe(RUNTIMES / "parakeet", "parakeet-server.exe")
    find_exe(RUNTIMES / "gemma", "llama-server.exe")

    for src, name in VOICES.values():
        pull(VOICE_HF + src, data / name)
    pull(PARAKEET_URL, models / PARAKEET_FILE)
    pull(GEMMA_URL, models / GEMMA_FILE)
    python = install_ui()
    log("install complete")
    return python
