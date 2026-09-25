import hashlib, json, os, re, shutil, subprocess, sys, urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILE = {}
ARCH_FLAG = None
BACKEND = None


@dataclass(frozen=True)
class Variant:
    name: str
    assets: tuple[str, ...]
    t3_ckpt: str
    architecture: str


_GPT2 = ("s3gen_meanflow.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")
VARIANTS = {
    "nano": Variant("nano", ("t3_nano_v1.safetensors",) + _GPT2, "t3_nano_v1.safetensors", "gpt2"),
    "turbo": Variant("turbo", ("t3_turbo_v1.safetensors",) + _GPT2, "t3_turbo_v1.safetensors", "gpt2"),
    "v3": Variant("v3", ("t3_mtl23ls_v3.safetensors", "s3gen.safetensors", "conds.pt", "ve.safetensors", "grapheme_mtl_merged_expanded_v1.json"), "t3_mtl23ls_v3.safetensors", "llama"),
}
ARCH = {
    "gpt2": {"ckpt": "ckpt", "s3_checkpoint": "s3gen_meanflow.safetensors", "s3_family": "meanflow"},
    "llama": {"ckpt": "ckpt-v3", "s3_checkpoint": "s3gen.safetensors", "s3_family": "v3"},
}
BRAIN_DEFS = (
    ("install.brain_ggml_cpu", "GGML_CPU"),
    ("install.brain_ggml_metal", "GGML_METAL"),
    ("install.brain_ggml_openmp", "GGML_OPENMP"),
    ("install.brain_ggml_blas", "GGML_BLAS"),
    ("install.brain_ggml_accelerate", "GGML_ACCELERATE"),
    ("install.brain_ggml_native", "GGML_NATIVE"),
    ("install.llama_build_common", "LLAMA_BUILD_COMMON"),
    ("install.llama_build_tools", "LLAMA_BUILD_TOOLS"),
    ("install.llama_build_mtmd", "LLAMA_BUILD_MTMD"),
    ("install.llama_build_tests", "LLAMA_BUILD_TESTS"),
    ("install.llama_build_examples", "LLAMA_BUILD_EXAMPLES"),
    ("install.llama_build_server", "LLAMA_BUILD_SERVER"),
    ("install.llama_curl", "LLAMA_CURL"),
    ("install.llama_openssl", "LLAMA_OPENSSL"),
    ("install.llama_subprocess", "LLAMA_SUBPROCESS"),
    ("install.mtmd_video", "MTMD_VIDEO"),
    ("install.brain_build_shared", "BUILD_SHARED_LIBS"),
    ("install.ggml_ccache", "GGML_CCACHE"),
)
CUDA_DEFS = (
    ("install.ggml_cuda_force_mmq", "GGML_CUDA_FORCE_MMQ"),
    ("install.ggml_cuda_force_cublas", "GGML_CUDA_FORCE_CUBLAS"),
    ("install.ggml_cuda_fa", "GGML_CUDA_FA"),
    ("install.ggml_cuda_fa_all_quants", "GGML_CUDA_FA_ALL_QUANTS"),
    ("install.ggml_cuda_graphs", "GGML_CUDA_GRAPHS"),
    ("install.ggml_cuda_nccl", "GGML_CUDA_NCCL"),
)
EAR_SWITCHES = (
    ("install.ear_asr_only", "-AsrOnly"),
    ("install.ear_grpc", "-Grpc"),
    ("install.ear_nmt", "-Nmt"),
    ("install.ear_flashlight", "-Flashlight"),
    ("install.ear_http", "-Http"),
    ("install.ear_http_tls", "-HttpTls"),
    ("install.ear_tts_ja", "-TtsJa"),
    ("install.ear_tts_zh", "-TtsZh"),
    ("install.ear_tests", "-Tests"),
    ("install.ear_cublas_shim", "-CublasShim"),
)


def load_trident() -> dict:
    path = ROOT / "trident.txt"
    if not path.is_file():
        raise SystemExit("trident.txt missing")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    out = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.startswith("\ufeff"):
            line = line.lstrip("\ufeff")
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        if len(stripped) >= 2 and stripped.endswith("<<"):
            key = stripped[:-2].rstrip(" \t")
            body = []
            while index < len(lines):
                part = lines[index]
                index += 1
                if part == "<<":
                    break
                body.append(part)
            out[key] = "\n".join(body)
            continue
        space = stripped.find(" ")
        if space < 0:
            out[stripped] = ""
        else:
            out[stripped[:space]] = stripped[space + 1 :]
    return out


