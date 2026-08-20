from __future__ import annotations

import argparse
import array
import json
import math
import os
import platform
import re
import shutil
import string
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import wave
import zipfile
from pathlib import Path

from cfg import ASR_RUNTIME, BRAIN_GENERATION, BRAIN_MODEL, BRAIN_RUNTIME, BRAIN_SYSTEM, BRAIN_THINKING, FAMILIES, RAINBOW

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MODELS_DIR = ROOT / "models"
THIRD_PARTY = ROOT / "third_party"
TOOLS = ROOT / "tools"
PATCHES = ROOT / "patches"
TTS = ROOT / "tts"
CHATTERBOX = THIRD_PARTY / "chatterbox.cpp"
GGML = CHATTERBOX / "ggml"
RUNTIMES = TOOLS / "runtime"
CONVERTER = TOOLS / "convert"
TRANSCRIPT = DATA / "transcript.txt"
ANSWER = DATA / "answer.txt"
SYSTEM_PROMPT = DATA / "system.txt"

SOURCES = {
    "chatterbox": ("https://github.com/gianni-cor/chatterbox.cpp", "ddca05fb69c2910b0d7b5eae420d360ed98c067b"),
    "ggml": ("https://github.com/ggml-org/ggml.git", "58c3805840b516b2a88ff867ccf7bb41dba79951"),
}
BINARIES = {
    "parakeet": {"label": "PARAKEET.CPP V0.5 VULKAN", "repo": "mudler/parakeet.cpp", "tag": "v0.5.0", "asset": "parakeet-v0.5.0-bin-win-vulkan-x64.zip", "exe": "parakeet-cli.exe"},
    "gemma": {"label": "LLAMA.CPP B10453 VULKAN", "repo": "ggml-org/llama.cpp", "tag": "b10453", "asset": "llama-b10453-bin-win-vulkan-x64.zip", "exe": "llama-cli.exe"},
}
SHARED_MODELS = {
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
CHATTERBOX_LIBRARY = CHATTERBOX / "build" / "Release" / "tts-cpp.lib"
TTS_BINARY = TTS / "build" / "Release" / "trident-tts.exe"
TTS_EXE = "trident-tts.exe"


def note(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def present(path: Path, size: int = 0) -> bool:
    return path.is_file() and (not size or path.stat().st_size == size)


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
    note(f"{component} {stage}: {' '.join(command)}")
    subprocess.run(command, cwd=cwd, env=env or build_env(), stdout=sys.stderr, stderr=sys.stderr, check=True)


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


def apply_chatterbox_patches() -> None:
    git = need("git")
    patches = sorted(PATCHES.glob("chatterbox-*.patch"))
    if not patches:
        raise RuntimeError("Chatterbox patch set is missing")
    for patch in patches:
        run_process("tts", f"patch-{patch.name}", [git, "apply", "--unidiff-zero", str(patch)], CHATTERBOX)


def github_release_asset(spec: dict) -> tuple[str, int]:
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
    return url, size


def fetch(url: str, destination: Path, size: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if present(destination, size):
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "trident/1"})
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            if response.status != 200:
                raise RuntimeError(f"download returned HTTP {response.status}: {url}")
            for block in iter(lambda: response.read(1024 * 1024), b""):
                output.write(block)
                done += len(block)
                if size:
                    note(f"download {destination.name}: {done}/{size}")
        if size and done != size:
            raise RuntimeError(f"download size mismatch for {destination.name}: expected {size}, got {done}")
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def extract_release_bundle(archive: Path, destination: Path, executable_name: str) -> None:
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
        matches = [p for p in partial.rglob("*") if p.is_file() and p.name.lower() == executable_name.lower()]
        if len(matches) != 1:
            raise RuntimeError(f"release bundle must contain exactly one {executable_name}; found {len(matches)}")
        rmtree_retry(destination)
        partial.rename(destination)
    except Exception:
        rmtree_retry(partial)
        raise


def runtime_executable(name: str, required: bool = True) -> Path | None:
    exe = TTS_EXE if name == "tts" else BINARIES[name]["exe"]
    root = RUNTIMES / name
    matches = [p for p in root.rglob("*") if p.is_file() and p.name.lower() == exe.lower()] if root.exists() else []
    if len(matches) == 1:
        return matches[0]
    if required:
        raise RuntimeError(f"runtime must contain exactly one {exe}; found {len(matches)}")
    return None


def install_release_binary(name: str) -> None:
    spec = BINARIES[name]
    if runtime_executable(name, required=False):
        note(f"{spec['label']}: ready")
        return
    note(f"{spec['label']}: installing pinned {spec['tag']} release")
    url, size = github_release_asset(spec)
    archive = TOOLS / "downloads" / spec["asset"]
    fetch(url, archive, size)
    extract_release_bundle(archive, RUNTIMES / name, spec["exe"])
    archive.unlink(missing_ok=True)
    runtime_executable(name)


def install_prerequisite(name: str) -> None:
    if prerequisite_ready(name):
        note(f"{name}: ready")
        return
    if name == "python":
        raise RuntimeError("Python 3.11+ must be installed before running main.py")
    spec = PACKAGES[name]
    archive = TOOLS / "downloads" / spec["file"]
    note(f"{name}: installing")
    fetch(spec["url"], archive, spec["size"])
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
    if runtime_executable("tts", required=False) and CHATTERBOX_LIBRARY.is_file():
        note("trident-tts: ready")
        return
    cmake = need("cmake")
    checkout("tts", CHATTERBOX, "chatterbox")
    apply_chatterbox_patches()
    checkout("tts", GGML, "ggml")
    run_process("tts", "configure-chatterbox", [cmake, "-S", ".", "-B", "build", "-A", "x64", "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=OFF", "-DTTS_CPP_BUILD_EXECUTABLES=OFF", "-DTTS_CPP_BUILD_TESTS=OFF"], CHATTERBOX)
    run_process("tts", "build-chatterbox", [cmake, "--build", "build", "--config", "Release", "--target", "tts-cpp", "mtl_tokenizer", "--parallel"], CHATTERBOX)
    if not CHATTERBOX_LIBRARY.is_file():
        raise RuntimeError(f"Chatterbox build did not create {CHATTERBOX_LIBRARY}")
    run_process("tts", "configure-trident-tts", [cmake, "-S", ".", "-B", "build", "-A", "x64", f"-DCHATTERBOX_CPP_ROOT={CHATTERBOX}"], TTS)
    run_process("tts", "build-trident-tts", [cmake, "--build", "build", "--config", "Release", "--target", "trident-tts", "--parallel"], TTS)
    if not TTS_BINARY.is_file():
        raise RuntimeError(f"TTS build did not create {TTS_BINARY}")
    runtime = RUNTIMES / "tts"
    partial = runtime.with_name(runtime.name + ".part")
    rmtree_retry(partial)
    partial.mkdir(parents=True)
    try:
        for artifact in TTS_BINARY.parent.iterdir():
            if artifact.is_file() and (artifact.name == TTS_BINARY.name or artifact.suffix.lower() == ".dll"):
                shutil.copy2(artifact, partial / artifact.name)
        rmtree_retry(runtime)
        partial.rename(runtime)
    except Exception:
        rmtree_retry(partial)
        raise
    runtime_executable("tts")


def models_for(family: str) -> dict:
    return {**FAMILIES[family]["TTS_MODELS"], **SHARED_MODELS}


def model_path(spec: dict) -> Path:
    root = DATA if spec.get("directory") == "data" else MODELS_DIR
    return root / spec["file"]


def download_model(spec: dict) -> None:
    destination = model_path(spec)
    if present(destination, spec["size"]):
        note(f"{spec['label']}: ready")
        return
    note(f"{spec['label']}: installing")
    if spec.get("source"):
        source = ROOT / spec["source"]
        if not present(source, spec["size"]):
            raise RuntimeError(f"bundled asset missing or wrong size: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        shutil.copyfile(source, partial)
        os.replace(partial, destination)
        return
    if not spec.get("convert"):
        url = spec.get("url") or f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{spec['file']}"
        fetch(url, destination, spec["size"])
        return
    recipe = spec["convert"]
    script = CHATTERBOX / "scripts" / recipe["script"]
    if not CHATTERBOX_LIBRARY.is_file() or not script.is_file():
        raise RuntimeError("install Chatterbox TTS before converting its models")
    python = CONVERTER / "Scripts" / "python.exe"
    lock = "numpy==1.26.4 torch==2.6.0 gguf==0.19.0 safetensors==0.5.3 scipy==1.15.3 librosa==0.11.0 resampy==0.4.3 huggingface-hub==0.34.4"
    stamp = CONVERTER / ".packages"
    if not python.is_file():
        run_process(spec["label"], "venv", [sys.executable, "-m", "venv", str(CONVERTER)], ROOT, os.environ.copy())
    if not stamp.is_file() or stamp.read_text(encoding="ascii") != lock:
        run_process(spec["label"], "torch", [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], ROOT, os.environ.copy())
        run_process(spec["label"], "dependencies", [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *lock.split()], ROOT, os.environ.copy())
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


def install(family: str) -> None:
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Trident installation requires Windows x64")
    for name in ("python", "git", "cmake", "msvc", "vulkan"):
        install_prerequisite(name)
    install_release_binary("parakeet")
    install_release_binary("gemma")
    selected = models_for(family)
    if any(not present(model_path(spec), spec["size"]) for spec in FAMILIES[family]["TTS_MODELS"].values()):
        install_tts()
    elif not runtime_executable("tts", required=False):
        install_tts()
    for spec in selected.values():
        download_model(spec)
    note(f"install complete: family={family}")


def validate_wav(path: Path, rate: int | None, minimum_seconds: float = 0.0) -> None:
    if not path.is_file():
        raise RuntimeError(f"WAV file is missing: {path}")
    try:
        with path.open("rb") as raw:
            header = raw.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise RuntimeError("not a RIFF/WAVE file")
        with wave.open(str(path), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
                raise RuntimeError("must be mono PCM16 WAV")
            if rate is not None and audio.getframerate() != rate:
                raise RuntimeError(f"must be {rate} Hz")
            if audio.getframerate() <= 0 or audio.getnframes() <= 0:
                raise RuntimeError("WAV contains no audio frames")
            if audio.getnframes() / audio.getframerate() < minimum_seconds:
                raise RuntimeError(f"WAV must be at least {minimum_seconds:g} seconds long")
    except (wave.Error, EOFError, OSError, RuntimeError) as exc:
        raise RuntimeError(f"invalid WAV {path}: {exc}") from exc


def require_model(spec: dict) -> Path:
    path = model_path(spec)
    if not present(path, spec["size"]):
        actual = path.stat().st_size if path.is_file() else 0
        raise RuntimeError(f"model missing or wrong size: {path} (expected {spec['size']}, got {actual})")
    return path


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(text, encoding="utf-8", newline="\n")
    os.replace(partial, path)


def transcribe(exe: Path, model: Path, input_wav: Path, out: Path = TRANSCRIPT) -> str:
    env = os.environ.copy()
    env["PARAKEET_DEVICE"] = str(ASR_RUNTIME["device"])
    command = [str(exe), "transcribe", "--model", str(model), "--input", str(input_wav), "--decoder", "tdt", "--threads", str(ASR_RUNTIME["threads"]), "--json"]
    note("asr: " + " ".join(command))
    result = subprocess.run(command, cwd=exe.parent, env=env, stdout=subprocess.PIPE, stderr=None, text=True, encoding="utf-8", errors="strict", check=True)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Parakeet returned malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Parakeet JSON output is not an object")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise RuntimeError("Parakeet returned an empty transcript")
    write_text_atomic(out, text + "\n")
    return text


def spoken_reply(raw: str) -> str:
    # llama-cli --output-file in conversation mode writes the session transcript:
    #   User:\n{prompt}\n\nAssistant:\n{reply}\n
    # optionally wrapping reasoning in [Start thinking]...[End thinking].
    # TTS reads answer.txt as spoken prose, so keep only the assistant reply.
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\nAssistant:\n" in text:
        text = text.rsplit("\nAssistant:\n", 1)[1].strip()
    elif text.startswith("Assistant:\n"):
        text = text[len("Assistant:\n"):].strip()
    start, end = "[Start thinking]", "[End thinking]"
    if start in text and end in text:
        text = text.split(end, 1)[1].strip()
    return text


def brain(exe: Path, model: Path, language: str, language_name: str) -> str:
    system = BRAIN_SYSTEM.format(language_name=language_name, language=language)
    write_text_atomic(SYSTEM_PROMPT, system + "\n")
    ANSWER.unlink(missing_ok=True)
    g = BRAIN_GENERATION
    r = BRAIN_RUNTIME
    thinking = "on" if BRAIN_THINKING else "off"
    template_kwargs = json.dumps({"enable_thinking": bool(BRAIN_THINKING)}, separators=(",", ":"))
    command = [
        str(exe), "-m", str(model), "--system-prompt-file", str(SYSTEM_PROMPT), "--file", str(TRANSCRIPT),
        "--conversation", "--single-turn", "--output-file", str(ANSWER), "--no-display-prompt", "--no-show-timings",
        "--offline", "--device", str(r["device"]), "--n-gpu-layers", str(r["gpu_layers"]), "--ctx-size", str(r["context"]),
        "--no-mmproj", "--load-mode", "auto", "--flash-attn", str(r["flash_attn"]), "--repack", "--fit", str(r["fit"]),
        "--fit-target", str(r["fit_target"]), "--fit-ctx", str(r["fit_ctx"]), "--seed", str(g["seed"]),
        "--n-predict", str(g["max_tokens"]), "--temperature", str(g["temperature"]), "--top-p", str(g["top_p"]),
        "--top-k", str(g["top_k"]), "--min-p", str(g["min_p"]), "--repeat-penalty", str(g["repeat_penalty"]),
        "--reasoning", thinking, "--chat-template-kwargs", template_kwargs,
    ]
    note("brain: " + " ".join(command))
    subprocess.run(command, cwd=exe.parent, stdout=sys.stderr, stderr=sys.stderr, check=True)
    if not ANSWER.is_file():
        raise RuntimeError("llama-cli did not create answer.txt")
    try:
        text = spoken_reply(ANSWER.read_text(encoding="utf-8"))
    except UnicodeError as exc:
        raise RuntimeError("llama-cli answer is not valid UTF-8") from exc
    if not text:
        raise RuntimeError("llama-cli returned an empty answer")
    write_text_atomic(ANSWER, text + "\n")
    return text


def synthesize(exe: Path, t3: Path, codec: Path, reference: Path, output: Path, language: str, family: dict,
               text_file: Path = ANSWER, capture: bool = False) -> dict:
    runtime, sample, voice = family["TTS_RUNTIME"], family["TTS_SAMPLE"], family["TTS_VOICE"]
    command = [
        str(exe), "--model", str(t3), "--s3gen-gguf", str(codec), "--reference", str(reference), "--text-file", str(text_file),
        "--output", str(output), "--language", language, "--n-gpu-layers", str(runtime["gpu_layers"]), "--context", str(runtime["context"]),
        "--threads", str(runtime["threads"]), "--seed", str(sample["seed"]), "--max-tokens", str(sample["max_tokens"]),
        "--top-k", str(sample["top_k"]), "--top-p", str(sample["top_p"]), "--min-p", str(sample["min_p"]),
        "--temperature", str(sample["temperature"]), "--repeat-penalty", str(sample["repeat_penalty"]),
        "--cfg-weight", str(voice["cfg_weight"]), "--exaggeration", str(voice["exaggeration"]),
        "--cfm-steps", str(sample["cfm_steps"]), "--chunk-chars", str(family["TTS_CHUNK"]["chars"]),
    ]
    output.unlink(missing_ok=True)
    note("tts: " + " ".join(command))
    result = subprocess.run(command, cwd=exe.parent, stdout=sys.stderr, check=True,
                            stderr=subprocess.PIPE if capture else sys.stderr, text=capture, encoding="utf-8" if capture else None,
                            errors="replace" if capture else None)
    validate_wav(output, 24000)
    metrics = {}
    if capture:
        match = re.search(r"samples=(\d+) seconds=([\d.]+) chunks=(\d+) total_ms=([\d.]+) t3_ms=([\d.]+) s3gen_ms=([\d.]+)",
                          result.stderr or "")
        if match:
            metrics = {key: float(value) for key, value in
                       zip(("samples", "seconds", "chunks", "total_ms", "t3_ms", "s3gen_ms"), match.groups())}
    return metrics


def resample_16k(src: Path, dst: Path) -> None:
    validate_wav(src, 24000)
    with wave.open(str(src), "rb") as audio:
        pcm = array.array("h")
        pcm.frombytes(audio.readframes(audio.getnframes()))
    cutoff, taps = 2.0 / 3.0, 8  # 24k -> 16k anti-alias cutoff; sinc crossings per side
    kernels = []
    for phase in (0.0, 0.5):  # stride 3/2 alternates between two fractional phases
        kernel = []
        for i in range(-12, 13):  # taps/cutoff = 12 input samples per side
            d = i - phase
            h = math.sin(math.pi * cutoff * d) / (math.pi * d) if d else cutoff
            kernel.append(h * 0.5 * (1.0 + math.cos(math.pi * d / 12.0)))
        kernels.append(kernel)
    out = array.array("h")
    for j in range(len(pcm) * 2 // 3):
        center = 1.5 * j
        base = math.floor(center) - 12
        kernel = kernels[j % 2]
        total = weight = 0.0
        for t, h in enumerate(kernel):
            i = base + t
            if 0 <= i < len(pcm):
                total += h * pcm[i]
                weight += h
        out.append(max(-32768, min(32767, round(total / weight))))
    with wave.open(str(dst), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(out.tobytes())


def score(reference: str, hypothesis: str) -> tuple[float, float]:
    def norm(text: str) -> list[str]:
        table = str.maketrans({c: " " for c in string.punctuation + "„""‚''«»‹›—–"})
        return " ".join(text.casefold().translate(table).split()).split()

    def distance(a: list, b: list) -> int:
        row = list(range(len(b) + 1))
        for i, x in enumerate(a, 1):
            prev, row[0] = row[0], i
            for j, y in enumerate(b, 1):
                prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
        return row[-1]

    ref_words, hyp_words = norm(reference), norm(hypothesis)
    wer = distance(ref_words, hyp_words) / max(len(ref_words), 1)
    ref_chars, hyp_chars = list(" ".join(ref_words)), list(" ".join(hyp_words))
    cer = distance(ref_chars, hyp_chars) / max(len(ref_chars), 1)
    return wer, cer


def run_rainbow(output_name: str, family_name: str, language: str | None, reference_name: str | None, text_name: str | None) -> None:
    family = FAMILIES[family_name]
    language = language or family["DEFAULT_REPLY_LANGUAGE"]
    if language not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {language!r} is not supported by family {family_name}; choose from {', '.join(family['TTS_LANGUAGES'])}")
    if text_name:
        text = Path(text_name).expanduser().resolve().read_text(encoding="utf-8").strip()
    elif language in RAINBOW:
        text = RAINBOW[language]
    else:
        raise RuntimeError(f"no embedded rainbow text for {language!r}; pass --text")
    default_reference = DATA / "reference.wav" if (DATA / "reference.wav").is_file() else DATA / "default-reference.wav"
    reference = Path(reference_name).expanduser().resolve() if reference_name else default_reference.resolve()
    output_wav = Path(output_name).expanduser().resolve()
    if len({reference, output_wav}) != 2:
        raise RuntimeError("reference and output must resolve to different paths")
    validate_wav(reference, None, 5.0)
    models = models_for(family_name)
    asr_exe, tts_exe = runtime_executable("parakeet"), runtime_executable("tts")
    assert asr_exe and tts_exe
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    text_path = output_wav.with_suffix(".txt")
    write_text_atomic(text_path, text + "\n")
    asr_wav = output_wav.with_suffix(".16k.wav")
    asr_wav.unlink(missing_ok=True)
    started = time.perf_counter()
    metrics = synthesize(tts_exe, require_model(models["chatterbox-t3"]), require_model(models["chatterbox-codec"]),
                         reference, output_wav, language, family, text_file=text_path, capture=True)
    wall = time.perf_counter() - started
    resample_16k(output_wav, asr_wav)
    started = time.perf_counter()
    hypothesis = transcribe(asr_exe, require_model(models["parakeet"]), asr_wav, out=output_wav.with_suffix(".transcript.txt"))
    stt_wall = time.perf_counter() - started
    wer, cer = score(text, hypothesis)
    sys.stdout.reconfigure(errors="replace")  # Windows console code pages can't print pl/de text
    audio_ms = metrics.get("seconds", 0.0) * 1000.0
    gen_rtf = (metrics.get("t3_ms", 0.0) + metrics.get("s3gen_ms", 0.0)) / audio_ms if audio_ms else 0.0
    print(f"rainbow family={family_name} lang={language} ref={reference.name}")
    print(f"audio_s={metrics.get('seconds', 0.0):.2f} chunks={int(metrics.get('chunks', 0))} "
          f"total_ms={metrics.get('total_ms', 0.0):.0f} t3_ms={metrics.get('t3_ms', 0.0):.0f} "
          f"s3gen_ms={metrics.get('s3gen_ms', 0.0):.0f} gen_RTF={gen_rtf:.3f} wall_s={wall:.1f} stt_s={stt_wall:.1f}")
    print(f"WER={wer * 100:.2f}% CER={cer * 100:.2f}%")
    print(f"Source: {text}")
    print(f"Heard: {hypothesis}")


def run_pipeline(input_name: str, output_name: str, family_name: str, language: str | None, reference_name: str | None) -> None:
    family = FAMILIES[family_name]
    language = language or family["DEFAULT_REPLY_LANGUAGE"]
    if language not in family["TTS_LANGUAGES"]:
        raise RuntimeError(f"language {language!r} is not supported by family {family_name}; choose from {', '.join(family['TTS_LANGUAGES'])}")
    input_wav = Path(input_name).expanduser().resolve()
    output_wav = Path(output_name).expanduser().resolve()
    default_reference = DATA / "reference.wav" if (DATA / "reference.wav").is_file() else DATA / "default-reference.wav"
    reference = Path(reference_name).expanduser().resolve() if reference_name else default_reference.resolve()
    if len({input_wav, reference, output_wav}) != 3:
        raise RuntimeError("input, reference, and output must resolve to three different paths")
    if output_wav in {TRANSCRIPT.resolve(), ANSWER.resolve(), SYSTEM_PROMPT.resolve()}:
        raise RuntimeError("output path conflicts with Trident intermediate files")
    validate_wav(input_wav, 16000)
    validate_wav(reference, None, 5.0)
    models = models_for(family_name)
    asr_model = require_model(models["parakeet"])
    brain_model = require_model(models[BRAIN_MODEL])
    t3_model = require_model(models["chatterbox-t3"])
    codec_model = require_model(models["chatterbox-codec"])
    asr_exe = runtime_executable("parakeet")
    brain_exe = runtime_executable("gemma")
    tts_exe = runtime_executable("tts")
    assert asr_exe and brain_exe and tts_exe
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    transcript = transcribe(asr_exe, asr_model, input_wav)
    answer = brain(brain_exe, brain_model, language, family["TTS_LANGUAGES"][language])
    synthesize(tts_exe, t3_model, codec_model, reference, output_wav, language, family)
    print(f"Transcript: {transcript}")
    print(f"Answer: {answer}")
    print(f"Output: {output_wav}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python main.py")
    commands = p.add_subparsers(dest="command", required=True)
    install_cmd = commands.add_parser("install")
    install_cmd.add_argument("--family", choices=tuple(FAMILIES), default="turbo")
    run_cmd = commands.add_parser("run")
    run_cmd.add_argument("input")
    run_cmd.add_argument("output")
    run_cmd.add_argument("--family", choices=tuple(FAMILIES), default="turbo")
    run_cmd.add_argument("--language")
    run_cmd.add_argument("--reference")
    rainbow_cmd = commands.add_parser("rainbow")
    rainbow_cmd.add_argument("output")
    rainbow_cmd.add_argument("--family", choices=tuple(FAMILIES), required=True)
    rainbow_cmd.add_argument("--language")
    rainbow_cmd.add_argument("--reference")
    rainbow_cmd.add_argument("--text")
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "install":
            install(args.family)
        elif args.command == "rainbow":
            run_rainbow(args.output, args.family, args.language, args.reference, args.text)
        else:
            run_pipeline(args.input, args.output, args.family, args.language, args.reference)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        note(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
