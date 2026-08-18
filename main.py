from __future__ import annotations
import hashlib
import io
import math
import json
import os
import queue
import re
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
from log import debug, error, info, ingest as ingest_trace, new_id as new_trace_id, record as trace, run_id as trace_run_id, scope as trace_scope, warn
from cfg import (
    ASR_LANGUAGES, ASR_RUNTIME, BRAIN_FAMILIES, BRAIN_GENERATION, BRAIN_MODEL,
    BRAIN_RUNTIME, BRAIN_SYSTEM, CONTROLLER, DEFAULT_REPLY_LANGUAGE, MIC, PORTS,
    TTS_LANGUAGES, TTS_RUNTIME, TTS_SAMPLE, TTS_SAMPLE_RATE, TTS_STREAM, TTS_VOICE,
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
    "chatterbox-t3": {"label": "CHATTERBOX V3 T3", "repo": "ResembleAI/chatterbox", "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18", "file": "chatterbox-t3-mtl-v3-q4_0.gguf", "size": 344985408, "sha256": "d886fba27183c3000becb096b1a16526fafb67fe7abd541d7901040931524d16"},
    "chatterbox-codec": {"label": "CHATTERBOX V3 S3GEN", "repo": "ResembleAI/chatterbox", "revision": "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18", "file": "chatterbox-s3gen-mtl-v3-f16.gguf", "size": 1056431360, "sha256": "1856e341c4da688adc5f2bbcd94f86a692cb11d3cc111ef9352f9422c78d85a3", "accepted_sha256": ["1856e341c4da688adc5f2bbcd94f86a692cb11d3cc111ef9352f9422c78d85a3", "81f8f1a6164b97f71691f4954773dbf5af64f39efd008c7c24967259e1cbf445"]},
    "parakeet": {"label": "PARAKEET TDT 0.6B V3 Q4_K", "repo": "mudler/parakeet-cpp-gguf", "revision": "bf0af9f425fa01809cadec671b3cb672709d13e9", "file": "tdt-0.6b-v3-q4_k.gguf", "size": 675200864, "sha256": "993d73feb4206dadda865ab25bd64b50c48dc4d013c3bf6126a721f28b1d5ee8"},
    "gemma": {"label": "GEMMA 4 E2B", "repo": "google/gemma-4-E2B-it-qat-q4_0-gguf", "revision": "675cff42a74c774d6cb76f76d8eacb49b48c9b93", "file": "gemma-4-E2B_q4_0-it.gguf", "size": 3349516256, "sha256": "fa401b55b07ee70a54c6dae3903c783a6e65064312529ea57175cb5f8dec6634"},
    "qwen35-0.8b": {"label": "QWEN3.5 0.8B", "repo": "unsloth/Qwen3.5-0.8B-GGUF", "revision": "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0", "file": "Qwen3.5-0.8B-Q4_K_M.gguf", "size": 532517120, "sha256": "bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517"},
    "qwen35-4b": {"label": "QWEN3.5 4B", "repo": "unsloth/Qwen3.5-4B-GGUF", "revision": "e87f176479d0855a907a41277aca2f8ee7a09523", "file": "Qwen3.5-4B-Q4_K_M.gguf", "size": 2740937888, "sha256": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"},
    "reference": {"label": "DEFAULT VOICE", "source": "assets/default-reference.wav", "file": "default-reference.wav", "directory": "data", "size": 1012558, "sha256": "de2579b22226261784d6a944c07b9c1fba7fdd0c7e8c9e90da6bc581c78171a9", "license": "Resemble demo prompt"},
}
VULKAN_VERSION = "1.4.357.0"
PACKAGES = {
    "git": {"url": "https://github.com/git-for-windows/git/releases/download/v2.54.0.windows.1/MinGit-2.54.0-64-bit.zip", "file": "MinGit-2.54.0-64-bit.zip", "size": 39989839, "sha256": "04f937e1f0918b17b9be6f2294cb2bb66e96e1d9832d1c298e2de088a1d0e668"},
    "cmake": {"url": "https://github.com/Kitware/CMake/releases/download/v4.4.2/cmake-4.4.2-windows-x86_64.zip", "file": "cmake-4.4.2-windows-x86_64.zip", "size": 54405968, "sha256": "e8139d85b3813bc38833142ae1940472e9a587e9b5d2718ac1804c60f4e57a64"},
    "msvc": {"url": "https://download.visualstudio.microsoft.com/download/pr/00d9d26c-2727-42c2-aa9e-eda63b03e1ee/15df9d3b4c2b2eaf44704d5e938c895341b9cd8ba40a9a18610f8d18cbe01b53/vs_BuildTools.exe", "file": "vs_BuildTools.exe", "size": 4458736, "sha256": "15df9d3b4c2b2eaf44704d5e938c895341b9cd8ba40a9a18610f8d18cbe01b53"},
    "vulkan": {"url": f"https://sdk.lunarg.com/sdk/download/{VULKAN_VERSION}/windows/vulkansdk-windows-X64-{VULKAN_VERSION}.exe", "file": f"vulkansdk-windows-X64-{VULKAN_VERSION}.exe", "size": 0, "sha256": "81f474711e9042f4cd22b31b2f7a8870db2e428b21586fb43dd80150be97310d"},
}
BUILD_LOG_TOKENS = ("compiler identification", "found vulkan:", "build files have been written")
NATIVE_EVENT_PREFIX = "TRIDENT_EVENT "
BRAINS = {
    "gemma": {"label": "GEMMA 4 E2B", "model": "gemma", "family": "gemma4"},
    "qwen35-0.8b": {"label": "QWEN3.5 0.8B", "model": "qwen35-0.8b", "family": "qwen35"},
    "qwen35-4b": {"label": "QWEN3.5 4B", "model": "qwen35-4b", "family": "qwen35"},
}
if BRAIN_MODEL not in BRAINS:
    raise RuntimeError(f"cfg.BRAIN_MODEL must be one of {list(BRAINS)}")
CHATTERBOX_LIBRARY = CHATTERBOX / "build" / "Release" / "tts-cpp.lib"
TTS_SERVER = SERVER / "build" / "Release" / "tts-server.exe"
RECEIPTS_FILE = DATA / "models.json"
LOCK = threading.RLock()
SUBSCRIBERS: set[queue.Queue] = set()
PROCESSES: dict[str, subprocess.Popen] = {}
PROCESS_TRACES: dict[str, dict[str, str]] = {}
ENGINE_MODELS = {"tts": ("chatterbox-t3", "chatterbox-codec"), "asr": ("parakeet",), "brain": (BRAINS[BRAIN_MODEL]["model"],)}
RUNTIME = {
    "jobs": {},
    "engines": {name: {"status": "stopped", "error": "", "pid": None, "applied": {}} for name in ENGINE_MODELS},
    "results": {"asr": None, "brain": None},
    "reference_generation": 0,
}
class ApiError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
def identifier(value: Any, field: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if not text and not required:
        return ""
    if not IDENTIFIER_RE.fullmatch(text):
        raise ApiError(400, f"{field} is not a valid trace identifier")
    return text
def client_gone(exception: BaseException) -> bool:
    if isinstance(exception, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    return getattr(exception, "winerror", None) in (10053, 10054)
def load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return deepcopy(default)
    value = json.loads(path.read_text(encoding="ascii"))
    if type(value) is not dict:
        raise RuntimeError(f"{path} must contain an object")
    return value
def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(value, separators=(",", ":"), ensure_ascii=True), encoding="ascii")
    os.replace(partial, path)
def atomic_bytes(path: Path, value: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_bytes(value)
    os.replace(partial, path)
RECEIPTS = load_json(RECEIPTS_FILE, {})
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
def accepted_hashes(spec: dict) -> set[str]:
    return set(spec.get("accepted_sha256") or [spec["sha256"]])
def verified_file(path: Path, spec: dict) -> tuple[bool, str]:
    if not path.is_file() or path.stat().st_size != spec["size"]:
        return False, ""
    digest = sha256(path)
    return digest in accepted_hashes(spec), digest
def model_status(name: str) -> dict:
    spec = MODELS[name]
    path = model_path(name)
    size = path.stat().st_size if path.is_file() else 0
    receipt = str(RECEIPTS.get(name) or "")
    verified = size == spec["size"] and receipt in accepted_hashes(spec)
    return {"status": "ready" if verified else "unverified" if size == spec["size"] else "missing", "path": str(path), "bytes": size, "size": spec["size"], "sha256": spec["sha256"], "revision": spec.get("revision", "")}
def tts_build_id() -> str:
    digest = hashlib.sha256((SOURCES["chatterbox"][1] + SOURCES["ggml"][1]).encode())
    paths = [*sorted(PATCHES.glob("chatterbox-*.patch")), SERVER / "CMakeLists.txt", *sorted((SERVER / "include").glob("*.hpp")), *sorted((SERVER / "src").glob("*.cpp"))]
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
def component_artifact(name: str) -> Path:
    spec = {"tts": {"exe": "tts-server.exe"}, **BINARIES}[name]
    root = RUNTIMES / name
    matches = [path for path in root.rglob("*") if path.is_file() and path.name.lower() == spec["exe"].lower()] if root.is_dir() else []
    return matches[0] if len(matches) == 1 else root / spec["exe"]
def component_status(name: str) -> dict:
    path = component_artifact(name)
    revision = SOURCES["chatterbox"][1] if name == "tts" else BINARIES[name]["tag"]
    status = "ready" if path.is_file() else "missing"
    if name == "tts" and status == "ready":
        receipt = load_json(path.parent / "build.json", {})
        status = "ready" if receipt.get("build_id") == tts_build_id() else "unverified"
    return {"status": status, "path": str(path), "revision": revision}
def reference_path() -> Path:
    custom = DATA / "reference.wav"
    if custom.is_file():
        return custom
    default = model_path("reference")
    if model_status("reference")["status"] == "ready":
        return default
    if default.is_file():
        raise ApiError(409, "default reference is present but not verified; download DEFAULT VOICE again")
    raise ApiError(409, "default reference is missing; download DEFAULT VOICE")
def bump_reference():
    with LOCK:
        RUNTIME["reference_generation"] = int(RUNTIME.get("reference_generation", 0)) + 1
def reference_state() -> dict:
    custom = DATA / "reference.wav"
    path = custom if custom.is_file() else model_path("reference")
    if not path.is_file():
        return {"status": "missing", "path": str(path), "duration": 0.0, "custom": False}
    if path != custom and model_status("reference")["status"] != "ready":
        return {"status": "unverified", "path": str(path), "duration": 0.0, "custom": False}
    try:
        with wave.open(str(path), "rb") as audio:
            valid = audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getcomptype() == "NONE"
            duration = audio.getnframes() / float(audio.getframerate() or 1)
        if not valid or duration < 5:
            return {"status": "invalid", "path": str(path), "duration": duration, "custom": path == custom}
    except (wave.Error, OSError):
        return {"status": "invalid", "path": str(path), "duration": 0.0, "custom": path == custom}
    return {"status": "ready", "path": str(path), "duration": duration, "custom": path == custom}
def reference_evidence() -> dict:
    state = reference_state()
    if state["status"] != "ready":
        return {**state, "generation": RUNTIME.get("reference_generation", 0), "sha256": "", "bytes": 0}
    path = Path(state["path"])
    audio = path.read_bytes()
    return {
        **state,
        "generation": RUNTIME.get("reference_generation", 0),
        "sha256": hashlib.sha256(audio).hexdigest(),
        "bytes": len(audio),
        "metrics": wav_metrics(audio),
    }
def active_brain_id() -> str:
    return BRAIN_MODEL
def active_brain_family() -> str:
    return BRAINS[BRAIN_MODEL]["family"]
def active_brain_path() -> Path:
    name = BRAINS[BRAIN_MODEL]["model"]
    if model_status(name)["status"] != "ready":
        raise ApiError(409, f"brain model is not verified: {model_path(name)}")
    return model_path(name)
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
            "brain": {"id": BRAIN_MODEL, **BRAINS[BRAIN_MODEL]},
            "jobs": deepcopy(RUNTIME["jobs"]),
        }
def emit(event: str, data: dict):
    with LOCK:
        for subscriber in SUBSCRIBERS:
            subscriber.put((event, data))
def emit_state():
    emit("state", snapshot())
def set_job(key: str, status: str, stage: str, progress: int, message: str, failure: str = "", job_id: str = ""):
    with LOCK:
        previous = RUNTIME["jobs"].get(key, {})
        job_id = job_id or str(previous.get("job_id") or new_trace_id("job"))
        RUNTIME["jobs"][key] = {"status": status, "stage": stage, "progress": progress, "message": message, "error": failure, "job_id": job_id}
        current = deepcopy(RUNTIME["jobs"][key])
    important = status != previous.get("status") or stage != previous.get("stage") or progress == 100 or progress // 10 != int(previous.get("progress") or 0) // 10
    if important:
        (error if status == "error" else info)("job", "job.progress", {"key": key, **current}, job_id=job_id)
    emit("job", {"key": key, **current})
def start_job(kind: str, name: str, work: Callable[[str], None]):
    key = f"{kind}:{name}"
    with LOCK:
        if RUNTIME["jobs"].get(key, {}).get("status") == "running":
            raise ApiError(409, f"{key} is already running")
    job_id = new_trace_id("job")
    set_job(key, "running", "start", 0, f"starting {name}", job_id=job_id)
    def worker():
        with trace_scope(job_id=job_id):
            try:
                work(key)
                set_job(key, "done", "done", 100, f"{name} complete", job_id=job_id)
            except Exception as exception:
                message = str(exception)
                error("job", "job.failed", {"key": key, "error": message}, job_id=job_id)
                set_job(key, "error", "error", 0, message, message, job_id=job_id)
            emit_state()
    threading.Thread(target=worker, daemon=True).start()
    return job_id
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
def line_level(text: str) -> str:
    lower = text.lower()
    if "fatal" in lower or lower.startswith("error") or " error " in f" {lower} " or " e " in lower[:40]:
        return "error"
    if "warning" in lower or "could not find" in lower or lower.startswith("w ") or " w " in lower[:40]:
        return "warn"
    return "info"
def run(component: str, stage: str, command: list[str], cwd: Path, env: dict | None = None):
    started = time.monotonic()
    info(component, "stage", {"stage": stage, "status": "start", "command": command, "cwd": str(cwd)})
    process = subprocess.Popen(command, cwd=cwd, env=env or build_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    tail = failure = ""
    suppressed, warnings = 0, {}
    if not process.stdout:
        raise RuntimeError(f"{component} {stage} has no output pipe")
    for raw in process.stdout:
        tail = raw.rstrip()
        level = line_level(tail)
        if level == "error":
            failure = tail
            error(component, tail, {"stage": stage})
        elif level == "warn":
            match = re.search(r"\b(C\d{4})\b", tail)
            key = match.group(1) if match else tail[:96]
            warnings[key] = warnings.get(key, 0) + 1
            if warnings[key] == 1: warn(component, tail, {"stage": stage})
        elif tail and any(token in tail.lower() for token in BUILD_LOG_TOKENS):
            info(component, tail, {"stage": stage})
        else:
            suppressed += bool(tail)
    code = process.wait()
    data = {"stage": stage, "status": "done" if not code else "failed", "code": code, "seconds": round(time.monotonic() - started, 3), "suppressed": suppressed, "warnings": warnings}
    (info if not code else error)(component, "stage", data)
    if code:
        raise RuntimeError(f"{component} {stage} exited {code}: {failure or tail}")
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
def github_release_asset(spec: dict) -> tuple[str, int, str]:
    repo = urllib.parse.quote(spec["repo"], safe="/")
    tag = urllib.parse.quote(spec["tag"], safe="")
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "trident/1", "X-GitHub-Api-Version": "2026-03-10"})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    if release.get("tag_name") != spec["tag"] or release.get("draft"):
        raise RuntimeError(f"unexpected GitHub release metadata for {spec['repo']} {spec['tag']}")
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == spec["asset"]]
    if len(matches) != 1:
        raise RuntimeError(f"GitHub release asset not found exactly once: {spec['asset']}")
    asset = matches[0]
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise RuntimeError(f"GitHub did not provide a SHA-256 digest for {spec['asset']}")
    size = int(asset.get("size") or 0)
    download = str(asset.get("browser_download_url") or "")
    if size <= 0 or not download.startswith("https://github.com/"):
        raise RuntimeError(f"invalid GitHub release metadata for {spec['asset']}")
    return download, size, digest.removeprefix("sha256:")
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
    url, size, digest = github_release_asset(spec)
    archive = TOOLS / "downloads" / spec["asset"]
    fetch(url, archive, size, digest, key)
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
        atomic_json(partial / "build.json", {"build_id": tts_build_id(), "chatterbox": SOURCES["chatterbox"][1], "ggml": SOURCES["ggml"][1]})
        if runtime.exists():
            rmtree_retry(runtime)
        partial.rename(runtime)
    except Exception:
        if partial.exists():
            rmtree_retry(partial)
        raise
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
def fetch(url: str, destination: Path, size: int, digest: str, key: str):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and (not size or destination.stat().st_size == size) and sha256(destination) == digest:
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
    hasher = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
        if response.status != 200:
            raise RuntimeError(f"download returned HTTP {response.status}: {url}")
        for block in iter(lambda: response.read(1024 * 1024), b""):
            output.write(block)
            hasher.update(block)
            done += len(block)
            set_job(key, "running", "download", done * 90 // size if size else min(89, done // (4 * 1024 * 1024)), f"{done} / {size} bytes" if size else f"{done} bytes")
    if size and done != size:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"download size mismatch: expected {size}, got {done}")
    actual = hasher.hexdigest()
    if actual != digest:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"download SHA-256 mismatch: expected {digest}, got {actual}")
    os.replace(partial, destination)
