from __future__ import annotations
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
import webbrowser
import zipfile
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from cfg import (
    ASR_CHUNK, ASR_LANGUAGES, ASR_RUNTIME, BRAIN_FAMILY, BRAIN_GENERATION,
    BRAIN_MODEL, BRAIN_RUNTIME, BRAIN_SYSTEM, CONTROLLER, DEFAULT_REPLY_LANGUAGE,
    MIC, PORTS, TTS_CHUNK, TTS_LANGUAGES, TTS_RUNTIME, TTS_SAMPLE, TTS_VOICE,
)
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
MODELS_DIR = ROOT / "models"
THIRD_PARTY = ROOT / "third_party"
TOOLS = ROOT / "tools"
PATCHES = ROOT / "patches"
SERVER = ROOT / "server"
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML = CHATTERBOX / "ggml"
RUNTIMES = TOOLS / "runtime"
CONVERTER = TOOLS / "convert"
SOURCES = {
    "chatterbox": ("https://github.com/gianni-cor/chatterbox.cpp", "ddca05fb69c2910b0d7b5eae420d360ed98c067b"),
    "ggml": ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951"),
}
BINARIES = {
    "parakeet": {"label": "PARAKEET.CPP V0.5 VULKAN", "repo": "mudler/parakeet.cpp", "tag": "v0.5.0", "asset": "parakeet-v0.5.0-bin-win-vulkan-x64.zip", "exe": "parakeet-server.exe"},
    "gemma": {"label": "LLAMA.CPP B10453 VULKAN", "repo": "ggml-org/llama.cpp", "tag": "b10453", "asset": "llama-b10453-bin-win-vulkan-x64.zip", "exe": "llama-server.exe"},
}
MODELS = {
    "chatterbox-t3": {"label": "CHATTERBOX V3 T3", "repo": "ResembleAI/chatterbox", "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18", "file": "chatterbox-t3-mtl-v3-q4_0.gguf", "size": 344985408},
    "chatterbox-codec": {"label": "CHATTERBOX V3 S3GEN", "repo": "ResembleAI/chatterbox", "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18", "file": "chatterbox-s3gen-mtl-v3-f16.gguf", "size": 1056431360},
    "parakeet": {"label": "PARAKEET TDT 0.6B V3 Q4_K", "repo": "mudler/parakeet-cpp-gguf", "revision": "bf0af9f425fa01809cadec671b3cb672709d13e9", "file": "tdt-0.6b-v3-q4_k.gguf", "size": 675200864},
    "gemma": {"label": "GEMMA 4 E2B", "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf", "revision": "675cff42a74c774d6cb76f76d8eacb49b48c9b93", "file": "gemma-4-E2B_q4_0-it.gguf", "size": 3349516256},
    "reference": {"label": "DEFAULT VOICE", "source": "assets/default-reference.wav", "file": "default-reference.wav", "directory": "data", "size": 1012558},
}
VULKAN_VERSION = "1.4.357.0"
PACKAGES = {
    "git": {"url": "https://github.com/git-for-windows/git/releases/download/v2.54.0.windows.1/MinGit-2.54.0-64-bit.zip", "file": "MinGit-2.54.0-64-bit.zip", "size": 39989839},
    "cmake": {"url": "https://github.com/Kitware/CMake/releases/download/v4.4.2/cmake-4.4.2-windows-x86_64.zip", "file": "cmake-4.4.2-windows-x86_64.zip", "size": 54405968},
    "msvc": {"url": "https://download.visualstudio.microsoft.com/download/pr/00d9d26c-2727-42c2-aa9e-eda63b03e1ee/15df9d3b4c2b2eaf44704d5e938c895341b9cd8ba40a9a18610f8d18cbe01b53/vs_BuildTools.exe", "file": "vs_BuildTools.exe", "size": 4458736},
    "vulkan": {"url": f"https://sdk.lunarg.com/sdk/download/{VULKAN_VERSION}/windows/vulkansdk-windows-X64-{VULKAN_VERSION}.exe", "file": f"vulkansdk-windows-X64-{VULKAN_VERSION}.exe", "size": 0},
}
BRAIN = {"id": BRAIN_MODEL, "label": "GEMMA 4 E2B", "model": "gemma", "family": "gemma4"}
CHATTERBOX_LIBRARY = CHATTERBOX / "build" / "Release" / "tts-cpp.lib"
TTS_SERVER = SERVER / "build" / "Release" / "tts-server.exe"
LOCK = threading.RLock()
PROCESSES: dict[str, subprocess.Popen] = {}
ENGINE_MODELS = {"tts": ("chatterbox-t3", "chatterbox-codec"), "asr": ("parakeet",), "brain": (BRAIN["model"],)}
LOG = ROOT / "trident.log"
def log(component: str, event: str, **data: Any):
    line = component + " " + event
    if data:
        line += " " + " ".join(f"{k}={data[k]}" for k in data)
    print(line, flush=True)
    with LOCK:
        with LOG.open("a", encoding="utf-8") as out:
            out.write(line + "\n")
