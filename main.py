import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

args = sys.argv[1:]

if args and args[0] in ("--load", "--unload"):
    cmd = args[0]
    for script in ("brain.py", "tts_nano.py", "parakeet.py"):
        subprocess.run(["python", str(ROOT / script), cmd], check=True)
    sys.exit(0)

if args and not args[0].startswith("-"):
    text = " ".join(args)
    (ROOT / "pipe_in.txt").write_text(text, encoding="utf-8")
    print(f"text: {text}")
    subprocess.run(["python", str(ROOT / "brain.py")], check=True)
    subprocess.run(["python", str(ROOT / "tts_nano.py")], check=True)
    wavs = sorted(ROOT.glob("tts_out*.wav"))
    last = wavs[-1] if wavs else None
    if last is None:
        raise RuntimeError("tts_nano.py produced no wav")
    print(f"wav: {last}")
    subprocess.run(["python", str(ROOT / "parakeet.py"), str(last)], check=True)
    sys.exit(0)

if args:
    print("usage:")
    print("  python main.py <text>           run full pipeline (brain -> tts -> parakeet)")
    print("  python main.py --load           install all 3 modules and keep them loaded")
    print("  python main.py --unload         stop all 3 modules")
    sys.exit(1)