def install_prerequisite(name: str, key: str):
    if prerequisites()[name]["status"] == "ready":
        return
    if name == "python":
        raise RuntimeError("Python must be installed before running main.py")
    spec = PACKAGES[name]
    archive = TOOLS / "downloads" / spec["file"]
    fetch(spec["url"], archive, spec["size"], spec["sha256"], key)
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
    ok, existing_digest = verified_file(destination, spec)
    if ok:
        with LOCK:
            RECEIPTS[name] = existing_digest
            atomic_json(RECEIPTS_FILE, RECEIPTS)
        if name == "reference":
            bump_reference()
        return
    if spec.get("source"):
        source = ROOT / spec["source"]
        if not source.is_file():
            raise RuntimeError(f"bundled asset missing: {source}")
        if source.stat().st_size != spec["size"] or sha256(source) != spec["sha256"]:
            raise RuntimeError("bundled default voice does not match its pin")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        digest = spec["sha256"]
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
        size, digest = partial.stat().st_size, sha256(partial)
        if size != spec["size"] or digest not in accepted_hashes(spec):
            partial.unlink(missing_ok=True)
            expected = ", ".join(sorted(accepted_hashes(spec)))
            raise RuntimeError(f"converted model mismatch: {size} bytes, SHA-256 {digest}; expected {spec['size']} bytes and one of [{expected}]")
        os.replace(partial, destination)
    else:
        url = spec.get("url") or f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{spec['file']}"
        fetch(url, destination, spec["size"], spec["sha256"], key)
        digest = spec["sha256"]
    with LOCK:
        RECEIPTS[name] = digest
        atomic_json(RECEIPTS_FILE, RECEIPTS)
    if name == "reference":
        bump_reference()

