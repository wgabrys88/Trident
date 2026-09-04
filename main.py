import re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def parse_rtf(text):
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

def run(name, cmd):
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
    _, dt_brain, rtf_brain = run("brain",   ["python", str(ROOT / "brain.py")])
    brain_out = (ROOT / "brain_out.txt").read_text(encoding="utf-8")
    print(f"[brain]    answer_chars={len(brain_out)}")
    print()
    _, dt_tts, rtf_tts = run("tts",       ["python", str(ROOT / "tts_nano.py")])
    wavs = sorted(ROOT.glob("tts_out*.wav"))
    last_wav = wavs[-1] if wavs else None
    print(f"[tts]      wav={last_wav}")
    print()
    _, dt_para, rtf_para = run("parakeet", ["python", str(ROOT / "parakeet.py"), str(last_wav)])
    print()

    print("=== RTF ===")
    print(f"brain     outer={dt_brain:.3f}s | startup={rtf_brain.get('brain_startup','?')}s ttft={rtf_brain.get('brain_ttft','?')}s inference={rtf_brain.get('brain_inference','?')}s tokens={rtf_brain.get('brain_tokens','?')} tps={rtf_brain.get('brain_tps','?')}")
    print(f"tts       outer={dt_tts:.3f}s | start={rtf_tts.get('tts_start','?')}s synth={rtf_tts.get('tts_synth','?')}s audio_s={rtf_tts.get('audio_s','?')}s")
    print(f"parakeet  outer={dt_para:.3f}s | total={rtf_para.get('parakeet_total','?')}s audio_s={rtf_para.get('audio_s','?')}s")
    pipe = time.perf_counter() - t_pipeline
    print(f"pipeline  outer={pipe:.3f}s")
    if rtf_brain.get("brain_inference") and rtf_tts.get("tts_synth"):
        print(f"brain rtf = brain_inference/tts_synth = {rtf_brain['brain_inference']/rtf_tts['tts_synth']:.3f}x")
    if rtf_tts.get("audio_s") and rtf_tts.get("tts_synth"):
        print(f"tts rtf = synth/audio = {rtf_tts['tts_synth']/rtf_tts['audio_s']:.3f}x real-time")
    if rtf_para.get("audio_s") and rtf_para.get("parakeet_total"):
        print(f"parakeet rtf = total/audio = {rtf_para['parakeet_total']/rtf_para['audio_s']:.3f}x real-time")
    sys.exit(0)

print("usage:")
print("  python main.py <text>           run full pipeline (brain -> tts -> parakeet)")
print("  python main.py --load           install all 3 modules and keep them loaded")
print("  python main.py --unload         stop all 3 modules")
