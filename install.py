from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import venv
import zipfile
from pathlib import Path

from config import (
    CHATTERBOX, CHATTERBOX_URL, CODEC_FILE, CODEC_QUANT, CONVERTER, DATA, GEMMA_FILE, GEMMA_URL,
    GGML, GGML_GIT, HARDWARE, LLAMA_ZIP, MODELS, NANO_FILES, NANO_REPO, NANO_REV, PARAKEET_FILE,
    PARAKEET_URL, PARAKEET_ZIP, ROOT, RUNTIMES, T3_FILE, THIRD_PARTY, TOOLS, TTS, VOICE_HF, VOICES,
    find_exe, log,
)

PIN = RUNTIMES / "tts" / ".pin"


def sh(cmd, cwd=None, env=None) -> None:
    log("exec " + " ".join(str(c) for c in cmd))
    subprocess.check_call(cmd, cwd=cwd, env=env)


def wipe(path: Path) -> None:
    if not path.exists():
        return
    def onerror(fn, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    shutil.rmtree(path, onerror=onerror)
    log(f"wipe {path}")


def pull(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size:
        log(f"have {dest.name} {dest.stat().st_size} bytes")
        return dest
    log(f"get {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req) as response, tmp.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)
    log(f"got {dest.name} {dest.stat().st_size} bytes elapsed_ms={(time.perf_counter() - t0) * 1000:.0f}")
    return dest


def unzip(archive: Path, dest: Path) -> None:
    wipe(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    log(f"unzip {archive.name} -> {dest}")


def remote_head(url: str) -> str:
    return subprocess.check_output(["git", "ls-remote", url, "HEAD"], text=True).split()[0]


def clone(url: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (dest / ".git").is_dir():
        sh(["git", "fetch", "--depth", "1", "origin"], dest)
        sh(["git", "reset", "--hard", "FETCH_HEAD"], dest)
    else:
        wipe(dest)
        sh(["git", "clone", "--filter=blob:none", "--depth", "1", url, str(dest)])
    rev = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=dest, text=True).strip()
    log(f"git {dest.name} {rev}")
    return rev


def pin(url: str, rev: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").is_dir():
        wipe(dest)
        sh(["git", "clone", "--filter=blob:none", "--no-checkout", url, str(dest)])
    sh(["git", "fetch", "--depth", "1", "origin", rev], dest)
    sh(["git", "checkout", "--detach", "--force", rev], dest)


def patch_ggml() -> None:
    path = GGML / "src" / "ggml-vulkan" / "ggml-vulkan.cpp"
    lines = path.read_text(encoding="utf-8").splitlines()
    direct = next(i for i, line in enumerate(lines) if "force_disable_f16" in line and "getenv(" in line)
    staged = next(i for i, line in enumerate(lines[:-1]) if "getenv(" in line and "force_disable_f16" in lines[i + 1])
    lines[direct] = "    const bool force_disable_f16 = device->vendor_id == VK_VENDOR_ID_NVIDIA && device->architecture == vk_device_architecture::NVIDIA_PRE_TURING;"
    lines[staged] = ""
    lines[staged + 1] = "    bool force_disable_f16 = physical_device.getProperties().vendorID == VK_VENDOR_ID_NVIDIA && device_architecture == vk_device_architecture::NVIDIA_PRE_TURING;"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("ggml vulkan: Pascal pre-Turing FP16 off")


def tts_pin(chatter_rev: str) -> str:
    blob = (TTS / "src" / "server.cpp").read_bytes() + (TTS / "CMakeLists.txt").read_bytes()
    return f"{chatter_rev} {HARDWARE} {GGML_GIT[1]} {hashlib.sha256(blob).hexdigest()[:16]}"


def build_tts(chatter_rev: str) -> None:
    t0 = time.perf_counter()
    pin(*GGML_GIT, GGML)
    if HARDWARE == "pascal":
        patch_ggml()
    else:
        log(f"ggml vulkan: {HARDWARE} stock")
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
    PIN.parent.mkdir(parents=True, exist_ok=True)
    PIN.write_text(tts_pin(chatter_rev) + "\n", encoding="ascii")
    log(f"tts server ready hardware={HARDWARE} elapsed_ms={(time.perf_counter() - t0) * 1000:.0f}")


def convert_nano(models: Path) -> None:
    t0 = time.perf_counter()
    py = CONVERTER / "Scripts" / "python.exe"
    if not py.is_file():
        sh([sys.executable, "-m", "venv", str(CONVERTER)])
        sh([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input",
            "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"])
        sh([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input",
            "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3", "scipy==1.15.3",
            "librosa==0.11.0", "resampy==0.4.3", "huggingface-hub==0.34.4"])
    ckpt = CONVERTER / "ckpt"
    ckpt.mkdir(parents=True, exist_ok=True)
    missing = [f for f in NANO_FILES if not (ckpt / f).is_file()]
    env = os.environ.copy()
    env["HF_HOME"] = str(TOOLS / "huggingface")
    if missing:
        code = (
            "from huggingface_hub import snapshot_download; "
            f"snapshot_download(repo_id={NANO_REPO!r}, revision={NANO_REV!r}, "
            f"allow_patterns={list(NANO_FILES)!r}, local_dir={str(ckpt)!r})"
        )
        sh([str(py), "-c", code], ROOT, env)
    turbo = ckpt / "t3_turbo_v1.safetensors"
    if not turbo.is_file():
        shutil.copyfile(ckpt / "t3_nano_v1.safetensors", turbo)
    models.mkdir(parents=True, exist_ok=True)
    t3, codec = models / T3_FILE, models / CODEC_FILE
    if not t3.is_file():
        sh([str(py), str(CHATTERBOX / "scripts" / "convert-t3-turbo-to-gguf.py"),
            "--ckpt-dir", str(ckpt), "--out", str(t3), "--quant", "q4_0"], ROOT, env)
    if not codec.is_file():
        sh([str(py), str(CHATTERBOX / "scripts" / "convert-s3gen-to-gguf.py"),
            "--variant", "turbo", "--ckpt-dir", str(ckpt), "--out", str(codec),
            "--quant", CODEC_QUANT], ROOT, env)
    log(f"nano gguf ready t3={t3.name} codec={codec.name} hardware={HARDWARE} elapsed_ms={(time.perf_counter() - t0) * 1000:.0f}")


def install_ui() -> None:
    env = ROOT / ".venv"
    python = env / "Scripts" / "python.exe"
    req = ROOT / "requirements-ui.txt"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    marker = env / ".req"
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(env)
    if not marker.is_file() or marker.read_text(encoding="ascii").strip() != digest:
        sh([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input",
            "-r", str(req)])
        marker.write_text(digest + "\n", encoding="ascii")
    log(f"ui venv {python}")


def install(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    if sys.version_info < (3, 11) or os.name != "nt":
        raise RuntimeError("Trident needs Python 3.11+ on Windows")
    models = Path(models_dir or MODELS)
    data = Path(data_dir or DATA)
    models.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    log(f"install hardware={HARDWARE}")

    head = remote_head(CHATTERBOX_URL)
    server = RUNTIMES / "tts" / "trident-tts-server.exe"
    pin_text = PIN.read_text(encoding="ascii").strip() if PIN.is_file() else ""
    need_tts = not server.is_file() or pin_text != tts_pin(head)
    need_gguf = not (models / T3_FILE).is_file() or not (models / CODEC_FILE).is_file()
    if need_tts or need_gguf:
        head = clone(CHATTERBOX_URL, CHATTERBOX)
    if need_tts:
        build_tts(head)
    if need_gguf:
        convert_nano(models)

    if find_exe(RUNTIMES / "parakeet", "parakeet-server.exe") is None:
        unzip(pull(PARAKEET_ZIP[0], TOOLS / "downloads" / PARAKEET_ZIP[1]), RUNTIMES / "parakeet")
    if find_exe(RUNTIMES / "gemma", "llama-server.exe") is None:
        unzip(pull(LLAMA_ZIP[0], TOOLS / "downloads" / LLAMA_ZIP[1]), RUNTIMES / "gemma")

    for source, name in VOICES.values():
        pull(VOICE_HF + source, data / name)
    pull(PARAKEET_URL, models / PARAKEET_FILE)
    pull(GEMMA_URL, models / GEMMA_FILE)
    install_ui()
    for path in (THIRD_PARTY, TTS / "build", CONVERTER, TOOLS / "huggingface", TOOLS / "downloads"):
        wipe(path)
    log("install complete")