def log_native_line(name: str, message: str):
    with LOCK:
        active = deepcopy(PROCESS_TRACES.get(name, {}))
    if message.startswith(NATIVE_EVENT_PREFIX):
        try:
            payload = json.loads(message[len(NATIVE_EVENT_PREFIX):])
            if type(payload) is not dict:
                raise ValueError("native event must be an object")
            for field in ("trace_id", "turn_id", "config_id", "session_id", "request_id"):
                if payload.get(field):
                    active[field] = str(payload[field])
            with LOCK:
                PROCESS_TRACES[name] = active
            ingest_trace(name, payload, source=f"{name}-native", **active)
            return
        except (json.JSONDecodeError, ValueError) as exc:
            error(name, "native.event.invalid", {"raw": message, "error": str(exc)}, source=f"{name}-process", **active)
            return
    level = line_level(message)
    if level == "error":
        error(name, "process.error", {"line": message}, source=f"{name}-process", **active)
    elif level == "warn":
        warn(name, "process.warning", {"line": message}, source=f"{name}-process", **active)
    else:
        debug(name, "process.output", {"line": message}, source=f"{name}-process", **active)
def log_process(name: str, process: subprocess.Popen):
    message = ""
    if not process.stdout:
        raise RuntimeError(f"{name} has no output pipe")
    for raw in process.stdout:
        message = raw.rstrip()
        if message:
            log_native_line(name, message)
        with LOCK:
            if PROCESSES.get(name) is process:
                RUNTIME["engines"][name]["message"] = message
    code = process.wait()
    expected = True
    with LOCK:
        if PROCESSES.get(name) is process:
            expected = False
            PROCESSES.pop(name)
            PROCESS_TRACES.pop(name, None)
            RUNTIME["engines"][name].update(status="error", error=f"process exited {code}: {message}", pid=None)
    (info if expected and code == 0 else error)(name, "engine.process_exited", {"code": code, "last_message": message, "expected": expected}, source=f"{name}-process")
    emit_state()
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
        PROCESS_TRACES.pop(name, None)
        RUNTIME["engines"][name].update(status="stopping", error="")
    info("engine", "engine.stop_requested", {"engine": name, "pid": process.pid if process else None})
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            warn(name, "terminate timed out; killing", {"pid": process.pid})
            process.kill()
            process.wait(5)
    with LOCK:
        RUNTIME["engines"][name].update(status="stopped", error="", pid=None, applied={})
    if process:
        info("engine", "engine.stopped", {"engine": name, "pid": process.pid})
