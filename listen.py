import subprocess
import sys
import venv
from pathlib import Path

SECONDS = 10
MODEL = "nemo-parakeet-tdt-0.6b-v3"
FFMPEG = Path(
    r"C:\Users\px-wjt\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.exe"
)
DEVICE = r"@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{6805B0E9-027D-452C-B192-800B415DC326}"
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv-parakeet"
VPY = VENV / "Scripts" / "python.exe"
OUT = ROOT / "listen.wav"
TXT = ROOT / "listen.txt"


def hear_python() -> Path:
    here = Path(sys.executable).resolve()
    if here == VPY.resolve():
        return here
    if not VPY.is_file():
        print("venv-parakeet", file=sys.stderr)
        venv.EnvBuilder(with_pip=True).create(VENV)
        subprocess.run(
            [
                str(VPY),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "onnx-asr[cpu,hub]",
            ],
            check=True,
        )
    raise SystemExit(
        subprocess.run([str(VPY), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode
    )


hear_python()

import onnx_asr

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

if len(sys.argv) != 2:
    raise SystemExit("usage: python listen.py <wav>|mic")

if sys.argv[1] == "mic":
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
    wav = OUT
else:
    wav = Path(sys.argv[1])
if not wav.is_file() or wav.stat().st_size <= 44:
    raise SystemExit("WAV Length")
print("transcribe", MODEL, file=sys.stderr)
line = onnx_asr.load_model(MODEL).recognize(str(wav))
line = (line if isinstance(line, str) else str(line)).strip()
if not line:
    raise SystemExit("empty transcript")
TXT.write_text(line + "\n", encoding="utf-8")
print(line)
