import argparse, hashlib, json, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request, venv, wave
sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TTS_RUNTIME = ROOT / "tools/runtime/tts"
TTS_MODELS = ROOT / "models"
TTS_VOICE = ROOT / "ref-trump.wav"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN_SDK = Path("C:/VulkanSDK/1.4.357.0")
CHATTERBOX_REV = "f78621ced9187e400ebddb2ae341664307a6836d"
GGML_REV = "58c3805840b516b2a88ff867ccf7bb41dba79951"
NATIVE_PIN = f"{CHATTERBOX_REV} {GGML_REV}"
VOICE_URL = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4"
VOICE_SHA = "9d8b44d73192e9c04dd241f16177e4c5753bcefadde69e6e24b45e278b821f8c"
TTS_RUNTIME_FILES = ("chatterbox-server.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll")
TTS_RUNTIME_REQUIRED = (*TTS_RUNTIME_FILES, "chatterbox-LICENSE.txt", "ggml-LICENSE.txt")
TTS_RATE, TTS_MAGIC, TTS_VERSION = 24000, 0x32525454, 4
TTS_FRAME = struct.Struct("<7I")
TTS_CHUNKER = ROOT / "tools/runtime/chunker/Scripts/python.exe"
TTS_BASE_KNOBS = {"n-gpu-layers": 99, "fastconv": 1, "seed": 42, "max-tokens": 1000,
                  "top-k": 1000, "top-p": .95, "min-p": 0.0, "temperature": .8}


def tts_knobs(context: int, threads: int, cfm_steps: int, repeat_penalty: float = 1.2,
              cfg_weight: float = 0.0, exaggeration: float = 0.0) -> dict:
    return {**TTS_BASE_KNOBS, "context": context, "threads": threads, "repeat-penalty": repeat_penalty,
            "cfm-steps": cfm_steps, "cfg-weight": cfg_weight, "exaggeration": exaggeration}


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


