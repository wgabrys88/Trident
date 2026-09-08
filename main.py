import argparse, hashlib, json, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request, venv, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TTS_RUNTIME = ROOT / "tools/runtime/tts"
TTS_MODELS = ROOT / "models"
TTS_VOICE = ROOT / "data/ref-trump.wav"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN_SDK = Path("C:/VulkanSDK/1.4.357.0")
CHATTERBOX_REV = "ac41675aefef56aabf3d445e4aa4baf144274643"
GGML_REV = "58c3805840b516b2a88ff867ccf7bb41dba79951"
NATIVE_PIN = f"{CHATTERBOX_REV} {GGML_REV}"
CHATTERBOX_URL = "https://github.com/wgabrys88/chatterbox.cpp.git"
CHATTERBOX_LOCAL = ROOT.parent / "chatterbox.cpp"
VOICE_URL = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4"
VOICE_SHA = "9d8b44d73192e9c04dd241f16177e4c5753bcefadde69e6e24b45e278b821f8c"
TTS_RUNTIME_FILES = ("chatterbox-server.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll")
TTS_RUNTIME_REQUIRED = (*TTS_RUNTIME_FILES, "chatterbox-LICENSE.txt", "ggml-LICENSE.txt")
TTS_RATE, TTS_MAGIC, TTS_VERSION = 24000, 0x32525454, 4
TTS_FRAME = struct.Struct("<7I")
TTS_CHUNKER = ROOT / "tools/runtime/chunker/Scripts/python.exe"
TTS_LOG = ROOT / ".runtime-logs/tts.log"
TTS_BASE_KNOBS = {"n-gpu-layers": 99, "fastconv": 1, "seed": 42, "max-tokens": 1000,
                  "top-k": 1000, "top-p": .95, "min-p": 0.0, "temperature": .8}
TTS_MIN_SPEECH_RATIO = 2.0
TTS_MIN_SPEECH_TOKENS = 16


def _dbg(hypothesis_id: str, location: str, message: str, **data) -> None:
    # #region agent log
    try:
        with (ROOT / "debug-de999f.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"sessionId": "de999f", "hypothesisId": hypothesis_id, "location": location,
                                 "message": message, "data": data, "timestamp": int(time.time() * 1000)},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def _native_ledger(piece_id: int, since: int) -> dict:
    if not TTS_LOG.is_file():
        return {}
    with TTS_LOG.open("rb") as fh:
        fh.seek(since)
        tail = fh.read().decode("utf-8", "replace")
    found = {}
    for line in tail.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            fields = json.loads(text)
        except json.JSONDecodeError:
            continue
        if fields.get("piece") == piece_id and "n_speech_tok" in fields:
            found = fields
    return found


def _guard_native_piece(piece_id: int, since: int) -> dict:
    native = _native_ledger(piece_id, since)
    if not native:
        return native
    n_text = int(native.get("n_text_tok", 0))
    n_speech = int(native.get("n_speech_tok", 0))
    eos_min = int(native.get("eos_min_speech", 0))
    floor = max(TTS_MIN_SPEECH_TOKENS, int(n_text * TTS_MIN_SPEECH_RATIO), eos_min)
    _dbg("A", "main.py:_guard_native_piece", "native_metrics", piece=piece_id,
         n_text_tok=n_text, n_speech_tok=n_speech, eos_min_speech=eos_min, floor=floor)
    if n_text > 5 and n_speech < floor:
        raise RuntimeError(
            f"TTS piece {piece_id} collapsed: n_speech_tok={n_speech} n_text_tok={n_text} floor={floor}")
    return native


def jsonl(event: str, *, file=None, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), file=file or sys.stderr, flush=True)


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


