from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import venv
import time
import urllib.parse
import urllib.request
import wave
import zipfile
from pathlib import Path

from config import (
    FAMILIES, SHARED_MODELS, SOURCES, BINARIES,
    CHATTERBOX_LIBRARY, TTS_BUILD, TTS_SERVER_EXE, TTS,
    CHATTERBOX, GGML, RUNTIMES, CONVERTER, THIRD_PARTY, TOOLS, ROOT,
    REFERENCE_VOICES, REFERENCE_MIN_SECONDS, Paths, HARDWARE_PROFILE,
)
from log import note, run as run_logged


def validate_wav(path: Path, rate: int | None = None, minimum_seconds: float = 0.0, channels: int | None = None) -> None:
    if not path.is_file():
        raise RuntimeError(f"missing {path}; run: python main.py")
    with path.open("rb") as raw:
        header = raw.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise RuntimeError(f"invalid WAV {path}: not a RIFF/WAVE file")
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


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(text, encoding="utf-8", newline="\n")
    os.replace(partial, path)


def present(path: Path, size: int = 0) -> bool:
    return path.is_file() and path.stat().st_size > 0 and (not size or path.stat().st_size == size)



def run_process(component: str, stage: str, command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    note(f"component={component} stage={stage} event=start")
    started = time.perf_counter()
    try:
        run_logged(command, cwd, env or os.environ.copy())
    except Exception as exc:
        message = str(exc).splitlines()[0].strip() or type(exc).__name__
        note(f"component={component} stage={stage} event=failed message={message}")
        raise
    note(f"component={component} stage={stage} event=done elapsed_ms={(time.perf_counter() - started) * 1000.0:.3f}")


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    attrib = Path(os.environ["SystemRoot"]) / "System32" / "attrib.exe"
    subprocess.run([str(attrib), "-R", str(path / "*"), "/S", "/D"], check=True)
    shutil.rmtree(path)


def checkout(component: str, path: Path, source: str) -> None:
    url, revision = SOURCES[source]
    git = "git"
    if path.exists() and not (path / ".git").is_dir():
        raise RuntimeError(f"non-git path blocks checkout: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        run_process(component, f"clone-{source}", [git, "clone", "--filter=blob:none", "--no-checkout", url, str(path)], path.parent, os.environ.copy())
    for stage, args in (
        (f"fetch-{source}", [git, "fetch", "--depth", "1", "origin", revision]),
        (f"checkout-{source}", [git, "checkout", "--detach", revision]),
        (f"reset-{source}", [git, "reset", "--hard", revision]),
        (f"clean-{source}", [git, "clean", "-fdx"]),
    ):
        run_process(component, stage, args, path, os.environ.copy())


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
        platform.processor().encode("utf-8"), HARDWARE_PROFILE.encode("ascii"),
        b"ggml-vulkan:auto-disable-f16-nvidia-pre-turing-v2",
        b"-DGGML_VULKAN=ON|-DGGML_CUDA=OFF|-DGGML_NATIVE=ON|-DGGML_CCACHE=OFF|-DTTS_CPP_BUILD_EXECUTABLES=OFF|-DTTS_CPP_BUILD_TESTS=OFF",
    ]
    return _hash_identity(parts)


def _native_build_marker() -> Path:
    return CHATTERBOX / "build" / ".trident-native-revision"


def _native_build_ready() -> bool:
    marker = _native_build_marker()
    return (
        CHATTERBOX_LIBRARY.is_file()
        and marker.is_file()
        and marker.read_text(encoding="ascii", errors="ignore").strip() == chatterbox_native_revision()
    )


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
    note(f"component=download event=start file={destination.name} expected_bytes={size}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                output.write(block)
                done += len(block)
        if size and done != size:
            note(
                f"component=download event=failed file={destination.name} invariant=size"
                f" expected_bytes={size} actual_bytes={done}"
            )
            raise RuntimeError(f"download size mismatch for {destination.name}: expected {size}, got {done}")
        if sha256:
            with partial.open("rb") as downloaded:
                actual = hashlib.file_digest(downloaded, "sha256").hexdigest()
            if actual != sha256:
                note(f"component=download event=failed file={destination.name} invariant=sha256")
                raise RuntimeError(f"download SHA-256 mismatch for {destination.name}")
        os.replace(partial, destination)
        note(f"component=download event=complete file={destination.name} bytes={done}")
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def extract_release_bundle(archives: tuple[Path, ...], destination: Path, required_names: tuple[str, ...]) -> None:
    partial = destination.with_name(destination.name + ".part")
    remove_tree(partial)
    partial.mkdir(parents=True)
    root = partial.resolve()
    try:
        for archive in archives:
            with zipfile.ZipFile(archive) as package:
                for member in package.infolist():
                    target = (partial / member.filename).resolve()
                    if target != root and root not in target.parents:
                        raise RuntimeError(f"unsafe ZIP member: {member.filename}")
                package.extractall(partial)
        for name in required_names:
            matches = [p for p in partial.rglob("*") if p.is_file() and p.name.lower() == name.lower()]
            if len(matches) != 1:
                raise RuntimeError(f"release bundle must contain exactly one {name}; found {len(matches)}")
        remove_tree(destination)
        partial.rename(destination)
    except Exception:
        remove_tree(partial)
        raise


def _runtime_binary(name: str, key: str, required: bool = True) -> Path | None:
    exe = BINARIES[name][key]
    root = RUNTIMES / name
    matches = [p for p in root.rglob("*") if p.is_file() and p.name.lower() == exe.lower()] if root.exists() else []
    if len(matches) == 1:
        return matches[0]
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


def install_ui() -> Path:
    env = ROOT / ".venv"
    python = env / "Scripts" / "python.exe"
    requirements = ROOT / "requirements-ui.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = env / ".trident-ui"
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(env)
    if not marker.is_file() or marker.read_text(encoding="ascii").strip() != digest:
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)], check=True)
        marker.write_text(digest + "\n", encoding="ascii")
    return python