def _sha(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _tts_runtime_ok() -> bool:
    revision = TTS_RUNTIME / "REVISION"
    return (len(CHATTERBOX_REV) == 40 and all((TTS_RUNTIME / name).is_file() for name in TTS_RUNTIME_REQUIRED)
            and revision.is_file() and revision.read_text(encoding="utf-8").strip() == NATIVE_PIN)


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


def _wait_port(proc: subprocess.Popen, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Process died: {proc.returncode}")
        if _port_in_use(port):
            return
        time.sleep(.1)
    proc.kill()
    raise TimeoutError(f"Process did not open port {port}")


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
    build = work / "b"
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


def _missing_conversions(spec: dict) -> list:
    return [(conversion, output) for conversion, output in zip(spec["conversions"], spec["models"]) if not output.is_file()]


def _convert_tts(spec: dict, work: Path, source: Path, missing: list) -> None:
    converter, checkpoint = work / "c", work / "k"
    venv.EnvBuilder(with_pip=True).create(converter)
    python = str(converter / "Scripts/python.exe")
    pip = [python, "-m", "pip", "--isolated", "install", "--no-cache-dir",
           "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
    subprocess.run([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], check=True)
    subprocess.run([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3",
                    "scipy==1.15.3", "librosa==0.11.0", "huggingface-hub==0.34.4"], check=True)
    assets = dict.fromkeys(name for (script, model_args, quant, files), output in missing for name in files)
    for name in assets:
        _download(f"{spec['url']}/{name}", checkpoint / name)
    TTS_MODELS.mkdir(parents=True, exist_ok=True)
    for (script, model_args, quant, files), output in missing:
        converted = work / output.name
        subprocess.run([python, str(source / "scripts" / script), *model_args,
                        "--ckpt-dir", str(checkpoint), "--out", str(converted), "--quant", quant],
                       cwd=work, check=True)
        converted.replace(output)


def install_tts(spec: dict, with_chunker: bool = True) -> None:
    if len(CHATTERBOX_REV) != 40:
        raise RuntimeError("Set CHATTERBOX_REV to the pushed chatterbox.cpp commit SHA before install")
    family, label = spec["family"], spec["label"]
    outputs = spec["models"]
    missing = _missing_conversions(spec)
    revision = TTS_RUNTIME / "REVISION"
    card = TTS_MODELS / spec["card"]
    voice_card = TTS_VOICE.with_suffix(".md")
    runtime_ok = _tts_runtime_ok()
    models_ok = not missing
    card_ok, voice_ok = card.is_file(), TTS_VOICE.is_file() and voice_card.is_file()
    if runtime_ok and models_ok and card_ok and voice_ok:
        print(f"[{label}] install | pin={NATIVE_PIN} skip")
    else:
        print(f"[{label}] install | runtime_ok={runtime_ok} models_ok={models_ok} card_ok={card_ok} voice_ok={voice_ok}")
        if not runtime_ok or not models_ok:
            (ROOT / "tools").mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f".{family[0]}-", dir=ROOT / "tools") as tmp:
                work, source = Path(tmp), Path(tmp) / "s"
                print(f"[{label}] install | checkout {CHATTERBOX_REV}")
                patterns = []
                if not runtime_ok:
                    patterns += ["/CMakeLists.txt", "/LICENSE", "/src/", "/include/"]
                if missing:
                    patterns += [*(f"/scripts/{conversion[0]}" for conversion, output in missing), "/scripts/quant_policy.py"]
                _checkout("https://github.com/wgabrys88/chatterbox.cpp.git", CHATTERBOX_REV, source, patterns)
                if not runtime_ok:
                    print(f"[{label}] install | building")
                    _build_tts(work, source)
                    revision.write_text(NATIVE_PIN + "\n", encoding="utf-8")
                if not models_ok:
                    print(f"[{label}] install | converting")
                    _convert_tts(spec, work, source, missing)
        if not card_ok:
            _download(f"{spec['url']}/README.md", card)
        if not voice_ok:
            print(f"[{label}] install | voice")
            if not TTS_VOICE.is_file():
                _download(f"{VOICE_URL}/audio/donald-trump.wav", TTS_VOICE, VOICE_SHA)
            if not voice_card.is_file():
                _download(f"{VOICE_URL}/README.md", voice_card)
        print(f"[{label}] install | done")
    if with_chunker:
        import chunk as chunker
        chunker.install()


class TTS:
    def __init__(self, spec: dict) -> None:
        self.spec, self._proc, self._log_fh, self._response_id = spec, None, None, 0
        self.stderr = b""
        self.emission = []
        self.pieces = []

    def _command(self, language: str) -> list:
        spec = self.spec
        t3, s3 = spec["models"]
        command = [str(TTS_RUNTIME / "chatterbox-server.exe"), "--model", str(t3), "--s3gen-gguf", str(s3),
                   "--reference", str(TTS_VOICE), "--language", language, "--port", str(spec["port"]),
                   "--audit-prefix", str(spec.get("audit_prefix", ""))]
        command.extend(arg for name, value in spec["knobs"].items()
                       for arg in (f"--{name}", str(value)))
        return command

    def start(self, language: str = None) -> "TTS":
        if not _tts_runtime_ok():
            raise RuntimeError("TTS runtime does not match the pinned chatterbox/GGML revision; run --install")
        language = self.spec["language"] if language is None else language
        if self._proc is not None and self._proc.poll() is None:
            return self
        if _port_in_use(self.spec["port"]):
            if self.spec.get("audit_prefix"):
                raise RuntimeError("Audited synthesis requires its own server; unload the existing server first")
            return self
        self._log_fh = tempfile.TemporaryFile() if self.spec.get("audit_prefix") else None
        self._proc = subprocess.Popen(self._command(language), cwd=TTS_RUNTIME, stdin=subprocess.DEVNULL,
                                      stdout=self._log_fh, stderr=self._log_fh)
        _wait_port(self._proc, self.spec["port"], 120)
        return self

    def stop(self) -> None:
        owned = self._proc is not None
        if owned:
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()
            self._proc = None
        if self._log_fh:
            self._log_fh.seek(0)
            self.stderr = self._log_fh.read()
            self._log_fh.close()
            self._log_fh = None
        if not owned and _port_in_use(self.spec["port"]):
            _kill_port(self.spec["port"])

    def synthesize(self, text: str) -> Path:
        self.pieces = [text] if self.spec.get("direct") else self._chunks(text)
        output = ROOT / "tts_out.wav"
        self._response_id += 1
        response = self._response_id
        with socket.create_connection(("127.0.0.1", self.spec["port"]), timeout=300) as sock, sock.makefile("rb") as reader:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for piece, text_piece in enumerate(self.pieces):
                self._send(sock, 1, response, piece, len(self.pieces), text_piece)
            samples = 0
            with output.open("xb") as target, wave.open(target, "wb") as wav:
                wav.setparams((1, 2, TTS_RATE, 0, "NONE", "not compressed"))
                for piece in range(len(self.pieces)):
                    before = samples
                    while True:
                        kind, returned_response, returned_piece, chunk, payload = self._receive(reader)
                        if (returned_response, returned_piece) != (response, piece):
                            raise RuntimeError("Unexpected TTS piece")
                        if kind == 2:
                            break
                        if kind != 1:
                            raise RuntimeError(f"Unexpected TTS response kind: {kind}")
                        wire_sha = hashlib.sha256(payload).hexdigest()
                        trim = TTS_RATE // 50 * 2
                        trim = trim if piece == 0 and chunk == 0 and len(payload) > trim and payload[:trim] == b"\\0" * trim else 0
                        payload = payload[trim:]
                        wav.writeframesraw(payload)
                        self.emission.append({"response": response, "piece": piece, "chunk": chunk,
                                              "utc_ns": time.time_ns(), "start": samples,
                                              "end": samples + len(payload)//2, "trimmed_bytes": trim,
                                              "wire_sha256": wire_sha})
                        samples += len(payload)//2
                    if samples == before:
                        raise RuntimeError(f"TTS piece {piece} produced no audio")
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
    def _send(sock, kind: int, response: int = 0, piece: int = 0, total: int = 0, text: str = "") -> None:
        payload = text.encode("utf-8")
        sock.sendall(TTS_FRAME.pack(TTS_MAGIC, TTS_VERSION, kind, response, piece, total, len(payload)) + payload)

    @staticmethod
    def _receive(reader) -> tuple:
        header = reader.read(TTS_FRAME.size)
        if len(header) != TTS_FRAME.size:
            raise EOFError("Native TTS closed the connection")
        magic, version, kind, response, piece, chunk, length = TTS_FRAME.unpack(header)
        if (magic, version) != (TTS_MAGIC, TTS_VERSION):
            raise RuntimeError("Unexpected TTS response header")
        payload = reader.read(length)
        if len(payload) != length:
            raise EOFError("Incomplete native TTS audio frame")
        if kind == 4:
            raise RuntimeError(payload.decode("utf-8", errors="replace"))
        return kind, response, piece, chunk, payload


def _provenance(spec: dict) -> None:
    files = [TTS_RUNTIME / name for name in TTS_RUNTIME_FILES]
    files += [*spec["models"], TTS_VOICE]
    print(f"pin chatterbox={CHATTERBOX_REV} ggml={GGML_REV}")
    for path in files:
        if not path.is_file():
            print(f"missing {path.relative_to(ROOT)}")
            continue
        print(f"sha256={_sha(path)} bytes={path.stat().st_size} path={path.relative_to(ROOT)}")


def run_tts(spec: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--unload", action="store_true")
    parser.add_argument("--provenance", action="store_true")
    parser.add_argument("--audit", action="store_true", help="record raw replay inputs and outputs")
    parser.add_argument("--direct", action="store_true", help="send exact text as one native piece, bypassing SaT")
    if spec["multilingual"]:
        parser.add_argument("--language", default=spec["language"],
                            help="ISO 639-1 language code (e.g. en, fr, zh)")
    text = parser.add_mutually_exclusive_group()
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    for name, default in spec["knobs"].items():
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=type(default), default=None)
    args = parser.parse_args()
    spec = {**spec, "knobs": dict(spec["knobs"])}
    spec["direct"] = args.direct
    if args.audit:
        audit_prefix = ROOT / ("audit-" +
            f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}-{spec['family']}"
        )
        spec["audit_prefix"] = audit_prefix
    else:
        spec["audit_prefix"] = ""
    for name in spec["knobs"]:
        value = getattr(args, name.replace("-", "_"))
        if value is not None:
            spec["knobs"][name] = value
    language = args.language if spec["multilingual"] else spec["language"]
    tts = TTS(spec)
    if args.provenance:
        _provenance(spec)
        return
    if args.install:
        install_tts(spec, with_chunker=not args.direct)
        tts.start(language)
        return
    if args.load:
        install_tts(spec, with_chunker=not args.direct)
        tts.start(language)
        print(f"[{spec['label']}] ready", flush=True)
        input()
        tts.stop()
        return
    if args.unload:
        tts.stop()
        return
    import platform
    from replay import pack
    log_path = ROOT / "tts_out.log.zip"
    wav_path = ROOT / "tts_out.wav"
    wav_path.unlink(missing_ok=True)
    log_path.unlink(missing_ok=True)
    source = (args.text if args.text is not None else
              (ROOT / args.text_file).read_text(encoding="utf-8") if args.text_file else
              (ROOT / "brain_out.txt").read_text(encoding="utf-8"))
    metadata = {"schema": 2, "family": spec["family"], "source": source, "settings": spec["knobs"],
                "language": language, "protocol": TTS_VERSION, "status": "FAILED",
                "native_pin": NATIVE_PIN, "direct": args.direct, "utc_ns": time.time_ns(),
                "platform": platform.platform(), "python": sys.version, "command": tts._command(language)}
    try:
        if args.audit:
            paths = [*(TTS_RUNTIME / name for name in TTS_RUNTIME_FILES), *spec["models"], TTS_VOICE]
            metadata["provenance"] = [{"file": str(path), "sha256": _sha(path), "bytes": path.stat().st_size}
                                      for path in paths]
            metadata["python_sources"] = [{"file": path.name, "sha256": _sha(path)}
                                          for path in sorted(ROOT.glob("*.py"))]
            drivers = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,PNPDeviceID | ConvertTo-Json -Compress"],
                capture_output=True, text=True, check=True)
            metadata["display_drivers"] = json.loads(drivers.stdout)
        tts.start(language)
        wav_path = tts.synthesize(source)
        metadata.update(status="CAPTURED" if args.audit else "SYNTHESIS_COMPLETE", wav_sha256=_sha(wav_path))
    except Exception as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        wav_path.unlink(missing_ok=True)
        raise
    finally:
        if tts._proc is not None:
            tts.stop()
        metadata.update(pieces=tts.pieces, emission=tts.emission)
        pack(log_path, metadata, tts.stderr, Path(str(spec["audit_prefix"]) + ".capture") if args.audit else None)
    print(f"{wav_path.name}\n{log_path.name}")



def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="No arguments installs and loads all models.", allow_abbrev=False)
    command = parser.add_mutually_exclusive_group()
    command.add_argument("prompt", nargs="?", help="run Brain, TTS and Parakeet without installation")
    command.add_argument("--unload", action="store_true", help="stop all three model servers")
    parser.add_argument("--audit", action="store_true",
                        help="run Nano with replayable diagnostic artifacts; not for RTF")
    args = parser.parse_args()
    mode = "unload" if args.unload else "install" if args.prompt is None else "pipeline"
    models = (("brain", "brain.py"), ("tts_nano", "tts_nano.py"), ("tts_turbo", "tts_turbo.py"),
              ("tts_v3", "tts_v3.py"), ("parakeet", "parakeet.py"))
    stages = ((("brain", "brain.py", (f"--request={args.prompt}",)),
               ("tts_nano", "tts_nano.py", ("--audit",) if args.audit else ()),
               ("parakeet", "parakeet.py", ("tts_out.wav",)))
              if mode == "pipeline" else tuple((*model, ()) for model in models))
    started = time.perf_counter()
    def emit(text: str) -> None:
        print(text, flush=True)

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
