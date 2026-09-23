import hashlib, json, os, re, shutil, subprocess, sys, urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
CMAKE_GENERATOR, CMAKE_ARCH = "Visual Studio 17 2022", "x64"
PYTORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
BRAIN_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/0314792d7f1f7e229411f620751375812bb9faf2/gemma-4-E2B-it-Q4_K_M.gguf"
BRAIN_FILE = "brain-gemma-4-e2b-it-q4_k_m.gguf"
EAR_REPO = "https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/ea30d66debe3740a08b573244286791d423d6b3e"
EAR_DIR = "nemotron-3.5-asr-streaming-0.6b"
EAR_FILES = ("config.json", "generation_config.json", "processor_config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors")


@dataclass(frozen=True)
class Variant:
    name: str
    hf: str
    assets: tuple[str, ...]
    t3_ckpt: str
    architecture: str
    external_assets: tuple[tuple[str, str], ...] = ()


_GPT2 = ("s3gen_meanflow.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")
VARIANTS = {
    "nano": Variant("nano", "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64", ("t3_nano_v1.safetensors",) + _GPT2, "t3_nano_v1.safetensors", "gpt2"),
    "turbo": Variant("turbo", "https://huggingface.co/ResembleAI/chatterbox-turbo/resolve/749d1c1a46eb10492095d68fbcf55691ccf137cd", ("t3_turbo_v1.safetensors",) + _GPT2, "t3_turbo_v1.safetensors", "gpt2"),
    "v3": Variant("v3", "https://huggingface.co/ResembleAI/chatterbox/resolve/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18", ("t3_mtl23ls_v3.safetensors", "s3gen.safetensors", "conds.pt", "ve.safetensors", "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json"), "t3_mtl23ls_v3.safetensors", "llama", (("official_mtl_tokenizer.py", "https://raw.githubusercontent.com/resemble-ai/chatterbox/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/models/tokenizers/tokenizer.py"), ("official_mtl_tts.py", "https://raw.githubusercontent.com/resemble-ai/chatterbox/5de7a54aa4e5e2baadb0182dde554908b48b85c2/src/chatterbox/mtl_tts.py"), ("dicta-1.0.int8.onnx", "https://github.com/thewh1teagle/dicta-onnx/releases/download/model-files-v1.0/dicta-1.0.int8.onnx"))),
}
ARCH = {
    "gpt2": {"ckpt": ".ckpt", "s3_checkpoint": "s3gen_meanflow.safetensors", "s3_family": "meanflow"},
    "llama": {"ckpt": ".ckpt-v3", "s3_checkpoint": "s3gen.safetensors", "s3_family": "v3"},
}
FLAGS = [
    ("reference", "reference.wav", "conversion", "both"),
    ("t3-weight-type", "q4_0", "conversion", "both"),
    ("s3-weight-type", "q4_0", "conversion", "both"),
    ("t3-quant-policy", "scripts/quant_t3.json", "conversion", "both"),
    ("s3-quant-policy", "scripts/quant_s3.json", "conversion", "both"),
]


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"