def runtime_tts_server(required: bool = True) -> Path | None:
    root = RUNTIMES / "tts"
    matches = [p for p in root.rglob("*") if p.is_file() and p.name.lower() == TTS_SERVER_EXE.lower()] if root.exists() else []
    if len(matches) == 1:
        return matches[0]
    if required:
        raise RuntimeError(f"{TTS_SERVER_EXE} is not installed; run: python main.py")
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



def install_tts() -> None:
    cmake = "cmake"

    native_revision = chatterbox_native_revision()
    if not _native_build_ready():
        note("component=tts stage=native event=rebuild reason=source_changed")
        checkout("tts", CHATTERBOX, "chatterbox")
        checkout("tts", GGML, "ggml")
        tune_ggml_vulkan()
        run_process("tts", "configure-chatterbox", [cmake, "-S", ".", "-B", "build", "-A", "x64", "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF", "-DTTS_CPP_BUILD_EXECUTABLES=OFF", "-DTTS_CPP_BUILD_TESTS=OFF"], CHATTERBOX)
        run_process("tts", "build-chatterbox", [cmake, "--build", "build", "--config", "Release", "--target", "tts-cpp", "mtl_tokenizer", "--parallel"], CHATTERBOX)
        if not CHATTERBOX_LIBRARY.is_file():
            raise RuntimeError(f"Chatterbox build did not create {CHATTERBOX_LIBRARY}")
        marker = _native_build_marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(native_revision + "\n", encoding="ascii")

    revision = trident_tts_revision()
    runtime = RUNTIMES / "tts"
    if runtime_tts_server(required=False) is not None and not _runtime_marker_matches("server", revision):
        remove_tree(TTS / "build")

    run_process("tts", "configure-trident-tts", [cmake, "-S", ".", "-B", "build", "-A", "x64", f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}"], TTS)
    run_process("tts", "build-server", [cmake, "--build", "build", "--config", "Release", "--target", "trident-tts-server", "--parallel"], TTS)
    built_server = TTS_BUILD / TTS_SERVER_EXE
    if not built_server.is_file():
        raise RuntimeError(f"TTS build did not create {built_server}")

    from resident import stop_owned
    stop_owned("chatterbox")
    runtime.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built_server, runtime / built_server.name)
    for artifact in TTS_BUILD.iterdir():
        if artifact.is_file() and artifact.suffix.lower() == ".dll":
            shutil.copy2(artifact, runtime / artifact.name)
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
        raise RuntimeError(f"missing model {path}; run: python main.py")
    return path


