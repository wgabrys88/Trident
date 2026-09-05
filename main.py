import argparse, hashlib, shutil, socket, subprocess, sys, threading, time, urllib.request
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
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False


def _kill_port(port: int) -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "Get-NetTCPConnection -ErrorAction Stop | "
         f"Where-Object {{ $_.LocalPort -eq {port} -and $_.State -eq 'Listen' }} | "
         "Select-Object -ExpandProperty OwningProcess -Unique | "
         "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction Stop }"], check=True)


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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="No arguments installs and loads all models.", allow_abbrev=False)
    command = parser.add_mutually_exclusive_group()
    command.add_argument("prompt", nargs="?", help="run Brain, TTS and Parakeet without installation")
    command.add_argument("--unload", action="store_true", help="stop all three model servers")
    args = parser.parse_args()
    mode = "unload" if args.unload else "install" if args.prompt is None else "pipeline"
    stages = (("brain", "brain.py", (f"--request={args.prompt}",)),
              ("tts", "tts_nano.py", ()), ("parakeet", "parakeet.py", ("tts_out.wav",)))
    started = time.perf_counter()
    log_path = ROOT / ".runtime-logs/main.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        def emit(text: str) -> None:
            print(text, flush=True)
            print(text, file=log)

        emit(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {mode} {args.prompt or ''}".rstrip())
        for name, script, request in stages:
            stage_started = time.perf_counter()
            emit(f"[{name}] {mode}")
            flags = request if mode == "pipeline" else (f"--{mode}",)
            with subprocess.Popen([sys.executable, "-u", script, *flags], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8") as process:
                for line in process.stdout:
                    emit(line.rstrip("\n"))
                code = process.wait()
            emit(f"[{name}] exit={code} wall_s={time.perf_counter()-stage_started:.3f}")
            if code:
                emit(f"[{mode}] failed wall_s={time.perf_counter()-started:.3f}")
                raise SystemExit(code)
        emit(f"[{mode}] done wall_s={time.perf_counter()-started:.3f}")
