import argparse, hashlib, json, os, re, shutil, subprocess, sys, urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"

MODELS = ROOT / "models"
OPT = {}
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


@dataclass(frozen=True)
class Variant:
    name: str
    hf: str
    assets: tuple[str, ...]
    t3_ckpt: str
    architecture: str


_GPT2 = ("s3gen_meanflow.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")
VARIANTS = {
    "nano": Variant("nano", "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64", ("t3_nano_v1.safetensors",) + _GPT2, "t3_nano_v1.safetensors", "gpt2"),
    "turbo": Variant("turbo", "https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/749d1c1a46eb10492095d68fbcf55691ccf137cd", ("t3_turbo_v1.safetensors",) + _GPT2, "t3_turbo_v1.safetensors", "gpt2"),
    "v3": Variant("v3", "https://huggingface.co/ResembleAI/chatterbox/resolve/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18", ("t3_mtl23ls_v3.safetensors", "s3gen.safetensors", "conds.pt", "ve.safetensors", "grapheme_mtl_merged_expanded_v1.json"), "t3_mtl23ls_v3.safetensors", "llama"),
}
ARCH = {
    "gpt2": {"ckpt": ".ckpt", "s3_checkpoint": "s3gen_meanflow.safetensors", "s3_family": "meanflow"},
    "llama": {"ckpt": ".ckpt-v3", "s3_checkpoint": "s3gen.safetensors", "s3_family": "v3"},
}
BRAIN_DEFS = (
    ("cuda_architectures", "CMAKE_CUDA_ARCHITECTURES"),
    ("cuda_flags_release", "CMAKE_CUDA_FLAGS_RELEASE"),
    ("brain_ggml_cuda", "GGML_CUDA"),
    ("brain_ggml_cuda_force_mmq", "GGML_CUDA_FORCE_MMQ"),
    ("brain_ggml_cuda_force_cublas", "GGML_CUDA_FORCE_CUBLAS"),
    ("brain_ggml_cuda_fa", "GGML_CUDA_FA"),
    ("brain_ggml_cuda_fa_all_quants", "GGML_CUDA_FA_ALL_QUANTS"),
    ("brain_ggml_cuda_graphs", "GGML_CUDA_GRAPHS"),
    ("brain_ggml_cuda_nccl", "GGML_CUDA_NCCL"),
    ("brain_ggml_cuda_no_peer_copy", "GGML_CUDA_NO_PEER_COPY"),
    ("brain_ggml_cuda_no_vmm", "GGML_CUDA_NO_VMM"),
    ("brain_ggml_cpu", "GGML_CPU"),
    ("brain_ggml_vulkan", "GGML_VULKAN"),
    ("brain_ggml_metal", "GGML_METAL"),
    ("brain_ggml_openmp", "GGML_OPENMP"),
    ("brain_ggml_blas", "GGML_BLAS"),
    ("brain_ggml_accelerate", "GGML_ACCELERATE"),
    ("brain_ggml_native", "GGML_NATIVE"),
    ("llama_build_common", "LLAMA_BUILD_COMMON"),
    ("llama_build_tools", "LLAMA_BUILD_TOOLS"),
    ("llama_build_mtmd", "LLAMA_BUILD_MTMD"),
    ("llama_build_tests", "LLAMA_BUILD_TESTS"),
    ("llama_build_examples", "LLAMA_BUILD_EXAMPLES"),
    ("llama_build_server", "LLAMA_BUILD_SERVER"),
    ("llama_curl", "LLAMA_CURL"),
    ("llama_openssl", "LLAMA_OPENSSL"),
    ("llama_subprocess", "LLAMA_SUBPROCESS"),
    ("mtmd_video", "MTMD_VIDEO"),
    ("brain_build_shared", "BUILD_SHARED_LIBS"),
    ("ggml_ccache", "GGML_CCACHE"),
)


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def matches(stamp: Path, payload: dict, *outputs: Path) -> bool:
    return stamp.is_file() and json.loads(stamp.read_text(encoding="utf-8")) == payload and all(path.is_file() for path in outputs)


