from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import venv
import wave
import zipfile
from pathlib import Path

from config import (
    FAMILIES, SHARED_MODELS, VULKAN_VERSION, PACKAGES, SOURCES, BINARIES,
    TTS_SERVER_EXE, TTS, CHATTERBOX, GGML, RUNTIMES, CONVERTER, THIRD_PARTY, TOOLS, ROOT,
    REFERENCE_VOICES, REFERENCE_MIN_SECONDS, Paths, HARDWARE_PROFILE, PLATFORM, ARCHITECTURE,
)
from log import note, run as run_logged


def validate_wav(path: Path, rate: int | None = None, minimum_seconds: float = 0.0, channels: int | None = None, *, pcm16: bool = True) -> None:
    if not path.is_file():
        raise RuntimeError(f"missing WAV: {path}")
    with path.open("rb") as raw:
        header = raw.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise RuntimeError(f"invalid WAV {path}: not a RIFF/WAVE file")
    if not pcm16:
        return
    with wave.open(str(path), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise RuntimeError(f"invalid WAV {path}: must be PCM16")
        if channels is not None and audio.getnchannels() != channels:
            raise RuntimeError(f"invalid WAV {path}: must be {channels}-channel")
        if rate is not None and audio.getframerate() != rate:
            raise RuntimeError(f"invalid WAV {path}: must be {rate} Hz")
        if audio.getframerate() <= 0 or audio.getnframes() <= 0:
            raise RuntimeError(f"invalid WAV {path}: no audio frames")
        if audio.getnframes() / audio.getframerate() < minimum_seconds:
            raise RuntimeError(f"invalid WAV {path}: must be at least {minimum_seconds:g} seconds")



def present(path: Path, size: int = 0) -> bool:
    return path.is_file() and path.stat().st_size > 0 and (not size or path.stat().st_size == size)


def cmake_bin() -> str:
    path = Path(sys.executable).parent / ("cmake.exe" if PLATFORM == "windows" else "cmake")
    if not path.is_file():
        raise RuntimeError(f"bootstrap did not install CMake: {path}")
    return str(path)

def compiler_path() -> Path | None:
    if PLATFORM == "linux":
        path = TOOLS / "toolchain" / "clang++"
        return path if path.is_file() else None
    root = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio"
    matches = sorted(root.glob("*/BuildTools/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe"), reverse=True)
    return matches[0] if matches else None


def vulkan_path() -> Path | None:
    root = TOOLS / "VulkanSDK" / VULKAN_VERSION
    sdk = root if PLATFORM == "windows" else root / "x86_64"
    include = sdk / ("Include/vulkan/vulkan.h" if PLATFORM == "windows" else "include/vulkan/vulkan.h")
    library = sdk / ("Lib/vulkan-1.lib" if PLATFORM == "windows" else "lib/libvulkan.so")
    glslc = sdk / ("Bin/glslc.exe" if PLATFORM == "windows" else "bin/glslc")
    return sdk if include.is_file() and library.is_file() and glslc.is_file() else None


def _zig_toolchain() -> None:
    zig = Path(sys.executable).parent / "python-zig"
    if not zig.is_file():
        raise RuntimeError(f"bootstrap did not install Zig: {zig}")
    directory = TOOLS / "toolchain"
    directory.mkdir(parents=True, exist_ok=True)
    quoted = shlex.quote(str(zig))
    for name, mode in (("clang", "cc"), ("clang++", "c++")):
        wrapper = directory / name
        wrapper.write_text(f'#!/bin/sh\nexec {quoted} {mode} "$@"\n', encoding="utf-8", newline="\n")
        wrapper.chmod(0o755)


def prerequisite_ready(name: str) -> bool:
    if name == "compiler": return compiler_path() is not None
    if name == "vulkan": return vulkan_path() is not None
    raise ValueError(name)


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    sdk = vulkan_path()
    compiler = compiler_path()
    if sdk is None or compiler is None:
        raise RuntimeError("native build prerequisites are not installed")
    paths = [str(Path(cmake_bin()).parent), str(sdk / ("Bin" if PLATFORM == "windows" else "bin"))]
    env["VULKAN_SDK"] = str(sdk)
    env["CMAKE_PREFIX_PATH"] = os.pathsep.join((str(sdk), str(sdk / "lib" / "VulkanLoader"), env.get("CMAKE_PREFIX_PATH", "")))
    if PLATFORM == "linux":
        toolchain = str(compiler.parent)
        paths.insert(0, toolchain)
        env["CC"] = str(compiler.parent / "clang")
        env["CXX"] = str(compiler)
        env["LD_LIBRARY_PATH"] = os.pathsep.join((str(sdk / "lib"), env.get("LD_LIBRARY_PATH", "")))
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    return env

def run_process(component: str, stage: str, command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    note(f"component={component} stage={stage} event=start")
    started = time.perf_counter()
    try:
        run_logged(command, cwd, env or os.environ.copy())
    except RuntimeError as exc:
        message = str(exc).splitlines()[0].strip() or type(exc).__name__
        note(f"component={component} stage={stage} event=failed message={message}")
        raise
    note(f"component={component} stage={stage} event=done elapsed_ms={(time.perf_counter() - started) * 1000.0:.3f}")


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if PLATFORM == "windows":
        attrib = Path(os.environ["SystemRoot"]) / "System32" / "attrib.exe"
        subprocess.run([str(attrib), "-R", str(path / "*"), "/S", "/D"], check=True)
    shutil.rmtree(path)


def source_tree(name: str, path: Path) -> None:
    repo, revision = SOURCES[name]
    archive = TOOLS / "downloads" / f"{name}-{revision}.tar.gz"
    fetch(repo.removesuffix(".git") + f"/archive/{revision}.tar.gz", archive, 0)
    partial = path.with_name(path.name + ".part")
    remove_tree(partial)
    partial.mkdir(parents=True)
    with tarfile.open(archive) as package:
        members = package.getmembers()
        for member in members:
            _archive_target(partial.resolve(), member.name)
        roots = {Path(member.name).parts[0] for member in members if member.name}
        if len(roots) != 1:
            raise RuntimeError(f"source archive must contain one root: {name}")
        package.extractall(partial)
    source = partial / roots.pop()
    remove_tree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    source.rename(path)
    remove_tree(partial)
    archive.unlink()

def tune_ggml_vulkan() -> None:
    path = GGML / "src" / "ggml-vulkan" / "ggml-vulkan.cpp"
    lines = path.read_text(encoding="utf-8").splitlines()
    direct = [i for i, line in enumerate(lines) if "force_disable_f16" in line and "getenv(" in line]
    staged = [i for i, line in enumerate(lines[:-1]) if "getenv(" in line and "force_disable_f16" in lines[i + 1]]
    if len(direct) != 1 or len(staged) != 1:
        raise RuntimeError("pinned ggml Vulkan FP16 policy no longer matches")
    lines[direct[0]] = "    const bool force_disable_f16 = device->vendor_id == VK_VENDOR_ID_NVIDIA && device->architecture == vk_device_architecture::NVIDIA_PRE_TURING;"
    i = staged[0]
    lines[i] = ""
    lines[i + 1] = "    bool force_disable_f16 = physical_device.getProperties().vendorID == VK_VENDOR_ID_NVIDIA && device_architecture == vk_device_architecture::NVIDIA_PRE_TURING;"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _hash_identity(parts: list[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "little"))
        digest.update(part)
    return digest.hexdigest()


def chatterbox_native_revision() -> str:
    parts = [
        repr(SOURCES["chatterbox"]).encode("utf-8"),
        repr(SOURCES["ggml"]).encode("utf-8"),
        PLATFORM.encode("ascii"), ARCHITECTURE.encode("ascii"), platform.processor().encode("utf-8"), HARDWARE_PROFILE.encode("ascii"),
        b"ggml-vulkan:auto-disable-f16-nvidia-pre-turing-v2",
        b"trident-cmake:add-subdirectory-static-v1",
        f"vulkan-sdk={VULKAN_VERSION}".encode("ascii"),
        (b"toolchain=msvc" if PLATFORM == "windows" else b"toolchain=ziglang-0.16.0+ninja-1.13.0"),
    ]
    return _hash_identity(parts)


def trident_tts_revision() -> str:
    parts = [chatterbox_native_revision().encode("ascii")]
    for path in sorted(p for p in TTS.rglob("*") if p.is_file() and "build" not in p.parts):
        rel = path.relative_to(TTS).as_posix().encode("utf-8")
        parts.append(rel)
        parts.append(path.read_bytes())
    return _hash_identity(parts)


def _runtime_build_marker(component: str) -> Path:
    return RUNTIMES / "tts" / f".build-{component}.revision"


def _runtime_marker_matches(component: str, revision: str) -> bool:
    marker = _runtime_build_marker(component)
    return marker.is_file() and marker.read_text(encoding="ascii", errors="ignore").strip() == revision


def github_release_asset(spec: dict, asset_name: str | None = None) -> tuple[str, int, str | None]:
    repo = urllib.parse.quote(spec["repo"], safe="/")
    tag = urllib.parse.quote(spec["tag"], safe="")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "trident/1", "X-GitHub-Api-Version": "2026-03-10"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    asset_name = asset_name or spec["asset"]
    matches = [a for a in release.get("assets", []) if a.get("name") == asset_name]
    if len(matches) != 1:
        raise RuntimeError(f"GitHub release asset not found exactly once: {asset_name}")
    asset = matches[0]
    size = int(asset.get("size") or 0)
    url = str(asset.get("browser_download_url") or "")
    if size <= 0 or not url.startswith("https://github.com/"):
        raise RuntimeError(f"invalid GitHub release metadata for {asset_name}")
    digest = str(asset.get("digest") or "")
    sha256 = digest.removeprefix("sha256:") if digest.startswith("sha256:") else None
    if sha256 is not None and (len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256)):
        raise RuntimeError(f"invalid GitHub SHA-256 metadata for {asset_name}")
    return url, size, sha256.lower() if sha256 else None


def fetch(url: str, destination: Path, size: int, sha256: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if present(destination, size):
        if not sha256:
            return
        with destination.open("rb") as cached:
            if hashlib.file_digest(cached, "sha256").hexdigest() == sha256:
                return
        destination.unlink()
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
    done = 0
    note(f"download start file={destination.name} size={size}")
    complete = False
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                output.write(block)
                done += len(block)
        note(f"download done file={destination.name} bytes={done}")
        if size and done != size:
            raise RuntimeError(f"download size mismatch for {destination.name}: expected {size}, got {done}")
        if sha256:
            with partial.open("rb") as downloaded:
                actual = hashlib.file_digest(downloaded, "sha256").hexdigest()
            if actual != sha256:
                raise RuntimeError(f"download SHA-256 mismatch for {destination.name}: expected {sha256}, got {actual}")
        os.replace(partial, destination)
        complete = True
    finally:
        if not complete:
            partial.unlink(missing_ok=True)


def _archive_target(root: Path, name: str) -> Path:
    target = (root / name).resolve()
    if target != root and root not in target.parents:
        raise RuntimeError(f"unsafe archive member: {name}")
    return target


def extract_release_bundle(archives: tuple[Path, ...], destination: Path, required_names: tuple[str, ...]) -> None:
    partial = destination.with_name(destination.name + ".part")
    remove_tree(partial)
    partial.mkdir(parents=True)
    root = partial.resolve()
    complete = False
    try:
        for archive in archives:
            if zipfile.is_zipfile(archive):
                with zipfile.ZipFile(archive) as package:
                    for member in package.infolist():
                        _archive_target(root, member.filename)
                    package.extractall(partial)
            elif tarfile.is_tarfile(archive):
                with tarfile.open(archive) as package:
                    for member in package.getmembers():
                        target = _archive_target(root, member.name)
                        if member.issym() or member.islnk():
                            link = (target.parent / member.linkname).resolve()
                            if link != root and root not in link.parents:
                                raise RuntimeError(f"unsafe archive link: {member.name} -> {member.linkname}")
                    package.extractall(partial)
            else:
                raise RuntimeError(f"unsupported release archive: {archive.name}")
        for name in required_names:
            matches = [p for p in partial.rglob("*") if p.is_file() and p.name.lower() == name.lower()]
            if len(matches) != 1:
                raise RuntimeError(f"release bundle must contain exactly one {name}; found {len(matches)}")
        remove_tree(destination)
        partial.rename(destination)
        complete = True
    finally:
        if not complete:
            remove_tree(partial)


def _runtime_binary(name: str, key: str, required: bool = True) -> Path | None:
    exe = BINARIES[name][key]
    root = RUNTIMES / name
    matches = [p for p in root.rglob("*") if p.is_file() and p.name.lower() == exe.lower()] if root.exists() else []
    if len(matches) == 1:
        binary = matches[0]
        if PLATFORM == "linux" and not os.access(binary, os.X_OK):
            raise RuntimeError(f"runtime binary is not executable: {binary}")
        return binary
    if required:
        raise RuntimeError(f"runtime must contain exactly one {exe}; found {len(matches)}")
    return None


def runtime_server(name: str, required: bool = True) -> Path | None:
    return _runtime_binary(name, "server_exe", required)


def _release_marker(name: str) -> Path:
    return RUNTIMES / name / ".trident-release"


def _release_identity(name: str) -> str:
    spec = BINARIES[name]
    return f"{spec['repo']}@{spec['tag']}:{spec['asset']}"


def runtime_tts_server(required: bool = True) -> Path | None:
    root = RUNTIMES / "tts"
    matches = [p for p in root.rglob("*") if p.is_file() and p.name.lower() == TTS_SERVER_EXE.lower()] if root.exists() else []
    if len(matches) == 1:
        binary = matches[0]
        if PLATFORM == "linux" and not os.access(binary, os.X_OK):
            raise RuntimeError(f"runtime binary is not executable: {binary}")
        return binary
    if required:
        raise RuntimeError(f"missing TTS runtime: {TTS_SERVER_EXE}")
    return None


def tts_runtime_ready() -> bool:
    return _runtime_marker_matches("server", trident_tts_revision()) and runtime_tts_server(required=False) is not None


def install_release_binary(name: str) -> None:
    spec = BINARIES[name]
    marker = _release_marker(name)
    identity = _release_identity(name)
    installed = runtime_server(name, required=False)
    if installed and marker.is_file() and marker.read_text(encoding="ascii", errors="ignore").strip() == identity:
        note(f"{spec['label']}: ready (pinned resident server)")
        return
    note(f"{spec['label']}: installing pinned {spec['tag']} release")
    url, size, sha256 = github_release_asset(spec)
    archive = TOOLS / "downloads" / spec["asset"]
    fetch(url, archive, size, sha256)
    extract_release_bundle((archive,), RUNTIMES / name, (spec["server_exe"],))
    archive.unlink(missing_ok=True)
    runtime_server(name)
    marker.write_text(identity + "\n", encoding="ascii")


def install_prerequisite(name: str) -> None:
    if name == "compiler" and PLATFORM == "linux":
        _zig_toolchain()
        note("compiler: ready (Zig)")
        return
    if prerequisite_ready(name):
        note(f"{name}: ready")
        return
    spec = PACKAGES[name]
    archive = TOOLS / "downloads" / spec["file"]
    note(f"{name}: installing")
    fetch(spec["url"], archive, spec.get("size", 0), spec.get("sha256"))
    if name == "compiler":
        run_process(name, "install", [str(archive), "--quiet", "--wait", "--norestart", "--nocache", "--add", "Microsoft.VisualStudio.Workload.VCTools", "--includeRecommended"], ROOT, os.environ.copy())
    elif PLATFORM == "windows":
        destination = TOOLS / "VulkanSDK" / VULKAN_VERSION
        run_process(name, "install", [str(archive), "--root", str(destination), "--accept-licenses", "--default-answer", "--confirm-command", "install"], ROOT, os.environ.copy())
    else:
        destination = TOOLS / "VulkanSDK"
        remove_tree(destination)
        destination.mkdir(parents=True)
        root = destination.resolve()
        with tarfile.open(archive) as package:
            for member in package.getmembers():
                _archive_target(root, member.name)
            package.extractall(destination)
    archive.unlink(missing_ok=True)
    if not prerequisite_ready(name):
        raise RuntimeError(f"{name} installer completed but prerequisite is still missing")



def install_tts() -> None:
    if tts_runtime_ready():
        note("CHATTERBOX TTS RUNTIME: ready")
        return
    cmake = cmake_bin()
    env = build_env()
    source_tree("chatterbox", CHATTERBOX)
    source_tree("ggml", GGML)
    tune_ggml_vulkan()
    revision = trident_tts_revision()
    if runtime_tts_server(required=False) is not None and not _runtime_marker_matches("server", revision):
        remove_tree(TTS / "build")
    configure = [
        cmake, "-S", ".", "-B", "build", "-DCMAKE_BUILD_TYPE=Release",
        f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}",
    ]
    if PLATFORM == "windows":
        configure += ["-A", "x64"]
    else:
        configure += ["-G", "Ninja"]
    run_process("tts", "configure", configure, TTS, env)
    run_process("tts", "build", [cmake, "--build", "build", "--config", "Release", "--target", "trident-tts-server", "--parallel"], TTS, env)
    matches = [path for path in (TTS / "build").rglob(TTS_SERVER_EXE) if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f"TTS build must create exactly one {TTS_SERVER_EXE}; found {len(matches)}")
    from resident import stop_owned
    stop_owned("chatterbox")
    runtime = RUNTIMES / "tts"
    remove_tree(runtime)
    runtime.mkdir(parents=True)
    shutil.copy2(matches[0], runtime / TTS_SERVER_EXE)
    _runtime_build_marker("server").write_text(revision + "\n", encoding="ascii")
    if not tts_runtime_ready():
        raise RuntimeError(f"TTS runtime verification failed for {TTS_SERVER_EXE}")


def models_for(family: str) -> dict:
    return {**FAMILIES[family]["TTS_MODELS"], **SHARED_MODELS}


def model_path(spec: dict, models_dir: Path) -> Path:
    return models_dir / spec["file"]


def require_model(spec: dict, models_dir: Path) -> Path:
    path = model_path(spec, models_dir)
    if not present(path, spec["size"]):
        raise RuntimeError(f"missing model: {path}")
    return path


def clean_install_artifacts() -> None:
    for path in (
        THIRD_PARTY,
        TTS / "build",
        CONVERTER,
        TOOLS / "downloads",
        TOOLS / "VulkanSDK",
        TOOLS / "toolchain",
    ):
        remove_tree(path)
    note("install build artifacts: pruned")


def download_model(spec: dict, models_dir: Path) -> None:
    destination = model_path(spec, models_dir)
    if present(destination, spec["size"]):
        note(f"{spec['label']}: ready")
        return
    note(f"{spec['label']}: installing")
    if not spec.get("convert"):
        url = spec.get("url") or f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{spec['file']}"
        fetch(url, destination, spec["size"], spec.get("sha256"))
        return
    recipe = spec["convert"]
    script = CHATTERBOX / "scripts" / recipe["script"]
    if not script.is_file():
        raise RuntimeError("Chatterbox conversion sources are missing")
    builder = venv.EnvBuilder(with_pip=True)
    python = Path(builder.ensure_directories(CONVERTER).env_exe)
    torch_pin = "torch==2.6.0"
    packages = "numpy==1.26.4 gguf==0.19.0 safetensors==0.5.3 scipy==1.15.3 librosa==0.11.0 resampy==0.4.3"
    lock = torch_pin + " " + packages
    stamp = CONVERTER / ".packages"
    if not python.is_file():
        run_process(spec["label"], "venv", [sys.executable, "-m", "venv", str(CONVERTER)], ROOT, os.environ.copy())
    if not stamp.is_file() or stamp.read_text(encoding="ascii") != lock:
        run_process(spec["label"], "torch", [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", torch_pin, "--index-url", "https://download.pytorch.org/whl/cpu"], ROOT, os.environ.copy())
        run_process(spec["label"], "dependencies", [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", *packages.split()], ROOT, os.environ.copy())
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(lock, encoding="ascii")
    checkpoint = CONVERTER / "checkpoints" / spec["revision"]
    for name in recipe["files"]:
        fetch(f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}", checkpoint / name, 0)
    for src, dst in recipe.get("copy", {}).items():
        shutil.copyfile(checkpoint / src, checkpoint / dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    command = [str(python), str(script)]
    if recipe.get("variant"):
        command += ["--variant", recipe["variant"]]
    command += ["--ckpt-dir", str(checkpoint), "--out", str(partial), "--quant", recipe["quant"]]
    run_process(spec["label"], "convert", command, ROOT, os.environ.copy())
    if not present(partial, spec["size"]):
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"converted model missing or wrong size: {partial}")
    os.replace(partial, destination)


def download_reference_voices(data_dir: Path) -> None:
    for spec in REFERENCE_VOICES.values():
        dest = data_dir / spec["file"]
        if present(dest, spec["size"]):
            note(f"{spec['label']}: ready")
            continue
        note(f"{spec['label']}: installing")
        url = f"https://huggingface.co/datasets/{spec['repo']}/resolve/{spec['revision']}/{spec['source']}"
        fetch(url, dest, spec["size"])
        validate_wav(dest, minimum_seconds=REFERENCE_MIN_SECONDS)


def install(models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    if PLATFORM not in {"windows", "linux"} or ARCHITECTURE != "x64":
        raise RuntimeError(f"Trident installation requires Windows or Linux x64, got {PLATFORM}/{ARCHITECTURE}")
    paths = Paths(models_dir, data_dir)
    paths.models_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    specs = {spec["file"]: spec for spec in SHARED_MODELS.values()}
    for family in FAMILIES.values():
        for spec in family["TTS_MODELS"].values():
            specs[spec["file"]] = spec
    needs_tts = not tts_runtime_ready()
    missing_conversion = any(spec.get("convert") and not present(model_path(spec, paths.models_dir), spec["size"]) for spec in specs.values())
    if needs_tts:
        for name in ("compiler", "vulkan"):
            install_prerequisite(name)
    from media import ensure_ffmpeg
    ensure_ffmpeg()
    install_release_binary("parakeet")
    install_release_binary("gemma")
    download_reference_voices(paths.data_dir)
    if needs_tts:
        install_tts()
    elif missing_conversion:
        source_tree("chatterbox", CHATTERBOX)
    for spec in specs.values():
        download_model(spec, paths.models_dir)
    clean_install_artifacts()
    note("component=install event=complete")
