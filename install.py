import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from next import BIN, CONFIG, MODELS, ROOT

INSTALL = CONFIG["install"]
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
def binary(name, executable, sources, release=None):
    target = BIN / name
    executable = Path(executable)
    version = target / "release.txt"
    if (target / executable).is_file() and (release is None or
            version.is_file() and version.read_text().strip() == release):
        say(f"skip {name}")
        return
    stage = BIN / (name + ".stage")
    shutil.rmtree(stage, ignore_errors=True)
    for url in sources():
        archive = BIN / url.rsplit("/", 1)[-1]
        download(url, archive)
        with zipfile.ZipFile(archive) as packed:
            packed.extractall(stage)
        archive.unlink()
    exe = next(stage.rglob(executable.name))
    shutil.rmtree(target, ignore_errors=True)
    if len(executable.parts) == 1:
        target.mkdir(parents=True)
        for item in stage.rglob("*"):
            if item.is_file() and (item.parent == exe.parent or item.suffix.lower() == ".dll"):
                shutil.copy2(item, target / item.name)
    else:
        shutil.copytree(exe.parents[len(executable.parts) - 1], target)
    shutil.rmtree(stage, ignore_errors=True)
    if release is not None:
        version.write_text(release + "\n", encoding="utf-8")
def llama_sources():
    request = urllib.request.Request("https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/" + INSTALL["llama_tag"],
                                     headers={"User-Agent": "trident", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        assets = {a["name"]: a["browser_download_url"] for a in json.load(response)["assets"]}
    wanted = [name for name in assets if name.endswith(INSTALL["llama_asset"]) and name.startswith("llama-")]
    if not wanted:
        raise SystemExit(f"release {INSTALL['llama_tag']} has no asset ending in {INSTALL['llama_asset']}: {sorted(assets)}")
    return [assets[wanted[0]], assets[INSTALL["cudart_asset"]]]
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
    binary("llama", "llama-server.exe", llama_sources, INSTALL["llama_tag"])
    binary("nemo-speech", "bin/nemo-speech.exe", lambda: [INSTALL["nemo_url"]])
    models()
    say("done. next: put reference.wav here if it is missing, then  python trident.py")
if __name__ == "__main__":
    main()