def need(key: str) -> str:
    if key not in FILE or FILE[key] == "":
        raise SystemExit("trident.txt missing " + key)
    return FILE[key]


def opt(key: str) -> str:
    return FILE.get(key, "")


def on(key: str) -> bool:
    text = need(key)
    if text == "on":
        return True
    if text == "off":
        return False
    raise SystemExit("trident.txt " + key + " must be on or off")


def cmake_on(key: str) -> str:
    return "ON" if on(key) else "OFF"


def place(key: str) -> Path:
    return (ROOT / need(key)).resolve()


def stamps() -> Path:
    path = place("install.cache") / "stamps"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"


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


def pip(py: Path, name: str, args: list[str]) -> None:
    stamp = stamps() / (name + ".json")
    payload = {"args": args}
    if matches(stamp, payload):
        print("skip " + name, flush=True)
        return
    print("install " + name, flush=True)
    run([str(py), "-m", "pip", "install", "--disable-pip-version-check", *args])
    atomic_json(stamp, payload)


def python_packages(py: Path) -> None:
    packages = [line.strip() for line in need("install.pip").splitlines() if line.strip()]
    args = packages + [need("install.torch"), "--index-url", need("install.torch_index"), "--extra-index-url", need("install.torch_extra_index")]
    pip(py, "tts-packages", args)


def pin(home: Path, url: str, rev: str, track: str) -> str:
    if not (home / ".git").exists():
        run(["git", "clone", "--filter=blob:none", url, str(home)])
    if rev:
        run(["git", "-C", str(home), "fetch", "origin", rev, "--depth", "1"])
        run(["git", "-C", str(home), "checkout", "--detach", rev])
    elif track:
        run(["git", "-C", str(home), "fetch", "origin", track])
        run(["git", "-C", str(home), "checkout", track])
        run(["git", "-C", str(home), "pull", "--ff-only", "origin", track])
    return subprocess.run(["git", "-C", str(home), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def pin_sources() -> None:
    pin(place("install.src_ggml"), need("install.ggml_repo"), need("install.ggml_rev"), "")
    pin(place("install.src_llama"), need("install.llama_repo"), opt("install.llama_rev"), "master")
    pin(place("install.src_nemo"), need("install.nemo_repo"), opt("install.nemo_rev"), "")


def msvc_arch() -> str:
    global ARCH_FLAG
    if ARCH_FLAG is not None:
        return ARCH_FLAG
    place("install.cache").mkdir(parents=True, exist_ok=True)
    out = place("install.cache") / "HostCpu.generated.cmake"
    ps = shutil.which("powershell") or "powershell.exe"
    run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "gemma" / "scripts" / "detect_cpu.ps1"), "-OutFile", str(out)])
    text = out.read_text(encoding="utf-8-sig")
    found = re.search(r'GEMMA_MSVC_ARCH_FLAG "([^"]*)"', text)
    detected = found.group(1) if found else ""
    ARCH_FLAG = opt("install.msvc_arch") or detected
    return ARCH_FLAG


def host_isa() -> str:
    msvc_arch()
    text = (place("install.cache") / "HostCpu.generated.cmake").read_text(encoding="utf-8-sig")
    found = re.search(r'GEMMA_HOST_ISA "([^"]*)"', text)
    return (found.group(1) if found else "host").lower()


def find_vulkan() -> Path:
    given = opt("install.vulkan_sdk")
    if given:
        path = Path(given)
        return path if path.is_absolute() else (ROOT / path).resolve()
    hits = list(Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"))
    if not hits:
        raise SystemExit("Vulkan SDK not found")

    def version(path: Path):
        return tuple(int(part) for part in re.findall(r"\d+", path.parts[-3]))

    return max(hits, key=version).parents[1]


def gemma_backend() -> str:
    global BACKEND
    if BACKEND:
        return BACKEND
    choice = need("install.gemma_backend")
    if choice != "auto":
        if choice not in ("cuda", "vulkan"):
            raise SystemExit("trident.txt install.gemma_backend must be auto, cuda, or vulkan")
        BACKEND = choice
        return BACKEND
    ps = shutil.which("powershell") or "powershell.exe"
    out = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "gemma" / "scripts" / "detect_gpu.ps1")], check=True, capture_output=True, text=True)
    for line in reversed(out.stdout.splitlines()):
        if line.strip() in ("cuda", "vulkan"):
            BACKEND = line.strip()
            return BACKEND
    raise SystemExit("detect_gpu.ps1 did not print cuda or vulkan")