RUNTIME = {
    "jobs": {},
    "engines": {name: {"status": "stopped", "error": "", "pid": None, "applied": {}} for name in ENGINE_MODELS},
    "results": {"asr": None, "brain": None},
}
class ApiError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
def client_gone(exception: BaseException) -> bool:
    if isinstance(exception, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    return getattr(exception, "winerror", None) in (10053, 10054)
def present(path: Path, size: int = 0) -> bool:
    return path.is_file() and (not size or path.stat().st_size == size)
def executable(name: str) -> str | None:
    local = {"git": TOOLS / "git" / "cmd" / "git.exe", "cmake": TOOLS / "cmake-4.4.2-windows-x86_64" / "bin" / "cmake.exe"}.get(name)
    return str(local) if local and local.is_file() else shutil.which(name)
def msvc_path() -> Path | None:
    root = Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio"
    matches = sorted(root.glob("*/BuildTools/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe"), reverse=True)
    return matches[0] if matches else None
def vulkan_path() -> Path | None:
    roots = [Path(os.environ["VULKAN_SDK"])] if os.environ.get("VULKAN_SDK") else []
    roots += [TOOLS / "VulkanSDK" / VULKAN_VERSION]
    roots += sorted(Path("C:/VulkanSDK").glob("*"), reverse=True)
    return next((path for path in roots if (path / "Include/vulkan/vulkan.h").is_file() and (path / "Lib/vulkan-1.lib").is_file()), None)
def prerequisites() -> dict:
    paths = {"python": Path(sys.executable), "git": executable("git"), "cmake": executable("cmake"), "msvc": msvc_path(), "vulkan": vulkan_path()}
    return {name: {"status": "ready" if path else "missing", "path": str(path or "")} for name, path in paths.items()}
def model_path(name: str) -> Path:
    spec = MODELS[name]
    root = DATA if spec.get("directory") == "data" else MODELS_DIR
    return root / spec["file"]
def model_status(name: str) -> dict:
    spec = MODELS[name]
    path = model_path(name)
    size = path.stat().st_size if path.is_file() else 0
    return {"status": "ready" if size else "missing", "path": str(path), "bytes": size, "size": spec["size"], "revision": spec.get("revision", "")}
def component_artifact(name: str) -> Path:
    spec = {"tts": {"exe": "tts-server.exe"}, **BINARIES}[name]
    root = RUNTIMES / name
    matches = [path for path in root.rglob("*") if path.is_file() and path.name.lower() == spec["exe"].lower()] if root.is_dir() else []
    return matches[0] if len(matches) == 1 else root / spec["exe"]
def component_status(name: str) -> dict:
    path = component_artifact(name)
    revision = SOURCES["chatterbox"][1] if name == "tts" else BINARIES[name]["tag"]
    return {"status": "ready" if path.is_file() else "missing", "path": str(path), "revision": revision}
def reference_path() -> Path:
    custom = DATA / "reference.wav"
    if custom.is_file():
        return custom
    default = model_path("reference")
    if default.is_file():
        return default
    raise ApiError(409, "default reference is missing; download DEFAULT VOICE")
def reference_state() -> dict:
    custom = DATA / "reference.wav"
    path = custom if custom.is_file() else model_path("reference")
    if not path.is_file():
        return {"status": "missing", "path": str(path), "duration": 0.0, "custom": False}
    try:
        with wave.open(str(path), "rb") as audio:
            valid = audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getcomptype() == "NONE"
            duration = audio.getnframes() / float(audio.getframerate() or 1)
        if not valid or duration < 5:
            return {"status": "invalid", "path": str(path), "duration": duration, "custom": path == custom}
    except (wave.Error, OSError):
        return {"status": "invalid", "path": str(path), "duration": 0.0, "custom": path == custom}
    return {"status": "ready", "path": str(path), "duration": duration, "custom": path == custom}
def snapshot() -> dict:
    with LOCK:
        engines = deepcopy(RUNTIME["engines"])
        for name, process in PROCESSES.items():
            engines[name]["pid"] = process.pid
        return {
            "prerequisites": prerequisites(),
            "components": {name: component_status(name) for name in ("tts", "parakeet", "gemma")},
            "models": {name: model_status(name) for name in MODELS},
            "engines": engines,
            "reference": reference_state(),
            "brain": dict(BRAIN),
            "jobs": deepcopy(RUNTIME["jobs"]),
        }
def set_job(key: str, status: str, stage: str, progress: int, message: str, failure: str = ""):
    with LOCK:
        RUNTIME["jobs"][key] = {"status": status, "stage": stage, "progress": progress, "message": message, "error": failure}
def start_job(kind: str, name: str, work: Callable[[str], None]):
    key = f"{kind}:{name}"
    with LOCK:
        if RUNTIME["jobs"].get(key, {}).get("status") == "running":
            raise ApiError(409, f"{key} is already running")
    set_job(key, "running", "start", 0, f"starting {name}")
    def worker():
        try:
            work(key)
            set_job(key, "done", "done", 100, f"{name} complete")
        except Exception as exception:
            log("job", "failed", key=key, error=str(exception))
            set_job(key, "error", "error", 0, str(exception), str(exception))
    threading.Thread(target=worker, daemon=True).start()
    return key
def build_env() -> dict:
    env = os.environ.copy()
    sdk = vulkan_path()
    if not sdk:
        raise RuntimeError("Vulkan SDK is missing")
    env["VULKAN_SDK"] = str(sdk)
    paths = [str(sdk / "Bin")]
    for name in ("git", "cmake"):
        path = executable(name)
        if not path:
            raise RuntimeError(f"{name} is missing")
        paths.append(str(Path(path).parent))
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    return env
def run(component: str, stage: str, command: list[str], cwd: Path, env: dict | None = None):
    log(component, stage, cmd=" ".join(command))
    process = subprocess.Popen(command, cwd=cwd, env=env or build_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    tail = ""
    for raw in process.stdout or []:
        tail = raw.rstrip()
        if tail:
            print(tail, flush=True)
    code = process.wait()
    if code:
        raise RuntimeError(f"{component} {stage} exited {code}: {tail}")
def checkout(component: str, path: Path, source: str):
    url, revision = SOURCES[source]
    git = executable("git")
    if not git:
        raise RuntimeError("git is missing")
    if path.exists() and not (path / ".git").is_dir():
        raise RuntimeError(f"non-git path blocks checkout: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        run(component, f"clone-{source}", [git, "clone", "--filter=blob:none", "--no-checkout", url, str(path)], path.parent)
    run(component, f"fetch-{source}", [git, "fetch", "--depth", "1", "origin", revision], path)
    run(component, f"checkout-{source}", [git, "checkout", "--detach", revision], path)
    run(component, f"reset-{source}", [git, "reset", "--hard", revision], path)
    run(component, f"clean-{source}", [git, "clean", "-fdx"], path)
def apply_chatterbox_patches(cwd: Path):
    git = executable("git")
    if not git:
        raise RuntimeError("git is missing")
    names = [path.name for path in sorted(PATCHES.glob("chatterbox-*.patch"))]
    if not names:
        raise RuntimeError("Chatterbox patch set is missing")
    for name in names:
        run("tts", f"patch-{name}", [git, "apply", "--unidiff-zero", str(PATCHES / name)], cwd)

def require_build_tools():
    missing = [name for name, value in prerequisites().items() if name in ("git", "cmake", "msvc", "vulkan") and value["status"] != "ready"]
    if missing:
        raise RuntimeError("missing TTS build prerequisites: " + ", ".join(missing))
def github_release_asset(spec: dict) -> tuple[str, int]:
    repo = urllib.parse.quote(spec["repo"], safe="/")
    tag = urllib.parse.quote(spec["tag"], safe="")
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "trident/1", "X-GitHub-Api-Version": "2026-03-10"})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == spec["asset"]]
    if len(matches) != 1:
        raise RuntimeError(f"GitHub release asset not found exactly once: {spec['asset']}")
    asset = matches[0]
    size = int(asset.get("size") or 0)
    download = str(asset.get("browser_download_url") or "")
    if size <= 0 or not download.startswith("https://github.com/"):
        raise RuntimeError(f"invalid GitHub release metadata for {spec['asset']}")
    return download, size