def kill_server() -> None:
    path = MODELS / "server.pid"
    if not path.is_file():
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(record["pid"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        path.unlink(missing_ok=True)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def matches(stamp: Path, payload: dict, *outputs: Path) -> bool:
    return stamp.is_file() and json.loads(stamp.read_text(encoding="utf-8")) == payload and all(path.is_file() for path in outputs)


def kept(name: str, payload: dict, *outputs: Path) -> bool:
    if matches(MODELS / f"{name}.json", payload, *outputs):
        print("skip " + name, flush=True)
        return True
    print("install " + name, flush=True)
    return False


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


class Ggml:
    REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"

    @staticmethod
    def ensure() -> None:
        home = ROOT / "ggml"
        if not (home / ".git").exists():
            run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(home)])
        head = subprocess.run(["git", "-C", str(home), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        if head != Ggml.REV:
            run(["git", "-C", str(home), "fetch", "origin", Ggml.REV, "--depth", "1"])
            run(["git", "-C", str(home), "checkout", "--detach", Ggml.REV])


def build_engine() -> tuple[Path, Path, dict]:
    outputs = ROOT / "build" / "bin" / "chatterbox-server.exe", ROOT / "build" / "bin" / "chatterbox-bake.exe"
    wanted = {"ggml": Ggml.REV, "generator": CMAKE_GENERATOR, "architecture": CMAKE_ARCH, "source": digest(ROOT / "CMakeLists.txt", ROOT / "src")}
    stamp = MODELS / "build-contract.json"
    if stamp.is_file() and json.loads(stamp.read_text(encoding="utf-8")) == wanted and all(path.is_file() for path in outputs):
        print("skip ggml", flush=True)
        print("skip engine", flush=True)
        return *outputs, wanted
    print("install ggml", flush=True)
    Ggml.ensure()
    print("install engine", flush=True)
    kill_server()
    sdk = max(Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"), key=lambda path: tuple(map(int, re.findall(r"\d+", path.parts[-3])))).parents[1]
    build = ROOT / "build"
    run(["cmake", "-S", str(ROOT), "-B", str(build), "-G", CMAKE_GENERATOR, "-A", CMAKE_ARCH, f"-DVulkan_INCLUDE_DIR={sdk / 'Include'}", f"-DVulkan_LIBRARY={sdk / 'Lib/vulkan-1.lib'}", f"-DVulkan_GLSLC_EXECUTABLE={sdk / 'Bin/glslc.exe'}"])
    run(["cmake", "--build", str(build), "--config", "Release", "--target", "chatterbox-server", "chatterbox-bake", "--parallel", "2"])
    atomic_json(stamp, wanted)
    return *outputs, wanted


def launch_conversion(cfg: Variant):
    conv = {}
    for name, default, group, arch in FLAGS:
        if arch in ("both", cfg.architecture) and group == "conversion":
            conv[name] = default
    return conv


def policy(path: Path, default: str) -> dict:
    return {"default": default, "rules": json.loads(path.read_text(encoding="utf-8"))["rules"]}


def gguf_name(kind: str, family: str, spec: dict) -> Path:
    digest8 = hashlib.sha256(json.dumps(spec["rules"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:8]
    return MODELS / f"chatterbox-{kind}-{family}-{spec['default']}-{digest8}.gguf"


def checkpoints(cfg: Variant) -> Path:
    home = ROOT / ARCH[cfg.architecture]["ckpt"]
    home.mkdir(parents=True, exist_ok=True)
    assets = [(asset, cfg.hf + "/" + asset) for asset in cfg.assets] + list(cfg.external_assets)
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
    kill_server()
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


def python_packages(py: Path, ear: bool, mouth: bool) -> None:
    names = ["numpy"]
    if ear:
        names += ["transformers", "sounddevice", "librosa"]
    if mouth:
        names += ["gguf", "safetensors", "librosa", "sounddevice", "tokenizers", "pykakasi", "spacy-pkuseg", "dicta-onnx", "add-stress-to-epub"]
    names = list(dict.fromkeys(names))
    args = specs(*names)
    if ear or mouth:
        args += ["torch==2.6.0", "--index-url", PYTORCH_CPU_INDEX, "--extra-index-url", "https://pypi.org/simple"]
    pip(py, "packages" if ear and mouth else "asr-packages" if ear else "tts-packages", args)


def install_ear() -> None:
    home = MODELS / "ear" / EAR_DIR
    home.mkdir(parents=True, exist_ok=True)
    files = tuple(home / name for name in EAR_FILES)
    if kept("nemotron", {"repo": EAR_REPO}, *files):
        return
    for name in EAR_FILES:
        print(name, flush=True)
        download(EAR_REPO + "/" + name, home / name)
    atomic_json(MODELS / "nemotron.json", {"repo": EAR_REPO})


def install_mouth(name: str) -> None:
    cfg, py = VARIANTS[name], venv_python()
    server, bake, build = build_engine()
    ckpt = checkpoints(cfg)
    conv = launch_conversion(cfg)
    t3, s3, contracts = convert(cfg, py, ckpt, conv)
    bake_voice(cfg, t3, s3, bake, contracts, build, conv)


def install_brain() -> None:
    py = venv_python()
    env = os.environ.copy()
    env["CMAKE_ARGS"], env["FORCE_CMAKE"] = "-DGGML_VULKAN=ON", "1"
    pip(py, "brain-packages", [*specs("gguf", "llama-cpp-python"), "--no-binary", "llama-cpp-python"], env)
    path = MODELS / BRAIN_FILE
    if not kept("gemma", {"url": BRAIN_URL}, path):
        download(BRAIN_URL, path)
        atomic_json(MODELS / "gemma.json", {"url": BRAIN_URL})


def install_all(name: str) -> None:
    python_packages(venv_python(), True, True)
    install_ear()
    install_mouth(name)
    install_brain()


def main() -> None:
    argv = sys.argv
    created = not venv_python().is_file()
    ensure_venv()
    if Path(sys.executable).resolve() != venv_python().resolve():
        env = os.environ.copy()
        if created:
            env["TRIDENT_VENV_NEW"] = "1"
        raise SystemExit(subprocess.call([str(venv_python()), *argv], env=env))
    print("install venv" if os.environ.pop("TRIDENT_VENV_NEW", None) == "1" else "skip venv", flush=True)
    if len(argv) == 1:
        install_all("nano")
    elif len(argv) == 2 and argv[1] == "ear":
        python_packages(venv_python(), True, False)
        install_ear()
    elif len(argv) == 2 and argv[1] == "brain":
        install_brain()
    elif len(argv) == 3 and argv[1] == "mouth" and argv[2] in VARIANTS:
        python_packages(venv_python(), False, True)
        install_mouth(argv[2])
    elif len(argv) == 2 and argv[1] in VARIANTS:
        install_all(argv[1])
    elif len(argv) == 2 and argv[1] == "all":
        python_packages(venv_python(), True, True)
        install_ear()
        install_brain()
        install_mouth("nano")
        install_mouth("turbo")
        install_mouth("v3")
    else:
        raise SystemExit("usage: python install.py [ear | brain | mouth <variant> | nano | turbo | v3 | all]")


if __name__ == "__main__":
    main()
