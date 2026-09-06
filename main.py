import argparse, hashlib, json, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request, venv, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TTS_RUNTIME = ROOT / "tools/runtime/tts"
TTS_MODELS = ROOT / "models"
TTS_VOICE = ROOT / "data/ref-trump.wav"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN_SDK = Path("C:/VulkanSDK/1.4.357.0")
CHATTERBOX_REV = "c4e051e82f086b80c5379e4219e2693c15db90f8"
GGML_REV = "58c3805840b516b2a88ff867ccf7bb41dba79951"
NATIVE_PIN = f"{CHATTERBOX_REV} {GGML_REV}"
VOICE_URL = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4"
VOICE_SHA = "9d8b44d73192e9c04dd241f16177e4c5753bcefadde69e6e24b45e278b821f8c"
TTS_RUNTIME_FILES = ("chatterbox-server.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll")
TTS_RATE, TTS_MAGIC, TTS_VERSION = 24000, 0x32525454, 2
TTS_REQUEST, TTS_RESPONSE = struct.Struct("<7I"), struct.Struct("<8I")
TTS_CHUNKER = ROOT / "tools/runtime/chunker/Scripts/python.exe"
TTS_LOG = ROOT / ".runtime-logs/tts.log"


def _download(url: str, path: Path, sha: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Trident/1"})
        with urllib.request.urlopen(req, timeout=3600) as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst, 4 << 20)
        if sha:
            with partial.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != sha:
                    raise RuntimeError(f"Checksum mismatch: {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False


def _kill_port(port: int) -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "Get-NetTCPConnection -ErrorAction Stop | "
         f"Where-Object {{ $_.LocalPort -eq {port} -and $_.State -eq 'Listen' }} | "
         "Select-Object -ExpandProperty OwningProcess -Unique | "
         "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction Stop }"], check=True)


def _drain(proc: subprocess.Popen, ready_event: threading.Event, ready_str: str, tail: list) -> None:
    for line in proc.stdout:
        tail.append(line.rstrip())
        if ready_str in line:
            ready_event.set()


def _wait_ready(proc: subprocess.Popen, ready_event: threading.Event, tail: list,
                timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while not ready_event.wait(0.05):
        if proc.poll() is not None:
            raise RuntimeError(f"Process died: {proc.returncode}\n" + "\n".join(tail))
        if time.monotonic() >= deadline:
            proc.kill()
            raise TimeoutError(f"Startup timed out\n" + "\n".join(tail))


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


def _build_tts(work: Path, source: Path) -> None:
    _checkout("https://github.com/ggml-org/ggml.git", GGML_REV, source / "ggml",
              ("/CMakeLists.txt", "/LICENSE", "/cmake/", "/include/", "/src/*", "!/src/*/",
               "/src/ggml-cpu/", "/src/ggml-vulkan/"))
    subprocess.run(["git", "-C", str(source / "ggml"), "apply", "--whitespace=nowarn",
                    str(source / "src/ggml-vulkan-queue.patch")], check=True)
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
                    "--parallel", "4"], check=True)
    TTS_RUNTIME.mkdir(parents=True, exist_ok=True)
    for name in TTS_RUNTIME_FILES:
        shutil.copy2(build / "bin" / name, TTS_RUNTIME / name)
    shutil.copy2(source / "LICENSE", TTS_RUNTIME / "chatterbox-LICENSE.txt")
    shutil.copy2(source / "ggml/LICENSE", TTS_RUNTIME / "ggml-LICENSE.txt")


def _convert_tts(spec: dict, work: Path, source: Path, outputs: tuple) -> None:
    converter, checkpoint = work / "converter", work / "checkpoint"
    venv.EnvBuilder(with_pip=True).create(converter)
    python = str(converter / "Scripts/python.exe")
    pip = [python, "-m", "pip", "--isolated", "install", "--no-cache-dir",
           "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
    subprocess.run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
    subprocess.run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3",
                    "scipy==1.15.3", "librosa==0.11.0", "huggingface-hub==0.34.4"], check=True)
    for name in spec["checkpoints"]:
        _download(f"{spec['url']}/{name}", checkpoint / name)
    for (script, model_args, quant), output in zip(spec["conversions"], outputs):
        converted = work / output.name
        subprocess.run([python, str(source / "scripts" / script), *model_args,
                        "--ckpt-dir", str(checkpoint), "--out", str(converted), "--quant", quant],
                       cwd=work, check=True)
        TTS_MODELS.mkdir(parents=True, exist_ok=True)
        converted.replace(output)