def extract_release_bundle(archive: Path, destination: Path, executable_name: str):
    partial = destination.with_name(destination.name + ".part")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    root = partial.resolve()
    try:
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                target = (partial / member.filename).resolve()
                if target != root and root not in target.parents:
                    raise RuntimeError(f"unsafe ZIP member: {member.filename}")
            package.extractall(partial)
        matches = [path for path in partial.rglob("*") if path.is_file() and path.name.lower() == executable_name.lower()]
        if len(matches) != 1:
            raise RuntimeError(f"release bundle must contain exactly one {executable_name}; found {len(matches)}")
        if destination.exists():
            shutil.rmtree(destination)
        partial.rename(destination)
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise
def install_release_binary(name: str, key: str):
    spec = BINARIES[name]
    set_job(key, "running", "metadata", 5, f"checking pinned {spec['tag']} release")
    url, size = github_release_asset(spec)
    archive = TOOLS / "downloads" / spec["asset"]
    fetch(url, archive, size, key)
    set_job(key, "running", "extract", 92, f"extracting {name} Vulkan bundle")
    extract_release_bundle(archive, RUNTIMES / name, spec["exe"])
    archive.unlink(missing_ok=True)
    if not component_artifact(name).is_file():
        raise RuntimeError(f"release did not create {spec['exe']}")
