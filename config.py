import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from journal import Journal, WorkerSupervisor

ROOT = Path(__file__).resolve().parent
MODELS, DATA, TOOLS = (ROOT / n for n in ("models", "data", "tools"))
RUNTIMES = TOOLS / "runtime"
PARAKEET_FILE = "tdt-0.6b-v3-q4_k.gguf"
PARAKEET_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9/" + PARAKEET_FILE
PARAKEET_SIZE, PARAKEET_SHA256 = 675200864, "993d73feb4206dadda865ab25bd64b50c48dc4d013c3bf6126a721f28b1d5ee8"
PARAKEET_ZIP = ("https://github.com/mudler/parakeet.cpp/releases/download/v0.5.0/parakeet-v0.5.0-bin-win-vulkan-x64.zip", "parakeet-v0.5.0-bin-win-vulkan-x64.zip", 0, "717c416fab299755e8140137e3a0115121ce1acb6379d13c60f2f0613f6c13a3")
PORTS = {"parakeet": 17931}
ASR_RATE = 16000


def find_exe(root: Path, name: str) -> Path | None:
    return next((p for p in root.rglob(name) if p.is_file()), None) if root.is_dir() else None


def load_settings(data_dir: Path) -> dict:
    path = Path(data_dir) / "live-settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


class Paths:
    def __init__(self, models_dir=None, data_dir=None, command="install", console=False, wavs=()) -> None:
        self.models_dir, self.data_dir = Path(models_dir or MODELS).resolve(), Path(data_dir or DATA).resolve()
        self.command, self.wavs = command, tuple(Path(p) for p in wavs)
        bits = [datetime.now().strftime("%Y%m%d-%H%M%S-%f"), command]
        self.stamp, self.run_dir = bits[0], self.data_dir / "runs" / "-".join(bits)
        self.run_dir.mkdir(parents=True)
        self.journal = Journal(self.run_dir, console)
        self.supervisor = WorkerSupervisor(self.journal)
        print(f"trident.run {self.run_dir}", flush=True)

    def close(self) -> None:
        self.journal.close()


def git_identity(path: Path) -> dict:
    try:
        run = lambda *a: subprocess.check_output(["git", "-C", str(path), *a], text=True, stderr=subprocess.DEVNULL, timeout=15).strip()
        return {"sha": run("rev-parse", "HEAD"), "branch": run("branch", "--show-current"),
                "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {"sha": "", "branch": "", "dirty": None}