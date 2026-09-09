import argparse, json, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request, venv, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TTS_RUNTIME = ROOT / "tools/runtime/tts"
TTS_MODELS = ROOT / "models"
TTS_VOICE = ROOT / "data/ref-trump.wav"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN_SDK = Path("C:/VulkanSDK/1.4.357.0")
CHATTERBOX_REV = "ff6e4794c52b60ef9a5b52960828d3f96ca8de1b"
GGML_REV = "58c3805840b516b2a88ff867ccf7bb41dba79951"
VOICE_URL = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4"
TTS_RUNTIME_FILES = ("chatterbox-server.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll")
TTS_RUNTIME_REQUIRED = (*TTS_RUNTIME_FILES, "chatterbox-LICENSE.txt", "ggml-LICENSE.txt")
TTS_RATE, TTS_MAGIC, TTS_VERSION = 24000, 0x32525454, 4
TTS_FRAME = struct.Struct("<7I")
TTS_CHUNKER = ROOT / "tools/runtime/chunker/Scripts/python.exe"
LOG_DIR = ROOT / ".runtime-logs"
TRIDENT_LOG = LOG_DIR / "trident.log"
INSTALL_LOG = LOG_DIR / "install.log"
TTS_LOG = LOG_DIR / "tts.log"
def jsonl(event: str, **fields) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with TRIDENT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n")


def _read_tts_log_json(*, event: str | None = None, response: int | None = None,
                       piece: int | None = None) -> dict | None:
    if not TTS_LOG.is_file():
        return None
    for line in reversed(TTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event is not None and obj.get("event") != event:
            continue
        if response is not None and obj.get("response") != response:
            continue
        if piece is not None and obj.get("piece") != piece:
            continue
        return obj
    return None


def _run_logged(cmd, *, step, **kwargs) -> None:
    jsonl("run", step=step)
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    with INSTALL_LOG.open("a", encoding="utf-8", errors="replace") as fh:
        print(f"# {step}", file=fh, flush=True)
        subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, check=True, **kwargs)


def tts_knobs(context: int, threads: int, cfm_steps: int, repeat_penalty: float = 1.2,
              cfg_weight: float = 0.0, exaggeration: float = 0.0, min_p: float = 0.0,
              n_gpu_layers: int = 99, fastconv: int = 1, seed: int = 42, max_tokens: int = 1000,
              top_k: int = 1000, top_p: float = .95, temperature: float = .8) -> dict:
    return {
        "n-gpu-layers": n_gpu_layers, "fastconv": fastconv, "seed": seed, "max-tokens": max_tokens,
        "top-k": top_k, "top-p": top_p, "min-p": min_p, "temperature": temperature,
        "context": context, "threads": threads, "repeat-penalty": repeat_penalty,
        "cfm-steps": cfm_steps, "cfg-weight": cfg_weight, "exaggeration": exaggeration,
    }


