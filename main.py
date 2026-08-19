from __future__ import annotations
import http.client
import io
import json
import os
import queue
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
    ASR_CHUNK, ASR_RUNTIME, BRAIN_GENERATION, BRAIN_MODEL, BRAIN_RUNTIME,
    BRAIN_SYSTEM, BRAIN_THINKING, CONTROLLER, DEFAULT_REPLY_LANGUAGE, MIC, PORTS,
    TTS_CHUNK, TTS_LANGUAGES, TTS_RUNTIME, TTS_SAMPLE, TTS_VOICE,
)
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
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
    "reference": {"label": "DEFAULT VOICE", "source": "assets/default-reference.wav", "file": "default-reference.wav", "directory": "data", "size": 1440078},
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
SUBS: list[queue.Queue] = []
ENGINE_MODELS = {"tts": ("chatterbox-t3", "chatterbox-codec"), "asr": ("parakeet",), "brain": (BRAIN["model"],)}
ENGINE_BIN = {"tts": "tts", "asr": "parakeet", "brain": "gemma"}
COMPONENTS = ("tts", *BINARIES)
PREREQ_LABELS = {"python": "PYTHON 3.11+", "git": "GIT", "cmake": "CMAKE", "msvc": "MSVC BUILD TOOLS", "vulkan": "VULKAN SDK"}
PANEL_FILES = {
    "/": (ROOT / "panel.html", "text/html; charset=utf-8"),
    "/panel.html": (ROOT / "panel.html", "text/html; charset=utf-8"),
    "/panel.js": (ROOT / "panel.js", "text/javascript; charset=utf-8"),
    "/audio-processor.js": (ROOT / "audio-processor.js", "text/javascript; charset=utf-8"),
}
LOG = ROOT / "trident.log"
def log(component: str, event: str, **data: Any):
    line = component + " " + event
    if data:
        line += " " + " ".join(f"{k}={data[k]}" for k in data)
    print(line, flush=True)
    with LOCK:
        with LOG.open("a", encoding="utf-8") as out:
            out.write(line + "\n")
def default_view() -> dict:
    return {
        "stage": "idle",
        "error": "",
        "asr": {"text": "", "status": "idle"},
        "brain": {"text": "", "language": "", "status": "idle"},
        "tts": {"text": "", "language": "", "seconds": 0.0, "mtime": 0.0, "chunk": 0, "chunks": 0, "status": "idle"},
    }

RUNTIME = {
    "jobs": {},
    "engines": {name: {"status": "stopped", "error": "", "pid": None} for name in ENGINE_MODELS},
    "live": default_view(),
}

def publish():
    payload = inspect()
    with LOCK:
        targets = list(SUBS)
    for inbox in targets:
        inbox.put(payload)

def close_subs():
    with LOCK:
        targets = list(SUBS)
        SUBS.clear()
    for inbox in targets:
        inbox.put(None)

def set_view(**fields):
    with LOCK:
        view = RUNTIME["live"]
        for key, value in fields.items():
            if key in ("asr", "brain", "tts") and isinstance(value, dict):
                view[key].update(value)
            else:
                view[key] = value
    publish()

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
def need(name: str) -> str:
    path = executable(name)
    if not path:
        raise RuntimeError(f"{name} is missing")
    return path
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
def reference_file() -> Path:
    custom = DATA / "reference.wav"
    return custom if custom.is_file() else model_path("reference")
def reference_path() -> Path:
    path = reference_file()
    if path.is_file():
        return path
    raise ApiError(409, "default reference is missing; download DEFAULT VOICE")
def reference_state() -> dict:
    path = reference_file()
    custom = path == DATA / "reference.wav"
    if not path.is_file():
        return {"status": "missing", "path": str(path), "duration": 0.0, "custom": False}
    try:
        with wave.open(str(path), "rb") as audio:
            valid = audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getcomptype() == "NONE"
            duration = audio.getnframes() / float(audio.getframerate() or 1)
        if not valid or duration < 5:
            return {"status": "invalid", "path": str(path), "duration": duration, "custom": custom}
    except (wave.Error, OSError):
        return {"status": "invalid", "path": str(path), "duration": 0.0, "custom": custom}
    return {"status": "ready", "path": str(path), "duration": duration, "custom": custom}
