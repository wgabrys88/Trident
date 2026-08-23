from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import time
import urllib.parse
import urllib.request
import wave
import zipfile
from pathlib import Path

from config import (
    FAMILIES, SHARED_MODELS, VULKAN_VERSION, PACKAGES, SOURCES, BINARIES,
    CHATTERBOX_LIBRARY, TTS_BUILD, TTS_SERVER_EXE, TTS,
    CHATTERBOX, GGML, RUNTIMES, CONVERTER, TOOLS, ROOT,
    REFERENCE_VOICES, REFERENCE_MIN_SECONDS, Paths,
)
from log import fail, note, run as run_logged


def validate_wav(path: Path, rate: int | None = None, minimum_seconds: float = 0.0, channels: int | None = None, *, pcm16: bool = True) -> None:
    if not path.is_file():
        raise RuntimeError(f"missing {path}; python main.py install --family nano")
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


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(text, encoding="utf-8", newline="\n")
    os.replace(partial, path)


def present(path: Path, size: int = 0) -> bool:
    return path.is_file() and path.stat().st_size > 0 and (not size or path.stat().st_size == size)


def executable(name: str) -> str | None:
    local = {
        "git": TOOLS / "git" / "cmd" / "git.exe",
        "cmake": TOOLS / "cmake-4.4.2-windows-x86_64" / "bin" / "cmake.exe",
    }.get(name)
    return str(local) if local and local.is_file() else shutil.which(name)


def need(name: str) -> str:
    value = executable(name)
    if not value:
        raise RuntimeError(f"{name} is missing")
    return value


def msvc_path() -> Path | None:
    root = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio"
    matches = sorted(root.glob("*/BuildTools/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe"), reverse=True)
    return matches[0] if matches else None


def vulkan_path() -> Path | None:
    roots = [Path(os.environ["VULKAN_SDK"])] if os.environ.get("VULKAN_SDK") else []
    roots += [TOOLS / "VulkanSDK" / VULKAN_VERSION]
    roots += sorted(Path("C:/VulkanSDK").glob("*"), reverse=True)
    return next((p for p in roots if (p / "Include/vulkan/vulkan.h").is_file() and (p / "Lib/vulkan-1.lib").is_file()), None)


def prerequisite_ready(name: str) -> bool:
    if name == "python":
        return sys.version_info >= (3, 11)
    if name == "git" or name == "cmake":
        return executable(name) is not None
    if name == "msvc":
        return msvc_path() is not None
    if name == "vulkan":
        return vulkan_path() is not None
    raise ValueError(name)


def build_env() -> dict[str, str]:
    sdk = vulkan_path()
    if not sdk:
        raise RuntimeError("Vulkan SDK is missing")
    env = os.environ.copy()
    env["VULKAN_SDK"] = str(sdk)
    paths = [str(sdk / "Bin"), str(Path(need("git")).parent), str(Path(need("cmake")).parent)]
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    return env


def run_process(component: str, stage: str, command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    note(f"component={component} stage={stage} event=start command={' '.join(command)}")
    started = time.perf_counter()
    run_logged(command, cwd, env or build_env())
    note(f"component={component} stage={stage} event=done elapsed_ms={(time.perf_counter() - started) * 1000.0:.3f}")


def rmtree_retry(path: Path, attempts: int = 10) -> None:
    last: OSError | None = None
    for _ in range(attempts):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.3)
    if last:
        raise last


def checkout(component: str, path: Path, source: str) -> None:
    url, revision = SOURCES[source]
    git = need("git")
    if path.exists() and not (path / ".git").is_dir():
        raise RuntimeError(f"non-git path blocks checkout: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        run_process(component, f"clone-{source}", [git, "clone", "--filter=blob:none", "--no-checkout", url, str(path)], path.parent)
    for stage, args in (
        (f"fetch-{source}", [git, "fetch", "--depth", "1", "origin", revision]),
        (f"checkout-{source}", [git, "checkout", "--detach", revision]),
        (f"reset-{source}", [git, "reset", "--hard", revision]),
        (f"clean-{source}", [git, "clean", "-fdx"]),
    ):
        run_process(component, stage, args, path)


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
        os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()).encode("utf-8"),
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


def _stop_owned_chatterbox_before_replace() -> None:
    from resident import stop_owned
    stop_owned("chatterbox")


