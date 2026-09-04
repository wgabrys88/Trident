import argparse, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
IN, BRAIN_OUT, TTS_OUT = ROOT / "pipe_in.txt", ROOT / "brain_out.txt", ROOT / "tts_out.wav"
p = argparse.ArgumentParser()
p.add_argument("--load", action="store_true")
p.add_argument("--unload", action="store_true")
p.add_argument("text", nargs="?")
args = p.parse_args()
cmds = []
if args.load:
    cmds = ["python brain.py --load", "python tts_nano.py --load", "python parakeet.py --load"]
elif args.unload:
    cmds = ["python parakeet.py --unload", "python tts_nano.py --unload", "python brain.py --unload"]
elif args.text:
    IN.write_text(args.text, encoding="utf-8")
    cmds = ["python brain.py", "python tts_nano.py", "python parakeet.py"]
for c in cmds:
    subprocess.run(c, shell=True, check=True)
