"""Standalone Nano TTS, ported from codex/tts-only-nano at 97b4480.

Install once, then run English text-to-WAV synthesis on Windows / Iris Xe.
The native runtime needs no Python packages; conversion dependencies are temporary.
"""

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.request
import venv
import wave

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "tools" / "runtime" / "tts"
MODELS = ROOT / "models"
VOICE = ROOT / "data" / "ref-trump.wav"
CHATTERBOX_REV = "bb0717cec20fafecf5491654a758cbee93cbe962"
GGML_REV = "58c3805840b516b2a88ff867ccf7bb41dba79951"
NANO_REV = "71ccd1d0081b430592cea481f4307e764e07bc64"
NANO_URL = f"https://huggingface.co/ResembleAI/chatterbox-nano/resolve/{NANO_REV}"
VOICE_URL = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4"
VOICE_SHA = "9d8b44d73192e9c04dd241f16177e4c5753bcefadde69e6e24b45e278b821f8c"
T3 = MODELS / "chatterbox-t3-nano-q4_0.gguf"
S3 = MODELS / "chatterbox-s3gen-nano-irisxe-q4_0-rawf32-v1.gguf"
RUNTIME_FILES = ("chatterbox-server.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll")
CHECKPOINT_FILES = ("t3_nano_v1.safetensors", "s3gen_meanflow.safetensors", "conds.pt",
                    "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")
CONVERTERS = ("convert-t3-turbo-to-gguf.py", "convert-s3gen-to-gguf.py", "quant_policy.py")
KNOBS = {
    "n-gpu-layers": 99, "context": 2048, "threads": 4, "fastconv": 1, "seed": 42,
    "max-tokens": 1000, "top-k": 1000, "top-p": .95, "min-p": .05,
    "temperature": .8, "repeat-penalty": 1.2, "cfm-steps": 1,
    "cfg-weight": .5, "exaggeration": .5,
}
RATE, CHUNK_CHARS, PORT = 24000, 50, 17933
MAGIC, VERSION = 0x32525454, 2
REQUEST, RESPONSE = struct.Struct("<7I"), struct.Struct("<8I")


