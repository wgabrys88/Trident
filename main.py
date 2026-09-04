import subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if len(sys.argv) > 1:
    (ROOT / "pipe_in.txt").write_text(sys.argv[1], encoding="utf-8")
subprocess.run(["python", "brain.py"], check=True)
subprocess.run(["python", "tts_nano.py"], check=True)
subprocess.run(["python", "parakeet.py"], check=True)
