import subprocess, sys, time
from runtime import MODELS, ROOT, kill, reexec
from settings import VARIANTS, WORK, bus, retire

def stop(workers):
    for proc in workers:
        if proc.poll() is None:
            proc.terminate()
    kill(MODELS / "server.pid")

def main(variant: str, inbox_only: bool = False):
    bus()
    folder = WORK / "ready"
    folder.mkdir(exist_ok=True)
    for name in ("ear", "brain", "mouth"):
        path = folder / name
        if path.is_file():
            retire(path, "ready")
    py = sys.executable
    asr = [py, str(ROOT / "asr.py"), "--inbox"] if inbox_only else [py, str(ROOT / "asr.py")]
    workers = [
        subprocess.Popen([py, str(ROOT / "brain.py")]),
        subprocess.Popen([py, str(ROOT / "tts.py"), variant]),
        subprocess.Popen(asr),
    ]
    try:
        while not all((folder / name).is_file() for name in ("ear", "brain", "mouth")):
            for proc in workers:
                code = proc.poll()
                if code is not None:
                    stop(workers)
                    raise SystemExit(code)
            time.sleep(0.05)
        print("jarvis ready", flush=True)
        while True:
            for proc in workers:
                code = proc.poll()
                if code is not None:
                    stop(workers)
                    raise SystemExit(code)
            time.sleep(0.25)
    except KeyboardInterrupt:
        stop(workers)

if __name__ == "__main__":
    reexec()
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in VARIANTS:
        raise SystemExit("usage: python trident.py <variant> [inbox]")
    inbox_only = len(sys.argv) == 3 and sys.argv[2] == "inbox"
    if len(sys.argv) == 3 and not inbox_only:
        raise SystemExit("usage: python trident.py <variant> [inbox]")
    main(sys.argv[1], inbox_only)