def load_engine(name: str, key: str):
    stop_engine(name)
    if name == "brain":
        paths = [active_brain_path()]
    else:
        paths = [model_path(model) for model in ENGINE_MODELS[name]]
        for model, model_file in zip(ENGINE_MODELS[name], paths):
            if model_status(model)["status"] != "ready":
                raise RuntimeError(f"model is not verified: {model_file}")
    executable_path = component_artifact("tts" if name == "tts" else "parakeet" if name == "asr" else "gemma")
    if not executable_path.is_file():
        raise RuntimeError(f"component is missing: {executable_path}")
    if name == "tts":
        runtime = dict(TTS_RUNTIME)
        applied = {"runtime": runtime}
        command = [str(executable_path), "--port", str(PORTS["tts"]), "--model", str(paths[0]), "--s3gen-gguf", str(paths[1]), "--n-gpu-layers", str(runtime["gpu_layers"]), "--context", str(runtime["context"]), "--max-sessions", str(runtime["sessions"]), "--threads", str(runtime["threads"])]
        cwd, health, env = executable_path.parent, f"http://127.0.0.1:{PORTS['tts']}/health", os.environ.copy()
    elif name == "asr":
        applied = dict(ASR_RUNTIME)
        command = [str(executable_path), "--model", str(paths[0]), "--host", "127.0.0.1", "--port", str(PORTS["asr"]), "--threads", str(applied["threads"])]
        cwd, health, env = executable_path.parent, f"http://127.0.0.1:{PORTS['asr']}/health", os.environ.copy()
        env["PARAKEET_DEVICE"] = str(applied["device"])
    else:
        runtime = dict(BRAIN_RUNTIME)
        applied = {**runtime, "id": active_brain_id(), "family": active_brain_family(), "path": str(paths[0])}
        command = [str(executable_path), "-m", str(paths[0]), "--host", "127.0.0.1", "--port", str(PORTS["brain"]), "--device", str(runtime["device"]), "--n-gpu-layers", str(runtime["gpu_layers"]), "--ctx-size", str(runtime["context"]), "--parallel", str(runtime["parallel"]), "--no-mmproj", "--load-mode", "auto", "--flash-attn", str(runtime["flash_attn"]), "--repack", "--fit", str(runtime["fit"]), "--fit-target", str(runtime["fit_target"]), "--fit-ctx", str(runtime["fit_ctx"])]
        cwd, health, env = executable_path.parent, f"http://127.0.0.1:{PORTS['brain']}/health", os.environ.copy()
    set_job(key, "running", "load", 20, f"loading {name}")
    info("engine", "engine.launch", {"engine": name, "command": command, "cwd": str(cwd), "applied": applied})
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    with LOCK:
        PROCESSES[name] = process
        PROCESS_TRACES[name] = {}
        RUNTIME["engines"][name].update(status="loading", error="", pid=process.pid, applied=applied)
    threading.Thread(target=log_process, args=(name, process), daemon=True).start()
    wait_ready(name, process, health)
    with LOCK:
        RUNTIME["engines"][name]["status"] = "running"
    info("engine", "engine.ready", {"engine": name, "pid": process.pid, "health": health, "applied": applied})
    set_job(key, "running", "ready", 95, f"{name} ready")