def install_tts(spec: dict) -> None:
    family, label = spec["family"], spec["label"]
    outputs = spec["models"]
    revision = TTS_RUNTIME / "REVISION"
    runtime = [*(TTS_RUNTIME / n for n in TTS_RUNTIME_FILES), TTS_RUNTIME / "chatterbox-LICENSE.txt",
               TTS_RUNTIME / "ggml-LICENSE.txt"]
    models = [*outputs, TTS_MODELS / spec["card"]]
    voice = [TTS_VOICE, TTS_VOICE.with_suffix(".md")]
    stamp = revision.read_text(encoding="utf-8").strip() if revision.is_file() else ""
    runtime_ok = all(p.is_file() for p in runtime) and stamp == NATIVE_PIN
    if all(p.is_file() for p in runtime) and not stamp:
        revision.write_text(NATIVE_PIN + "\n", encoding="utf-8")
        runtime_ok = True
    models_ok, voice_ok = all(p.is_file() for p in models), all(p.is_file() for p in voice)
    if runtime_ok and models_ok and voice_ok:
        print(f"[{label}] install | pin={NATIVE_PIN} skip")
    else:
        print(f"[{label}] install | runtime_ok={runtime_ok} models_ok={models_ok} voice_ok={voice_ok}")
        with tempfile.TemporaryDirectory(prefix=f".{family}-install-", dir=ROOT) as tmp:
            work, source = Path(tmp), Path(tmp) / "chatterbox"
            if not runtime_ok or not models_ok:
                print(f"[{label}] install | checkout {CHATTERBOX_REV}")
                converters = tuple(f"/scripts/{item[0]}" for item in spec["conversions"])
                _checkout("https://github.com/wgabrys88/chatterbox.cpp.git", CHATTERBOX_REV, source,
                          ("/CMakeLists.txt", "/LICENSE", "/src/", "/include/", *converters,
                           "/scripts/quant_policy.py"))
            if not runtime_ok:
                print(f"[{label}] install | building")
                _build_tts(work, source)
                revision.write_text(NATIVE_PIN + "\n", encoding="utf-8")
            if not models_ok:
                print(f"[{label}] install | converting")
                _convert_tts(spec, work, source, outputs)
                _download(f"{spec['url']}/README.md", TTS_MODELS / spec["card"])
            if not voice_ok:
                print(f"[{label}] install | voice")
                _download(f"{VOICE_URL}/audio/donald-trump.wav", TTS_VOICE, VOICE_SHA)
                _download(f"{VOICE_URL}/README.md", TTS_VOICE.with_suffix(".md"))
            print(f"[{label}] install | done")
    import chunk as chunker
    chunker.install()


