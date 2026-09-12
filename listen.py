import subprocess
import sys
import venv
from pathlib import Path
from shutil import which

SECONDS = 10
MODEL = "nemo-parakeet-tdt-0.6b-v3"
FFMPEG_PIN = Path(
    r"C:\Users\eb-wjt\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.exe"
)
DEVICE = "Microphone Array (Intel® Smart Sound Technology for Digital Microphones)"
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


def ffmpeg_exe() -> Path:
    if FFMPEG_PIN.is_file():
        return FFMPEG_PIN
    found = which("ffmpeg")
    if found:
        return Path(found)
    raise SystemExit("ffmpeg")


if not (len(sys.argv) == 2 and sys.argv[1] == "existing"):
    ff = ffmpeg_exe()
    print("mic ffmpeg dshow", SECONDS, "s", file=sys.stderr)
    subprocess.run(
        [
            str(ff),
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
