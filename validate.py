"""One real Nano capture. No other models, ASR, analysis or synthetic checks."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
COUNT = (
    'One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. '
    'Eleven. Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. '
    'Eighteen. Nineteen. Twenty. Twenty-one. Twenty-two. Twenty-three. '
    'Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. '
    'Twenty-nine. Thirty.'
)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--text', default=COUNT)
    source.add_argument('--text-file', type=Path)
    args, settings = parser.parse_known_args()
    for name in ("tts_out.wav", "tts_out.log.zip"):
        (ROOT/name).unlink(missing_ok=True)
    subprocess.run([sys.executable, str(ROOT/'tts_nano.py'), '--unload'], cwd=ROOT, check=True)
    text = args.text_file.read_text(encoding='utf-8') if args.text_file else args.text
    result = subprocess.run([sys.executable, str(ROOT/'tts_nano.py'), '--audit', '--direct',
                             '--text', text, *settings], cwd=ROOT)
    raise SystemExit(result.returncode)