class TTS:
    def __init__(self, spec: dict) -> None:
        self.spec, self._proc, self._log_fh = spec, None, None

    def _emit(self, text: str) -> None:
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
        sys.stderr.flush()

    def _command(self, language: str) -> list:
        spec = self.spec
        t3, s3 = spec["models"]
        command = [str(TTS_RUNTIME / "chatterbox-server.exe"), "--run-id", spec["family"],
                   "--family", spec["family"], "--model", str(t3), "--s3gen-gguf", str(s3),
                   "--reference", str(TTS_VOICE), "--language", language, "--port", str(spec["port"])]
        command.extend(arg for name, value in spec["knobs"].items()
                       for arg in (f"--{name}", str(value)))
        return command

    def start(self, language: str = None) -> "TTS":
        language = self.spec["language"] if language is None else language
        if self._proc is not None and self._proc.poll() is None:
            return self
        if _port_in_use(self.spec["port"]):
            self._emit("start | port already listening")
            return self
        self._emit("start | spawning server")
        TTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = TTS_LOG.open("ab", buffering=0)
        pre_size = self._log_fh.tell()
        self._proc = subprocess.Popen(self._command(language), cwd=TTS_RUNTIME,
                                      stdin=subprocess.DEVNULL, stdout=self._log_fh,
                                      stderr=self._log_fh)
        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(0.5)
            if self._proc.poll() is not None:
                raise RuntimeError(f"server died with code {self._proc.poll()}")
            self._log_fh.flush()
            with TTS_LOG.open("r", encoding="utf-8", errors="replace") as log:
                log.seek(pre_size)
                for line in log:
                    if " server.ready" in line and f"| {self.spec['family']}" in line:
                        self._emit("start | ready")
                        return self
        self._proc.kill()
        raise TimeoutError("server startup timed out")

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()
            self._proc = None
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None
        _kill_port(self.spec["port"])

    def synthesize(self, text: str, language: str = None) -> Path:
        self.start(language)
        pieces = self._chunks(text)
        output = ROOT / f"out_{time.strftime('%d-%m-%y-%H-%M-%S')}_{self.spec['output']}.wav"
        with socket.create_connection(("127.0.0.1", self.spec["port"]), timeout=300) as sock, sock.makefile("rb") as reader:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for piece_id, piece in enumerate(pieces):
                self._send(sock, 1, piece_id, piece)
            pcm_bytes = 0
            with output.open("xb") as target, wave.open(target, "wb") as wav:
                wav.setparams((1, 2, TTS_RATE, 0, "NONE", "not compressed"))
                for piece_id in range(len(pieces)):
                    before = pcm_bytes
                    while True:
                        kind, returned_piece, chunk, payload = self._receive(reader)
                        if returned_piece != piece_id:
                            raise RuntimeError("Unexpected TTS piece")
                        if kind == 2:
                            break
                        if kind != 1:
                            raise RuntimeError(f"Unexpected TTS response kind: {kind}")
                        zeros = TTS_RATE // 50 * 2
                        if piece_id == 0 and chunk == 0 and len(payload) > zeros and payload[:zeros] == b"\0" * zeros:
                            payload = payload[zeros:]
                        wav.writeframesraw(payload)
                        pcm_bytes += len(payload)
                    if pcm_bytes == before:
                        raise RuntimeError(f"TTS piece {piece_id} produced no audio")
            sock.settimeout(10)
            self._send(sock, 3)
            if self._receive(reader)[0] != 5:
                raise RuntimeError("TTS did not acknowledge close")
        return output

    @staticmethod
    def _chunks(text: str) -> list:
        if not TTS_CHUNKER.is_file():
            raise RuntimeError("CPU chunker not installed")
        process = subprocess.run([str(TTS_CHUNKER), str(ROOT / "chunk.py")], input=text,
                                 capture_output=True, text=True, encoding="utf-8")
        if process.stderr:
            sys.stderr.write(process.stderr)
            sys.stderr.flush()
        if process.returncode:
            raise RuntimeError(process.stderr.strip() or "CPU chunker failed")
        pieces = json.loads(process.stdout)
        if not pieces:
            raise ValueError("TTS input is empty")
        return pieces

    @staticmethod
    def _send(sock, kind: int, piece: int = 0, text: str = "") -> None:
        payload = text.encode("utf-8")
        sock.sendall(TTS_REQUEST.pack(TTS_MAGIC, TTS_VERSION, kind, 0, 0, piece, len(payload)) + payload)

    @staticmethod
    def _receive(reader) -> tuple:
        header = reader.read(TTS_RESPONSE.size)
        if len(header) != TTS_RESPONSE.size:
            raise EOFError("Native TTS closed the connection")
        magic, version, kind, epoch, response, piece, chunk, length = TTS_RESPONSE.unpack(header)
        if (magic, version) != (TTS_MAGIC, TTS_VERSION) or (kind != 5 and (epoch, response) != (0, 0)):
            raise RuntimeError("Unexpected TTS response header")
        payload = reader.read(length)
        if len(payload) != length:
            raise EOFError("Incomplete native TTS audio frame")
        if kind == 4:
            raise RuntimeError(payload.decode("utf-8", errors="replace"))
        return kind, piece, chunk, payload