def rmtree_retry(path: Path, attempts: int = 10):
    last = None
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
def terminate_executable(path: Path):
    if os.name != "nt":
        return
    target = os.path.normcase(str(path.resolve()) if path.exists() else str(path))
    escaped = target.replace("'", "''")
    script = (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        f"$target = [IO.Path]::GetFullPath('{escaped}'); "
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -eq $target) "
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
        "$deadline = (Get-Date).AddSeconds(8); "
        "while ((Get-Date) -lt $deadline) { "
        "if (-not (Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -eq $target) })) { break }; "
        "Start-Sleep -Milliseconds 200 "
        "}"
    )
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
def install_component(name: str, key: str):
    if name in BINARIES:
        install_release_binary(name, key)
        return
    if name != "tts":
        raise RuntimeError(f"unknown component: {name}")
    require_build_tools()
    cmake = executable("cmake")
    if not cmake:
        raise RuntimeError("cmake is missing")
    set_job(key, "running", "source", 5, "checking out Chatterbox")
    checkout(name, CHATTERBOX, "chatterbox")
    apply_chatterbox_patches(CHATTERBOX)
    set_job(key, "running", "ggml", 18, "checking out ggml")
    checkout(name, GGML, "ggml")
    set_job(key, "running", "configure", 30, "configuring Chatterbox Vulkan")
    run(name, "configure", [cmake, "-S", ".", "-B", "build", "-A", "x64", "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=OFF"], CHATTERBOX)
    set_job(key, "running", "build", 48, "building Chatterbox")
    run(name, "build", [cmake, "--build", "build", "--config", "Release", "--target", "tts-cpp", "mtl_tokenizer", "--parallel"], CHATTERBOX)
    if not CHATTERBOX_LIBRARY.is_file():
        raise RuntimeError(f"Chatterbox build did not create {CHATTERBOX_LIBRARY}")
    set_job(key, "running", "server-configure", 70, "configuring TTS server")
    run(name, "server-configure", [cmake, "-S", ".", "-B", "build", "-A", "x64", f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}"], SERVER)
    set_job(key, "running", "server-build", 82, "building TTS server")
    run(name, "server-build", [cmake, "--build", "build", "--config", "Release", "--parallel"], SERVER)
    if not TTS_SERVER.is_file():
        raise RuntimeError(f"TTS build did not create {TTS_SERVER}")
    stop_engine("tts")
    terminate_executable(RUNTIMES / "tts" / TTS_SERVER.name)
    set_job(key, "running", "install", 90, "installing TTS runtime")
    runtime = RUNTIMES / "tts"
    partial = runtime.with_name(runtime.name + ".part")
    if partial.exists():
        rmtree_retry(partial)
    partial.mkdir(parents=True)
    try:
        for artifact in TTS_SERVER.parent.iterdir():
            if artifact.is_file() and (artifact.name == TTS_SERVER.name or artifact.suffix.lower() == ".dll"):
                shutil.copy2(artifact, partial / artifact.name)
        if runtime.exists():
            rmtree_retry(runtime)
        partial.rename(runtime)
    except Exception:
        if partial.exists():
            rmtree_retry(partial)
        raise
def fetch(url: str, destination: Path, size: int, key: str):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if present(destination, size):
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
    done = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
        if response.status != 200:
            raise RuntimeError(f"download returned HTTP {response.status}: {url}")
        for block in iter(lambda: response.read(1024 * 1024), b""):
            output.write(block)
            done += len(block)
            set_job(key, "running", "download", done * 90 // size if size else min(89, done // (4 * 1024 * 1024)), f"{done} / {size} bytes" if size else f"{done} bytes")
    if size and done != size:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"download size mismatch: expected {size}, got {done}")
    os.replace(partial, destination)
def install_prerequisite(name: str, key: str):
    if prerequisites()[name]["status"] == "ready":
        return
    if name == "python":
        raise RuntimeError("Python must be installed before running main.py")
    spec = PACKAGES[name]
    archive = TOOLS / "downloads" / spec["file"]
    fetch(spec["url"], archive, spec["size"], key)
    set_job(key, "running", "install", 92, f"installing {name}")
    if name == "git":
        destination = TOOLS / "git"
        if destination.exists():
            shutil.rmtree(destination)
        with zipfile.ZipFile(archive) as package:
            package.extractall(destination)
    elif name == "cmake":
        destination = TOOLS / "cmake-4.4.2-windows-x86_64"
        if destination.exists():
            shutil.rmtree(destination)
        with zipfile.ZipFile(archive) as package:
            package.extractall(TOOLS)
    elif name == "msvc":
        run(name, "install", [str(archive), "--quiet", "--wait", "--norestart", "--nocache", "--add", "Microsoft.VisualStudio.Workload.VCTools", "--includeRecommended"], ROOT, os.environ.copy())
    else:
        destination = TOOLS / "VulkanSDK" / VULKAN_VERSION
        run(name, "install", [str(archive), "--root", str(destination), "--accept-licenses", "--default-answer", "--confirm-command", "install"], ROOT, os.environ.copy())
    if prerequisites()[name]["status"] != "ready":
        raise RuntimeError(f"{name} installer completed but prerequisite is still missing")
def download_model(name: str, key: str):
    spec = MODELS[name]
    destination = model_path(name)
    if present(destination, spec["size"]):
        return
    if spec.get("source"):
        source = ROOT / spec["source"]
        if not source.is_file():
            raise RuntimeError(f"bundled asset missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        set_job(key, "running", "copy", 90, f"installed {spec['file']}")
    elif name.startswith("chatterbox"):
        converter_script = CHATTERBOX / "scripts" / ("convert-t3-mtl-to-gguf.py" if name == "chatterbox-t3" else "convert-s3gen-to-gguf.py")
        if not CHATTERBOX_LIBRARY.is_file() or not converter_script.is_file():
            raise RuntimeError("install Chatterbox TTS before converting its models")
        python = CONVERTER / "Scripts" / "python.exe"
        lock = "numpy==1.26.4 torch==2.6.0 gguf==0.19.0 safetensors==0.5.3 scipy==1.15.3 librosa==0.11.0 resampy==0.4.3 huggingface-hub==0.34.4"
        stamp = CONVERTER / ".packages"
        if not python.is_file():
            set_job(key, "running", "environment", 5, "creating converter environment")
            run(name, "venv", [sys.executable, "-m", "venv", str(CONVERTER)], ROOT, os.environ.copy())
        if not stamp.is_file() or stamp.read_text(encoding="ascii") != lock:
            run(name, "torch", [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], ROOT, os.environ.copy())
            run(name, "converter-dependencies", [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *lock.split()], ROOT, os.environ.copy())
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(lock, encoding="ascii")
        checkpoint = CONVERTER / "checkpoints" / spec["revision"]
        marker = checkpoint / ".revision"
        required = ("ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen_v3.pt", "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json")
        recipe = "trident-v3-checkpoints:" + spec["revision"] + ":" + ",".join(required)
        cache_ok = marker.is_file() and marker.read_text(encoding="ascii") == recipe and all((checkpoint / item).is_file() for item in required)
        if not cache_ok:
            if checkpoint.exists():
                shutil.rmtree(checkpoint)
            checkpoint.mkdir(parents=True, exist_ok=True)
            set_job(key, "running", "checkpoint", 12, "downloading pinned Chatterbox V3 checkpoint")
            code = (
                "from huggingface_hub import snapshot_download; "
                f"snapshot_download(repo_id={spec['repo']!r}, revision={spec['revision']!r}, "
                f"allow_patterns={list(required)!r}, local_dir={str(checkpoint)!r})"
            )
            env = os.environ.copy()
            env["HF_HOME"] = str(TOOLS / "huggingface")
            run(name, "checkpoint", [str(python), "-c", code], ROOT, env)
            missing = [item for item in required if not (checkpoint / item).is_file()]
            if missing:
                raise RuntimeError(f"checkpoint download incomplete: {missing}")
            marker.write_text(recipe, encoding="ascii")
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        script = "convert-t3-mtl-to-gguf.py" if name == "chatterbox-t3" else "convert-s3gen-to-gguf.py"
        command = [str(python), str(CHATTERBOX / "scripts" / script)]
        if name != "chatterbox-t3":
            command += ["--variant", "mtl"]
        command += ["--ckpt-dir", str(checkpoint), "--out", str(partial), "--quant", "q4_0" if name == "chatterbox-t3" else "f16"]
        env = build_env()
        env["HF_HOME"] = str(TOOLS / "huggingface")
        set_job(key, "running", "convert", 20, f"converting {spec['label']}")
        run(name, "convert", command, ROOT, env)
        if not present(partial, spec["size"]):
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"converted model missing or truncated: {partial}")
        os.replace(partial, destination)
    else:
        url = spec.get("url") or f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{spec['file']}"
        fetch(url, destination, spec["size"], key)

def log_process(name: str, process: subprocess.Popen):
    message = ""
    for raw in process.stdout or []:
        message = raw.rstrip()
        if message:
            print(f"{name}: {message}", flush=True)
        with LOCK:
            if PROCESSES.get(name) is process:
                RUNTIME["engines"][name]["message"] = message
    code = process.wait()
    with LOCK:
        if PROCESSES.get(name) is process:
            PROCESSES.pop(name)
            RUNTIME["engines"][name].update(status="error", error=f"process exited {code}: {message}", pid=None)
            log(name, "exited", code=code, last=message)
def remote(url: str, body: bytes | None = None, content_type: str = "application/json", timeout: int = 600) -> bytes:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type} if body is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exception:
        detail = exception.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exception.code} from {url}: {detail}") from exception
