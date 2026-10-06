import json, os, shutil, subprocess, sys, urllib.request, zipfile
from pathlib import Path
from core import ACCELERATOR, BIN, CONFIG, HARDWARE, MODELS, ROOT

CFG = CONFIG["install"]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

def download(url, path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Trident"}), timeout=60) as source:
            with temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
        temporary.replace(path)

def binary(name, executable, urls, version):
    target, stage = BIN / name, BIN / (name + ".stage")
    marker = target / "release.txt"
    if (target / executable).exists() and marker.exists() and marker.read_text().strip() == version:
        return
    if stage.exists():
        shutil.rmtree(stage)
    for url in urls:
        archive = BIN / url.rsplit("/", 1)[-1]
        download(url, archive)
        with zipfile.ZipFile(archive) as packed:
            packed.extractall(stage)
        archive.unlink()
    exe = next(stage.rglob(executable.name))
    if target.exists():
        shutil.rmtree(target)
    if len(executable.parts) == 1:
        target.mkdir()
        for path in stage.rglob("*"):
            if path.is_file() and (path.parent == exe.parent or path.suffix.lower() == ".dll"):
                shutil.copy2(path, target / path.name)
    else:
        shutil.copytree(exe.parents[len(executable.parts) - 1], target)
    shutil.rmtree(stage)
    marker.write_text(version + "\n", encoding="utf-8")

if __name__ == "__main__":
    os.chdir(ROOT)
    if not PYTHON.exists():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise SystemExit(subprocess.call([str(PYTHON), __file__]))
    print(ACCELERATOR, flush=True)
    pip = [str(PYTHON), "-m", "pip", "install", "--disable-pip-version-check"]
    subprocess.run(pip + ["--force-reinstall", *CFG["torch_packages"], "--index-url", HARDWARE["torch_index"]], check=True)
    if "sdk" in HARDWARE:
        subprocess.run(pip + HARDWARE["sdk"], check=True)
    subprocess.run(pip + ["-r", str(ROOT / "requirements.txt")], check=True)
    with urllib.request.urlopen(urllib.request.Request(
            "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/" + CFG["llama_tag"],
            headers={"User-Agent": "Trident"}), timeout=30) as response:
        assets = {item["name"]: item["browser_download_url"] for item in json.load(response)["assets"]}
    binary("llama", Path("llama-server.exe"),
        [assets[name.format(tag=CFG["llama_tag"])] for name in HARDWARE["llama_assets"]], CFG["llama_tag"] + "-" + ACCELERATOR)
    download(CFG["template_url"], BIN / "llama" / "gemma.jinja")
    binary("nemo-speech", Path("bin/nemo-speech.exe"), [CFG["nemo_url"]], "v0.1.0")
    for key, path in (("gemma_url", MODELS / CONFIG["brain"]["model"]), ("mmproj_url", MODELS / CONFIG["brain"]["mmproj"]),
                      ("ear_url", MODELS / CONFIG["ears"]["model"]), ("silero_url", MODELS / "silero_vad.onnx")):
        download(CFG[key], path)
    from huggingface_hub import snapshot_download
    snapshot_download("ResembleAI/chatterbox-turbo", local_dir=MODELS / CONFIG["mouth"]["model"],
                      allow_patterns=["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"])
