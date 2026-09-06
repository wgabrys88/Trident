from __future__ import annotations
import argparse, hashlib, json, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request, venv, wave
from collections import deque
from pathlib import Path

from _log import emit, span

ROOT = Path(__file__).resolve().parent
TTS_RUNTIME = ROOT / "tools/runtime/tts"
TTS_MODELS = ROOT / "models"
TTS_VOICE = ROOT / "data/ref-trump.wav"
TTS_PORTS = (17933, 17935, 17936)
TTS_FILES = ("chatterbox-server.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll")
TTS_LOG = ROOT / ".runtime-logs/tts.log"
TTS_RATE = 24000
TTS_REQUEST, TTS_RESPONSE = struct.Struct("<7I"), struct.Struct("<8I")
VOICE_URL = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4"
VOICE_SHA = "9d8b44d73192e9c04dd241f16177e4c5753bcefadde69e6e24b45e278b821f8c"


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


def _listener_command(port: int) -> str:
    script = (
        f"$c = Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction Stop | Select-Object -First 1; "
        "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\" -ErrorAction Stop; "
        "$p.CommandLine"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _prepare_tts(port: int, run_id: str) -> bool:
    reuse = False
    for active in TTS_PORTS:
        if not _port_in_use(active):
            continue
        command = _listener_command(active)
        if "chatterbox-server.exe" not in command.lower():
            raise RuntimeError(f"TTS port {active} belongs to: {command}")
        if active == port and f"--run-id {run_id}" in command:
            reuse = True
        else:
            _kill_port(active)
    return not reuse


def _unload_tts(family: str | None = None) -> None:
    for port in TTS_PORTS:
        if not _port_in_use(port):
            continue
        command = _listener_command(port)
        if "chatterbox-server.exe" not in command.lower():
            raise RuntimeError(f"TTS port {port} belongs to: {command}")
        if family is None or f"--family {family}" in command:
            _kill_port(port)


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


class Chatterbox:
    family = language = model_url = hf_folder = output_tag = ""
    port = 0
    t3 = s3 = model_card = Path()
    chatterbox_rev = ggml_rev = native_pin = ""
    conversions = extra_scripts = ()
    knobs = {}

    def __init__(self) -> None:
        self.proc = self.log = None
        self.run_id = ""

    @property
    def runtime_ok(self) -> bool:
        stamp = TTS_RUNTIME / "REVISION"
        files = [*(TTS_RUNTIME / name for name in TTS_FILES), TTS_RUNTIME / "chatterbox-LICENSE.txt",
                 TTS_RUNTIME / "ggml-LICENSE.txt"]
        return all(path.is_file() for path in files) and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == self.native_pin

    def _source(self, work: Path) -> Path:
        source = work / "chatterbox"
        scripts = {spec[0] for spec in self.conversions} | set(self.extra_scripts)
        _checkout("https://github.com/wgabrys88/chatterbox.cpp.git", self.chatterbox_rev, source,
                  ("/CMakeLists.txt", "/LICENSE", "/src/", "/include/", *(f"/scripts/{name}" for name in scripts)))
        return source

    def _build(self, work: Path, source: Path) -> None:
        _checkout("https://github.com/ggml-org/ggml.git", self.ggml_rev, source / "ggml",
                  ("/CMakeLists.txt", "/LICENSE", "/cmake/", "/include/", "/src/*", "!/src/*/",
                   "/src/ggml-cpu/", "/src/ggml-vulkan/"))
        subprocess.run(["git", "-C", str(source / "ggml"), "apply", "--whitespace=nowarn",
                        str(source / "src/ggml-vulkan-queue.patch")], check=True)
        build = work / "build"
        sdk = Path("C:/VulkanSDK/1.4.357.0")
        cmake = "C:/Program Files/CMake/bin/cmake.exe"
        subprocess.run([
            cmake, "-S", str(source), "-B", str(build), "-G", "Visual Studio 17 2022", "-A", "x64",
            "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF",
            "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON", "-DTTS_CPP_BUILD_TESTS=OFF",
            "-DGGML_BUILD_TESTS=OFF", "-DGGML_BUILD_EXAMPLES=OFF",
            f"-DVulkan_INCLUDE_DIR={sdk / 'Include'}", f"-DVulkan_LIBRARY={sdk / 'Lib/vulkan-1.lib'}",
            f"-DVulkan_GLSLC_EXECUTABLE={sdk / 'Bin/glslc.exe'}"], check=True)
        subprocess.run([cmake, "--build", str(build), "--config", "Release", "--target",
                        "chatterbox-server", "--parallel", "4"], check=True)
        _unload_tts()
        if TTS_RUNTIME.exists():
            shutil.rmtree(TTS_RUNTIME)
        TTS_RUNTIME.mkdir(parents=True)
        for name in TTS_FILES:
            shutil.copy2(build / "bin" / name, TTS_RUNTIME / name)
        shutil.copy2(source / "LICENSE", TTS_RUNTIME / "chatterbox-LICENSE.txt")
        shutil.copy2(source / "ggml/LICENSE", TTS_RUNTIME / "ggml-LICENSE.txt")
        (TTS_RUNTIME / "REVISION").write_text(self.native_pin + "\n", encoding="utf-8")

    def _convert(self, work: Path, source: Path, missing: list) -> None:
        converter, checkpoint = work / "converter", work / "checkpoint"
        venv.EnvBuilder(with_pip=True).create(converter)
        python = str(converter / "Scripts/python.exe")
        pip = [python, "-m", "pip", "--isolated", "install", "--no-cache-dir",
               "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
        subprocess.run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
        subprocess.run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3",
                        "huggingface-hub==0.34.4"], check=True)
        checkpoint.mkdir()
        for script, args, output, quant, files in missing:
            for name in files:
                path = checkpoint / name
                if not path.is_file():
                    _download(f"{self.model_url}/{name}", path)
            converted = work / output.name
            subprocess.run([python, str(source / "scripts" / script), *args, "--ckpt-dir", str(checkpoint),
                            "--out", str(converted), "--quant", quant], cwd=work, check=True)
            TTS_MODELS.mkdir(parents=True, exist_ok=True)
            converted.replace(output)

    def install(self, from_hf: bool = False) -> None:
        missing = [spec for spec in self.conversions if not spec[2].is_file()]
        complete = self.runtime_ok and not missing and self.model_card.is_file() and TTS_VOICE.is_file() and TTS_VOICE.with_suffix(".md").is_file()
        if complete:
            print(f"[{self.family}] install | skip {self.native_pin}", flush=True)
            return
        print(f"[{self.family}] install | runtime={self.runtime_ok} models={2-len(missing)}/2", flush=True)
        with tempfile.TemporaryDirectory(prefix=f".{self.family}-install-", dir=ROOT) as tmp:
            work = Path(tmp)
            source = self._source(work) if not self.runtime_ok or missing and not from_hf else None
            if not self.runtime_ok:
                self._build(work, source)
            if from_hf:
                from hf_pull import pull
                for output in (self.t3, self.s3):
                    if not output.is_file():
                        pull(self.hf_folder, output.name, output)
                if not self.model_card.is_file():
                    pull(self.hf_folder, "README.md", self.model_card)
            else:
                if missing:
                    self._convert(work, source, missing)
                if not self.model_card.is_file():
                    _download(f"{self.model_url}/README.md", self.model_card)
            if not TTS_VOICE.is_file():
                TTS_VOICE.parent.mkdir(parents=True, exist_ok=True)
                if from_hf:
                    from hf_pull import pull
                    pull("voices", "ref-trump.wav", TTS_VOICE)
                else:
                    _download(f"{VOICE_URL}/audio/donald-trump.wav", TTS_VOICE, VOICE_SHA)
            if not TTS_VOICE.with_suffix(".md").is_file():
                if from_hf:
                    from hf_pull import pull
                    pull("voices", "README.md", TTS_VOICE.with_suffix(".md"))
                else:
                    _download(f"{VOICE_URL}/README.md", TTS_VOICE.with_suffix(".md"))

    def command(self, language: str) -> list:
        run_id = self.family if self.family != "v3" else f"v3-{language}"
        cmd = [str(TTS_RUNTIME / "chatterbox-server.exe"), "--run-id", run_id, "--family", self.family,
               "--model", str(self.t3), "--s3gen-gguf", str(self.s3), "--reference", str(TTS_VOICE),
               "--language", language, "--port", str(self.port)]
        cmd.extend(arg for name, value in self.knobs.items() for arg in (f"--{name}", str(value)))
        return cmd

    def start(self, language: str = "") -> Chatterbox:
        language = language or self.language
        command = self.command(language)
        run_id = command[command.index("--run-id") + 1]
        if self.proc is not None and self.proc.poll() is None:
            if self.run_id == run_id:
                return self
            self.stop()
        if not _prepare_tts(self.port, run_id):
            emit(f"[{self.family}] ready")
            return self
        TTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        self.log = TTS_LOG.open("ab", buffering=0)
        offset = self.log.tell()
        self.proc = subprocess.Popen(command, cwd=TTS_RUNTIME, stdin=subprocess.DEVNULL,
                                     stdout=self.log, stderr=self.log, creationflags=subprocess.CREATE_NO_WINDOW)
        self.run_id = run_id
        deadline = time.monotonic() + 120
        with TTS_LOG.open("r", encoding="utf-8", errors="replace") as reader:
            reader.seek(offset)
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"chatterbox-server died with code {self.proc.returncode}")
                line = reader.readline()
                if f" server.ready" in line and f"| {self.family}" in line:
                    emit(f"[{self.family}] ready")
                    return self
                if not line:
                    time.sleep(.1)
        self.proc.kill()
        raise TimeoutError("chatterbox-server startup timed out")

    def load(self, language: str = "", from_hf: bool = False) -> Chatterbox:
        self.install(from_hf)
        import chunk
        chunk.install()
        return self.start(language)

    def stop(self) -> None:
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait()
            self.proc = None
        if self.log is not None:
            self.log.close()
            self.log = None
        _unload_tts(self.family)

    def synthesize(self, text: str, language: str = "") -> Path:
        self.start(language)
        chunker = ROOT / "tools/runtime/chunker/Scripts/python.exe"
        result = subprocess.run([str(chunker), str(ROOT / "chunk.py")], input=text, capture_output=True,
                                text=True, encoding="utf-8", check=True)
        if result.stderr:
            sys.stderr.write(result.stderr)
        pieces = json.loads(result.stdout)
        if not pieces:
            raise ValueError("TTS input is empty")
        output = ROOT / f"out_{time.strftime('%d-%m-%y-%H-%M-%S')}_{self.output_tag}.wav"
        with socket.create_connection(("127.0.0.1", self.port), timeout=300) as sock, sock.makefile("rb") as reader:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for piece, text in enumerate(pieces):
                self._send(sock, 1, piece, text)
            pcm = 0
            with output.open("xb") as target, wave.open(target, "wb") as wav:
                wav.setparams((1, 2, TTS_RATE, 0, "NONE", "not compressed"))
                for piece in range(len(pieces)):
                    before = pcm
                    while True:
                        kind, returned, block, payload = self._receive(reader)
                        if returned != piece:
                            raise RuntimeError("Unexpected TTS piece")
                        if kind == 2:
                            break
                        if kind != 1:
                            raise RuntimeError(f"Unexpected TTS response kind: {kind}")
                        silence = TTS_RATE // 50 * 2
                        if piece == 0 and block == 0 and len(payload) > silence and payload[:silence] == b"\0" * silence:
                            payload = payload[silence:]
                        wav.writeframesraw(payload)
                        pcm += len(payload)
                    if pcm == before:
                        raise RuntimeError(f"TTS piece {piece} produced no audio")
            sock.settimeout(10)
            self._send(sock, 3)
            if self._receive(reader)[0] != 5:
                raise RuntimeError("TTS did not acknowledge close")
        return output

    @staticmethod
    def _send(sock, kind: int, piece: int = 0, text: str = "") -> None:
        payload = text.encode("utf-8")
        sock.sendall(TTS_REQUEST.pack(0x32525454, 2, kind, 0, 0, piece, len(payload)) + payload)

    @staticmethod
    def _receive(reader) -> tuple:
        header = reader.read(TTS_RESPONSE.size)
        if len(header) != TTS_RESPONSE.size:
            raise EOFError("Native TTS closed the connection")
        magic, version, kind, epoch, response, piece, block, length = TTS_RESPONSE.unpack(header)
        if (magic, version) != (0x32525454, 2) or kind != 5 and (epoch, response) != (0, 0):
            raise RuntimeError("Unexpected TTS response header")
        payload = reader.read(length)
        if len(payload) != length:
            raise EOFError("Incomplete native TTS audio frame")
        if kind == 4:
            raise RuntimeError(payload.decode("utf-8", errors="replace"))
        return kind, piece, block, payload