def github_release_asset(spec: dict) -> tuple[str, int, str | None]:
    repo = urllib.parse.quote(spec["repo"], safe="/")
    tag = urllib.parse.quote(spec["tag"], safe="")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "trident/1", "X-GitHub-Api-Version": "2026-03-10"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    matches = [a for a in release.get("assets", []) if a.get("name") == spec["asset"]]
    if len(matches) != 1:
        raise RuntimeError(f"GitHub release asset not found exactly once: {spec['asset']}")
    asset = matches[0]
    size = int(asset.get("size") or 0)
    url = str(asset.get("browser_download_url") or "")
    if size <= 0 or not url.startswith("https://github.com/"):
        raise RuntimeError(f"invalid GitHub release metadata for {spec['asset']}")
    digest = str(asset.get("digest") or "")
    sha256 = digest.removeprefix("sha256:") if digest.startswith("sha256:") else None
    if sha256 is not None and (len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256)):
        raise RuntimeError(f"invalid GitHub SHA-256 metadata for {spec['asset']}")
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
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def extract_release_bundle(archive: Path, destination: Path, executable_names: tuple[str, ...]) -> None:
    partial = destination.with_name(destination.name + ".part")
    rmtree_retry(partial)
    partial.mkdir(parents=True)
    root = partial.resolve()
    try:
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                target = (partial / member.filename).resolve()
                if target != root and root not in target.parents:
                    raise RuntimeError(f"unsafe ZIP member: {member.filename}")
            package.extractall(partial)
        for executable_name in executable_names:
            matches = [p for p in partial.rglob("*") if p.is_file() and p.name.lower() == executable_name.lower()]
            if len(matches) != 1:
                raise RuntimeError(f"release bundle must contain exactly one {executable_name}; found {len(matches)}")
        rmtree_retry(destination)
        partial.rename(destination)
    except Exception:
        rmtree_retry(partial)
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


def runtime_tts_server(required: bool = True) -> Path | None:
    root = RUNTIMES / "tts"
    matches = [p for p in root.rglob("*") if p.is_file() and p.name.lower() == TTS_SERVER_EXE.lower()] if root.exists() else []
    if len(matches) == 1:
        return matches[0]
    if required:
        raise RuntimeError(f"{TTS_SERVER_EXE} is not installed; python main.py install --family all")
    return None


def tts_runtime_ready() -> bool:
    if not _native_build_ready():
        return False
    revision = trident_tts_revision()
    return _runtime_marker_matches("server", revision) and runtime_tts_server(required=False) is not None


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
    extract_release_bundle(archive, RUNTIMES / name, (spec["server_exe"],))
    archive.unlink(missing_ok=True)
    runtime_server(name)
    marker.write_text(identity + "\n", encoding="ascii")


def install_prerequisite(name: str) -> None:
    if prerequisite_ready(name):
        note(f"{name}: ready")
        return
    if name == "python":
        raise RuntimeError("Python 3.11+ must be installed before running main.py")
    spec = PACKAGES[name]
    archive = TOOLS / "downloads" / spec["file"]
    note(f"{name}: installing")
    fetch(spec["url"], archive, spec["size"], spec.get("sha256"))
    if name == "git":
        destination = TOOLS / "git"
        rmtree_retry(destination)
        with zipfile.ZipFile(archive) as package:
            package.extractall(destination)
    elif name == "cmake":
        destination = TOOLS / "cmake-4.4.2-windows-x86_64"
        rmtree_retry(destination)
        with zipfile.ZipFile(archive) as package:
            package.extractall(TOOLS)
    elif name == "msvc":
        run_process(name, "install", [str(archive), "--quiet", "--wait", "--norestart", "--nocache", "--add", "Microsoft.VisualStudio.Workload.VCTools", "--includeRecommended"], ROOT, os.environ.copy())
    elif name == "vulkan":
        destination = TOOLS / "VulkanSDK" / VULKAN_VERSION
        run_process(name, "install", [str(archive), "--root", str(destination), "--accept-licenses", "--default-answer", "--confirm-command", "install"], ROOT, os.environ.copy())
    if not prerequisite_ready(name):
        raise RuntimeError(f"{name} installer completed but prerequisite is still missing")


