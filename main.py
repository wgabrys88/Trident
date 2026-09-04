import subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def run(name, cmd, capture=False):
    t0 = time.perf_counter()
    print(f"[{name}] START", flush=True)
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = r.stdout + r.stderr
        t1 = time.perf_counter()
        print(f"[{name}] END   dt={t1-t0:.3f}s", flush=True)
        return out, t1 - t0
    else:
        subprocess.run(cmd, check=True)
        t1 = time.perf_counter()
        print(f"[{name}] END   dt={t1-t0:.3f}s", flush=True)
        return t1 - t0

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
    print()

    t_pipeline = time.perf_counter()

    out, dt_brain = run("brain", ["python", str(ROOT / "brain.py")], capture=True)
    brain_out = (ROOT / "brain_out.txt").read_text(encoding="utf-8")
    print(f"[brain]    answer_chars={len(brain_out)}")
    print()

    out, dt_tts = run("tts", ["python", str(ROOT / "tts_nano.py")], capture=True)
    wavs = sorted(ROOT.glob("tts_out*.wav"))
    last_wav = wavs[-1] if wavs else None
    print(f"[tts]      wav={last_wav}")
    print()

    out, dt_para = run("parakeet", ["python", str(ROOT / "parakeet.py"), str(last_wav)], capture=True)
    print()
    print(f"=== RTF ===")
    print(f"brain     dt={dt_brain:.3f}s")
    print(f"tts       dt={dt_tts:.3f}s")
    print(f"parakeet  dt={dt_para:.3f}s")
    print(f"pipeline  dt={time.perf_counter()-t_pipeline:.3f}s")
    print()
    print(f"transcribed: {out.strip()}")
    sys.exit(0)

print("usage:")
print("  python main.py <text>           run full pipeline (brain -> tts -> parakeet)")
print("  python main.py --load           install all 3 modules and keep them loaded")
print("  python main.py --unload         stop all 3 modules")