def multipart(audio: bytes) -> tuple[bytes, str]:
    boundary = "trident-" + uuid.uuid4().hex
    fields = [("file", "speech.wav", "audio/wav", audio), ("response_format", "", "text/plain", str(ASR_RUNTIME["response_format"]).encode())]
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
def transcribe(audio: bytes, *, trace_id: str = "", turn_id: str = "") -> dict:
    require_engine("asr")
    started = time.monotonic()
    audio_data = {"bytes": len(audio), "sha256": hashlib.sha256(audio).hexdigest()}
    try:
        audio_data["metrics"] = wav_metrics(audio)
    except (wave.Error, EOFError):
        pass
    info("asr", "asr.requested", audio_data, trace_id=trace_id, turn_id=turn_id)
    body, content_type = multipart(audio)
    result = json.loads(remote(f"http://127.0.0.1:{PORTS['asr']}/v1/audio/transcriptions", body, content_type))
    with LOCK:
        RUNTIME["results"]["asr"] = result
    transcript = str(result.get("text") or "")
    info("asr", "asr.completed", {"duration_ms": round((time.monotonic() - started) * 1000, 3), "transcript": transcript, "characters": len(transcript), "result": result}, trace_id=trace_id, turn_id=turn_id)
    emit_state()
    return result
def brain(prompt: str, language: str, *, trace_id: str = "", turn_id: str = "") -> dict:
    require_engine("brain")
    if language not in TTS_LANGUAGES:
        raise ApiError(400, f"unsupported reply language: {language}")
    language_name = TTS_LANGUAGES[language]
    system = BRAIN_SYSTEM.format(language_name=language_name, language=language)
    request = {
        "model": active_brain_id(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        **BRAIN_GENERATION,
        "stream": False,
        **BRAIN_FAMILIES[active_brain_family()],
    }
    started = time.monotonic()
    info("brain", "brain.requested", {"language": language, "language_name": language_name, "brain": active_brain_id(), "family": active_brain_family(), "system": system, "prompt": prompt, "sampling": {key: request[key] for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "seed", "max_tokens")}}, trace_id=trace_id, turn_id=turn_id)
    result = json.loads(remote(f"http://127.0.0.1:{PORTS['brain']}/v1/chat/completions", json.dumps(request, separators=(",", ":")).encode()))
    with LOCK:
        RUNTIME["results"]["brain"] = result
    response = brain_reply_text(result)
    info("brain", "brain.completed", {"duration_ms": round((time.monotonic() - started) * 1000, 3), "language": language, "response": response, "characters": len(response), "finish_reason": ((result.get("choices") or [{}])[0]).get("finish_reason"), "usage": result.get("usage", {}), "timings": result.get("timings", {})}, trace_id=trace_id, turn_id=turn_id)
    emit_state()
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
    bump_reference()
    info("reference", "reference.updated", {"path": str(DATA / "reference.wav"), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "generation": RUNTIME.get("reference_generation", 0), "metrics": wav_metrics(data)})