def _download(url: str, path: Path) -> None:
    jsonl("run", step="download", name=path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.unlink(missing_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Trident/1"})
        with urllib.request.urlopen(req, timeout=3600) as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst, 4 << 20)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def _tts_runtime_present() -> bool:
    return all((TTS_RUNTIME / name).is_file() for name in TTS_RUNTIME_REQUIRED)


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
    _run_logged(["git", "init", str(path)], step="git-init")
    git = ["git", "-C", str(path)]
    for step, args in (("git-remote", ("remote", "add", "origin", url)),
                       ("git-config", ("config", "remote.origin.promisor", "true")),
                       ("git-filter", ("config", "remote.origin.partialclonefilter", "blob:none")),
                       ("git-fetch", ("fetch", "--depth=1", "--filter=blob:none", "--no-tags", "origin", rev))):
        _run_logged([*git, *args], step=step)
    _run_logged([*git, "sparse-checkout", "set", "--no-cone", "--stdin"], step="git-sparse",
                input="\n".join(patterns) + "\n", text=True)
    _run_logged([*git, "checkout", "--detach", rev], step="git-checkout")


def _build_tts(work: Path, source: Path) -> None:
    _checkout("https://github.com/ggml-org/ggml.git", GGML_REV, source / "ggml",
              ("/CMakeLists.txt", "/LICENSE", "/cmake/", "/include/", "/src/*", "!/src/*/",
               "/src/ggml-cpu/", "/src/ggml-vulkan/"))
    _run_logged(["git", "-C", str(source / "ggml"), "apply", "--whitespace=nowarn",
                 str(source / "src/ggml-vulkan-queue.patch")], step="ggml-patch")
    build = work / "b"
    _run_logged([
        CMAKE, "-S", str(source), "-B", str(build), "-G", "Visual Studio 17 2022", "-A", "x64",
        "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF",
        "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON", "-DTTS_CPP_BUILD_TESTS=OFF",
        "-DTTS_CPP_MTL=OFF",
        "-DGGML_BUILD_TESTS=OFF", "-DGGML_BUILD_EXAMPLES=OFF",
        f"-DVulkan_INCLUDE_DIR={VULKAN_SDK / 'Include'}", f"-DVulkan_LIBRARY={VULKAN_SDK / 'Lib/vulkan-1.lib'}",
        f"-DVulkan_GLSLC_EXECUTABLE={VULKAN_SDK / 'Bin/glslc.exe'}",
    ], step="cmake")
    _run_logged([CMAKE, "--build", str(build), "--config", "Release", "--target", "chatterbox-server",
                 "--parallel", "4"], step="msbuild")
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
    _run_logged([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], step="pip-torch")
    _run_logged([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3",
                 "scipy==1.15.3", "librosa==0.11.0", "huggingface-hub==0.34.4"], step="pip-convert")
    assets = dict.fromkeys(name for (script, model_args, quant, files), output in missing for name in files)
    for name in assets:
        _download(f"{spec['url']}/{name}", checkpoint / name)
    TTS_MODELS.mkdir(parents=True, exist_ok=True)
    for (script, model_args, quant, files), output in missing:
        converted = work / output.name
        _run_logged([python, str(source / "scripts" / script), *model_args,
                     "--ckpt-dir", str(checkpoint), "--out", str(converted), "--quant", quant],
                    step=script, cwd=work)
        converted.replace(output)


def install_tts(spec: dict) -> None:
    if len(CHATTERBOX_REV) != 40:
        raise RuntimeError("Set CHATTERBOX_REV to the pushed chatterbox.cpp commit SHA before install")
    family = spec["family"]
    missing = _missing_conversions(spec)
    card = TTS_MODELS / spec["card"]
    voice_card = TTS_VOICE.with_suffix(".md")
    need_runtime = not _tts_runtime_present()
    if not need_runtime and not missing and card.is_file() and TTS_VOICE.is_file() and voice_card.is_file():
        jsonl("tts.install", family=family, skip=True)
    else:
        jsonl("tts.install", family=family, skip=False)
        if need_runtime or missing:
            with tempfile.TemporaryDirectory(prefix=f".{family[0]}-", dir=ROOT) as tmp:
                work, source = Path(tmp), Path(tmp) / "s"
                jsonl("tts.install.checkout", family=family, rev=CHATTERBOX_REV)
                patterns = []
                if need_runtime:
                    patterns += ["/CMakeLists.txt", "/LICENSE", "/src/", "/include/"]
                if missing:
                    patterns += [*(f"/scripts/{conversion[0]}" for conversion, output in missing),
                                 "/scripts/quant_policy.py"]
                _checkout("https://github.com/wgabrys88/chatterbox.cpp.git", CHATTERBOX_REV, source, patterns)
                if need_runtime:
                    jsonl("tts.install.build", family=family)
                    _build_tts(work, source)
                if missing:
                    jsonl("tts.install.convert", family=family)
                    _convert_tts(spec, work, source, missing)
        if not card.is_file():
            _download(f"{spec['url']}/README.md", card)
        if not TTS_VOICE.is_file():
            jsonl("tts.install.voice", family=family)
            _download(f"{VOICE_URL}/audio/donald-trump.wav", TTS_VOICE)
        if not voice_card.is_file():
            _download(f"{VOICE_URL}/README.md", voice_card)
        jsonl("tts.install.done", family=family)
    import analyze, chunk as chunker
    chunker.install()
    analyze.install()


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
        if not (TTS_RUNTIME / "chatterbox-server.exe").is_file():
            raise RuntimeError("TTS runtime missing; run --install")
        language = self.spec["language"] if language is None else language
        if self._proc is not None and self._proc.poll() is None:
            jsonl("tts.reuse", family=self.spec["family"], port=self.spec["port"],
                  pid=self._proc.pid, knobs=self.spec["knobs"])
            return self
        if _port_in_use(self.spec["port"]):
            jsonl("tts.port_blocked", family=self.spec["family"], port=self.spec["port"],
                  knobs=self.spec["knobs"])
            raise RuntimeError(
                f"Port {self.spec['port']} is already in use by a process this TTS instance did not spawn. "
                f"Unload the existing {self.spec['family']} server with --unload before starting with new sampler settings.")
        command = self._command(language)
        jsonl("tts.spawn", family=self.spec["family"], port=self.spec["port"],
              chatterbox_rev=CHATTERBOX_REV, knobs=self.spec["knobs"], command=command)
        jsonl("tts.start", family=self.spec["family"], port=self.spec["port"], spawning=True)
        TTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = TTS_LOG.open("ab", buffering=0)
        self._proc = subprocess.Popen(command, cwd=TTS_RUNTIME, stdin=subprocess.DEVNULL,
                                      stdout=self._log_fh, stderr=self._log_fh)
        _wait_port(self._proc, self.spec["port"], 120)
        native_config = _read_tts_log_json(event="server.config")
        jsonl("tts.ready", family=self.spec["family"], port=self.spec["port"],
              pid=self._proc.pid, knobs=self.spec["knobs"], native_config=native_config)
        return self

    def stop(self) -> None:
        pid = self._proc.pid if self._proc is not None else None
        jsonl("tts.stop", family=self.spec["family"], port=self.spec["port"], pid=pid)
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()
            self._proc = None
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None
        _kill_port(self.spec["port"])

    def synthesize(self, text: str, *, pieces: list | None = None) -> Path:
        chunk_t0 = time.perf_counter()
        one_piece = pieces is not None
        if pieces is None:
            pieces = self._chunks(text)
        self.chunk_s = time.perf_counter() - chunk_t0
        output = ROOT / f"out_{time.strftime('%d-%m-%y-%H-%M-%S')}_{self.spec['output']}.wav"
        self._response_id += 1
        response_id = self._response_id
        fields = dict(response=response_id, pieces=len(pieces),
                      total_chars=sum(len(p) for p in pieces), chunk_s=round(self.chunk_s, 3),
                      one_piece=one_piece)
        if one_piece:
            fields["chunk_bypassed"] = True
        if self.spec.get("audit_dir"):
            audit_dir = Path(self.spec["audit_dir"])
            fields["audit_dir"] = str(audit_dir.relative_to(ROOT) if audit_dir.is_absolute() else audit_dir)
        jsonl("synth.begin", **fields)
        for piece_id, piece in enumerate(pieces):
            jsonl("synth.piece.send", response=response_id, piece=piece_id, total=len(pieces),
                  chars=len(piece), text=piece, one_piece=one_piece)
        synth_t0 = time.perf_counter()
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
                    start, end = before // 2, pcm_bytes // 2
                    jsonl("synth.piece", response=response_id, piece=piece_id,
                          text=pieces[piece_id], chars=len(pieces[piece_id]),
                          sample_start=start, sample_end=end,
                          t0=round(start / TTS_RATE, 3), t1=round(end / TTS_RATE, 3),
                          trimmed_leading_bytes=leading_trim,
                          wall_ms=int((time.perf_counter() - piece_t0) * 1000))
                    pieces_written += 1
            native_pieces = []
            for piece_id in range(pieces_written):
                native = _read_tts_log_json(response=response_id, piece=piece_id)
                if native:
                    native_pieces.append(native)
                    jsonl("synth.native", response=response_id, piece=piece_id,
                          n_text_tok=native.get("n_text_tok"),
                          n_speech_tok=native.get("n_speech_tok"),
                          stop=native.get("stop"),
                          t3_ms=native.get("t3_ms"),
                          s3_ms=native.get("s3_ms"),
                          text_sha=native.get("text_sha"),
                          speech_hash=native.get("speech_hash"))
            jsonl("synth.complete", response=response_id,
                  pieces=pieces_written, samples=pcm_bytes // 2, wav=output.name,
                  native_pieces=native_pieces or None)
            sock.settimeout(10)
            self._send(sock, 3)
            if self._receive(reader)[0] != 5:
                raise RuntimeError("TTS did not acknowledge close")
        self.synth_s = time.perf_counter() - synth_t0
        return output

    @staticmethod
    def _one_piece(text: str) -> list:
        piece = text.strip()
        if not piece:
            raise ValueError("TTS one-piece input is empty")
        return [piece]

    @staticmethod
    def _chunks(text: str) -> list:
        if not TTS_CHUNKER.is_file():
            raise RuntimeError("CPU chunker not installed")
        process = subprocess.run([str(TTS_CHUNKER), str(ROOT / "chunk.py")], input=text,
                                 capture_output=True, text=True, encoding="utf-8")
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


def run_tts(spec: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--load", action="store_true")
    parser.add_argument("--unload", action="store_true")
    parser.add_argument("--audit", action="store_true",
                        help="capture replayable native stage artifacts; do not use for RTF")
    parser.add_argument("--one-piece", action="store_true",
                        help="bypass SaT chunking; synthesize the supplied text as exactly one piece")
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
    install_tts(spec)
    if args.install:
        tts.start(language)
        return
    if args.load:
        tts.start(language)
        jsonl("tts.loaded", family=spec["family"])
        input()
        tts.stop()
        return
    source = (args.text if args.text is not None else
              (ROOT / args.text_file).read_text(encoding="utf-8") if args.text_file else
              (ROOT / "brain_out.txt").read_text(encoding="utf-8"))
    jsonl("tts.run", family=spec["family"], one_piece=args.one_piece,
          chatterbox_rev=CHATTERBOX_REV, knobs=spec["knobs"],
          source_chars=len(source), text_file=str(args.text_file) if args.text_file else None)
    started = time.perf_counter()
    tts.start(language)
    warmup_s = time.perf_counter() - started
    if args.one_piece:
        pieces = TTS._one_piece(source)
        jsonl("synth.chunk_bypass", pieces=1, chars=len(pieces[0]), text=pieces[0])
        wav_path = tts.synthesize(source, pieces=pieces)
    else:
        wav_path = tts.synthesize(source)
    (ROOT / "tts_out.wav").write_bytes(wav_path.read_bytes())
    with wave.open(str(wav_path)) as wav:
        duration = wav.getnframes() / wav.getframerate()
    synth_s = tts.synth_s
    audit = bool(spec.get("audit_dir"))
    jsonl("synth.rtf", family=spec["family"],
          warmup_s=round(warmup_s, 3), chunk_s=round(tts.chunk_s, 3),
          synth_s=round(synth_s, 3), audio_s=round(duration, 3),
          rtf=round(synth_s / duration, 3) if duration else None,
          audit=audit, rtf_valid=not audit)
    import analyze
    analyze.report(wav_path)


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
    jsonl("main", mode=mode, prompt=args.prompt or "", t=time.strftime("%Y-%m-%d %H:%M:%S"))
    for name, script, request in stages:
        stage_started = time.perf_counter()
        jsonl("main.stage", name=name, mode=mode)
        flags = request if mode == "pipeline" else (f"--{mode}",)
        code = subprocess.run([sys.executable, "-u", script, *flags], cwd=ROOT).returncode
        jsonl("main.stage.done", name=name, exit=code, wall_s=round(time.perf_counter() - stage_started, 3))
        if code:
            jsonl("main.failed", mode=mode, wall_s=round(time.perf_counter() - started, 3))
            raise SystemExit(code)
    jsonl("main.done", mode=mode, wall_s=round(time.perf_counter() - started, 3))


if __name__ == "__main__":
    main()