def digest(*paths) -> str:
    hasher, files = hashlib.sha256(), []
    for item in paths:
        item = Path(item)
        files.append(item) if item.is_file() else files.extend(path for path in item.rglob("*") if path.is_file())
    for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
        hasher.update(path.relative_to(ROOT).as_posix().encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, tmp.open("wb") as out:
        shutil.copyfileobj(response, out)
    tmp.replace(dest)


def specs(*names: str) -> list[str]:
    lines = [line.strip() for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    found = {}
    for line in lines:
        key = line.split("/")[-1].split(".git")[0] if line.startswith("git+") else line.split("==")[0].split(">=")[0]
        found[key] = line
    missing = [name for name in names if name not in found]
    if missing:
        raise RuntimeError("requirements missing " + ", ".join(missing))
    return [found[name] for name in names]


def pip(py: Path, name: str, args: list[str], env=None) -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    stamp = MODELS / f"{name}.stamp"
    payload = json.dumps(args)
    if stamp.is_file() and stamp.read_text(encoding="utf-8") == payload:
        print("skip " + name, flush=True)
        return
    print("install " + name, flush=True)
    run([str(py), "-m", "pip", "install", "--disable-pip-version-check", *args], env=env)
    stamp.write_text(payload, encoding="utf-8")


class LlamaCpp:
    HOME = ROOT / "gemma" / "llama.cpp"

    @staticmethod
    def rev() -> str:
        return subprocess.run(["git", "-C", str(LlamaCpp.HOME), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def ensure() -> str:
        home = LlamaCpp.HOME
        if not (home / ".git").exists():
            run(["git", "clone", "--filter=blob:none", OPT["llama_repo"], str(home)])
        rev = OPT["llama_rev"]
        if rev:
            run(["git", "-C", str(home), "fetch", "origin", rev, "--depth", "1"])
            run(["git", "-C", str(home), "checkout", "--detach", rev])
            return rev
        run(["git", "-C", str(home), "fetch", "origin", "master"])
        run(["git", "-C", str(home), "checkout", "master"])
        run(["git", "-C", str(home), "pull", "--ff-only", "origin", "master"])
        return LlamaCpp.rev()


class Ggml:
    @staticmethod
    def ensure() -> None:
        home = ROOT / "ggml"
        if not (home / ".git").exists():
            run(["git", "clone", "--filter=blob:none", OPT["ggml_repo"], str(home)])
        head = subprocess.run(["git", "-C", str(home), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        if head != OPT["ggml_rev"]:
            run(["git", "-C", str(home), "fetch", "origin", OPT["ggml_rev"], "--depth", "1"])
            run(["git", "-C", str(home), "checkout", "--detach", OPT["ggml_rev"]])


def mouth_defs() -> list[str]:
    return [
        f"-DGGML_VULKAN={OPT['mouth_ggml_vulkan']}",
        f"-DGGML_CPU={OPT['mouth_ggml_cpu']}",
        f"-DGGML_CUDA={OPT['mouth_ggml_cuda']}",
        f"-DGGML_OPENMP={OPT['mouth_ggml_openmp']}",
        f"-DGGML_BUILD_TESTS={OPT['mouth_ggml_build_tests']}",
        f"-DGGML_BUILD_EXAMPLES={OPT['mouth_ggml_build_examples']}",
        f"-DBUILD_SHARED_LIBS={OPT['mouth_build_shared']}",
    ]


def build_engine() -> tuple[Path, Path, dict]:
    outputs = (
        ROOT / "build" / "bin" / "chatterbox.exe",
        ROOT / "build" / "bin" / "chatterbox-bake.exe",
        ROOT / "build" / "bin" / "ear.exe",
    )
    defs = mouth_defs()
    wanted = {
        "ggml": OPT["ggml_rev"],
        "generator": OPT["generator"],
        "architecture": OPT["arch"],
        "cmake": defs,
        "source": digest(ROOT / "CMakeLists.txt", ROOT / "src"),
    }
    stamp = MODELS / "build-contract.json"
    if stamp.is_file() and json.loads(stamp.read_text(encoding="utf-8")) == wanted and all(path.is_file() for path in outputs):
        print("skip ggml", flush=True)
        print("skip engine", flush=True)
        return *outputs, wanted
    print("install ggml", flush=True)
    Ggml.ensure()
    print("install engine", flush=True)
    if OPT["vulkan_sdk"]:
        sdk = Path(OPT["vulkan_sdk"])
    else:
        sdk = max(Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"), key=lambda path: tuple(map(int, re.findall(r"\d+", path.parts[-3])))).parents[1]
    build = ROOT / "build"
    run(["cmake", "-S", str(ROOT), "-B", str(build), "-G", OPT["generator"], "-A", OPT["arch"], f"-DVulkan_INCLUDE_DIR={sdk / 'Include'}", f"-DVulkan_LIBRARY={sdk / 'Lib/vulkan-1.lib'}", f"-DVulkan_GLSLC_EXECUTABLE={sdk / 'Bin/glslc.exe'}", *defs])
    run(
        [
            "cmake",
            "--build",
            str(build),
            "--config",
            "Release",
            "--target",
            "chatterbox",
            "chatterbox-bake",
            "ear",
            "--parallel",
            OPT["parallel"],
        ]
    )
    atomic_json(stamp, wanted)
    return *outputs, wanted


def launch_conversion(cfg: Variant):
    return {
        "reference": OPT["reference"],
        "t3-weight-type": OPT["t3_weight_type"],
        "s3-weight-type": OPT["s3_weight_type"],
        "t3-quant-policy": OPT["t3_quant_policy"],
        "s3-quant-policy": OPT["s3_quant_policy"],
    }


def policy(path: Path, default: str) -> dict:
    return {"default": default, "rules": json.loads(path.read_text(encoding="utf-8"))["rules"]}


def gguf_name(kind: str, family: str, spec: dict) -> Path:
    digest8 = hashlib.sha256(json.dumps(spec["rules"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]
    return MODELS / f"chatterbox-{kind}-{family}-{spec['default']}-{digest8}.gguf"


def checkpoints(cfg: Variant) -> Path:
    home = ROOT / ARCH[cfg.architecture]["ckpt"]
    home.mkdir(parents=True, exist_ok=True)
    root_url = OPT["hf_" + cfg.name] or cfg.hf
    assets = [(asset, root_url + "/" + asset) for asset in cfg.assets]
    if all((home / name).is_file() for name, _ in assets):
        print("skip checkpoints", flush=True)
        return home
    print("install checkpoints", flush=True)
    for name, url in assets:
        if not (home / name).is_file():
            download(url, home / name)
    return home


def convert(cfg: Variant, py: Path, ckpt: Path, conv: dict):
    family = ARCH[cfg.architecture]
    t3_policy = policy(ROOT / conv["t3-quant-policy"], conv["t3-weight-type"])
    s3_policy = policy(ROOT / conv["s3-quant-policy"], conv["s3-weight-type"])
    t3 = gguf_name("t3", cfg.name, t3_policy)
    s3 = gguf_name("s3gen", family["s3_family"], s3_policy)
    jobs = [
        (t3, {"kind": "t3", "policy": t3_policy, "t3_ckpt": cfg.t3_ckpt, "converter": digest(ROOT / "scripts" / "convert_t3.py", ROOT / "scripts" / "quant.py")}, [str(py), str(ROOT / "scripts" / "convert_t3.py"), str(ckpt), str(t3.with_suffix(".gguf.converting")), cfg.t3_ckpt, "--matrix-type", conv["t3-weight-type"], "--quant-policy", str(ROOT / conv["t3-quant-policy"]), "--s3-checkpoint", family["s3_checkpoint"]]),
        (s3, {"kind": "s3", "policy": s3_policy, "checkpoint": family["s3_checkpoint"], "converter": digest(ROOT / "scripts" / "convert_s3.py", ROOT / "scripts" / "quant.py")}, [str(py), str(ROOT / "scripts" / "convert_s3.py"), str(ckpt), str(s3.with_suffix(".gguf.converting")), "--checkpoint", family["s3_checkpoint"], "--weight-type", conv["s3-weight-type"], "--quant-policy", str(ROOT / conv["s3-quant-policy"])]),
    ]
    contracts = []
    for dest, wanted, cmd in jobs:
        stamp = MODELS / f"{dest.stem}.convert.json"
        if matches(stamp, wanted, dest):
            print("skip " + dest.name, flush=True)
            contracts.append(wanted)
            continue
        print("install " + dest.name, flush=True)
        tmp = dest.with_suffix(".gguf.converting")
        run(cmd)
        tmp.replace(dest)
        atomic_json(stamp, wanted)
        contracts.append(wanted)
    return t3, s3, contracts


def bake_voice(cfg: Variant, base_t3: Path, base_s3: Path, bake: Path, contracts: list[dict], build: dict, conv: dict):
    reference = (ROOT / conv["reference"]).resolve()
    voice = MODELS / "voices" / cfg.name
    t3, s3, stamp = voice / "t3.gguf", voice / "s3.gguf", voice / "bake.json"
    wanted = {"variant": cfg.name, "conversions": contracts, "reference": {"path": str(reference), "bytes": reference.stat().st_size, "mtime": reference.stat().st_mtime_ns}, "build": build}
    if matches(stamp, wanted, t3, s3):
        print("skip voice", flush=True)
        return
    print("install voice", flush=True)
    temp = voice.with_name(voice.name + ".baking")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    shutil.copy2(base_t3, temp / "t3.gguf")
    shutil.copy2(base_s3, temp / "s3.gguf")
    run([str(bake), str(temp / "t3.gguf"), str(temp / "s3.gguf"), str(reference)], cwd=ROOT)
    atomic_json(temp / "bake.json", wanted)
    voice.parent.mkdir(parents=True, exist_ok=True)
    if voice.exists():
        shutil.rmtree(voice)
    temp.replace(voice)


def ensure_venv() -> Path:
    py = venv_python()
    if py.is_file():
        return py
    print("install venv", flush=True)
    home = ROOT / ".venv"
    if home.exists():
        shutil.rmtree(home)
    run([sys.executable, "-m", "venv", str(home)])
    return py


def python_packages(py: Path) -> None:
    args = specs("numpy", "gguf", "safetensors", "librosa", "tokenizers")
    args += ["torch==2.6.0", "--index-url", PYTORCH_CPU_INDEX, "--extra-index-url", "https://pypi.org/simple"]
    pip(py, "tts-packages", args)


def windows_mt() -> Path:
    return max(Path("C:/Program Files (x86)/Windows Kits/10/bin").glob("*/x64/mt.exe"), key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parts[-3])))


def embed_utf8(exe: Path) -> None:
    merged = exe.with_suffix(".manifest")
    mt = str(windows_mt())
    run([mt, "-nologo", f"-inputresource:{exe};#1", "-manifest", str(ROOT / "utf8.manifest"), "-out:" + str(merged)])
    run([mt, "-nologo", "-manifest", str(merged), f"-outputresource:{exe};#1"])
    merged.unlink()


def on(name: str) -> bool:
    return OPT[name] in ("on", "1", "ON", "true")


def install_ear() -> None:
    gguf = OPT["ear_gguf_name"]
    url = OPT["ear_gguf_url"] or ("https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/main/" + gguf)
    dest = MODELS / "ear" / gguf
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        print("install " + gguf, flush=True)
        download(url, dest)
    home = ROOT / "nemo-speech"
    exe = ROOT / "build" / "bin" / "ear" / "nemo-speech.exe"
    if not (home / ".git").exists():
        print("install nemo-speech", flush=True)
        run(["git", "clone", "--filter=blob:none", OPT["nemo_repo"], str(home)])
    if OPT["nemo_rev"]:
        run(["git", "-C", str(home), "fetch", "origin", OPT["nemo_rev"], "--depth", "1"])
        run(["git", "-C", str(home), "checkout", "--detach", OPT["nemo_rev"]])
    rev = subprocess.run(["git", "-C", str(home), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    stamp = MODELS / "ear-build.json"
    build_choice = {key: OPT[key] for key in OPT if key.startswith("ear_") or key in ("nemo_repo", "nemo_rev")}
    wanted = {"rev": rev, "gguf": gguf, "build": build_choice}
    if matches(stamp, wanted, exe, dest):
        print("skip ear", flush=True)
        embed_utf8(exe)
        return
    print("install ear", flush=True)
    ps = shutil.which("powershell") or "powershell.exe"
    command = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(home / "scripts" / "windows" / "build.ps1"), "-Backend", OPT["ear_backend"], "-Profile", OPT["ear_profile"], "-Config", OPT["ear_config"], "-CudaArch", OPT["ear_cuda_arch"], "-Architecture", OPT["ear_architecture"], "-Compiler", OPT["ear_compiler"], "-Jobs", OPT["ear_jobs"]]
    if OPT["ear_build_dir"]:
        command += ["-BuildDir", OPT["ear_build_dir"]]
    if OPT["ear_vcpkg_root"]:
        command += ["-VcpkgRoot", OPT["ear_vcpkg_root"]]
    if OPT["ear_vcpkg_triplet"]:
        command += ["-VcpkgTriplet", OPT["ear_vcpkg_triplet"]]
    for flag, switch in (
        ("ear_asr_only", "-AsrOnly"),
        ("ear_grpc", "-Grpc"),
        ("ear_nmt", "-Nmt"),
        ("ear_flashlight", "-Flashlight"),
        ("ear_http", "-Http"),
        ("ear_http_tls", "-HttpTls"),
        ("ear_tts_ja", "-TtsJa"),
        ("ear_tts_zh", "-TtsZh"),
        ("ear_tests", "-Tests"),
        ("ear_cublas_shim", "-CublasShim"),
    ):
        if on(flag):
            command.append(switch)
    run(command, cwd=home)
    if OPT["ear_build_dir"]:
        built_dir = Path(OPT["ear_build_dir"])
    else:
        profile = "asr" if on("ear_asr_only") else OPT["ear_profile"]
        profile_suffix = "" if profile == "core" else "-" + profile
        arch_suffix = "" if OPT["ear_architecture"] == "auto" else "-" + OPT["ear_architecture"]
        built_dir = home / f"build-{OPT['ear_backend']}{profile_suffix}{arch_suffix}"
    built = built_dir / "bin" / "nemo-speech.exe"
    if exe.parent.exists():
        shutil.rmtree(exe.parent)
    exe.parent.mkdir(parents=True)
    for item in built.parent.iterdir():
        if item.suffix.lower() in {".exe", ".dll"}:
            shutil.copy2(item, exe.parent / item.name)
    embed_utf8(exe)
    atomic_json(stamp, wanted)


def install_mouth(name: str) -> None:
    cfg, py = VARIANTS[name], venv_python()
    _, bake, _, build = build_engine()
    ckpt = checkpoints(cfg)
    conv = launch_conversion(cfg)
    t3, s3, contracts = convert(cfg, py, ckpt, conv)
    bake_voice(cfg, t3, s3, bake, contracts, build, conv)


def install_gemma_brain() -> None:
    gemma = ROOT / "gemma"
    exe = gemma / "build" / "Release" / "gemma-brain.exe"
    llama = LlamaCpp.ensure()
    defs = [f"{cmake}={OPT[key]}" for key, cmake in BRAIN_DEFS]
    if OPT["msvc_arch"]:
        defs.append("GEMMA_MSVC_ARCH_FLAG=" + OPT["msvc_arch"])
    wanted = {
        "llama": llama,
        "source": digest(
            gemma / "CMakeLists.txt",
            gemma / "src" / "brain.cpp",
            gemma / "cmake" / "HostCpu.cmake",
            gemma / "cmake" / "Pascal1060.cmake",
            gemma / "cmake" / "PascalCuda.cmake",
            gemma / "scripts" / "build.ps1",
            gemma / "scripts" / "configure.ps1",
        ),
        "cmake": defs,
        "cuda_root": OPT["cuda_root"],
        "toolset": OPT["cuda_toolset"],
    }
    models = gemma / "models"
    models.mkdir(parents=True, exist_ok=True)
    for name, url in ((OPT["text_model_name"], OPT["text_model_url"]), (OPT["mmproj_name"], OPT["mmproj_url"])):
        dest = models / name
        if not dest.is_file():
            print("install " + name, flush=True)
            download(url, dest)
    stamp = MODELS / "gemma-brain-build.json"
    if matches(stamp, wanted, exe):
        print("skip gemma-brain", flush=True)
        return
    print("install gemma-brain", flush=True)
    ps = shutil.which("powershell") or "powershell.exe"
    command = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(gemma / "scripts" / "build.ps1"), "-CudaRoot", OPT["cuda_root"], "-Generator", OPT["generator"], "-Arch", OPT["arch"], "-Toolset", OPT["cuda_toolset"], "-CudaParallel", OPT["cuda_parallel"], "-BrainParallel", OPT["brain_parallel"]]
    if on("clean_cuda"):
        command.append("-CleanCuda")
    if defs:
        command += ["-Def", "\x1e".join(defs)]
    run(command, cwd=gemma)
    atomic_json(stamp, wanted)


def install_all(name: str) -> None:
    python_packages(venv_python())
    install_ear()
    install_mouth(name)
    install_gemma_brain()


def parse_args(argv: list[str]) -> dict:
    p = argparse.ArgumentParser(prog="install.py")
    p.add_argument("cmd", nargs="?", default="turbo", choices=["ear", "brain", "mouth", "nano", "turbo", "v3", "all"])
    p.add_argument("name", nargs="?")
    p.add_argument("--generator", default="Visual Studio 17 2022")
    p.add_argument("--arch", default="x64")
    p.add_argument("--parallel", default="2")
    p.add_argument("--vulkan-sdk", default="")
    p.add_argument("--ggml-repo", default="https://github.com/ggml-org/ggml.git")
    p.add_argument("--ggml-rev", default="7840aaba1989c6deeefede1d77d5aaf8f52b947e")
    p.add_argument("--reference", default="reference.wav")
    p.add_argument("--t3-weight-type", default="q4_0")
    p.add_argument("--s3-weight-type", default="q4_0")
    p.add_argument("--t3-quant-policy", default="scripts/quant_t3.json")
    p.add_argument("--s3-quant-policy", default="scripts/quant_s3.json")
    p.add_argument("--mouth-ggml-vulkan", default="ON")
    p.add_argument("--mouth-ggml-cpu", default="OFF")
    p.add_argument("--mouth-ggml-cuda", default="OFF")
    p.add_argument("--mouth-ggml-openmp", default="OFF")
    p.add_argument("--mouth-ggml-build-tests", default="OFF")
    p.add_argument("--mouth-ggml-build-examples", default="OFF")
    p.add_argument("--mouth-build-shared", default="ON")
    p.add_argument("--hf-nano", default="")
    p.add_argument("--hf-turbo", default="")
    p.add_argument("--hf-v3", default="")
    p.add_argument("--cuda-root", default=r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6")
    p.add_argument("--cuda-toolset", default="cuda=12.6")
    p.add_argument("--cuda-parallel", default="1")
    p.add_argument("--brain-parallel", default="4")
    p.add_argument("--clean-cuda", default="off")
    p.add_argument("--msvc-arch", default="")
    p.add_argument("--text-model-name", default="gemma-4-E2B-it-Q4_0.gguf")
    p.add_argument("--text-model-url", default="https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_0.gguf")
    p.add_argument("--mmproj-name", default="mmproj-gemma-4-E2B-it-Q8_0.gguf")
    p.add_argument("--mmproj-url", default="https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/mmproj-gemma-4-E2B-it-Q8_0.gguf")
    p.add_argument("--llama-repo", default="https://github.com/ggml-org/llama.cpp.git")
    p.add_argument("--llama-rev", default="")
    p.add_argument("--cuda-architectures", default="61-real")
    p.add_argument("--cuda-flags-release", default="-DNDEBUG")
    p.add_argument("--brain-ggml-cuda", default="ON")
    p.add_argument("--brain-ggml-cuda-force-mmq", default="ON")
    p.add_argument("--brain-ggml-cuda-force-cublas", default="OFF")
    p.add_argument("--brain-ggml-cuda-fa", default="ON")
    p.add_argument("--brain-ggml-cuda-fa-all-quants", default="OFF")
    p.add_argument("--brain-ggml-cuda-graphs", default="ON")
    p.add_argument("--brain-ggml-cuda-nccl", default="OFF")
    p.add_argument("--brain-ggml-cuda-no-peer-copy", default="OFF")
    p.add_argument("--brain-ggml-cuda-no-vmm", default="OFF")
    p.add_argument("--brain-ggml-cpu", default="ON")
    p.add_argument("--brain-ggml-vulkan", default="OFF")
    p.add_argument("--brain-ggml-metal", default="OFF")
    p.add_argument("--brain-ggml-openmp", default="ON")
    p.add_argument("--brain-ggml-blas", default="OFF")
    p.add_argument("--brain-ggml-accelerate", default="OFF")
    p.add_argument("--brain-ggml-native", default="ON")
    p.add_argument("--llama-build-common", default="ON")
    p.add_argument("--llama-build-tools", default="OFF")
    p.add_argument("--llama-build-mtmd", default="ON")
    p.add_argument("--llama-build-tests", default="OFF")
    p.add_argument("--llama-build-examples", default="OFF")
    p.add_argument("--llama-build-server", default="OFF")
    p.add_argument("--llama-curl", default="OFF")
    p.add_argument("--llama-openssl", default="OFF")
    p.add_argument("--llama-subprocess", default="OFF")
    p.add_argument("--mtmd-video", default="OFF")
    p.add_argument("--brain-build-shared", default="OFF")
    p.add_argument("--ggml-ccache", default="ON")
    p.add_argument("--nemo-repo", default="https://github.com/NVIDIA/NeMo-Speech.cpp.git")
    p.add_argument("--nemo-rev", default="")
    p.add_argument("--ear-gguf-name", default="nemotron-3.5-asr-streaming-0.6b.q8_0.gguf")
    p.add_argument("--ear-gguf-url", default="")
    p.add_argument("--ear-backend", default="cpu", choices=["cpu", "cuda", "vulkan"])
    p.add_argument("--ear-profile", default="core", choices=["core", "asr", "server", "full", "developer"])
    p.add_argument("--ear-config", default="Release", choices=["Release", "RelWithDebInfo", "Debug"])
    p.add_argument("--ear-cuda-arch", default="native")
    p.add_argument("--ear-architecture", default="auto", choices=["auto", "x64", "arm64"])
    p.add_argument("--ear-compiler", default="auto", choices=["auto", "msvc", "clang-cl"])
    p.add_argument("--ear-jobs", default="0")
    p.add_argument("--ear-build-dir", default="")
    p.add_argument("--ear-vcpkg-root", default="")
    p.add_argument("--ear-vcpkg-triplet", default="")
    p.add_argument("--ear-asr-only", default="on")
    p.add_argument("--ear-grpc", default="off")
    p.add_argument("--ear-nmt", default="off")
    p.add_argument("--ear-flashlight", default="off")
    p.add_argument("--ear-http", default="off")
    p.add_argument("--ear-http-tls", default="off")
    p.add_argument("--ear-tts-ja", default="off")
    p.add_argument("--ear-tts-zh", default="off")
    p.add_argument("--ear-tests", default="off")
    p.add_argument("--ear-cublas-shim", default="off")
    return vars(p.parse_args(argv[1:]))


def main() -> None:
    global OPT
    argv = sys.argv
    created = not venv_python().is_file()
    ensure_venv()
    if Path(sys.executable).resolve() != venv_python().resolve():
        env = os.environ.copy()
        if created:
            env["TRIDENT_VENV_NEW"] = "1"
        raise SystemExit(subprocess.call([str(venv_python()), *argv], env=env))
    print("install venv" if os.environ.pop("TRIDENT_VENV_NEW", None) == "1" else "skip venv", flush=True)
    OPT = parse_args(argv)
    cmd, name = OPT["cmd"], OPT["name"]
    if cmd == "mouth":
        if name not in VARIANTS:
            raise SystemExit("usage: python install.py mouth <nano|turbo|v3> [knobs]")
        python_packages(venv_python())
        install_mouth(name)
    elif cmd == "ear":
        install_ear()
    elif cmd == "brain":
        install_gemma_brain()
    elif cmd == "all":
        python_packages(venv_python())
        install_ear()
        install_gemma_brain()
        install_mouth("nano")
        install_mouth("turbo")
        install_mouth("v3")
    elif cmd in VARIANTS:
        install_all(cmd)
    else:
        raise SystemExit("usage: python install.py [ear | brain | mouth <variant> | nano | turbo | v3 | all] [knobs]")


if __name__ == "__main__":
    main()