def clean_install_artifacts() -> None:
    for path in (
        THIRD_PARTY,
        TTS / "build",
        CONVERTER,
        TOOLS / "huggingface",
        TOOLS / "downloads",
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
    python = CONVERTER / "Scripts" / "python.exe"
    torch_pin = "torch==2.6.0"
    packages = "numpy==1.26.4 gguf==0.19.0 safetensors==0.5.3 scipy==1.15.3 librosa==0.11.0 resampy==0.4.3 huggingface-hub==0.34.4"
    lock = torch_pin + " " + packages
    stamp = CONVERTER / ".packages"
    if not python.is_file():
        run_process(spec["label"], "venv", [sys.executable, "-m", "venv", str(CONVERTER)], ROOT, os.environ.copy())
    if not stamp.is_file() or stamp.read_text(encoding="ascii") != lock:
        run_process(spec["label"], "torch", [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", torch_pin, "--index-url", "https://download.pytorch.org/whl/cpu"], ROOT, os.environ.copy())
        run_process(spec["label"], "dependencies", [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off", "--no-input", *packages.split()], ROOT, os.environ.copy())
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(lock, encoding="ascii")
    required = tuple(recipe["files"])
    checkpoint = CONVERTER / "checkpoints" / spec["revision"]
    marker = checkpoint / ".revision"
    stamp_id = spec["repo"] + ":" + spec["revision"] + ":" + ",".join(required)
    cache_ok = marker.is_file() and marker.read_text(encoding="ascii") == stamp_id and all((checkpoint / f).is_file() for f in required)
    if not cache_ok:
        remove_tree(checkpoint)
        checkpoint.mkdir(parents=True, exist_ok=True)
        code = (
            "from huggingface_hub import snapshot_download; "
            f"snapshot_download(repo_id={spec['repo']!r}, revision={spec['revision']!r}, "
            f"allow_patterns={list(required)!r}, local_dir={str(checkpoint)!r})"
        )
        env = os.environ.copy()
        env["HF_HOME"] = str(TOOLS / "huggingface")
        run_process(spec["label"], "checkpoint", [str(python), "-c", code], ROOT, env)
        missing = [f for f in required if not (checkpoint / f).is_file()]
        if missing:
            raise RuntimeError(f"checkpoint download incomplete: {missing}")
        marker.write_text(stamp_id, encoding="ascii")
    for src, dst in recipe.get("copy", {}).items():
        shutil.copyfile(checkpoint / src, checkpoint / dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    command = [str(python), str(script)]
    if recipe.get("variant"):
        command += ["--variant", recipe["variant"]]
    command += ["--ckpt-dir", str(checkpoint), "--out", str(partial), "--quant", recipe["quant"]]
    env = os.environ.copy()
    env["HF_HOME"] = str(TOOLS / "huggingface")
    run_process(spec["label"], "convert", command, ROOT, env)
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


def install(models_dir: Path | None = None, data_dir: Path | None = None) -> Path:
    if sys.version_info < (3, 11):
        raise RuntimeError("Trident requires Python 3.11 or newer")
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Trident installation requires Windows x64")
    paths = Paths(models_dir, data_dir)
    paths.models_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    specs = {spec["file"]: spec for spec in SHARED_MODELS.values()}
    for family in FAMILIES.values():
        specs.update((spec["file"], spec) for spec in family["TTS_MODELS"].values())
    needs_tts = not tts_runtime_ready()
    missing_conversion = any(
        spec.get("convert") and not present(model_path(spec, paths.models_dir), spec["size"])
        for spec in specs.values()
    )

    if needs_tts:
        install_tts()
    elif missing_conversion:
        checkout("tts", CHATTERBOX, "chatterbox")

    install_release_binary("parakeet")
    install_release_binary("gemma")
    download_reference_voices(paths.data_dir)
    for spec in specs.values():
        download_model(spec, paths.models_dir)
    clean_install_artifacts()
    python = install_ui()
    note("component=install event=complete family=nano")
    return python
