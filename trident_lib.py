import json, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "workspace"
DONE = WORK / "done"
MODELS = ROOT / "models"


def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"


def reexec() -> None:
    py = venv_python()
    if not py.is_file():
        raise RuntimeError("missing " + str(py) + "; run python install.py first")
    if Path(sys.executable).resolve() != py.resolve():
        raise SystemExit(subprocess.call([str(py), *sys.argv]))
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def http(server: str, method: str, path: str, payload=None, timeout=70):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(server + path, data=raw, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])
    return data


def kill_server_pid() -> None:
    path = MODELS / "server.pid"
    if not path.is_file():
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(record["pid"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        path.unlink(missing_ok=True)
