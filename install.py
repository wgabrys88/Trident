import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def save(url, path, timeout):
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "trident-install"})
    with urllib.request.urlopen(request, timeout=timeout) as source, path.open("wb") as target:
        shutil.copyfileobj(source, target)


def unpack(url, target, timeout):
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        archive = folder / "pack.zip"
        save(url, archive, timeout)
        stage = folder / "pack"
        with zipfile.ZipFile(archive) as packed:
            packed.extractall(stage)
        children = list(stage.iterdir())
        source = children[0] if len(children) == 1 and children[0].is_dir() else stage
        if target.exists():
            for item in source.iterdir():
                dest = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
        else:
            shutil.copytree(source, target)


def main():
    if sys.platform != "win32" or sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32:
        raise RuntimeError("Install needs 64-bit Python 3.11 on Windows")
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    timeout = float(cfg["limits"]["install_timeout"])
    artifacts = ROOT / "artifacts"
    artifacts.mkdir()
    subprocess.run([sys.executable, "-m", "venv", str(artifacts / "python")], check=True)
    python = artifacts / "python" / "Scripts" / "python.exe"
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
    for name, url in cfg["fetch"]["models"].items():
        save(url, artifacts / name, timeout)
    for key, url in cfg["fetch"]["archives"].items():
        unpack(url, ROOT / cfg["fetch"]["unpack"][key], timeout)
    needed = [python, ROOT / cfg["ears"]["model"], ROOT / cfg["ears"]["library"]]
    needed += [ROOT / part for part in cfg["voice"]["command"] if str(part).startswith("artifacts/")]
    llama = cfg["llama"]
    needed += [ROOT / llama["bin"], ROOT / llama["weights"], ROOT / llama["mmproj"]]
    needed.append(ROOT / "artifacts" / "llama" / "cudart64_12.dll")
    for path in needed:
        if not path.is_file():
            raise RuntimeError(f"Install did not produce {path.name}")
    print(r"Installed. Start with: artifacts\python\Scripts\python.exe run.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
