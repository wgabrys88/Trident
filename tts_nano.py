import argparse, hashlib, re, shutil, socket, struct, subprocess, sys, tempfile, textwrap, threading, time, urllib.request, venv, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "tools/runtime/tts"
MODELS = ROOT / "models"
VOICE = ROOT / "data/ref-trump.wav"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN_SDK = Path("C:/VulkanSDK/1.4.357.0")
BUILD_THREADS = 4
LANGUAGE = "en"
TIMESTAMP_FORMAT = "%d-%m-%y-%H-%M-%S"
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
KNOBS = {"n-gpu-layers": 99, "context": 2048, "threads": 4, "fastconv": 1, "seed": 42,
         "max-tokens": 1000, "top-k": 1000, "top-p": .95, "min-p": .05,
         "temperature": .8, "repeat-penalty": 1.2, "cfm-steps": 1,
         "cfg-weight": .5, "exaggeration": .5}
RATE, CHUNK_CHARS, PORT = 24000, 50, 17933
MAGIC, VERSION = 0x32525454, 2
REQUEST, RESPONSE = struct.Struct("<7I"), struct.Struct("<8I")


def _download(url: str, path: Path, sha: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NanoTTS/1"})
        with urllib.request.urlopen(req, timeout=120) as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        if sha:
            with partial.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != sha:
                    raise RuntimeError(f"Download checksum mismatch: {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def _checkout(url: str, rev: str, path: Path, patterns: tuple) -> None:
    subprocess.run(["git", "init", str(path)], check=True)
    git = ["git", "-C", str(path)]
    for args in (("remote", "add", "origin", url), ("config", "remote.origin.promisor", "true"),
                 ("config", "remote.origin.partialclonefilter", "blob:none"),
                 ("fetch", "--depth=1", "--filter=blob:none", "--no-tags", "origin", rev)):
        subprocess.run([*git, *args], check=True)
    subprocess.run([*git, "sparse-checkout", "set", "--no-cone", "--stdin"],
                   input="\n".join(patterns) + "\n", text=True, check=True)
    subprocess.run([*git, "checkout", "--detach", rev], check=True)


def _build(work: Path, source: Path) -> None:
    _checkout("https://github.com/ggml-org/ggml.git", GGML_REV, source / "ggml",
              ("/CMakeLists.txt", "/LICENSE", "/cmake/", "/include/", "/src/*", "!/src/*/",
               "/src/ggml-cpu/", "/src/ggml-vulkan/"))
    build = work / "build"
    subprocess.run([
        CMAKE, "-S", str(source), "-B", str(build), "-G", "Visual Studio 17 2022", "-A", "x64",
        "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF",
        "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON", "-DTTS_CPP_BUILD_TESTS=OFF",
        "-DGGML_BUILD_TESTS=OFF", "-DGGML_BUILD_EXAMPLES=OFF",
        f"-DVulkan_INCLUDE_DIR={VULKAN_SDK / 'Include'}", f"-DVulkan_LIBRARY={VULKAN_SDK / 'Lib/vulkan-1.lib'}",
        f"-DVulkan_GLSLC_EXECUTABLE={VULKAN_SDK / 'Bin/glslc.exe'}",
    ], check=True)
    subprocess.run([CMAKE, "--build", str(build), "--config", "Release", "--target", "chatterbox-server",
                    "--parallel", str(BUILD_THREADS)], check=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES:
        shutil.copy2(build / "bin" / name, RUNTIME / name)
    shutil.copy2(source / "LICENSE", RUNTIME / "chatterbox-LICENSE.txt")
    shutil.copy2(source / "ggml/LICENSE", RUNTIME / "ggml-LICENSE.txt")


def _convert(work: Path, source: Path) -> None:
    converter, checkpoint = work / "converter", work / "checkpoint"
    venv.EnvBuilder(with_pip=True).create(converter)
    python = str(converter / "Scripts/python.exe")
    pip = [python, "-m", "pip", "--isolated", "install", "--no-cache-dir",
           "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
    subprocess.run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
    subprocess.run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3",
                    "scipy==1.15.3", "librosa==0.11.0", "huggingface-hub==0.34.4"], check=True)
    for name in CHECKPOINT_FILES:
        _download(f"{NANO_URL}/{name}", checkpoint / name)
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


def _install() -> None:
    required = [*(RUNTIME / n for n in RUNTIME_FILES), T3, S3, VOICE,
                RUNTIME / "chatterbox-LICENSE.txt", RUNTIME / "ggml-LICENSE.txt",
                MODELS / "nano-model-card.md", VOICE.with_suffix(".md")]
    if all(p.is_file() for p in required):
        return
    with tempfile.TemporaryDirectory(prefix=".nano-install-", dir=ROOT) as tmp:
        work = Path(tmp)
        source = work / "chatterbox"
        _checkout("https://github.com/wgabrys88/chatterbox.cpp.git", CHATTERBOX_REV, source,
                  ("/CMakeLists.txt", "/LICENSE", "/src/", "/include/",
                   *(f"/scripts/{n}" for n in CONVERTERS)))
        _build(work, source)
        _convert(work, source)
        _download(f"{VOICE_URL}/audio/donald-trump.wav", VOICE, VOICE_SHA)
        _download(f"{NANO_URL}/README.md", MODELS / "nano-model-card.md")
        _download(f"{VOICE_URL}/README.md", VOICE.with_suffix(".md"))


def _command() -> list:
    cmd = [str(RUNTIME / "chatterbox-server.exe"), "--run-id", "nano", "--family", "nano",
           "--model", str(T3), "--s3gen-gguf", str(S3), "--reference", str(VOICE),
           "--language", LANGUAGE, "--port", str(PORT)]
    cmd.extend(arg for n, v in KNOBS.items() for arg in (f"--{n}", str(v)))
    return cmd


def _run_server() -> tuple:
    LOG = ROOT / ".runtime-logs/tts_serve.log"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ready = threading.Event()
    def drain():
        with LOG.open("wb") as lf:
            for raw in proc.stdout:
                lf.write(raw)
                lf.flush()
                if b"server.ready" in raw:
                    ready.set()
    proc = subprocess.Popen(_command(), cwd=RUNTIME, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    if not ready.wait(timeout=120):
        proc.kill()
        raise TimeoutError("TTS server failed to start; see .runtime-logs/tts_serve.log")
    return proc


def _chunks(text: str) -> list:
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.!?\u2026])\s+", " ".join(text.split())):
        if len(sentence) > CHUNK_CHARS:
            if cur:
                out.append(cur)
                cur = ""
            out.extend(textwrap.wrap(sentence, width=CHUNK_CHARS, break_on_hyphens=False))
        elif len(c := f"{cur} {sentence}".strip()) <= CHUNK_CHARS:
            cur = c
        else:
            out.append(cur)
            cur = sentence
    if cur:
        out.append(cur)
    if not out:
        raise ValueError("TTS input is empty")
    return out


def _send(sock, kind: int, piece: int = 0, text: str = "") -> None:
    payload = text.encode("utf-8")
    sock.sendall(REQUEST.pack(MAGIC, VERSION, kind, 0, 0, piece, len(payload)) + payload)


def _receive(reader) -> tuple:
    header = reader.read(RESPONSE.size)
    if len(header) != RESPONSE.size:
        raise EOFError("Native TTS closed the connection")
    magic, version, kind, epoch, response, piece, chunk, length = RESPONSE.unpack(header)
    if (magic, version) != (MAGIC, VERSION) or (kind != 5 and (epoch, response) != (0, 0)):
        raise RuntimeError("Unexpected TTS response header")
    payload = reader.read(length)
    if len(payload) != length:
        raise EOFError("Incomplete native TTS audio frame")
    if kind == 4:
        raise RuntimeError(payload.decode("utf-8", errors="replace"))
    return kind, piece, chunk, payload


def _synthesize(text: str) -> Path:
    pieces = _chunks(text)
    output = ROOT / f"out_{time.strftime(TIMESTAMP_FORMAT)}_tts.wav"
    server = _run_server()
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=3600) as sock, sock.makefile("rb") as reader:
            pcm_bytes = 0
            with output.open("xb") as target, wave.open(target, "wb") as wav:
                wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                for piece_id, piece in enumerate(pieces):
                    _send(sock, 1, piece_id, piece)
                    before = pcm_bytes
                    while True:
                        kind, returned_piece, chunk, payload = _receive(reader)
                        if returned_piece != piece_id:
                            raise RuntimeError("Unexpected TTS piece")
                        if kind == 2:
                            break
                        if kind != 1:
                            raise RuntimeError(f"Unexpected TTS response kind: {kind}")
                        zeros = RATE // 50 * 2
                        if piece_id == 0 and chunk == 0 and len(payload) > zeros and payload[:zeros] == b"\0" * zeros:
                            payload = payload[zeros:]
                        wav.writeframesraw(payload)
                        pcm_bytes += len(payload)
                    if pcm_bytes == before:
                        raise RuntimeError(f"TTS piece {piece_id} produced no audio")
            sock.settimeout(10)
            _send(sock, 3)
            if _receive(reader)[0] != 5:
                raise RuntimeError("Native TTS did not acknowledge close")
    except Exception:
        output.unlink(missing_ok=True)
        server.kill()
        raise
    server.wait(timeout=30)
    return output


class NanoTTS:
    def synthesize(self, text: str) -> Path:
        return _synthesize(text)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    text = p.add_mutually_exclusive_group()
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    args = p.parse_args()
    if args.text is None and args.text_file is None:
        _install()
    src = args.text if args.text is not None else (ROOT / args.text_file).read_text(encoding="utf-8") if args.text_file else (ROOT / "brain_out.txt").read_text(encoding="utf-8")
    wav = _synthesize(src)
    (ROOT / "tts_out.wav").write_bytes(wav.read_bytes())
    print(ROOT / "tts_out.wav")
