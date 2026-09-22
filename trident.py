import subprocess, sys, time
from install import MODELS, ROOT, kill, reexec
from settings import VARIANTS, bus

def stop(workers):
    for proc in workers:
        if proc.poll() is None:
            proc.terminate()
    kill(MODELS / "server.pid")

def main(variant: str):
    bus()
    py = sys.executable
    workers = [
        subprocess.Popen([py, str(ROOT / "brain.py")]),
        subprocess.Popen([py, str(ROOT / "tts.py"), variant]),
        subprocess.Popen([py, str(ROOT / "asr.py")]),
    ]
    print("jarvis ready", flush=True)
    try:
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
    if len(sys.argv) != 2 or sys.argv[1] not in VARIANTS:
        raise SystemExit("usage: python trident.py <variant>")
    main(sys.argv[1])