def wait_ready(name: str, process: subprocess.Popen, url: str):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited {process.returncode} during load")
        try:
            remote(url, timeout=1)
            return
        except (urllib.error.URLError, TimeoutError, RuntimeError):
            time.sleep(.25)
    raise RuntimeError(f"{name} did not become ready within 600 seconds")
def stop_engine(name: str):
    with LOCK:
        process = PROCESSES.pop(name, None)
        RUNTIME["engines"][name].update(status="stopping", error="")
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
    with LOCK:
        RUNTIME["engines"][name].update(status="stopped", error="", pid=None, applied={})
    if process:
        log("engine", "stopped", name=name, pid=process.pid)
def load_engine(name: str, key: str):
    stop_engine(name)
    if name == "brain":
        if model_status(BRAIN["model"])["status"] != "ready":
            raise RuntimeError(f"brain model is missing: {model_path(BRAIN['model'])}")
        paths = [model_path(BRAIN["model"])]
    else:
        paths = [model_path(model) for model in ENGINE_MODELS[name]]
        for model, model_file in zip(ENGINE_MODELS[name], paths):
            if model_status(model)["status"] != "ready":
                raise RuntimeError(f"model is missing: {model_file}")
    executable_path = component_artifact("tts" if name == "tts" else "parakeet" if name == "asr" else "gemma")
    if not executable_path.is_file():
        raise RuntimeError(f"component is missing: {executable_path}")
    if name == "tts":
        runtime = dict(TTS_RUNTIME)
        applied = {"runtime": runtime}
        command = [str(executable_path), "--port", str(PORTS["tts"]), "--model", str(paths[0]), "--s3gen-gguf", str(paths[1]), "--n-gpu-layers", str(runtime["gpu_layers"]), "--context", str(runtime["context"]), "--threads", str(runtime["threads"])]
        cwd, health, env = executable_path.parent, f"http://127.0.0.1:{PORTS['tts']}/health", os.environ.copy()
    elif name == "asr":
        applied = dict(ASR_RUNTIME)
        command = [str(executable_path), "--model", str(paths[0]), "--host", "127.0.0.1", "--port", str(PORTS["asr"]), "--threads", str(applied["threads"])]
        cwd, health, env = executable_path.parent, f"http://127.0.0.1:{PORTS['asr']}/health", os.environ.copy()
        env["PARAKEET_DEVICE"] = str(applied["device"])
    else:
        runtime = dict(BRAIN_RUNTIME)
        applied = {**runtime, "id": BRAIN["id"], "family": BRAIN["family"], "path": str(paths[0])}
        command = [str(executable_path), "-m", str(paths[0]), "--host", "127.0.0.1", "--port", str(PORTS["brain"]), "--device", str(runtime["device"]), "--n-gpu-layers", str(runtime["gpu_layers"]), "--ctx-size", str(runtime["context"]), "--parallel", str(runtime["parallel"]), "--no-mmproj", "--load-mode", "auto", "--flash-attn", str(runtime["flash_attn"]), "--repack", "--fit", str(runtime["fit"]), "--fit-target", str(runtime["fit_target"]), "--fit-ctx", str(runtime["fit_ctx"])]
        cwd, health, env = executable_path.parent, f"http://127.0.0.1:{PORTS['brain']}/health", os.environ.copy()
    set_job(key, "running", "load", 20, f"loading {name}")
    log("engine", "launch", name=name, cmd=" ".join(command))
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    with LOCK:
        PROCESSES[name] = process
        RUNTIME["engines"][name].update(status="loading", error="", pid=process.pid, applied=applied)
    threading.Thread(target=log_process, args=(name, process), daemon=True).start()
    wait_ready(name, process, health)
    with LOCK:
        RUNTIME["engines"][name]["status"] = "running"
    log("engine", "ready", name=name, pid=process.pid)
    set_job(key, "running", "ready", 95, f"{name} ready")