class NanoInstaller:
    @staticmethod
    def _download(url: str, path: Path, sha: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".part")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "NanoTTS/1"})
            with urllib.request.urlopen(request, timeout=120) as source, partial.open("wb") as target:
                shutil.copyfileobj(source, target, 1 << 20)
            if sha:
                with partial.open("rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != sha:
                        raise RuntimeError(f"Download checksum mismatch: {path.name}")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def _checkout(url: str, rev: str, path: Path, patterns: tuple[str, ...]) -> None:
        subprocess.run(["git", "init", str(path)], check=True)
        git = ["git", "-C", str(path)]
        for args in (("remote", "add", "origin", url),
                     ("config", "remote.origin.promisor", "true"),
                     ("config", "remote.origin.partialclonefilter", "blob:none"),
                     ("fetch", "--depth=1", "--filter=blob:none", "--no-tags", "origin", rev)):
            subprocess.run([*git, *args], check=True)
        subprocess.run([*git, "sparse-checkout", "set", "--no-cone", "--stdin"],
                       input="\n".join(patterns) + "\n", text=True, check=True)
        subprocess.run([*git, "checkout", "--detach", rev], check=True)

    def _build(self, work: Path, source: Path, cmake: str, sdk: Path) -> None:
        self._checkout("https://github.com/ggml-org/ggml.git", GGML_REV, source / "ggml",
                       ("/CMakeLists.txt", "/LICENSE", "/cmake/", "/include/", "/src/*",
                        "!/src/*/", "/src/ggml-cpu/", "/src/ggml-vulkan/"))
        sdk, build = (ROOT / sdk).resolve(), work / "build"
        subprocess.run([
            cmake, "-S", str(source), "-B", str(build), "-G", "Visual Studio 17 2022", "-A", "x64",
            "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF",
            "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON", "-DTTS_CPP_BUILD_TESTS=OFF",
            "-DGGML_BUILD_TESTS=OFF", "-DGGML_BUILD_EXAMPLES=OFF",
            f"-DVulkan_INCLUDE_DIR={sdk / 'Include'}", f"-DVulkan_LIBRARY={sdk / 'Lib/vulkan-1.lib'}",
            f"-DVulkan_GLSLC_EXECUTABLE={sdk / 'Bin/glslc.exe'}",
        ], check=True)
        subprocess.run([cmake, "--build", str(build), "--config", "Release", "--target",
                        "chatterbox-server", "--parallel", "4"], check=True)
        RUNTIME.mkdir(parents=True, exist_ok=True)
        for name in RUNTIME_FILES:
            shutil.copy2(build / "bin" / name, RUNTIME / name)
        shutil.copy2(source / "LICENSE", RUNTIME / "chatterbox-LICENSE.txt")
        shutil.copy2(source / "ggml/LICENSE", RUNTIME / "ggml-LICENSE.txt")

    def _convert(self, work: Path, source: Path) -> None:
        converter, checkpoint = work / "converter", work / "checkpoint"
        venv.EnvBuilder(with_pip=True).create(converter)
        python = str(converter / "Scripts/python.exe")
        pip = [python, "-m", "pip", "--isolated", "install", "--no-cache-dir",
               "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
        subprocess.run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
        subprocess.run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3",
                        "scipy==1.15.3", "librosa==0.11.0", "huggingface-hub==0.34.4"], check=True)
        for name in CHECKPOINT_FILES:
            self._download(f"{NANO_URL}/{name}", checkpoint / name)
        for script, model_args, output in (
            (CONVERTERS[0], ("--model", "nano"), T3),
            (CONVERTERS[1], ("--variant", "turbo"), S3),
        ):
            converted = work / output.name
            subprocess.run([python, str(source / "scripts" / script), *model_args,
                            "--ckpt-dir", str(checkpoint), "--out", str(converted), "--quant", "q4_0"],
                           cwd=work, check=True)
            MODELS.mkdir(parents=True, exist_ok=True)
            converted.replace(output)

    def install(self, cmake: str = "cmake", vulkan_sdk: Path = Path("tools/vulkan-sdk")) -> None:
        required = [*(RUNTIME / name for name in RUNTIME_FILES), T3, S3, VOICE,
                    RUNTIME / "chatterbox-LICENSE.txt", RUNTIME / "ggml-LICENSE.txt",
                    MODELS / "nano-model-card.md", VOICE.with_suffix(".md")]
        if all(path.is_file() for path in required):
            print("Nano TTS is installed.")
            return
        # Everything needed only for compilation/conversion lives under this directory.
        with tempfile.TemporaryDirectory(prefix=".nano-install-", dir=ROOT) as temporary:
            work = Path(temporary)
            source = work / "chatterbox"
            self._checkout("https://github.com/wgabrys88/chatterbox.cpp.git", CHATTERBOX_REV, source,
                           ("/CMakeLists.txt", "/LICENSE", "/src/", "/include/",
                            *(f"/scripts/{name}" for name in CONVERTERS)))
            self._build(work, source, cmake, vulkan_sdk)
            self._convert(work, source)
            self._download(f"{VOICE_URL}/audio/donald-trump.wav", VOICE, VOICE_SHA)
            self._download(f"{NANO_URL}/README.md", MODELS / "nano-model-card.md")
            self._download(f"{VOICE_URL}/README.md", VOICE.with_suffix(".md"))
        print("Nano TTS is installed.")


