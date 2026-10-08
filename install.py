import importlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def save(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "trident-install"})
    with urllib.request.urlopen(request, timeout=3600) as source, path.open("wb") as target:
        shutil.copyfileobj(source, target)


def unpack(url, target):
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        archive = folder / "pack.zip"
        save(url, archive)
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
                    shutil.copy2(item, dest)
        else:
            shutil.copytree(source, target)


def cards():
    for path in sorted(ROOT.glob("mind_*.py")):
        mod = importlib.import_module(path.stem)
        for name, url in getattr(mod, "FETCH", {}).items():
            save(url, ROOT / "artifacts" / name)
        for folder, url in getattr(mod, "ARCHIVES", ()):
            unpack(url, ROOT / "artifacts" / folder)
        for rel in getattr(mod, "NEED", ()):
            if not (ROOT / rel).is_file():
                raise RuntimeError(f"Install did not produce {Path(rel).name}")


def main():
    if sys.platform != "win32" or sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32:
        raise RuntimeError("Install needs 64-bit Python 3.11 on Windows")
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    artifacts = ROOT / "artifacts"
    artifacts.mkdir()
    subprocess.run([sys.executable, "-m", "venv", str(artifacts / "python")], check=True)
    python = artifacts / "python" / "Scripts" / "python.exe"
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
    models = [cfg["ears"]["model"]]
    models += [part for part in cfg["voice"]["command"] if str(part).endswith(".gguf")]
    for rel in models:
        save(cfg["fetch"]["models"][Path(rel).name], ROOT / rel)
    library = Path(cfg["ears"]["library"])
    unpack(cfg["fetch"]["archives"]["ear"], ROOT / library.parents[1])
    exe = next(part for part in cfg["voice"]["command"] if str(part).endswith(".exe"))
    unpack(cfg["fetch"]["archives"]["voice"], ROOT / Path(exe).parent)
    needed = [python, ROOT / cfg["ears"]["model"], ROOT / cfg["ears"]["library"]]
    needed += [ROOT / part for part in cfg["voice"]["command"] if str(part).startswith("artifacts/")]
    for path in needed:
        if not path.is_file():
            raise RuntimeError(f"Install did not produce {path.name}")
    cards()
    print(r"Installed. Start with: artifacts\python\Scripts\python.exe run.py")
    print(r"Or: artifacts\python\Scripts\python.exe run.py lfm")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