def multipart(audio: bytes, response_format: str | None = None) -> tuple[bytes, str]:
    boundary = "trident-" + uuid.uuid4().hex
    fmt = (response_format or str(ASR_RUNTIME["response_format"])).encode()
    fields = [("file", "speech.wav", "audio/wav", audio), ("response_format", "", "text/plain", fmt)]
    body = bytearray()
    for name, filename, kind, value in fields:
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"".encode())
        if filename:
            body.extend(f"; filename=\"{filename}\"".encode())
        body.extend(f"\r\nContent-Type: {kind}\r\n\r\n".encode())
        body.extend(value)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"
def require_engine(name: str):
    if name not in ENGINE_MODELS:
        raise ApiError(400, f"unknown engine: {name}")
    if RUNTIME["engines"][name]["status"] != "running":
        raise ApiError(409, f"{name} is not running")
def wav_meta(data: bytes) -> tuple[int, int, int, bytes]:
    with wave.open(io.BytesIO(data), "rb") as audio:
        return audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.readframes(audio.getnframes())
def pcm_wav(rate: int, pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm)
    return out.getvalue()
def lcs_join(left: list[str], right: list[str]) -> list[str]:
    if not left:
        return right
    cap = min(len(left), len(right), 48)
    best = 0
    for size in range(1, cap + 1):
        if [token.casefold() for token in left[-size:]] == [token.casefold() for token in right[:size]]:
            best = size
    return left + right[best:]
def asr_words(result: dict) -> list[dict]:
    words = result.get("words")
    if isinstance(words, list) and words:
        return words
    tokens = result.get("tokens")
    if isinstance(tokens, list):
        return [{"w": str(item.get("t") or item.get("token") or ""), "start": float(item.get("start") or 0), "end": float(item.get("end") or 0)} for item in tokens]
    return [{"w": token, "start": 0.0, "end": 0.0} for token in str(result.get("text") or "").split()]
def stitch_asr(parts: list[dict], window: float, overlap: float) -> dict:
    if not parts:
        return {"text": ""}
    if len(parts) == 1:
        return parts[0]
    kept: list[str] = []
    for index, part in enumerate(parts):
        words = asr_words(part)
        timed = any(float(word.get("end") or 0) > 0 for word in words)
        piece = []
        for word in words:
            token = str(word.get("w") or word.get("word") or "").strip()
            start = float(word.get("start") or 0)
            if not token or (index and timed and start < overlap):
                continue
            piece.append(token)
        if not piece:
            piece = str(part.get("text") or "").split()
        kept = piece if index == 0 else lcs_join(kept, piece)
    return {"text": " ".join(kept)}
def transcribe(audio: bytes) -> dict:
    require_engine("asr")
    started = time.monotonic()
    try:
        rate, channels, width, pcm = wav_meta(audio)
        seconds = len(pcm) / float(rate * width * max(channels, 1))
    except (wave.Error, EOFError):
        rate, pcm, seconds = 16000, b"", 0.0
    window = float(ASR_CHUNK["seconds"])
    overlap = float(ASR_CHUNK["overlap"])
    if seconds <= window or not pcm:
        body, content_type = multipart(audio)
        result = json.loads(remote(f"http://127.0.0.1:{PORTS['asr']}/v1/audio/transcriptions", body, content_type))
    else:
        step = max(window - overlap, 1.0)
        frame = width * max(channels, 1)
        parts, cursor = [], 0.0
        while cursor < seconds:
            start = int(cursor * rate) * frame
            stop = int(min(seconds, cursor + window) * rate) * frame
            body, content_type = multipart(pcm_wav(rate, pcm[start:stop]), "verbose_json")
            parts.append(json.loads(remote(f"http://127.0.0.1:{PORTS['asr']}/v1/audio/transcriptions", body, content_type)))
            if cursor + window >= seconds:
                break
            cursor += step
        result = stitch_asr(parts, window, overlap)
    with LOCK:
        RUNTIME["results"]["asr"] = result
    text = str(result.get("text") or "")
    log("asr", "done", ms=round((time.monotonic() - started) * 1000, 1), text=text)
    return result
