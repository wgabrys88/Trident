from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(script: str, *flags: str) -> int:
    return subprocess.call([sys.executable, "-u", script, *flags], cwd=ROOT)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("prompt", nargs="?")
    p.add_argument("tts_model", nargs="?", default="nano", choices=["nano", "turbo", "v3"])
    p.add_argument("--unload", action="store_true")
    p.add_argument("--install", action="store_true")
    p.add_argument("--from-hf", action="store_true")
    p.add_argument("--language", default="en")
    a = p.parse_args()
    if a.prompt:
        tts = f"tts_{a.tts_model}.py"
        lang_flag = [f"--language={a.language}"] if a.tts_model == "v3" else []
        sys.exit(run("brain.py", f"--request={a.prompt}")
                 or run("brain.py", "--unload")
                 or run(tts, *lang_flag)
                 or run(tts, "--unload")
                 or run("parakeet.py", "tts_out.wav"))
    if a.install:
        sys.exit(run("brain.py", "--install")
                 or run("tts_nano.py", "--install")
                 or run("tts_turbo.py", "--install")
                 or run("tts_v3.py", "--install")
                 or run("parakeet.py", "--install"))
    if a.from_hf:
        sys.exit(run("brain.py", "--install", "--from-hf")
                 or run("tts_nano.py", "--install", "--from-hf")
                 or run("tts_turbo.py", "--install", "--from-hf")
                 or run("tts_v3.py", "--install", "--from-hf")
                 or run("parakeet.py", "--install", "--from-hf"))
    if a.unload:
        for s in ("brain.py", "tts_nano.py", "tts_turbo.py", "tts_v3.py", "parakeet.py"):
            run(s, "--unload")


if __name__ == "__main__":
    main()
