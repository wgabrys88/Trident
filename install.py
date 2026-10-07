import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from store import CONFIG, ROOT


def download(url, path):
    request = urllib.request.Request(url, headers={"User-Agent": "Trident"})
    temporary = path.with_name(path.name + ".part")
    with urllib.request.urlopen(request, timeout=600) as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target)
    temporary.replace(path)


def archive(url, name, executable):
    artifacts = ROOT / "artifacts"
    path = artifacts / (name + ".zip")
    stage = artifacts / (name + "-download")
    if stage.exists():
        shutil.rmtree(stage)
    download(url, path)
    with zipfile.ZipFile(path) as source:
        source.extractall(stage)
    [binary] = stage.rglob(executable.name)
    target = artifacts / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(binary.parents[len(executable.parts) - 1], target)
    shutil.rmtree(stage)
    path.unlink()


def install():
    if sys.platform != "win32" or sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32:
        raise RuntimeError("Install with 64-bit Python 3.11 on Windows")
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    environment = artifacts / "python"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    interpreter = environment / "Scripts" / "python.exe"
    subprocess.run([str(interpreter), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
    config = CONFIG["install"]
    request = urllib.request.Request(
        "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/" + config["llama_tag"],
        headers={"User-Agent": "Trident"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        assets = {asset["name"]: asset["browser_download_url"] for asset in json.load(response)["assets"]}
    destination = artifacts / "llama"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir()
    for index, name in enumerate(config["llama_assets"]):
        stage = f"llama-download-{index}"
        if (artifacts / stage).exists():
            shutil.rmtree(artifacts / stage)
        packed = artifacts / (stage + ".zip")
        download(assets[name], packed)
        with zipfile.ZipFile(packed) as source:
            source.extractall(artifacts / stage)
        for item in (artifacts / stage).rglob("*"):
            if item.is_file():
                shutil.move(str(item), destination / item.name)
        shutil.rmtree(artifacts / stage)
        packed.unlink()
    for name, url in config["models"].items():
        download(url, artifacts / name)
    archive(config["nemo_url"], "nemo-cpu", Path("bin/nemo-speech.exe"))
    archive(config["voice_url"], "voice", Path("crispasr.exe"))
    print(f"Installed. Start with: {interpreter} {ROOT / 'trident.py'}")


if __name__ == "__main__":
    try:
        install()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