def brain(prompt: str, language: str) -> dict:
    require_engine("brain")
    if language not in TTS_LANGUAGES:
        raise ApiError(400, f"unsupported reply language: {language}")
    language_name = TTS_LANGUAGES[language]
    system = BRAIN_SYSTEM.format(language_name=language_name, language=language)
    request = {
        "model": BRAIN["id"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        **BRAIN_GENERATION,
        "stream": False,
        **BRAIN_FAMILY,
    }
    started = time.monotonic()
    result = json.loads(remote(f"http://127.0.0.1:{PORTS['brain']}/v1/chat/completions", json.dumps(request, separators=(",", ":")).encode()))
    with LOCK:
        RUNTIME["results"]["brain"] = result
    text = brain_reply_text(result)
    log("brain", "done", ms=round((time.monotonic() - started) * 1000, 1), lang=language, text=text)
    return result
def validate_wav(data: bytes):
    partial = DATA / "reference.wav.part"
    DATA.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(data)
    try:
        with wave.open(str(partial), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getcomptype() != "NONE" or audio.getnframes() / audio.getframerate() < 5:
                raise ApiError(400, "reference must be mono PCM16 WAV at least 5 seconds long")
    except (EOFError, wave.Error) as exception:
        partial.unlink(missing_ok=True)
        raise ApiError(400, f"invalid WAV: {str(exception) or 'truncated header'}") from exception
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, DATA / "reference.wav")
    log("reference", "updated", bytes=len(data))
def synthesize(text: str, language: str) -> dict:
    text = text.strip()
    if not text:
        raise ApiError(400, "text is required")
    if language not in TTS_LANGUAGES:
        raise ApiError(400, f"unsupported speech language: {language}")
    require_engine("tts")
    ref = reference_path()
    payload = {
        "text": text, "language": language, "reference": str(ref),
        "reference_mtime": ref.stat().st_mtime, "chunk_chars": TTS_CHUNK["chars"],
        **TTS_SAMPLE, **TTS_VOICE,
    }
    started = time.monotonic()
    result = json.loads(remote(f"http://127.0.0.1:{PORTS['tts']}/tts", json.dumps(payload, separators=(",", ":")).encode()))
    if result.get("error"):
        raise RuntimeError(result["error"])
    log("tts", "done", ms=round((time.monotonic() - started) * 1000, 1), lang=language, seconds=result.get("seconds"), t3_ms=result.get("t3_ms"), s3gen_ms=result.get("s3gen_ms"), chunks=result.get("chunks"), cfm_steps=TTS_SAMPLE["cfm_steps"])
    return result
def cancel_tts() -> dict:
    require_engine("tts")
    remote(f"http://127.0.0.1:{PORTS['tts']}/cancel", b"{}", timeout=5)
    return {"ok": True}
def brain_reply_text(result: dict | None) -> str:
    if not result:
        return ""
    message = ((result.get("choices") or [{}])[0].get("message") or {})
    return str(message.get("content") or "").strip()
def wav_body(raw: bytes | None) -> bytes:
    if not raw:
        raise ApiError(400, "WAV body is required")
    return raw
REQUIRED_MODELS = ["chatterbox-t3", "chatterbox-codec", "parakeet", BRAIN["model"], "reference"]
OPS = {
    "inspect": {}, "schema": {}, "state": {},
    "install_prerequisite": {}, "install_component": {}, "download_model": {},
    "load_engine": {}, "unload_engine": {}, "upload_reference": {},
    "asr": {}, "brain": {}, "tts": {}, "tts_cancel": {},
}
SCHEMA = {
    "version": 7,
    "languages": {"reply": TTS_LANGUAGES, "asr": ASR_LANGUAGES, "default_reply": DEFAULT_REPLY_LANGUAGE},
    "mic": MIC,
    "brain": {**BRAIN, "generation": BRAIN_GENERATION, "runtime": BRAIN_RUNTIME},
    "prerequisites": {name: {"label": label} for name, label in {"python": "PYTHON 3.11+", "git": "GIT", "cmake": "CMAKE", "msvc": "MSVC BUILD TOOLS", "vulkan": "VULKAN SDK"}.items()},
    "components": {"tts": {"label": "CHATTERBOX TTS V3"}, "parakeet": {"label": BINARIES["parakeet"]["label"]}, "gemma": {"label": BINARIES["gemma"]["label"]}},
    "required_models": REQUIRED_MODELS,
    "models": {name: {"label": MODELS[name]["label"], "size": MODELS[name]["size"]} for name in REQUIRED_MODELS},
}
def inspect() -> dict:
    return {"ok": True, "version": 7, "schema": SCHEMA, "state": snapshot()}
def dispatch(op: str, payload: dict | None = None, raw: bytes | None = None) -> tuple[dict, int]:
    payload = payload or {}
    if op not in OPS:
        raise ApiError(400, f"unknown op: {op}")
    if op == "inspect":
        return inspect(), 200
    if op == "schema":
        return SCHEMA, 200
    if op == "state":
        return snapshot(), 200
    if op == "install_prerequisite":
        name = str(payload.get("name") or "")
        if name not in SCHEMA["prerequisites"]:
            raise ApiError(404, f"unknown prerequisite: {name}")
        return {"ok": True, "accepted": True, "job_id": start_job("prerequisite", name, lambda key: install_prerequisite(name, key))}, 202
    if op == "install_component":
        name = str(payload.get("name") or "")
        if name not in ("tts", *BINARIES):
            raise ApiError(404, f"unknown component: {name}")
        return {"ok": True, "accepted": True, "job_id": start_job("component", name, lambda key: install_component(name, key))}, 202
    if op == "download_model":
        name = str(payload.get("name") or "")
        if name not in MODELS:
            raise ApiError(404, f"unknown model: {name}")
        return {"ok": True, "accepted": True, "job_id": start_job("model", name, lambda key: download_model(name, key))}, 202
    if op in ("load_engine", "unload_engine"):
        name = str(payload.get("name") or "")
        if name not in ENGINE_MODELS:
            raise ApiError(404, f"unknown engine: {name}")
        work = (lambda key: load_engine(name, key)) if op == "load_engine" else (lambda key: stop_engine(name))
        return {"ok": True, "accepted": True, "job_id": start_job("engine", name, work)}, 202
    if op == "upload_reference":
        validate_wav(wav_body(raw))
        return {"ok": True, "reference": reference_state()}, 200
    if op == "asr":
        return {"ok": True, "result": transcribe(wav_body(raw))}, 200
    if op == "brain":
        prompt = str(payload.get("prompt") or "").strip()
        language = str(payload.get("language") or DEFAULT_REPLY_LANGUAGE)
        if not prompt:
            raise ApiError(400, "prompt is required")
        result = brain(prompt, language)
        return {"ok": True, "result": result, "text": brain_reply_text(result)}, 200
    if op == "tts":
        return {"ok": True, **synthesize(str(payload.get("text") or ""), str(payload.get("language") or DEFAULT_REPLY_LANGUAGE))}, 200
    if op == "tts_cancel":
        return cancel_tts(), 200
    raise ApiError(400, f"unhandled op: {op}")
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def body(self, limit: int = 50 * 1024 * 1024) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > limit:
            raise ApiError(400, f"body length must be between 1 and {limit}")
        return self.rfile.read(length)
    def request_json(self, optional: bool = False) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if optional and not length:
            return {}
        try:
            value = json.loads(self.body(1024 * 1024))
        except (json.JSONDecodeError, UnicodeDecodeError) as exception:
            raise ApiError(400, f"invalid JSON: {exception}") from exception
        if type(value) is not dict:
            raise ApiError(400, "JSON body must be an object")
        return value
    def send_bytes(self, data: bytes, content_type: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
    def send_json(self, value: Any, code: int = 200):
        self.send_bytes(json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii"), "application/json", code)
    def do_GET(self):
        op = ""
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
            files = {
                "/": (ROOT / "panel.html", "text/html; charset=utf-8"),
                "/panel.html": (ROOT / "panel.html", "text/html; charset=utf-8"),
                "/panel.css": (ROOT / "panel.css", "text/css; charset=utf-8"),
                "/panel.js": (ROOT / "panel.js", "text/javascript; charset=utf-8"),
                "/audio-processor.js": (ROOT / "audio-processor.js", "text/javascript; charset=utf-8"),
            }
            if path == "/last-output.wav":
                target = DATA / "last-output.wav"
                if not target.is_file():
                    raise ApiError(404, "no speech yet")
                self.send_bytes(target.read_bytes(), "audio/wav")
                return
            if path in files:
                target, content_type = files[path]
                self.send_bytes(target.read_bytes(), content_type)
                return
            if path != "/api":
                raise ApiError(404, f"unknown endpoint: {path}")
            op = query.get("op") or "inspect"
            if op not in ("inspect", "schema", "state"):
                raise ApiError(404, "GET /api accepts op=inspect, schema, or state")
            response_body, code = dispatch(op, query)
            self.send_json(response_body, code)
        except ApiError as exception:
            self.send_json({"error": str(exception)}, exception.code)
        except Exception as exception:
            if client_gone(exception):
                return
            log("api", "fail", path=self.path, error=str(exception))
            self.send_json({"error": str(exception)}, 500)
    def do_POST(self):
        op = ""
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/api":
                raise ApiError(404, f"unknown endpoint: {parsed.path}")
            query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type == "audio/wav":
                op = query.get("op") or ""
                if op not in ("asr", "upload_reference"):
                    raise ApiError(400, "WAV body requires op=asr or op=upload_reference")
                payload, request_body = query, self.body()
            else:
                payload = self.request_json(True)
                op = str(payload.get("op") or query.get("op") or "inspect")
                request_body = None
            response_body, code = dispatch(op, payload, request_body)
            self.send_json(response_body, code)
        except ApiError as exception:
            self.send_json({"error": str(exception)}, exception.code)
        except (KeyError, TypeError, ValueError) as exception:
            self.send_json({"error": f"invalid request: {exception}"}, 400)
        except Exception as exception:
            if not client_gone(exception):
                log("api", "fail", op=op, error=str(exception))
                self.send_json({"error": str(exception)}, 500)
class Server(ThreadingHTTPServer):
    daemon_threads = True
def main() -> int:
    server = Server((CONTROLLER["host"], CONTROLLER["port"]), Handler)
    log("controller", "start", port=CONTROLLER["port"], pid=os.getpid())
    timer = threading.Timer(.4, webbrowser.open, args=(f"http://{CONTROLLER['host']}:{CONTROLLER['port']}/",))
    timer.daemon = True
    timer.start()
    print(f"TRIDENT  http://{CONTROLLER['host']}:{CONTROLLER['port']}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for name in list(PROCESSES):
            stop_engine(name)
        log("controller", "stop")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