def snapshot() -> dict:
    with LOCK:
        engines = deepcopy(RUNTIME["engines"])
        for name, process in PROCESSES.items():
            engines[name]["pid"] = process.pid
        return {
            "prerequisites": prerequisites(),
            "components": {name: component_status(name) for name in COMPONENTS},
            "models": {name: model_status(name) for name in MODELS},
            "engines": engines,
            "reference": reference_state(),
            "brain": dict(BRAIN),
            "jobs": deepcopy(RUNTIME["jobs"]),
            "live": deepcopy(RUNTIME["live"]),
        }
def set_job(key: str, status: str, stage: str, progress: int, message: str, failure: str = ""):
    with LOCK:
        RUNTIME["jobs"][key] = {"status": status, "stage": stage, "progress": progress, "message": message, "error": failure}
    publish()
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
        paths.append(str(Path(need(name)).parent))
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
    git = need("git")
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
    git = need("git")
    names = [path.name for path in sorted(PATCHES.glob("chatterbox-*.patch"))]
    if not names:
        raise RuntimeError("Chatterbox patch set is missing")
    for name in names:
        run("tts", f"patch-{name}", [git, "apply", "--unidiff-zero", str(PATCHES / name)], cwd)
    if os.environ.get("TRIDENT_INSPECT_PATCHES"):
        run("tts", "patch-diff", [git, "--no-pager", "diff", "--stat"], cwd)
        raise SystemExit("TRIDENT_INSPECT_PATCHES: leaving patched tree at " + str(cwd))

def require_build_tools():
    missing = [name for name in ("git", "cmake", "msvc", "vulkan") if prerequisites()[name]["status"] != "ready"]
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
        matches = [path for path in partial.rglob("*") if path.is_file() and path.name.lower() == executable_name.lower()]
        if len(matches) != 1:
            raise RuntimeError(f"release bundle must contain exactly one {executable_name}; found {len(matches)}")
        rmtree_retry(destination)
        partial.rename(destination)
    except Exception:
        rmtree_retry(partial)
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
def _ps(script: str):
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)

def terminate_executable(path: Path):
    if os.name != "nt":
        return
    target = os.path.normcase(str(path.resolve()) if path.exists() else str(path))
    escaped = target.replace("'", "''")
    _ps(
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

def kill_port(port: int):
    if os.name != "nt":
        return
    _ps(
        "$ErrorActionPreference = 'SilentlyContinue'; "
        f"Get-NetTCPConnection -LocalPort {int(port)} -State Listen | "
        "Select-Object -ExpandProperty OwningProcess -Unique | "
        "ForEach-Object { if ($_ -and $_ -ne $PID) { Stop-Process -Id $_ -Force } }"
    )
def install_component(name: str, key: str):
    if name in BINARIES:
        install_release_binary(name, key)
        return
    if name != "tts":
        raise RuntimeError(f"unknown component: {name}")
    require_build_tools()
    cmake = need("cmake")
    set_job(key, "running", "source", 5, "checking out Chatterbox")
    checkout(name, CHATTERBOX, "chatterbox")
    apply_chatterbox_patches(CHATTERBOX)
    set_job(key, "running", "ggml", 18, "checking out ggml")
    checkout(name, GGML, "ggml")
    set_job(key, "running", "configure", 30, "configuring Chatterbox Vulkan")
    run(name, "configure", [cmake, "-S", ".", "-B", "build", "-A", "x64", "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=OFF", "-DTTS_CPP_BUILD_EXECUTABLES=OFF", "-DTTS_CPP_BUILD_TESTS=OFF"], CHATTERBOX)
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
        rmtree_retry(destination)
        with zipfile.ZipFile(archive) as package:
            package.extractall(destination)
    elif name == "cmake":
        destination = TOOLS / "cmake-4.4.2-windows-x86_64"
        rmtree_retry(destination)
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
        command = [str(python), str(converter_script)]
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
    publish()
def remote(url: str, body: bytes | None = None, content_type: str = "application/json", timeout: int = 600) -> bytes:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type} if body is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exception:
        detail = exception.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exception.code} from {url}: {detail}") from exception

