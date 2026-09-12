import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "eval_out" / "benchmark_text.txt").read_text(encoding="utf-8").strip()
family = sys.argv[1]
cmd = [sys.executable, str(ROOT / f"tts_{family}.py"), TEXT]
if family == "v3":
    cmd.append("en")
raise SystemExit(subprocess.run(cmd, cwd=str(ROOT)).returncode)
