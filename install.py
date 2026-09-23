import hashlib, json, os, shutil, subprocess, sys, urllib.request
from pathlib import Path
from runtime import MODELS, ROOT, Contract, LaunchArgs, kill, launch_args, venv_python
from settings import ARCHITECTURES, BRAIN, CMAKE_ARCH, CMAKE_GENERATOR, EAR, PYTHON_ENV_BOOTSTRAP, PYTORCH_CPU_INDEX, VARIANTS, Variant
def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)
def digest(*paths) -> str:
    hasher, files = hashlib.sha256(), []
    for item in paths:
        item = Path(item)
        files.append(item) if item.is_file() else files.extend(path for path in item.rglob("*") if path.is_file())
    for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
        hasher.update(path.relative_to(ROOT).as_posix().encode()); hasher.update(path.read_bytes())
    return hasher.hexdigest()
def download(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
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
def pip(py: Path, name: str, args: list[str], env=None):
    MODELS.mkdir(parents=True, exist_ok=True)
    stamp, payload = MODELS / f"{name}.stamp", json.dumps(args)
    if stamp.is_file() and stamp.read_text(encoding="utf-8") == payload:
        print("skip " + name, flush=True)
        return
    print("install " + name, flush=True)
    run([str(py), "-m", "pip", "install", "--disable-pip-version-check", *args], env=env)
    stamp.write_text(payload, encoding="utf-8")
def kept(name: str, payload: dict, *outputs: Path) -> bool:
    stamp, contract = MODELS / f"{name}.json", Contract(payload, *outputs)
    if contract.matches(stamp):
        print("skip " + name, flush=True)
        return True
    print("install " + name, flush=True)
    return False
class GgmlPin:
    REV = "7840aaba1989c6deeefede1d77d5aaf8f52b947e"
    def ensure(self):
        ggml = ROOT / "ggml"
        if not (ggml / ".git").exists():
            run(["git", "clone", "--filter=blob:none", "https://github.com/ggml-org/ggml.git", str(ggml)])
        head = subprocess.run(["git", "-C", str(ggml), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        if head != self.REV:
            run(["git", "-C", str(ggml), "fetch", "origin", self.REV, "--depth", "1"])
            run(["git", "-C", str(ggml), "checkout", "--detach", self.REV])
class EngineBuild:
    TARGETS = ("chatterbox-server", "chatterbox-bake")
    def wanted(self) -> dict:
        return {"ggml": GgmlPin.REV, "generator": CMAKE_GENERATOR, "architecture": CMAKE_ARCH,
                "source": digest(ROOT / "CMakeLists.txt", ROOT / "src")}
    def ensure(self) -> tuple[Path, Path]:
        outputs, wanted, stamp = tuple(ROOT / "build" / "bin" / f"{n}.exe" for n in self.TARGETS), self.wanted(), MODELS / "build-contract.json"
        if Contract(wanted, *outputs).matches(stamp):
            print("skip ggml", flush=True)
            print("skip engine", flush=True)
            return outputs
        print("install ggml", flush=True)
        GgmlPin().ensure()
        print("install engine", flush=True)
        kill(MODELS / "server.pid")
        vulkan = max(Path("C:/VulkanSDK").glob("*/Bin/glslc.exe"),
                     key=lambda path: tuple(map(int, re_digits(path.parts[-3])))).parents[1]
        build = ROOT / "build"
        run(["cmake", "-S", str(ROOT), "-B", str(build), "-G", CMAKE_GENERATOR, "-A", CMAKE_ARCH,
             f"-DVulkan_INCLUDE_DIR={vulkan / 'Include'}", f"-DVulkan_LIBRARY={vulkan / 'Lib/vulkan-1.lib'}",
             f"-DVulkan_GLSLC_EXECUTABLE={vulkan / 'Bin/glslc.exe'}"])
        run(["cmake", "--build", str(build), "--config", "Release", "--target", *self.TARGETS, "--parallel", "2"])
        Contract(wanted, *outputs).write(stamp)
        return outputs
def re_digits(text: str):
    import re
    return re.findall(r"\d+", text)
class Checkpoints:
    def ensure(self, cfg: Variant) -> Path:
        ckpt = ROOT / ARCHITECTURES[cfg.architecture]["ckpt"]
        ckpt.mkdir(parents=True, exist_ok=True)
        assets = [(asset, f"{cfg.hf}/{asset}") for asset in cfg.assets] + list(cfg.external_assets)
        if all((ckpt / name).is_file() for name, _ in assets):
            print("skip checkpoints", flush=True)
            return ckpt
        print("install checkpoints", flush=True)
        for name, source in assets:
            dest = ckpt / name
            if not dest.is_file():
                download(source, dest)
        return ckpt
class Converter:
    def ensure(self, cfg: Variant, py: Path, ckpt: Path, t3: Path, s3: Path, args: LaunchArgs) -> tuple[dict, dict]:
        family, scripts = ARCHITECTURES[cfg.architecture], ROOT / "scripts"
        t3_payload = {"kind": "t3", "policy": args.policy("t3"), "t3_ckpt": cfg.t3_ckpt,
                      "converter": digest(scripts / "convert_t3.py", scripts / "quant.py")}
        s3_payload = {"kind": "s3", "policy": args.policy("s3"), "checkpoint": family["s3_checkpoint"],
                      "converter": digest(scripts / "convert_s3.py", scripts / "quant.py")}
        jobs = (
            (Contract(t3_payload, t3), MODELS / f"{t3.stem}.convert.json", t3,
             [str(py), str(scripts / "convert_t3.py"), str(ckpt), str(t3.with_suffix(".gguf.converting")), cfg.t3_ckpt,
              "--matrix-type", args.t3_weight_type, "--quant-policy", str(args.t3_quant_policy), "--s3-checkpoint", family["s3_checkpoint"]]),
            (Contract(s3_payload, s3), MODELS / f"{s3.stem}.convert.json", s3,
             [str(py), str(scripts / "convert_s3.py"), str(ckpt), str(s3.with_suffix(".gguf.converting")),
              "--checkpoint", family["s3_checkpoint"], "--weight-type", args.s3_weight_type, "--quant-policy", str(args.s3_quant_policy)]),
        )
        for contract, stamp, dest, cmd in jobs:
            if contract.matches(stamp):
                print("skip " + dest.name, flush=True)
                continue
            print("install " + dest.name, flush=True)
            tmp = dest.with_suffix(".gguf.converting")
            run(cmd)
            tmp.replace(dest)
            contract.write(stamp)
        return t3_payload, s3_payload
class VoiceBake:
    def ensure(self, cfg: Variant, reference: Path, base_t3: Path, base_s3: Path, bake: Path,
               t3_contract: dict, s3_contract: dict, build: dict) -> tuple[Path, Path, dict]:
        wanted = {"variant": cfg.name, "t3_conversion": t3_contract, "s3_conversion": s3_contract,
                  "reference": {"path": str(reference.resolve()), "bytes": reference.stat().st_size, "mtime": reference.stat().st_mtime_ns}, "build": build}
        voice_dir, t3, s3, stamp = MODELS / "voices" / cfg.name, MODELS / "voices" / cfg.name / "t3.gguf", MODELS / "voices" / cfg.name / "s3.gguf", MODELS / "voices" / cfg.name / "bake.json"
        if Contract(wanted, t3, s3).matches(stamp):
            print("skip voice", flush=True)
            return t3, s3, wanted
        print("install voice", flush=True)
        kill(MODELS / "server.pid")
        tmp = voice_dir.with_name(voice_dir.name + ".baking")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=False)
        tmp_t3, tmp_s3 = tmp / "t3.gguf", tmp / "s3.gguf"
        shutil.copy2(base_t3, tmp_t3); shutil.copy2(base_s3, tmp_s3)
        run([str(bake), str(tmp_t3), str(tmp_s3), str(reference)], cwd=str(ROOT))
        Contract(wanted, tmp_t3, tmp_s3).write(tmp / "bake.json")
        voice_dir.parent.mkdir(parents=True, exist_ok=True)
        if voice_dir.exists():
            shutil.rmtree(voice_dir)
        tmp.replace(voice_dir)
        return t3, s3, wanted
def ensure_venv() -> Path:
    py, directory = venv_python(), ROOT / ".venv"
    if py.is_file():
        return py
    print("install venv", flush=True)
    if directory.exists():
        shutil.rmtree(directory)
    run([sys.executable, "-m", "venv", str(directory)])
    return py
def python_packages(py: Path, ear: bool, mouth: bool) -> None:
    names = ["numpy"]
    if ear:
        names.append("transformers")
    if mouth:
        names += ["sounddevice", "gguf", "safetensors", "librosa", "tokenizers", "pykakasi", "spacy-pkuseg", "dicta-onnx", "add-stress-to-epub"]
    args = specs(*names)
    if mouth:
        args += [*PYTHON_ENV_BOOTSTRAP["torch"], "--index-url", PYTORCH_CPU_INDEX, "--extra-index-url", "https://pypi.org/simple"]
    pip(py, "packages" if ear and mouth else "asr-packages" if ear else "tts-packages", args)
def install_ear():
    home = MODELS / "ear" / EAR["dir"]
    home.mkdir(parents=True, exist_ok=True)
    files = tuple(home / name for name in EAR["files"])
    if kept("nemotron", {"repo": EAR["repo"]}, *files):
        return
    for name in EAR["files"]:
        print(name, flush=True)
        download(EAR["repo"] + "/" + name, home / name)
    Contract({"repo": EAR["repo"]}, *files).write(MODELS / "nemotron.json")
def install_asr():
    python_packages(venv_python(), True, False)
    install_ear()
def install_mouth(name: str):
    if name not in VARIANTS:
        raise SystemExit("variant is nano, turbo, or v3")
    cfg, py = VARIANTS[name], venv_python()
    MODELS.mkdir(parents=True, exist_ok=True)
    args = launch_args(cfg)
    family = ARCHITECTURES[cfg.architecture]
    engine = EngineBuild()
    server, bake = engine.ensure()
    ckpt = Checkpoints().ensure(cfg)
    base_t3, base_s3 = args.gguf("t3", cfg.name), args.gguf("s3gen", family["s3_family"])
    t3_contract, s3_contract = Converter().ensure(cfg, py, ckpt, base_t3, base_s3, args)
    VoiceBake().ensure(cfg, args.reference, base_t3, base_s3, bake, t3_contract, s3_contract, engine.wanted())
    return server
def install_tts(name: str):
    python_packages(venv_python(), False, True)
    return install_mouth(name)
def install_brain():
    py = venv_python()
    env = os.environ.copy()
    env["CMAKE_ARGS"], env["FORCE_CMAKE"] = "-DGGML_VULKAN=ON", "1"
    pip(py, "brain-packages", [*specs("llama-cpp-python"), "--no-binary", "llama-cpp-python"], env)
    path = MODELS / BRAIN["file"]
    if not kept("gemma", {"url": BRAIN["url"]}, path):
        download(BRAIN["url"], path)
        Contract({"url": BRAIN["url"]}, path).write(MODELS / "gemma.json")
def install_all(name: str):
    print("asr", flush=True)
    python_packages(venv_python(), True, True)
    install_ear()
    print("tts " + name, flush=True)
    install_mouth(name)
    print("brain", flush=True)
    install_brain()
if __name__ == "__main__":
    argv = sys.argv
    created = not venv_python().is_file()
    ensure_venv()
    if Path(sys.executable).resolve() != venv_python().resolve():
        env = os.environ.copy()
        if created:
            env["TRIDENT_VENV_NEW"] = "1"
        raise SystemExit(subprocess.call([str(venv_python()), *argv], env=env))
    if os.environ.pop("TRIDENT_VENV_NEW", None) == "1":
        print("install venv", flush=True)
    else:
        print("skip venv", flush=True)
    if len(argv) == 1:
        install_all("nano")
    elif argv[1] == "asr" and len(argv) == 2:
        install_asr()
    elif argv[1] == "brain" and len(argv) == 2:
        install_brain()
    elif argv[1] == "tts" and len(argv) == 3:
        install_tts(argv[2])
    elif argv[1] == "all" and len(argv) == 2:
        print("asr", flush=True)
        python_packages(venv_python(), True, True)
        install_ear()
        print("brain", flush=True)
        install_brain()
        print("tts turbo", flush=True)
        install_mouth("turbo")
        print("tts v3", flush=True)
        install_mouth("v3")
    elif len(argv) == 2 and argv[1] in VARIANTS:
        install_all(argv[1])
    else:
        raise SystemExit("usage: python install.py [asr | brain | tts <variant> | <variant> | all]")