def pe_import_dlls(path: Path) -> set[str]:
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise SystemExit("not a PE " + path.name)
    offset = int.from_bytes(data[0x3C:0x40], "little")
    if data[offset : offset + 4] != b"PE\0\0":
        raise SystemExit("not a PE " + path.name)
    sections = int.from_bytes(data[offset + 6 : offset + 8], "little")
    opt_size = int.from_bytes(data[offset + 20 : offset + 22], "little")
    opt = offset + 24
    magic = int.from_bytes(data[opt : opt + 2], "little")
    data_directory = opt + (112 if magic == 0x20B else 96)
    if magic not in (0x10B, 0x20B):
        raise SystemExit("unknown PE " + path.name)
    import_rva = int.from_bytes(data[data_directory + 8 : data_directory + 12], "little")
    section = opt + opt_size

    def rva_off(rva: int) -> int:
        for index in range(sections):
            start = section + index * 40
            virtual = int.from_bytes(data[start + 12 : start + 16], "little")
            raw_size = int.from_bytes(data[start + 16 : start + 20], "little")
            raw = int.from_bytes(data[start + 20 : start + 24], "little")
            span = max(int.from_bytes(data[start + 8 : start + 12], "little"), raw_size)
            if virtual <= rva < virtual + span:
                return raw + (rva - virtual)
        raise SystemExit("import rva missing in " + path.name)

    if import_rva == 0:
        return set()
    cursor = rva_off(import_rva)
    names = set()
    while True:
        name_rva = int.from_bytes(data[cursor + 12 : cursor + 16], "little")
        if name_rva == 0:
            break
        start = rva_off(name_rva)
        end = data.index(b"\0", start)
        names.add(data[start:end].decode("ascii").lower())
        cursor += 20
    return names


def windows_mt() -> Path:
    return max(Path("C:/Program Files (x86)/Windows Kits/10/bin").glob("*/x64/mt.exe"), key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parts[-3])))


def embed_utf8(exe: Path) -> None:
    merged = exe.with_suffix(".manifest")
    manifest = str((ROOT / need("install.utf8_manifest")).resolve())
    mt = str(windows_mt())
    run([mt, "-nologo", f"-inputresource:{exe};#1", "-manifest", manifest, "-out:" + str(merged)])
    run([mt, "-nologo", "-manifest", str(merged), f"-outputresource:{exe};#1"])
    merged.unlink()


def build_mouth(sdk: Path) -> None:
    build = place("install.build_mouth")
    arch = msvc_arch()
    defs = [
        f"-DGGML_VULKAN={cmake_on('install.mouth_ggml_vulkan')}",
        f"-DGGML_CPU={cmake_on('install.mouth_ggml_cpu')}",
        f"-DGGML_OPENMP={cmake_on('install.mouth_ggml_openmp')}",
        f"-DGGML_BUILD_TESTS={cmake_on('install.mouth_ggml_build_tests')}",
        f"-DGGML_BUILD_EXAMPLES={cmake_on('install.mouth_ggml_build_examples')}",
        f"-DBUILD_SHARED_LIBS={cmake_on('install.mouth_build_shared')}",
        "-DTRIDENT_CONFIG=" + need("install.config"),
        "-DGGML_SRC=" + str(place("install.src_ggml")),
        "-DTRIDENT_UTF8_MANIFEST=" + str((ROOT / need("install.utf8_manifest")).resolve()),
        "-DTRIDENT_MOUTH_COMPILE=" + need("install.mouth_compile"),
        "-DTRIDENT_MOUTH_DEFINITIONS=" + need("install.mouth_definitions"),
        "-DMSVC_ARCH_FLAG=" + arch,
        f"-DVulkan_INCLUDE_DIR={sdk / 'Include'}",
        f"-DVulkan_LIBRARY={sdk / 'Lib' / 'vulkan-1.lib'}",
        f"-DVulkan_GLSLC_EXECUTABLE={sdk / 'Bin' / 'glslc.exe'}",
    ]
    outputs = tuple(build / "bin" / name for name in ("chatterbox.exe", "chatterbox-bake.exe", "ear.exe"))
    wanted = {"ggml": need("install.ggml_rev"), "cmake": defs, "source": digest(ROOT / "CMakeLists.txt", ROOT / "src")}
    stamp = stamps() / "mouth.json"
    if not matches(stamp, wanted, *outputs):
        print("install mouth", flush=True)
        run(["cmake", "-S", str(ROOT), "-B", str(build), "-G", need("install.generator"), "-A", need("install.arch"), *defs])
        run(["cmake", "--build", str(build), "--config", need("install.config"), "--target", "chatterbox", "chatterbox-bake", "ear", "--parallel", need("install.parallel")])
        atomic_json(stamp, wanted)
    else:
        print("skip mouth", flush=True)
    for exe in outputs:
        imports = pe_import_dlls(exe)
        if "ggml.dll" in imports or "ggml-base.dll" in imports:
            raise SystemExit(exe.name + " links ggml.dll; the mouth must stay static so the ear can keep its own ggml.dll")
        dest = ROOT / exe.name
        shutil.copy2(exe, dest)
        embed_utf8(dest)


