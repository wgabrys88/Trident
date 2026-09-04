"""Standalone LLM brain for Trident, powered by llama.cpp + Gemma 4 QAT.

Run with --request to get a bare-metal response.  On first run it downloads
everything under tools/runtime/brain/ and models/ (both gitignored).
No Python ML packages are required at runtime.  Windows Vulkan build.

Model:  unsloth/gemma-4-E2B-it-qat-GGUF  UD-Q4_K_XL  (2.62 GB, QAT, ~3 GB VRAM)
MTP:    mtp-gemma-4-E2B-it.gguf  (59.2 MB, explicit --model-draft)
Engine: llama.cpp b10797  Windows x64 Vulkan pre-built binary
"""

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "tools/runtime/brain"
EXE = RUNTIME / "llama-cli.exe"
MODEL = ROOT / "models/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
MODEL_CARD = MODEL.with_suffix(".md")
MTP = ROOT / "models/mtp-gemma-4-E2B-it.gguf"
MTP_CARD = MTP.with_suffix(".md")

LLAMA_REV = "b10797"
ARCHIVE = f"llama-{LLAMA_REV}-bin-win-vulkan-x64.zip"
RUNTIME_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_REV}/{ARCHIVE}"
RUNTIME_SHA = "851a05cb2ed0d35d7b336a193500d3baf86e80983b6d6c024c85afa14b0cadde"

MODEL_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
MODEL_SHA = "e531007218dfab990486a5de7676a6932d6ea8dea233d1f698d7c21cf8a16889"

MTP_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/mtp-gemma-4-E2B-it.gguf"
MTP_SHA = "586f2460b909008640981ec34060aa864e03c144fbabfb3173c4335087e4aae0"

CARD_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/README.md"
MTP_CARD_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/MTP/README.md"

THREADS = 6
MAX_TOKENS = 2048
CONTEXT = 4096


class BrainInstaller:
    @staticmethod
    def _download(url: str, path: Path, sha: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Trident/1"})
            with urllib.request.urlopen(req, timeout=3600) as src, \
                    partial.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            if sha:
                with partial.open("rb") as f:
                    digest = hashlib.file_digest(f, "sha256").hexdigest()
                    if digest != sha.lower():
                        raise RuntimeError(
                            f"Checksum mismatch for {path.name}: "
                            f"expected {sha}, got {digest}"
                        )
            partial.replace(path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    def install(self) -> None:
        runtime_done = (
            EXE.is_file()
            and (RUNTIME / "ggml-vulkan.dll").is_file()
            and (RUNTIME / "llama.dll").is_file()
        )
        model_done = MODEL.is_file() and MODEL_CARD.is_file()
        mtp_done = MTP.is_file() and MTP_CARD.is_file()

        if runtime_done and model_done and mtp_done:
            print("Brain is installed.", file=sys.stderr)
            return

        archive_path = None
        try:
            if not EXE.is_file():
                print(f"Downloading {ARCHIVE}", file=sys.stderr, flush=True)
                archive_path = ROOT / ARCHIVE
                self._download(RUNTIME_URL, archive_path, RUNTIME_SHA)
                RUNTIME.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive_path) as zf:
                    for name in zf.namelist():
                        if not name.endswith("/"):
                            (RUNTIME / name).write_bytes(zf.read(name))
                print("llama.cpp runtime installed.", file=sys.stderr)

            if not model_done:
                print(f"Downloading {MODEL.name}", file=sys.stderr, flush=True)
                MODEL.parent.mkdir(parents=True, exist_ok=True)
                self._download(MODEL_URL, MODEL, MODEL_SHA)
                self._download(CARD_URL, MODEL_CARD, "")

            if not mtp_done:
                print(f"Downloading {MTP.name}", file=sys.stderr, flush=True)
                self._download(MTP_URL, MTP, MTP_SHA)
                self._download(MTP_CARD_URL, MTP_CARD, "")

            print("Brain is installed.", file=sys.stderr)
        finally:
            if archive_path and archive_path.is_file():
                archive_path.unlink()


class Brain:
    _SYSTEM = (
        "You are a helpful assistant. "
        "Reason step by step and give concise, accurate answers."
    )

    def ask(self, request: str) -> str:
        prompt_file = ROOT / "brain_prompt.txt"
        prompt_file.write_text(
            f"<|im_start|>system\n{self._SYSTEM}<|im_end|>\n"
            f"<|im_start|>user\n{request}<|im_end|>\n"
            f"<|im_start|>assistant\n",
            encoding="utf-8",
        )
        cmd = [
            str(EXE), "-m", str(MODEL),
            "--model-draft", str(MTP),
            "-ngl", "99", "-c", str(CONTEXT),
            "-fa", "on", "-t", str(THREADS),
            "--temp", "1.0", "--top-p", "0.95",
            "--top-k", "64", "-n", str(MAX_TOKENS),
            "--no-display-prompt",
            "-f", str(prompt_file),
            "--log-disable",
        ]
        started = time.perf_counter()
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        elapsed = time.perf_counter() - started
        prompt_file.unlink(missing_ok=True)

        stderr_lines = [ln.strip() for ln in proc.stderr.splitlines() if ln.strip()]
        fatal_errors = [
            ln for ln in stderr_lines
            if "error" in ln.lower()
            and "ctx_other" not in ln
            and "Gemma4Assistant" not in ln
        ]

        if fatal_errors:
            for ln in stderr_lines:
                sys.stderr.write(ln + "\n")
            raise RuntimeError(
                f"llama-cli fatal error, exit code {proc.returncode}"
            )

        for ln in stderr_lines:
            sys.stderr.write(ln + "\n")

        tok_match = re.search(r"\btok/s:\s*([\d.]+)", proc.stderr)
        if tok_match:
            print(f"elapsed_s={elapsed:.2f} tok/s={tok_match.group(1)}",
                  file=sys.stderr)

        out = proc.stdout.strip()
        out = re.sub(r"<\|channel\|>thought[^\n]*\n.*?<\|channel\|>", "", out, flags=re.DOTALL)
        out = re.sub(r"<\|channel\|>thought[^\n]*\n.*", "", out, flags=re.DOTALL)
        out = re.sub(r"\[Start thinking\].*?\[End thinking\]", "", out, flags=re.DOTALL)
        return out.strip()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Text prompt for the LLM")
    args = parser.parse_args()
    BrainInstaller().install()
    print(Brain().ask(args.request), flush=True)