def run_tts(spec: dict, tts_type: type) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--unload", action="store_true")
    if spec["multilingual"]:
        parser.add_argument("--language", default=spec["language"],
                            help="ISO 639-1 language code (e.g. en, fr, zh)")
    text = parser.add_mutually_exclusive_group()
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    args = parser.parse_args()
    language = args.language if spec["multilingual"] else spec["language"]
    tts = tts_type()
    if args.install:
        install_tts(spec)
        tts.start(language)
        return
    if args.load:
        install_tts(spec)
        tts.start(language)
        print(f"[{spec['label']}] ready", flush=True)
        input()
        tts.stop()
        return
    if args.unload:
        tts.stop()
        return
    source = (args.text if args.text is not None else
              (ROOT / args.text_file).read_text(encoding="utf-8") if args.text_file else
              (ROOT / "brain_out.txt").read_text(encoding="utf-8"))
    started = time.perf_counter()
    if not spec["multilingual"]:
        tts.start()
        synthesis = time.perf_counter()
    wav_path = tts.synthesize(source, language)
    finished = time.perf_counter()
    (ROOT / "tts_out.wav").write_bytes(wav_path.read_bytes())
    print(wav_path)
    with wave.open(str(wav_path)) as wav:
        duration = wav.getnframes() / wav.getframerate()
    if spec["multilingual"]:
        print(f"[rtf] v3_start={started:.3f}s", file=sys.stderr)
        print(f"[rtf] v3_synth={finished-started:.3f}s", file=sys.stderr)
        print(f"[rtf] v3_total={finished-started:.3f}s", file=sys.stderr)
    else:
        print(f"[rtf] tts_start={synthesis-started:.3f}s", file=sys.stderr)
        print(f"[rtf] tts_synth={finished-synthesis:.3f}s", file=sys.stderr)
        print(f"[rtf] tts_total={finished-started:.3f}s", file=sys.stderr)
    print(f"[rtf] audio_s={duration:.3f}s", file=sys.stderr)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="No arguments installs and loads all models.", allow_abbrev=False)
    command = parser.add_mutually_exclusive_group()
    command.add_argument("prompt", nargs="?", help="run Brain, TTS and Parakeet without installation")
    command.add_argument("--unload", action="store_true", help="stop all three model servers")
    args = parser.parse_args()
    mode = "unload" if args.unload else "install" if args.prompt is None else "pipeline"
    models = (("brain", "brain.py"), ("tts_nano", "tts_nano.py"), ("tts_turbo", "tts_turbo.py"),
              ("tts_v3", "tts_v3.py"), ("parakeet", "parakeet.py"))
    stages = ((("brain", "brain.py", (f"--request={args.prompt}",)),
               ("tts_nano", "tts_nano.py", ()), ("parakeet", "parakeet.py", ("tts_out.wav",)))
              if mode == "pipeline" else tuple((*model, ()) for model in models))
    started = time.perf_counter()
    log_path = ROOT / ".runtime-logs/main.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        def emit(text: str) -> None:
            print(text, flush=True)
            print(text, file=log)

        emit(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {mode} {args.prompt or ''}".rstrip())
        for name, script, request in stages:
            stage_started = time.perf_counter()
            emit(f"[{name}] {mode}")
            flags = request if mode == "pipeline" else (f"--{mode}",)
            with subprocess.Popen([sys.executable, "-u", script, *flags], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8") as process:
                for line in process.stdout:
                    emit(line.rstrip("\n"))
                code = process.wait()
            emit(f"[{name}] exit={code} wall_s={time.perf_counter()-stage_started:.3f}")
            if code:
                emit(f"[{mode}] failed wall_s={time.perf_counter()-started:.3f}")
                raise SystemExit(code)
        emit(f"[{mode}] done wall_s={time.perf_counter()-started:.3f}")


if __name__ == "__main__":
    main()