def tts_session(language: str) -> dict:
    if language not in TTS_LANGUAGES:
        raise ApiError(400, f"unsupported speech language: {language}")
    require_engine("tts")
    config_id = new_trace_id("tts-config")
    reference = reference_evidence()
    init = {
        "type": "init",
        "reference_audio": str(reference_path()),
        "language": language,
        "config_id": config_id,
        **TTS_SAMPLE,
        "cfg_weight": TTS_VOICE["cfg_weight"],
        "exaggeration": TTS_VOICE["exaggeration"],
        "stream_first_chunk_tokens": TTS_STREAM["first_chunk_tokens"],
        "stream_chunk_tokens": TTS_STREAM["chunk_tokens"],
        "max_sentence_chars": TTS_STREAM["max_sentence_chars"],
    }
    with LOCK:
        PROCESS_TRACES["tts"] = {"config_id": config_id}
    info("tts", "tts.session.configured", {"language": language, "sampling": TTS_SAMPLE, "stream": TTS_STREAM, "voice": TTS_VOICE, "reference": reference}, config_id=config_id)
    return {"url": f"ws://127.0.0.1:{PORTS['tts']}/tts", "message": init, "config_id": config_id}
def brain_reply_text(result: dict | None) -> str:
    if not result:
        return ""
    message = ((result.get("choices") or [{}])[0].get("message") or {})
    content = str(message.get("content") or "").strip()
    if content:
        return content
    skip = ("thinking", "analyze", "analysis", "option", "theme", "constraint", "input", "role", "task", "draft", "determine")
    spoken = []
    for line in str(message.get("reasoning_content") or "").splitlines():
        text = line.strip(" -*\t")
        if not text or text.startswith(("#", "1.", "2.", "3.", "4.", "5.")) or text.lower().startswith(skip):
            continue
        spoken.append(text)
    return spoken[-1] if spoken else ""