def remote_lines(url: str, body: bytes, timeout: int = 600):
    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    try:
        conn.request("POST", parsed.path or "/tts", body, {"Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} from {url}: {response.read().decode('utf-8', 'replace')}")
        buf = b""
        while True:
            piece = response.read(256)
            if not piece:
                break
            buf += piece
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                if raw.strip():
                    yield json.loads(raw)
        if buf.strip():
            yield json.loads(buf)
    finally:
        conn.close()
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
    terminate_executable(component_artifact(ENGINE_BIN[name]))
    with LOCK:
        RUNTIME["engines"][name].update(status="stopped", error="", pid=None)
    publish()
    if process:
        log("engine", "stopped", name=name, pid=process.pid)

def stop_all():
    for name in list(ENGINE_MODELS):
        stop_engine(name)

def die():
    try:
        close_subs()
        stop_all()
    finally:
        os._exit(0)

def already_running() -> bool:
    try:
        remote(f"http://127.0.0.1:{CONTROLLER['port']}/api?op=inspect", timeout=1)
        return True
    except Exception:
        return False
def load_engine(name: str, key: str):
    stop_engine(name)
    paths = []
    for model in ENGINE_MODELS[name]:
        path = model_path(model)
        if model_status(model)["status"] != "ready":
            raise RuntimeError(f"model is missing: {path}")
        paths.append(path)
    executable_path = component_artifact(ENGINE_BIN[name])
    if not executable_path.is_file():
        raise RuntimeError(f"component is missing: {executable_path}")
    env = os.environ.copy()
    if name == "tts":
        command = [str(executable_path), "--port", str(PORTS["tts"]), "--model", str(paths[0]), "--s3gen-gguf", str(paths[1]), "--n-gpu-layers", str(TTS_RUNTIME["gpu_layers"]), "--context", str(TTS_RUNTIME["context"]), "--threads", str(TTS_RUNTIME["threads"])]
    elif name == "asr":
        command = [str(executable_path), "--model", str(paths[0]), "--host", "127.0.0.1", "--port", str(PORTS["asr"]), "--threads", str(ASR_RUNTIME["threads"])]
        env["PARAKEET_DEVICE"] = str(ASR_RUNTIME["device"])
    else:
        command = [str(executable_path), "-m", str(paths[0]), "--host", "127.0.0.1", "--port", str(PORTS["brain"]), "--device", str(BRAIN_RUNTIME["device"]), "--n-gpu-layers", str(BRAIN_RUNTIME["gpu_layers"]), "--ctx-size", str(BRAIN_RUNTIME["context"]), "--parallel", str(BRAIN_RUNTIME["parallel"]), "--no-mmproj", "--load-mode", "auto", "--flash-attn", str(BRAIN_RUNTIME["flash_attn"]), "--repack", "--fit", str(BRAIN_RUNTIME["fit"]), "--fit-target", str(BRAIN_RUNTIME["fit_target"]), "--fit-ctx", str(BRAIN_RUNTIME["fit_ctx"])]
    set_job(key, "running", "load", 20, f"loading {name}")
    log("engine", "launch", name=name, cmd=" ".join(command))
    process = subprocess.Popen(command, cwd=executable_path.parent, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    with LOCK:
        PROCESSES[name] = process
        RUNTIME["engines"][name].update(status="loading", error="", pid=process.pid)
    publish()
    threading.Thread(target=log_process, args=(name, process), daemon=True).start()
    wait_ready(name, process, f"http://127.0.0.1:{PORTS[name]}/health")
    with LOCK:
        RUNTIME["engines"][name]["status"] = "running"
    publish()
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
def stitch_asr(parts: list[dict], overlap: float) -> dict:
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
    set_view(stage="heard", error="", asr={"status": "running"})
    started = time.monotonic()
    try:
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
            result = stitch_asr(parts, overlap)
        text = str(result.get("text") or "")
        set_view(asr={"text": text, "status": "done"})
        log("asr", "done", ms=round((time.monotonic() - started) * 1000, 1), text=text)
        return result
    except Exception as exception:
        set_view(stage="idle", error=str(exception), asr={"status": "error"})
        raise
def brain(prompt: str, language: str) -> dict:
    require_engine("brain")
    if language not in TTS_LANGUAGES:
        raise ApiError(400, f"unsupported reply language: {language}")
    language_name = TTS_LANGUAGES[language]
    set_view(stage="thinking", error="", brain={"status": "running", "language": language})
    system = BRAIN_SYSTEM.format(language_name=language_name, language=language)
    generation = dict(BRAIN_GENERATION)
    thinking = bool(BRAIN_THINKING)
    request = {
        "model": BRAIN["id"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        **generation,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    started = time.monotonic()
    try:
        result = json.loads(remote(f"http://127.0.0.1:{PORTS['brain']}/v1/chat/completions", json.dumps(request, separators=(",", ":")).encode()))
        text = brain_reply_text(result)
        set_view(brain={"text": text, "language": language, "status": "done"})
        log("brain", "done", ms=round((time.monotonic() - started) * 1000, 1), lang=language, text=text)
        return result
    except Exception as exception:
        set_view(stage="idle", error=str(exception), brain={"status": "error"})
        raise
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
    publish()
def synthesize(text: str, language: str) -> dict:
    text = text.strip()
    if not text:
        raise ApiError(400, "text is required")
    if language not in TTS_LANGUAGES:
        raise ApiError(400, f"unsupported speech language: {language}")
    require_engine("tts")
    set_view(stage="speaking", error="", tts={"text": text, "language": language, "status": "running"})
    ref = reference_path()
    payload = {
        "text": text, "language": language, "reference": str(ref),
        "reference_mtime": ref.stat().st_mtime, "chunk_chars": TTS_CHUNK["chars"],
        **TTS_SAMPLE, **TTS_VOICE,
    }
    started = time.monotonic()
    try:
        result: dict = {}
        part = DATA / "last-chunk.wav"
        wav = DATA / "last-output.wav"
        for old in DATA.glob("pack-*.wav"):
            old.unlink(missing_ok=True)
        for line in remote_lines(f"http://127.0.0.1:{PORTS['tts']}/tts", json.dumps(payload, separators=(",", ":")).encode()):
            if line.get("error"):
                raise RuntimeError(line["error"])
            if not line.get("done"):
                set_view(stage="speaking", tts={
                    "text": text, "language": language, "status": "running",
                    "chunk": int(line.get("chunk") or 0),
                    "chunks": int(line.get("chunks") or 1),
                    "seconds": float(line.get("seconds") or 0),
                    "mtime": part.stat().st_mtime if part.is_file() else 0.0,
                })
                continue
            result = line
            set_view(stage="idle", tts={
                "text": text, "language": language, "status": "done",
                "chunk": int(line.get("chunks") or 1) - 1,
                "chunks": int(line.get("chunks") or 1),
                "seconds": float(line.get("seconds") or 0),
                "mtime": wav.stat().st_mtime if wav.is_file() else 0.0,
            })
        if not result:
            raise RuntimeError("tts stream ended without a result")
        log("tts", "done", ms=round((time.monotonic() - started) * 1000, 1), lang=language, seconds=result.get("seconds"), t3_ms=result.get("t3_ms"), s3gen_ms=result.get("s3gen_ms"), chunks=result.get("chunks"), cfm_steps=payload["cfm_steps"])
        return result
    except Exception as exception:
        set_view(stage="idle", error=str(exception), tts={"status": "error"})
        raise
def cancel_tts() -> dict:
    require_engine("tts")
    remote(f"http://127.0.0.1:{PORTS['tts']}/cancel", b"{}", timeout=5)
    set_view(stage="idle", tts={"status": "idle"})
    return {"ok": True}
def brain_reply_text(result: dict | None) -> str:
    if not result:
        return ""
    message = ((result.get("choices") or [{}])[0].get("message") or {})
    return str(message.get("content") or "").strip()
REQUIRED_MODELS = ["chatterbox-t3", "chatterbox-codec", "parakeet", BRAIN["model"], "reference"]
OPS = {
    "inspect", "install_prerequisite", "install_component", "download_model",
    "load_engine", "unload_engine", "upload_reference",
    "asr", "brain", "tts", "tts_cancel", "goodbye",
}

def schema() -> dict:
    return {
        "languages": {"reply": TTS_LANGUAGES, "default_reply": DEFAULT_REPLY_LANGUAGE},
        "mic": dict(MIC),
        "prerequisites": {name: {"label": label} for name, label in PREREQ_LABELS.items()},
        "components": {name: {"label": "CHATTERBOX TTS V3" if name == "tts" else BINARIES[name]["label"]} for name in COMPONENTS},
        "required_models": REQUIRED_MODELS,
    }

def inspect() -> dict:
    return {"ok": True, "schema": schema(), "state": snapshot()}
def accept_job(kind: str, name: str, work: Callable[[str], None]):
    return {"ok": True, "accepted": True, "job_id": start_job(kind, name, work)}, 202
def require_wav(raw: bytes | None) -> bytes:
    if not raw:
        raise ApiError(400, "WAV body is required")
    return raw
def dispatch(op: str, payload: dict | None = None, raw: bytes | None = None) -> tuple[dict, int]:
    payload = payload or {}
    if op not in OPS:
        raise ApiError(400, f"unknown op: {op}")
    if op == "inspect":
        return inspect(), 200
    name = str(payload.get("name") or "")
    if op == "install_prerequisite":
        if name not in PREREQ_LABELS:
            raise ApiError(404, f"unknown prerequisite: {name}")
        return accept_job("prerequisite", name, lambda key: install_prerequisite(name, key))
    if op == "install_component":
        if name not in COMPONENTS:
            raise ApiError(404, f"unknown component: {name}")
        return accept_job("component", name, lambda key: install_component(name, key))
    if op == "download_model":
        if name not in MODELS:
            raise ApiError(404, f"unknown model: {name}")
        return accept_job("model", name, lambda key: download_model(name, key))
    if op in ("load_engine", "unload_engine"):
        if name not in ENGINE_MODELS:
            raise ApiError(404, f"unknown engine: {name}")
        work = (lambda key: load_engine(name, key)) if op == "load_engine" else (lambda key: stop_engine(name))
        return accept_job("engine", name, work)
    if op == "upload_reference":
        validate_wav(require_wav(raw))
        return {"ok": True, "reference": reference_state()}, 200
    if op == "asr":
        return {"ok": True, "result": transcribe(require_wav(raw))}, 200
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
    if op == "goodbye":
        return {"ok": True}, 200
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
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
            if path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                inbox = queue.Queue()
                with LOCK:
                    SUBS.append(inbox)
                inbox.put(inspect())
                try:
                    while True:
                        try:
                            item = inbox.get(timeout=25)
                        except queue.Empty:
                            self.wfile.write(b":\n\n")
                            self.wfile.flush()
                            continue
                        if item is None:
                            break
                        blob = json.dumps(item, separators=(",", ":"), ensure_ascii=True).encode("ascii")
                        self.wfile.write(b"event: update\ndata: " + blob + b"\n\n")
                        self.wfile.flush()
                except Exception as exception:
                    if not client_gone(exception):
                        log("sse", "fail", error=str(exception))
                finally:
                    with LOCK:
                        if inbox in SUBS:
                            SUBS.remove(inbox)
                return
            if path == "/last-output.wav" or path == "/last-chunk.wav":
                name = path.lstrip("/")
                if path == "/last-chunk.wav" and query.get("c", "").isdigit():
                    name = f"pack-{int(query['c'])}.wav"
                target = DATA / name
                if not target.is_file():
                    raise ApiError(404, "no speech yet")
                self.send_bytes(target.read_bytes(), "audio/wav")
                return
            if path in PANEL_FILES:
                target, content_type = PANEL_FILES[path]
                self.send_bytes(target.read_bytes(), content_type)
                return
            if path != "/api":
                raise ApiError(404, f"unknown endpoint: {path}")
            if (query.get("op") or "inspect") != "inspect":
                raise ApiError(404, "GET /api accepts op=inspect")
            self.send_json(inspect())
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
            if op == "goodbye":
                die()
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
    if already_running():
        print(f"TRIDENT already http://{CONTROLLER['host']}:{CONTROLLER['port']}/")
        return 0
    kill_port(CONTROLLER["port"])
    for port in PORTS.values():
        kill_port(port)
    stop_all()
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
        close_subs()
        stop_all()
        log("controller", "stop")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