class NanoTTS:
    @staticmethod
    def _chunks(text: str) -> list[str]:
        chunks, current = [], ""
        for sentence in re.split(r"(?<=[.!?\u2026])\s+", " ".join(text.split())):
            if len(sentence) > CHUNK_CHARS:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(textwrap.wrap(sentence, width=CHUNK_CHARS, break_on_hyphens=False))
            elif len(candidate := f"{current} {sentence}".strip()) <= CHUNK_CHARS:
                current = candidate
            else:
                chunks.append(current)
                current = sentence
        if current:
            chunks.append(current)
        if not chunks:
            raise ValueError("TTS input is empty")
        return chunks

    @staticmethod
    def _send(sock: socket.socket, kind: int, piece: int = 0, text: str = "") -> None:
        payload = text.encode("utf-8")
        sock.sendall(REQUEST.pack(MAGIC, VERSION, kind, 0, 0, piece, len(payload)) + payload)

    @staticmethod
    def _receive(reader) -> tuple[int, int, int, bytes]:
        header = reader.read(RESPONSE.size)
        if len(header) != RESPONSE.size:
            raise EOFError("Native TTS closed the connection")
        magic, version, kind, epoch, response, piece, chunk, length = RESPONSE.unpack(header)
        # Closing advances the native epoch; synthesis responses stay at epoch zero.
        if (magic, version) != (MAGIC, VERSION) or (kind != 5 and (epoch, response) != (0, 0)):
            raise RuntimeError("Unexpected TTS response header")
        payload = reader.read(length)
        if len(payload) != length:
            raise EOFError("Incomplete native TTS audio frame")
        if kind == 4:
            raise RuntimeError(payload.decode("utf-8", errors="replace"))
        return kind, piece, chunk, payload

    def synthesize(self, text: str, output: Path) -> Path:
        pieces = self._chunks(text)
        output = (ROOT / output).resolve()
        command = [str(RUNTIME / "chatterbox-server.exe"), "--run-id", "nano", "--family", "nano",
                   "--model", str(T3), "--s3gen-gguf", str(S3), "--reference", str(VOICE),
                   "--language", "en", "--port", str(PORT)]
        command.extend(arg for name, value in KNOBS.items() for arg in (f"--{name}", str(value)))
        process = subprocess.Popen(command, cwd=RUNTIME, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        ready, finished = threading.Event(), threading.Event()

        def drain() -> None:
            for line in process.stdout:
                print(line.decode("utf-8", errors="replace").rstrip(), file=sys.stderr, flush=True)
                if b"server.ready" in line:
                    ready.set()
            finished.set()

        logs = threading.Thread(target=drain, daemon=True)
        logs.start()
        try:
            deadline = time.monotonic() + 300
            while not ready.wait(.1):
                if finished.is_set():
                    raise RuntimeError("Native TTS failed to start; see native diagnostics above")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Native TTS startup timed out")
            output.parent.mkdir(parents=True, exist_ok=True)
            with socket.create_connection(("127.0.0.1", PORT), timeout=3600) as sock, sock.makefile("rb") as reader:
                started, pcm_bytes = time.perf_counter(), 0
                with wave.open(str(output), "wb") as wav:
                    wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                    for piece_id, piece in enumerate(pieces):
                        self._send(sock, 1, piece_id, piece)
                        before = pcm_bytes
                        while True:
                            kind, returned_piece, chunk, payload = self._receive(reader)
                            if returned_piece != piece_id:
                                raise RuntimeError("Unexpected TTS piece")
                            if kind == 2:
                                break
                            if kind != 1:
                                raise RuntimeError(f"Unexpected TTS response kind: {kind}")
                            # Remove only the first piece's 20 ms opening zeros; keep later joins.
                            zeros = RATE // 50 * 2
                            if piece_id == 0 and chunk == 0 and len(payload) > zeros and payload[:zeros] == b"\0" * zeros:
                                payload = payload[zeros:]
                            wav.writeframesraw(payload)
                            pcm_bytes += len(payload)
                        if pcm_bytes == before:
                            raise RuntimeError(f"TTS piece {piece_id} produced no audio")
                elapsed = time.perf_counter() - started
                sock.settimeout(10)
                self._send(sock, 3)
                if self._receive(reader)[0] != 5:
                    raise RuntimeError("Native TTS did not acknowledge close")
            if process.wait(timeout=10):
                raise RuntimeError(f"Native TTS exited with code {process.returncode}")
            duration = pcm_bytes / (RATE * 2)
            print(f"audio_s={duration:.3f} synthesis_s={elapsed:.3f} synthesis_rtf={elapsed / duration:.3f}",
                  file=sys.stderr)
            return output
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            logs.join()
            process.stdout.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="Build the native server and convert Nano models")
    install.add_argument("--cmake", default="cmake", help="CMake executable (install only)")
    install.add_argument("--vulkan-sdk", type=Path, default=Path("tools/vulkan-sdk"),
                         help="Installed Vulkan SDK path (default: tools/vulkan-sdk)")
    run = commands.add_parser("run", help="Synthesize English text to a WAV file")
    text = run.add_mutually_exclusive_group(required=True)
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    run.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "install":
        NanoInstaller().install(args.cmake, args.vulkan_sdk)
    else:
        source = args.text if args.text is not None else (ROOT / args.text_file).read_text(encoding="utf-8")
        print(NanoTTS().synthesize(source, args.out))


if __name__ == "__main__":
    main()