def wav_metrics(data: bytes) -> dict:
    with wave.open(io.BytesIO(data), "rb") as audio:
        rate, frames = audio.getframerate(), audio.getnframes()
        raw = audio.readframes(frames)
    samples = memoryview(raw).cast("h")
    if not samples:
        return {"seconds": 0.0, "rate": rate, "rms_dbfs": -120.0, "peak_dbfs": -120.0, "clip_pct": 0.0}
    squares = sum(value * value for value in samples) / len(samples)
    rms = math.sqrt(squares) / 32768.0
    peak = max(abs(value) for value in samples) / 32768.0
    clipped = sum(abs(value) >= 32760 for value in samples)
    db = lambda value: round(20 * math.log10(max(value, 1e-6)), 2)
    return {"seconds": round(len(samples) / float(rate or 1), 3), "rate": rate, "rms_dbfs": db(rms), "peak_dbfs": db(peak), "clip_pct": round(clipped * 100 / len(samples), 4)}
def wav_body(raw: bytes | None) -> bytes:
    if not raw:
        raise ApiError(400, "WAV body is required")
    return raw
REQUIRED_MODELS = list(dict.fromkeys(["chatterbox-t3", "chatterbox-codec", "parakeet", BRAINS[BRAIN_MODEL]["model"], "reference"]))
OPS = {
    "inspect": {}, "schema": {}, "state": {},
    "install_prerequisite": {}, "install_component": {}, "download_model": {},
    "load_engine": {}, "unload_engine": {}, "upload_reference": {},
    "asr": {}, "brain": {}, "tts_session": {},
}
SCHEMA = {
    "version": 6,
    "languages": {"reply": TTS_LANGUAGES, "asr": ASR_LANGUAGES, "default_reply": DEFAULT_REPLY_LANGUAGE},
    "mic": MIC,
    "brain": {"id": BRAIN_MODEL, **BRAINS[BRAIN_MODEL], "generation": BRAIN_GENERATION, "runtime": BRAIN_RUNTIME},
    "prerequisites": {name: {"label": label} for name, label in {"python": "PYTHON 3.11+", "git": "GIT", "cmake": "CMAKE", "msvc": "MSVC BUILD TOOLS", "vulkan": "VULKAN SDK"}.items()},
    "components": {"tts": {"label": "CHATTERBOX TTS V3"}, "parakeet": {"label": BINARIES["parakeet"]["label"]}, "gemma": {"label": BINARIES["gemma"]["label"]}},
    "required_models": REQUIRED_MODELS,
    "models": {name: {"label": MODELS[name]["label"], "size": MODELS[name]["size"], "sha256": MODELS[name]["sha256"]} for name in REQUIRED_MODELS},
    "tts": {"url": f"ws://127.0.0.1:{PORTS['tts']}/tts", "sample_rate": TTS_SAMPLE_RATE, "audio": f"PCM16LE mono {TTS_SAMPLE_RATE} Hz", "sample": TTS_SAMPLE, "stream": TTS_STREAM, "voice": TTS_VOICE},
}
def inspect() -> dict:
    return {"ok": True, "version": 6, "schema": SCHEMA, "state": snapshot()}
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
        validate_wav(wav_body(raw)); emit_state()
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
    if op == "tts_session":
        return {"ok": True, **tts_session(str(payload.get("language") or DEFAULT_REPLY_LANGUAGE))}, 200
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
        http_id = new_trace_id("http")
        started = time.monotonic()
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
            if path in files:
                target, content_type = files[path]
                self.send_bytes(target.read_bytes(), content_type)
                return
            if path != "/api":
                raise ApiError(404, f"unknown endpoint: {path}")
            op = query.get("op") or "inspect"
            if op == "events":
                self.events(http_id)
                return
            if op not in ("inspect", "schema", "state"):
                raise ApiError(404, "GET /api accepts op=inspect, schema, state, or events")
            debug("api", "api.request", {"method": "GET", "op": op, "query": query}, http_id=http_id)
            response_body, code = dispatch(op, query)
            debug("api", "api.response", {"method": "GET", "op": op, "status": code, "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id)
            self.send_json(response_body, code)
        except ApiError as exception:
            warn("api", "api.rejected", {"method": "GET", "op": op, "path": self.path, "status": exception.code, "error": str(exception), "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id)
            self.send_json({"error": str(exception)}, exception.code)
        except Exception as exception:
            if client_gone(exception):
                return
            error("api", "api.failed", {"method": "GET", "op": op, "path": self.path, "error": str(exception), "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id)
            self.send_json({"error": str(exception)}, 500)
    def events(self, http_id: str):
        subscriber: queue.Queue = queue.Queue()
        with LOCK:
            SUBSCRIBERS.add(subscriber)
        info("api", "api.events.connected", {"subscribers": len(SUBSCRIBERS)}, http_id=http_id)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            subscriber.put(("state", snapshot()))
            while True:
                try:
                    event, data = subscriber.get(timeout=15)
                    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
                    self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode("ascii"))
                except queue.Empty:
                    self.wfile.write(b"event: ping\ndata:{}\n\n")
                self.wfile.flush()
        except Exception as exception:
            if not client_gone(exception):
                raise
        finally:
            with LOCK:
                SUBSCRIBERS.discard(subscriber)
                subscribers = len(SUBSCRIBERS)
            info("api", "api.events.disconnected", {"subscribers": subscribers}, http_id=http_id)
    def do_POST(self):
        http_id = new_trace_id("http")
        started = time.monotonic()
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
            info("api", "api.request", {"method": "POST", "op": op, "content_type": content_type, "content_length": int(self.headers.get("Content-Length", "0") or 0), "fields": sorted(payload)}, http_id=http_id)
            response_body, code = dispatch(op, payload, request_body)
            info("api", "api.response", {"method": "POST", "op": op, "status": code, "duration_ms": round((time.monotonic() - started) * 1000, 3)}, http_id=http_id)
            self.send_json(response_body, code)
        except ApiError as exception:
            warn("api", "api.rejected", {"method": "POST", "op": op, "path": self.path, "status": exception.code, "error": str(exception)}, http_id=http_id)
            self.send_json({"error": str(exception)}, exception.code)
        except (KeyError, TypeError, ValueError) as exception:
            self.send_json({"error": f"invalid request: {exception}"}, 400)
        except Exception as exception:
            if not client_gone(exception):
                error("api", "api.failed", {"method": "POST", "op": op, "path": self.path, "error": str(exception)}, http_id=http_id)
                self.send_json({"error": str(exception)}, 500)
class Server(ThreadingHTTPServer):
    daemon_threads = True
def main() -> int:
    server = Server((CONTROLLER["host"], CONTROLLER["port"]), Handler)
    info("controller", "controller.started", {"host": CONTROLLER["host"], "port": CONTROLLER["port"], "api_version": 6, "trace_run_id": trace_run_id(), "canonical_log": str(ROOT / "trident.log.jsonl"), "legacy_log": str(ROOT / "install.log.jsonl"), "pid": os.getpid()})
    timer = threading.Timer(.4, webbrowser.open, args=(f"http://{CONTROLLER['host']}:{CONTROLLER['port']}/",))
    timer.daemon = True
    timer.start()
    print(f"TRIDENT  http://{CONTROLLER['host']}:{CONTROLLER['port']}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        info("controller", "controller.interrupted", {})
    finally:
        info("controller", "controller.stopping", {"engines": list(PROCESSES)})
        server.server_close()
        for name in list(PROCESSES):
            stop_engine(name)
        info("controller", "controller.stopped", {})
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