def _text_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _fnv64(data: bytes) -> str:
    value = 1469598103934665603
    for byte in data:
        value ^= byte
        value = (value * 1099511628211) & 0xffffffffffffffff
    return f"{value:016x}"


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
    origin = url
    if url == CHATTERBOX_URL and CHATTERBOX_LOCAL.is_dir():
        local_ok = subprocess.run(["git", "-C", str(CHATTERBOX_LOCAL), "cat-file", "-e", f"{rev}^{{commit}}"],
                                  capture_output=True).returncode == 0
        if local_ok:
            origin = str(CHATTERBOX_LOCAL.resolve())
            jsonl("tts.install.checkout.local", rev=rev, path=str(CHATTERBOX_LOCAL))
    subprocess.run(["git", "init", str(path)], check=True)
    git = ["git", "-C", str(path)]
    for args in (("remote", "add", "origin", origin), ("config", "remote.origin.promisor", "true"),
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
        "-DTTS_CPP_MTL=OFF",
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


def install_tts(spec: dict) -> None:
    if len(CHATTERBOX_REV) != 40:
        raise RuntimeError("Set CHATTERBOX_REV to the pushed chatterbox.cpp commit SHA before install")
    family = spec["family"]
    outputs = spec["models"]
    missing = _missing_conversions(spec)
    revision = TTS_RUNTIME / "REVISION"
    card = TTS_MODELS / spec["card"]
    voice_card = TTS_VOICE.with_suffix(".md")
    runtime_ok = _tts_runtime_ok()
    models_ok = not missing
    card_ok, voice_ok = card.is_file(), TTS_VOICE.is_file() and voice_card.is_file()
    if runtime_ok and models_ok and card_ok and voice_ok:
        jsonl("tts.install", family=family, pin=NATIVE_PIN, skip=True)
    else:
        jsonl("tts.install", family=family, runtime_ok=runtime_ok, models_ok=models_ok,
              card_ok=card_ok, voice_ok=voice_ok, skip=False)
        if not runtime_ok or not models_ok:
            with tempfile.TemporaryDirectory(prefix=f".{family[0]}-", dir=ROOT) as tmp:
                work, source = Path(tmp), Path(tmp) / "s"
                jsonl("tts.install.checkout", family=family, rev=CHATTERBOX_REV)
                patterns = []
                if not runtime_ok:
                    patterns += ["/CMakeLists.txt", "/LICENSE", "/src/", "/include/"]
                if missing:
                    patterns += [*(f"/scripts/{conversion[0]}" for conversion, output in missing), "/scripts/quant_policy.py"]
                _checkout(CHATTERBOX_URL, CHATTERBOX_REV, source, patterns)
                if not runtime_ok:
                    jsonl("tts.install.build", family=family)
                    _build_tts(work, source)
                    revision.write_text(NATIVE_PIN + "\n", encoding="utf-8")
                if not models_ok:
                    jsonl("tts.install.convert", family=family)
                    _convert_tts(spec, work, source, missing)
        if not card_ok:
            _download(f"{spec['url']}/README.md", card)
        if not voice_ok:
            jsonl("tts.install.voice", family=family)
            if not TTS_VOICE.is_file():
                _download(f"{VOICE_URL}/audio/donald-trump.wav", TTS_VOICE, VOICE_SHA)
            if not voice_card.is_file():
                _download(f"{VOICE_URL}/README.md", voice_card)
        jsonl("tts.install.done", family=family)
    import chunk as chunker
    chunker.install()


class TTS:
    def __init__(self, spec: dict) -> None:
        self.spec, self._proc, self._log_fh, self._response_id = spec, None, None, 0
        self.chunk_s = self.synth_s = 0.0

    def _command(self, language: str) -> list:
        spec = self.spec
        t3, s3 = spec["models"]
        command = [str(TTS_RUNTIME / "chatterbox-server.exe"), "--run-id", spec["family"],
                   "--family", spec["family"], "--model", str(t3), "--s3gen-gguf", str(s3),
                   "--reference", str(TTS_VOICE), "--language", language, "--port", str(spec["port"]),
                   "--audit-dir", str(spec.get("audit_dir", ""))]
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
            jsonl("tts.start", family=self.spec["family"], port=self.spec["port"], reused=True)
            return self
        jsonl("tts.start", family=self.spec["family"], port=self.spec["port"], spawning=True)
        TTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = TTS_LOG.open("ab", buffering=0)
        self._proc = subprocess.Popen(self._command(language), cwd=TTS_RUNTIME, stdin=subprocess.DEVNULL,
                                      stdout=self._log_fh, stderr=self._log_fh)
        _wait_port(self._proc, self.spec["port"], 120)
        jsonl("tts.ready", family=self.spec["family"], port=self.spec["port"])
        return self

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

    def synthesize(self, text: str) -> Path:
        chunk_t0 = time.perf_counter()
        pieces = self._chunks(text)
        self.chunk_s = time.perf_counter() - chunk_t0
        output = ROOT / f"out_{time.strftime('%d-%m-%y-%H-%M-%S')}_{self.spec['output']}.wav"
        self._response_id += 1
        response_id = self._response_id
        begin = {"event": "synth.begin", "response": response_id, "pieces": len(pieces),
                 "total_chars": sum(len(p) for p in pieces), "source_sha": _text_id(text),
                 "chunk_s": round(self.chunk_s, 3)}
        if self.spec.get("audit_dir"):
            audit_dir = Path(self.spec["audit_dir"])
            begin["audit_dir"] = str(audit_dir.relative_to(ROOT) if audit_dir.is_absolute() else audit_dir)
        print(json.dumps(begin, ensure_ascii=False), flush=True)
        synth_t0 = time.perf_counter()
        tts_log_offset = TTS_LOG.stat().st_size if TTS_LOG.is_file() else 0
        with socket.create_connection(("127.0.0.1", self.spec["port"]), timeout=300) as sock, sock.makefile("rb") as reader:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for piece_id, piece in enumerate(pieces):
                self._send(sock, 1, response_id, piece_id, len(pieces), piece)
            pcm_bytes = 0
            pieces_written = 0
            with output.open("xb") as target, wave.open(target, "wb") as wav:
                wav.setparams((1, 2, TTS_RATE, 0, "NONE", "not compressed"))
                for piece_id in range(len(pieces)):
                    piece_t0 = time.perf_counter()
                    before = pcm_bytes
                    leading_trim = 0
                    while True:
                        kind, returned_response, returned_piece, chunk, payload = self._receive(reader)
                        if returned_response != response_id or returned_piece != piece_id:
                            raise RuntimeError("Unexpected TTS piece")
                        if kind == 2:
                            break
                        if kind != 1:
                            raise RuntimeError(f"Unexpected TTS response kind: {kind}")
                        zeros = TTS_RATE // 50 * 2
                        if piece_id == 0 and chunk == 0 and len(payload) > zeros and payload[:zeros] == b"\0" * zeros:
                            payload = payload[zeros:]
                            leading_trim = zeros
                        wav.writeframesraw(payload)
                        pcm_bytes += len(payload)
                    if pcm_bytes == before:
                        raise RuntimeError(f"TTS piece {piece_id} produced no audio")
                    native = _guard_native_piece(piece_id, tts_log_offset)
                    piece_samples = (pcm_bytes - before) // 2
                    _dbg("E", "main.py:synthesize", "piece_pcm", piece=piece_id, chars=len(pieces[piece_id]),
                         pcm_samples=piece_samples, native=native)
                    jsonl("synth.piece", file=sys.stdout, response=response_id, piece=piece_id,
                          text=pieces[piece_id], chars=len(pieces[piece_id]),
                          sample_start=before // 2, sample_end=pcm_bytes // 2,
                          trimmed_leading_bytes=leading_trim,
                          wall_ms=int((time.perf_counter() - piece_t0) * 1000))
                    pieces_written += 1
            jsonl("synth.complete", file=sys.stdout, response=response_id,
                  pieces=pieces_written, samples=pcm_bytes // 2,
                  wav=output.name, sha256=_sha(output))
            sock.settimeout(10)
            self._send(sock, 3)
            if self._receive(reader)[0] != 5:
                raise RuntimeError("TTS did not acknowledge close")
        self.synth_s = time.perf_counter() - synth_t0
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
            log = TTS_LOG.parent / "chunk.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(process.stderr)
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
    jsonl("tts.provenance", chatterbox=CHATTERBOX_REV, ggml=GGML_REV)
    for path in files:
        rel = str(path.relative_to(ROOT))
        if not path.is_file():
            jsonl("tts.provenance.file", path=rel, missing=True)
            continue
        jsonl("tts.provenance.file", path=rel, sha256=_sha(path), bytes=path.stat().st_size)


def run_tts(spec: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--unload", action="store_true")
    parser.add_argument("--provenance", action="store_true")
    parser.add_argument("--audit", action="store_true",
                        help="capture replayable native stage artifacts; do not use for RTF")
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
    if args.audit:
        audit_dir = ROOT / ".runtime-logs" / "audit" / (
            f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}-{spec['family']}"
        )
        audit_dir.mkdir(parents=True, exist_ok=False)
        spec["audit_dir"] = audit_dir
        jsonl("tts.audit", dir=str(audit_dir.relative_to(ROOT)))
    else:
        spec["audit_dir"] = ""
    for name in spec["knobs"]:
        value = getattr(args, name.replace("-", "_"))
        if value is not None:
            spec["knobs"][name] = value
    language = args.language if spec["multilingual"] else spec["language"]
    tts = TTS(spec)
    if args.unload:
        tts.stop()
        return
    if args.provenance:
        _provenance(spec)
        return
    install_tts(spec)
    if args.install:
        tts.start(language)
        return
    if args.load:
        tts.start(language)
        jsonl("tts.loaded", family=spec["family"], file=sys.stdout)
        input()
        tts.stop()
        return
    source = (args.text if args.text is not None else
              (ROOT / args.text_file).read_text(encoding="utf-8") if args.text_file else
              (ROOT / "brain_out.txt").read_text(encoding="utf-8"))
    started = time.perf_counter()
    tts.start(language)
    warmup_s = time.perf_counter() - started
    wav_path = tts.synthesize(source)
    (ROOT / "tts_out.wav").write_bytes(wav_path.read_bytes())
    print(wav_path)
    with wave.open(str(wav_path)) as wav:
        duration = wav.getnframes() / wav.getframerate()
    synth_s = tts.synth_s
    audit = bool(spec.get("audit_dir"))
    jsonl("synth.rtf", file=sys.stdout, family=spec["family"],
          warmup_s=round(warmup_s, 3), chunk_s=round(tts.chunk_s, 3),
          synth_s=round(synth_s, 3), audio_s=round(duration, 3),
          rtf=round(synth_s / duration, 3) if duration else None,
          audit=audit, rtf_valid=not audit)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="No arguments installs and loads Brain, Nano TTS, and Parakeet.", allow_abbrev=False)
    command = parser.add_mutually_exclusive_group()
    command.add_argument("prompt", nargs="?", help="run Brain, TTS and Parakeet without installation")
    command.add_argument("--unload", action="store_true", help="stop all three model servers")
    parser.add_argument("--audit", action="store_true",
                        help="run Nano with replayable diagnostic artifacts; not for RTF")
    args = parser.parse_args()
    mode = "unload" if args.unload else "install" if args.prompt is None else "pipeline"
    install_models = (("brain", "brain.py"), ("tts_nano", "tts_nano.py"), ("parakeet", "parakeet.py"))
    unload_models = (("brain", "brain.py"), ("tts_nano", "tts_nano.py"), ("tts_turbo", "tts_turbo.py"),
                     ("tts_v3", "tts_v3.py"), ("parakeet", "parakeet.py"))
    models = unload_models if mode == "unload" else install_models
    stages = ((("brain", "brain.py", (f"--request={args.prompt}",)),
               ("tts_nano", "tts_nano.py", ("--audit",) if args.audit else ()),
               ("parakeet", "parakeet.py", ("tts_out.wav",)))
              if mode == "pipeline" else tuple((*model, ()) for model in models))
    started = time.perf_counter()
    log_path = ROOT / ".runtime-logs/main.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        def emit(event: str, **fields) -> None:
            line = json.dumps({"event": event, **fields}, ensure_ascii=False)
            print(line, flush=True)
            print(line, file=log)

        emit("main", mode=mode, prompt=args.prompt or "", t=time.strftime("%Y-%m-%d %H:%M:%S"))
        for name, script, request in stages:
            stage_started = time.perf_counter()
            emit("main.stage", name=name, mode=mode)
            flags = request if mode == "pipeline" else (f"--{mode}",)
            with subprocess.Popen([sys.executable, "-u", script, *flags], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8") as process:
                for line in process.stdout:
                    print(line, end="" if line.endswith("\n") else "\n", flush=True)
                    print(line, end="" if line.endswith("\n") else "\n", file=log)
                code = process.wait()
            emit("main.stage.done", name=name, exit=code, wall_s=round(time.perf_counter() - stage_started, 3))
            if code:
                emit("main.failed", mode=mode, wall_s=round(time.perf_counter() - started, 3))
                raise SystemExit(code)
        emit("main.done", mode=mode, wall_s=round(time.perf_counter() - started, 3))


if __name__ == "__main__":
    main()
