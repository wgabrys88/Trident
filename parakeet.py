"""Install with no arguments; pass WAV paths to print and save timestamped transcripts."""

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
import zipfile

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "tools/runtime/parakeet"
EXE = RUNTIME / "parakeet-cli.exe"
MODEL = ROOT / "models/nemotron-3.5-asr-streaming-0.6b-q4_k.gguf"
MODEL_CARD = MODEL.with_suffix(".md")
ARCHIVE = "parakeet-v0.5.0-bin-win-cpu-x64.zip"
RUNTIME_URL = f"https://github.com/mudler/parakeet.cpp/releases/download/v0.5.0/{ARCHIVE}"
MODEL_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9"
RUNTIME_SHA = "df25af4095807d83957f6e135950120e7954fd2d4aca8ad0a5de248ada6287e0"
MODEL_SHA = "5ad85eb3f3014c1a300d67b7ccbd23c38c4c952405cbe33a861e19fb2775e84b"
THREADS = 6
LANGUAGE = "auto"
TIMESTAMP_FORMAT = "%d-%m-%y-%H-%M-%S"


class ParakeetInstaller:
    @staticmethod
    def _download(url: str, path: Path, sha: str = "") -> None:
        print(f"Downloading {path.name}", file=sys.stderr, flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "Parakeet/1"})
        with urllib.request.urlopen(request, timeout=1800) as source, path.open("wb") as target:
            shutil.copyfileobj(source, target, 1 << 20)
        if sha:
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != sha:
                    raise RuntimeError(f"Download checksum mismatch: {path.name}")

    def install(self) -> None:
        if all(path.is_file() for path in (EXE, RUNTIME / "LICENSE", MODEL, MODEL_CARD)):
            print("Parakeet is installed.")
            return
        with tempfile.TemporaryDirectory(prefix=".parakeet-install-", dir=ROOT) as temporary:
            work = Path(temporary)
            if not EXE.is_file() or not (RUNTIME / "LICENSE").is_file():
                archive = work / ARCHIVE
                self._download(RUNTIME_URL, archive, RUNTIME_SHA)
                RUNTIME.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive) as bundle:
                    for name in (EXE.name, "LICENSE"):
                        member = f"{ARCHIVE.removesuffix('.zip')}/{name}"
                        with bundle.open(member) as source, (RUNTIME / name).open("wb") as target:
                            shutil.copyfileobj(source, target)
            MODEL.parent.mkdir(parents=True, exist_ok=True)
            for output, name, sha in ((MODEL, MODEL.name, MODEL_SHA), (MODEL_CARD, "README.md", "")):
                if not output.is_file():
                    downloaded = work / output.name
                    self._download(f"{MODEL_URL}/{name}", downloaded, sha)
                    downloaded.replace(output)
        print("Parakeet is installed.")


class Parakeet:
    def transcribe(self, wav: Path) -> str:
        wav = ROOT / wav
        with wave.open(str(wav), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
        command = [str(EXE), "transcribe", "--model", str(MODEL), "--input", str(wav),
                   "--lang", LANGUAGE, "--threads", str(THREADS)]
        print(f"parakeet.run wav={wav.name} lang={LANGUAGE} threads={THREADS}", file=sys.stderr, flush=True)
        started = time.perf_counter()
        result = subprocess.run(command, stdout=subprocess.PIPE, text=True, encoding="utf-8", check=True)
        elapsed = time.perf_counter() - started
        with (ROOT / f"{time.strftime(TIMESTAMP_FORMAT)}-asr-{wav.stem}.txt").open("x", encoding="utf-8") as output:
            output.write(result.stdout)
        print(f"audio_s={duration:.3f} elapsed_s={elapsed:.3f} rtf={elapsed / duration:.4f}", file=sys.stderr)
        return result.stdout


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, nargs="*", help="WAV paths; omit to install")
    args = parser.parse_args()
    if not args.wav:
        ParakeetInstaller().install()
    else:
        asr = Parakeet()
        for wav in args.wav:
            print(asr.transcribe(wav), end="", flush=True)
