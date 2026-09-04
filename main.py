import subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if "--load" in sys.argv or "--unload" in sys.argv:
    cmd = "--load" if "--load" in sys.argv else "--unload"
    for script in ("brain.py", "tts_nano.py", "parakeet.py"):
        subprocess.run(["python", str(ROOT / script), cmd], check=True)
    sys.exit(0)
if len(sys.argv) > 1:
    (ROOT / "pipe_in.txt").write_text(sys.argv[1], encoding="utf-8")
subprocess.run(["python", str(ROOT / "brain.py")], check=True)
subprocess.run(["python", str(ROOT / "tts_nano.py")], check=True)
subprocess.run(["python", str(ROOT / "parakeet.py")], check=True)
