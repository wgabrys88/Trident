import hashlib, re, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def _download(url: str, path: Path, sha: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Trident/1"})
        with urllib.request.urlopen(req, timeout=3600) as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst, 4 << 20)
        if sha:
            with partial.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != sha:
                    raise RuntimeError(f"Checksum mismatch: {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)

def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0

def _kill_port(port: int) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
         "ForEach-Object {{ taskkill /F /PID $_.OwningProcess }}"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _drain(proc: subprocess.Popen, ready_event: threading.Event, ready_str: str, tail: list) -> None:
    for line in proc.stdout:
        tail.append(line.rstrip())
        if ready_str in line:
            ready_event.set()

def _wait_ready(proc: subprocess.Popen, ready_event: threading.Event, tail: list,
                timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while not ready_event.wait(0.05):
        if proc.poll() is not None:
            raise RuntimeError(f"Process died: {proc.returncode}\n" + "\n".join(tail))
        if time.monotonic() >= deadline:
            proc.kill()
            raise TimeoutError(f"Startup timed out\n" + "\n".join(tail))

def parse_rtf(text: str) -> dict:
    out = {}
    for m in re.finditer(r"\[rtf\]\s+([a-zA-Z_][\w]*)=([\d.]+)s", text):
        out[m.group(1)] = float(m.group(2))
    for m in re.finditer(r"(startup|ttft|inference)_s=([\d.]+)", text):
        out["brain_" + m.group(1)] = float(m.group(2))
    for m in re.finditer(r"audio_s=([\d.]+)", text):
        if "audio_s" not in out:
            out["audio_s"] = float(m.group(1))
    for m in re.finditer(r"tokens=(\d+)", text):
        if "brain_tokens" not in out:
            out["brain_tokens"] = int(m.group(1))
    for m in re.finditer(r"tps=([\d.]+)", text):
        if "brain_tps" not in out:
            out["brain_tps"] = float(m.group(1))
    return out

def run(name: str, cmd: list) -> tuple:
    t0 = time.perf_counter()
    print(f"[{name}] START", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, end="")
        print(r.stderr, end="", file=sys.stderr)
        raise SystemExit(r.returncode)
    out = r.stdout + r.stderr
    dt = time.perf_counter() - t0
    print(f"[{name}] END   outer_dt={dt:.3f}s", flush=True)
    for line in r.stderr.splitlines():
        if "[rtf]" in line or "startup_s=" in line or "inference_s=" in line:
            print("  " + line, flush=True)
    return out, dt, parse_rtf(out)