def install_tts() -> None:
    if tts_runtime_ready():
        note("CHATTERBOX TTS RUNTIME: ready")
        return
    cmake = need("cmake")

    native_revision = chatterbox_native_revision()
    if not _native_build_ready():
        note(f"tts native revision changed: rebuilding chatterbox.cpp {native_revision[:12]}")
        checkout("tts", CHATTERBOX, "chatterbox")
        checkout("tts", GGML, "ggml")
        run_process("tts", "configure-chatterbox", [cmake, "-S", ".", "-B", "build", "-A", "x64", "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF", "-DTTS_CPP_BUILD_EXECUTABLES=OFF", "-DTTS_CPP_BUILD_TESTS=OFF"], CHATTERBOX)
        run_process("tts", "build-chatterbox", [cmake, "--build", "build", "--config", "Release", "--target", "tts-cpp", "mtl_tokenizer", "--parallel"], CHATTERBOX)
        if not CHATTERBOX_LIBRARY.is_file():
            raise RuntimeError(f"Chatterbox build did not create {CHATTERBOX_LIBRARY}")
        marker = _native_build_marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(native_revision + "\n", encoding="ascii")

    revision = trident_tts_revision()
    runtime = RUNTIMES / "tts"
    source_changed = (
        (runtime_tts_server(required=False) is not None and not _runtime_marker_matches("server", revision))
        or (runtime / ".build-revision").is_file()
    )
    if source_changed:
        rmtree_retry(TTS / "build")

    run_process("tts", "configure-trident-tts", [cmake, "-S", ".", "-B", "build", "-A", "x64", f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}"], TTS)
    run_process("tts", "build-server", [cmake, "--build", "build", "--config", "Release", "--target", "trident-tts-server", "--parallel"], TTS)
    built_server = TTS_BUILD / TTS_SERVER_EXE
    if not built_server.is_file():
        raise RuntimeError(f"TTS build did not create {built_server}")

    _stop_owned_chatterbox_before_replace()
    runtime.mkdir(parents=True, exist_ok=True)
    for obsolete in (
        "trident-tts-nano.exe", "trident-tts-turbo.exe", "trident-tts-v3.exe",
        ".build-nano.revision", ".build-turbo.revision", ".build-v3.revision",
    ):
        (runtime / obsolete).unlink(missing_ok=True)
    shutil.copy2(built_server, runtime / built_server.name)
    for artifact in TTS_BUILD.iterdir():
        if artifact.is_file() and artifact.suffix.lower() == ".dll":
            shutil.copy2(artifact, runtime / artifact.name)
    _runtime_build_marker("server").write_text(revision + "\n", encoding="ascii")
    (runtime / ".build-revision").unlink(missing_ok=True)
    if not tts_runtime_ready():
        raise RuntimeError(f"TTS runtime verification failed for {TTS_SERVER_EXE}")

def models_for(family: str) -> dict:
    return {**FAMILIES[family]["TTS_MODELS"], **SHARED_MODELS}


def model_path(spec: dict, models_dir: Path) -> Path:
    return models_dir / spec["file"]


def require_model(spec: dict, models_dir: Path) -> Path:
    path = model_path(spec, models_dir)
    if not present(path, spec["size"]):
        raise RuntimeError(f"missing model {path}; python main.py install --family all")
    return path


def prune_convert_cache() -> None:
    rmtree_retry(CONVERTER / "checkpoints")
    rmtree_retry(TOOLS / "huggingface")
    note("convert cache: pruned")


def download_model(spec: dict, models_dir: Path) -> None:
    destination = model_path(spec, models_dir)
    if present(destination, spec["size"]):
        note(f"{spec['label']}: ready")
        return
    note(f"{spec['label']}: installing")
    if not spec.get("convert"):
        url = spec.get("url") or f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{spec['file']}"
        fetch(url, destination, spec["size"])
        return
    recipe = spec["convert"]
    script = CHATTERBOX / "scripts" / recipe["script"]
    if not CHATTERBOX_LIBRARY.is_file() or not script.is_file():
        raise RuntimeError("install Chatterbox TTS before converting its models")
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
        rmtree_retry(checkpoint)
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
    env = build_env()
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


def install(family: str, models_dir: Path | None = None, data_dir: Path | None = None) -> None:
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Trident installation requires Windows x64")
    if family != "all" and family not in FAMILIES:
        raise RuntimeError(f"unknown family: {family}")
    selected = list(FAMILIES) if family == "all" else [family]
    paths = Paths(models_dir, data_dir)
    paths.models_dir.mkdir(parents=True, exist_ok=True)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    for name in ("python", "git", "cmake", "msvc", "vulkan"):
        install_prerequisite(name)
    from media import ensure_ffmpeg
    ensure_ffmpeg()
    install_release_binary("parakeet")
    install_release_binary("gemma")
    download_reference_voices(paths.data_dir)
    if not tts_runtime_ready():
        install_tts()

    specs: dict[str, dict] = {spec["file"]: spec for spec in SHARED_MODELS.values()}
    for family_name in selected:
        for spec in FAMILIES[family_name]["TTS_MODELS"].values():
            specs[spec["file"]] = spec
    for spec in specs.values():
        download_model(spec, paths.models_dir)
    prune_convert_cache()
    note("install complete")
    note("five pipelines: parakeet, gemma, nano, turbo, v3" if family == "all" else f"installed TTS family: {family}")
    note("python main.py resident warm --family v3 --tts-language en -r trump  # preload Parakeet + Gemma + one Chatterbox voice")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m installer")
    parser.add_argument("--family", choices=("all", *tuple(FAMILIES)), default="all")
    parser.add_argument("--models-dir", type=Path, help="Override models directory")
    parser.add_argument("--data-dir", type=Path, help="Override data directory")
    args = parser.parse_args()
    try:
        install(args.family, args.models_dir, args.data_dir)
        return 0
    except Exception as exc:
        fail(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())