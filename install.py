import json
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
INSTALL = CONFIG["install"]
BIN = ROOT / CONFIG["paths"]["bin"]
MODELS = ROOT / CONFIG["paths"]["models"]
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
def say(text: str):
    print(text, flush=True)
def download(url: str, dest: Path):
    if dest.exists():
        say(f"skip {dest.name}")
        return
    say(f"get  {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "trident"})
    with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    part.replace(dest)
def github_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "trident", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
def venv():
    if not VENV_PY.is_file():
        say("make .venv")
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    if Path(sys.executable).resolve() != VENV_PY.resolve():
        raise SystemExit(subprocess.call([str(VENV_PY), __file__]))
def packages():
    pip = [str(VENV_PY), "-m", "pip", "install", "--disable-pip-version-check"]
    subprocess.run(pip + ["torch", "torchaudio", "--index-url", INSTALL["torch_index"]], check=True)
    subprocess.run(pip + ["-r", str(ROOT / "requirements.txt")], check=True)
def llama():
    target = BIN / "llama"
    version = target / "release.txt"
    if (target / "llama-server.exe").is_file() and version.is_file() and version.read_text().strip() == INSTALL["llama_tag"]:
        say("skip llama-server")
        return
    release = github_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/" + INSTALL["llama_tag"])
    assets = {a["name"]: a["browser_download_url"] for a in release["assets"]}
    wanted = [name for name in assets if name.endswith(INSTALL["llama_asset"]) and name.startswith("llama-")]
    if not wanted:
        raise SystemExit(f"release {release['tag_name']} has no asset ending in {INSTALL['llama_asset']}: {sorted(assets)}")
    say(f"llama.cpp {release['tag_name']}")
    stage = BIN / "llama.stage"
    shutil.rmtree(stage, ignore_errors=True)
    for name in (wanted[0], INSTALL["cudart_asset"]):
        archive = BIN / name
        download(assets[name], archive)
        with zipfile.ZipFile(archive) as packed:
            packed.extractall(stage)
        archive.unlink()
    exe = next(stage.rglob("llama-server.exe"))
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for item in stage.rglob("*"):
        if item.is_file() and (item.parent == exe.parent or item.suffix.lower() == ".dll"):
            shutil.copy2(item, target / item.name)
    shutil.rmtree(stage, ignore_errors=True)
    (target / "release.txt").write_text(release["tag_name"] + "\n", encoding="utf-8")
def nemo():
    target = BIN / "nemo-speech"
    if (target / "bin" / "nemo-speech.exe").is_file():
        say("skip nemo-speech")
        return
    archive = BIN / "nemo-speech.zip"
    download(INSTALL["nemo_url"], archive)
    stage = BIN / "nemo.stage"
    shutil.rmtree(stage, ignore_errors=True)
    with zipfile.ZipFile(archive) as packed:
        packed.extractall(stage)
    archive.unlink()
    exe = next(stage.rglob("nemo-speech.exe"))
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(exe.parent.parent, target)
    shutil.rmtree(stage, ignore_errors=True)
def models():
    from huggingface_hub import snapshot_download
    download(INSTALL["gemma_url"], MODELS / CONFIG["brain"]["model"])
    download(INSTALL["mmproj_url"], MODELS / CONFIG["brain"]["mmproj"])
    download(INSTALL["ear_url"], MODELS / CONFIG["ears"]["model"])
    download(INSTALL["silero_url"], MODELS / "silero_vad.onnx")
    snapshot_download("ResembleAI/chatterbox-turbo", local_dir=MODELS / CONFIG["mouth"]["model"],
                      allow_patterns=["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"])
def main():
    os.chdir(ROOT)
    venv()
    packages()
    llama()
    nemo()
    models()
    say("done. next: put reference.wav here if it is missing, then  python trident.py")
if __name__ == "__main__":
    main()