def build_ear() -> None:
    home = place("install.src_nemo")
    build = place("install.build_ear")
    exe = build / "bin" / "nemo-speech.exe"
    rev = subprocess.run(["git", "-C", str(home), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    choice = {key: FILE[key] for key in FILE if key.startswith("install.ear_") or key.startswith("install.nemo_")}
    wanted = {"rev": rev, "build": choice, "dir": str(build)}
    stamp = stamps() / "ear.json"
    if not matches(stamp, wanted, exe):
        print("install ear", flush=True)
        ps = shutil.which("powershell") or "powershell.exe"
        command = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(home / "scripts" / "windows" / "build.ps1"), "-Backend", need("install.ear_backend"), "-Profile", need("install.ear_profile"), "-Config", need("install.ear_config"), "-CudaArch", need("install.ear_cuda_arch"), "-Architecture", need("install.ear_architecture"), "-Compiler", need("install.ear_compiler"), "-Jobs", need("install.ear_jobs"), "-BuildDir", str(build)]
        if opt("install.ear_vcpkg_root"):
            command += ["-VcpkgRoot", opt("install.ear_vcpkg_root")]
        if opt("install.ear_vcpkg_triplet"):
            command += ["-VcpkgTriplet", opt("install.ear_vcpkg_triplet")]
        for key, switch in EAR_SWITCHES:
            if on(key):
                command.append(switch)
        run(command, cwd=home)
        atomic_json(stamp, wanted)
    else:
        print("skip ear", flush=True)
    built = exe.parent
    for item in built.iterdir():
        if item.suffix.lower() in {".exe", ".dll"}:
            dest = ROOT / item.name
            shutil.copy2(item, dest)
            if dest.suffix.lower() == ".exe":
                embed_utf8(dest)


def build_gemma(sdk: Path) -> None:
    backend = gemma_backend()
    build = place("install.build_gemma")
    exe = build / need("install.config") / "gemma-brain.exe"
    llama = subprocess.run(["git", "-C", str(place("install.src_llama")), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    defs = [f"{cmake}={cmake_on(key)}" for key, cmake in BRAIN_DEFS]
    defs.append("GEMMA_BACKEND=" + backend)
    defs.append("GGML_CUDA=" + ("ON" if backend == "cuda" else "OFF"))
    defs.append("GGML_VULKAN=" + ("ON" if backend == "vulkan" else "OFF"))
    defs.append("LLAMA_SRC=" + str(place("install.src_llama")))
    defs.append("TRIDENT_UTF8_MANIFEST=" + str((ROOT / need("install.utf8_manifest")).resolve()))
    defs.append("GEMMA_COMPILE=" + need("install.gemma_compile"))
    defs.append("GEMMA_LINK=" + need("install.gemma_link"))
    defs.append("GEMMA_DEFINITIONS=" + need("install.gemma_definitions"))
    defs.append("GEMMA_MSVC_ARCH_FLAG=" + msvc_arch())
    if backend == "cuda":
        defs.append("CMAKE_CUDA_ARCHITECTURES=" + need("install.cuda_architectures"))
        defs.extend(f"{cmake}={cmake_on(key)}" for key, cmake in CUDA_DEFS)
    wanted = {
        "llama": llama,
        "backend": backend,
        "cmake": defs,
        "source": digest(ROOT / "gemma" / "CMakeLists.txt", ROOT / "gemma" / "src" / "brain.cpp", ROOT / "gemma" / "cmake" / "HostCpu.cmake", ROOT / "gemma" / "scripts", ROOT / "src" / "common" / "config.h"),
    }
    stamp = stamps() / "gemma.json"
    if not matches(stamp, wanted, exe):
        print("install gemma-brain", flush=True)
        ps = shutil.which("powershell") or "powershell.exe"
        command = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "gemma" / "scripts" / "build.ps1"), "-BuildDir", str(build), "-BrainParallel", need("install.brain_parallel"), "-CudaCodegenParallel", need("install.cuda_codegen_parallel")]
        if backend == "vulkan":
            command += ["-VulkanSdk", str(sdk)]
        env = os.environ.copy()
        env["TRIDENT_GENERATOR"] = need("install.generator")
        env["TRIDENT_ARCH"] = need("install.arch")
        env["TRIDENT_GEMMA_BACKEND"] = backend
        env["TRIDENT_CUDA_MAX"] = need("install.cuda_max")
        env["TRIDENT_CUDA_ROOT"] = need("install.cuda_root")
        env["TRIDENT_CUDA_TOOLSET"] = need("install.cuda_toolset")
        env["TRIDENT_GEMMA_DEFS"] = "\n".join(defs)
        run(command, cwd=ROOT / "gemma", env=env)
        atomic_json(stamp, wanted)
    else:
        print("skip gemma-brain", flush=True)
    dest = ROOT / "gemma-brain.exe"
    shutil.copy2(exe, dest)
    embed_utf8(dest)


def policy(path: Path, default: str) -> dict:
    return {"default": default, "rules": json.loads(path.read_text(encoding="utf-8"))["rules"]}


def gguf_name(kind: str, family: str, spec: dict) -> Path:
    digest8 = hashlib.sha256(json.dumps(spec["rules"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]
    path = place("install.cache") / "gguf" / f"chatterbox-{kind}-{family}-{spec['default']}-{digest8}.gguf"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def checkpoints(cfg: Variant) -> Path:
    home = place("install.cache") / ARCH[cfg.architecture]["ckpt"]
    home.mkdir(parents=True, exist_ok=True)
    root_url = need("install.hf_" + cfg.name)
    assets = [(asset, root_url + "/" + asset) for asset in cfg.assets]
    if all((home / name).is_file() for name, _ in assets):
        print("skip checkpoints " + cfg.name, flush=True)
        return home
    print("install checkpoints " + cfg.name, flush=True)
    for name, url in assets:
        if not (home / name).is_file():
            download(url, home / name)
    return home


def convert(cfg: Variant, py: Path, ckpt: Path) -> tuple[Path, Path, list[dict]]:
    family = ARCH[cfg.architecture]
    t3_policy = policy(ROOT / need("install.t3_quant_policy"), need("install.t3_weight_type"))
    s3_policy = policy(ROOT / need("install.s3_quant_policy"), need("install.s3_weight_type"))
    t3 = gguf_name("t3", cfg.name, t3_policy)
    s3 = gguf_name("s3gen", family["s3_family"], s3_policy)
    jobs = [
        (t3, {"kind": "t3", "policy": t3_policy, "t3_ckpt": cfg.t3_ckpt, "converter": digest(ROOT / "scripts" / "convert_t3.py", ROOT / "scripts" / "quant.py")}, [str(py), str(ROOT / "scripts" / "convert_t3.py"), str(ckpt), str(t3.with_suffix(".gguf.converting")), cfg.t3_ckpt, "--matrix-type", need("install.t3_weight_type"), "--quant-policy", str(ROOT / need("install.t3_quant_policy")), "--s3-checkpoint", family["s3_checkpoint"]]),
        (s3, {"kind": "s3", "policy": s3_policy, "checkpoint": family["s3_checkpoint"], "converter": digest(ROOT / "scripts" / "convert_s3.py", ROOT / "scripts" / "quant.py")}, [str(py), str(ROOT / "scripts" / "convert_s3.py"), str(ckpt), str(s3.with_suffix(".gguf.converting")), "--checkpoint", family["s3_checkpoint"], "--weight-type", need("install.s3_weight_type"), "--quant-policy", str(ROOT / need("install.s3_quant_policy"))]),
    ]
    contracts = []
    for dest, wanted, cmd in jobs:
        stamp = stamps() / (dest.stem + ".json")
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


def bake_voice(cfg: Variant, base_t3: Path, base_s3: Path, contracts: list[dict]) -> None:
    reference = (ROOT / need("install.reference")).resolve()
    t3_out = ROOT / need(cfg.name + ".t3")
    s3_out = ROOT / need(cfg.name + ".s3")
    stamp = stamps() / (cfg.name + "-voice.json")
    wanted = {"variant": cfg.name, "conversions": contracts, "reference": {"path": str(reference), "bytes": reference.stat().st_size, "mtime": reference.stat().st_mtime_ns}}
    if matches(stamp, wanted, t3_out, s3_out):
        print("skip voice " + cfg.name, flush=True)
        return
    print("install voice " + cfg.name, flush=True)
    temp = place("install.cache") / "baking" / cfg.name
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    shutil.copy2(base_t3, temp / "t3.gguf")
    shutil.copy2(base_s3, temp / "s3.gguf")
    run([str(ROOT / "chatterbox-bake.exe"), str(temp / "t3.gguf"), str(temp / "s3.gguf"), str(reference)], cwd=ROOT)
    shutil.copy2(temp / "t3.gguf", t3_out)
    shutil.copy2(temp / "s3.gguf", s3_out)
    shutil.rmtree(temp)
    atomic_json(stamp, wanted)


def install_voice(name: str) -> None:
    cfg = VARIANTS[name]
    ckpt = checkpoints(cfg)
    t3, s3, contracts = convert(cfg, venv_python(), ckpt)
    bake_voice(cfg, t3, s3, contracts)


def fetch_model(name_key: str, url_key: str, runtime_key: str) -> None:
    if need(name_key) != need(runtime_key):
        raise SystemExit(name_key + " and " + runtime_key + " must name the same file")
    dest = ROOT / need(name_key)
    url = need(url_key)
    stamp = stamps() / (dest.name + ".download.json")
    wanted = {"url": url}
    if matches(stamp, wanted, dest):
        print("skip " + dest.name, flush=True)
        return
    print("install " + dest.name, flush=True)
    download(url, dest)
    atomic_json(stamp, wanted)


def publish() -> None:
    if not on("install.publish"):
        return
    tag = "trident-" + gemma_backend() + "-" + host_isa()
    notes = "Executables for this computer. Runtime knobs are trident.txt."
    viewed = subprocess.run(["gh", "release", "view", tag], capture_output=True, text=True)
    if viewed.returncode != 0:
        run(["gh", "release", "create", tag, "--title", tag, "--notes", notes])
    files = [str(path) for path in ROOT.iterdir() if path.suffix.lower() in {".exe", ".dll"}]
    run(["gh", "release", "upload", tag, *files, "--clobber"])
    print("published " + tag, flush=True)


def main() -> None:
    global FILE
    if len(sys.argv) != 1:
        raise SystemExit("edit trident.txt")
    created = not venv_python().is_file()
    ensure_venv()
    if Path(sys.executable).resolve() != venv_python().resolve():
        env = os.environ.copy()
        if created:
            env["TRIDENT_VENV_NEW"] = "1"
        raise SystemExit(subprocess.call([str(venv_python()), *sys.argv], env=env))
    print("install venv" if os.environ.pop("TRIDENT_VENV_NEW", None) == "1" else "skip venv", flush=True)
    FILE = load_trident()
    python_packages(venv_python())
    pin_sources()
    sdk = find_vulkan()
    build_mouth(sdk)
    build_ear()
    build_gemma(sdk)
    for name in ("nano", "turbo", "v3"):
        install_voice(name)
    fetch_model("install.text_model_name", "install.text_model_url", "gemma.model")
    fetch_model("install.mmproj_name", "install.mmproj_url", "gemma.mmproj")
    fetch_model("install.ear_gguf_name", "install.ear_gguf_url", "ear.model")
    publish()


if __name__ == "__main__":
    main()