def tts_cli(model: Chatterbox, languages: tuple = ()) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--install", action="store_true")
    actions.add_argument("--load", action="store_true")
    actions.add_argument("--unload", action="store_true")
    parser.add_argument("--from-hf", action="store_true")
    if languages:
        parser.add_argument("--language", choices=languages, default=model.language)
    text = parser.add_mutually_exclusive_group()
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    args = parser.parse_args()
    language = args.language if languages else model.language
    if args.install:
        model.install(args.from_hf)
    elif args.load:
        model.load(language, args.from_hf)
    elif args.unload:
        model.stop()
    else:
        source = (args.text if args.text is not None else
                  (ROOT / args.text_file).resolve().read_text(encoding="utf-8") if args.text_file else
                  (ROOT / "brain_out.txt").read_text(encoding="utf-8"))
        with span(f"tts_{model.family}.py"):
            model.start(language)
            started = time.perf_counter()
            output = model.synthesize(source, language)
            elapsed = time.perf_counter() - started
            (ROOT / "tts_out.wav").write_bytes(output.read_bytes())
            with wave.open(str(output)) as wav:
                duration = wav.getnframes() / wav.getframerate()
            emit(f"[rtf] tts_synth={elapsed:.3f}s audio_s={duration:.3f}s rtf={elapsed/duration:.2f}")


def _drain(proc: subprocess.Popen, ready_event: threading.Event, ready_str: str, tail: deque) -> None:
    for line in proc.stdout:
        tail.append(line.rstrip())
        if ready_str in line:
            ready_event.set()


def _wait_ready(proc: subprocess.Popen, ready_event: threading.Event, tail: deque,
                timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while not ready_event.wait(0.05):
        if proc.poll() is not None:
            raise RuntimeError(f"Process died: {proc.returncode}\n" + "\n".join(tail))
        if time.monotonic() >= deadline:
            proc.kill()
            raise TimeoutError(f"Startup timed out\n" + "\n".join(tail))
