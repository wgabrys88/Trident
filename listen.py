import subprocess
import sys
from pathlib import Path

import onnx_asr

SECONDS = 10
MODEL = "nemo-parakeet-tdt-0.6b-v3"
FFMPEG = Path(
    r"C:\Users\eb-wjt\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.exe"
)
DEVICE = "Microphone Array (Intel® Smart Sound Technology for Digital Microphones)"
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "listen.wav"
TXT = ROOT / "listen.txt"
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

if not (len(sys.argv) == 2 and sys.argv[1] == "existing"):
    if not FFMPEG.is_file():
        raise SystemExit("ffmpeg")
    print("mic ffmpeg dshow", SECONDS, "s", file=sys.stderr)
    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "dshow",
            "-t",
            str(SECONDS),
            "-i",
            f"audio={DEVICE}",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(OUT),
        ],
        check=True,
    )
if not OUT.is_file() or OUT.stat().st_size <= 44:
    raise SystemExit("WAV Length")
print("transcribe", MODEL, file=sys.stderr)
line = onnx_asr.load_model(MODEL).recognize(str(OUT))
line = (line if isinstance(line, str) else str(line)).strip()
if not line:
    raise SystemExit("empty transcript")
TXT.write_text(line + "\n", encoding="utf-8")
print(line)
