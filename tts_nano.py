import argparse, hashlib, json, shutil, socket, struct, subprocess, sys, tempfile, time, urllib.request, uuid, venv, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TTS_RUNTIME = ROOT / "tools/runtime/tts"
TTS_MODELS = ROOT / "models"
TTS_VOICE = ROOT / "data/ref-trump.wav"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN_SDK = Path("C:/VulkanSDK/1.4.357.0")
CHATTERBOX_REV = "d195d3c4fbd11ab264f96f43105b02867c00613a"
CHATTERBOX_SOURCE = ROOT.parent / "chatterbox.cpp"
GGML_REV = "58c3805840b516b2a88ff867ccf7bb41dba79951"
VOICE_URL = "https://huggingface.co/datasets/sdialog/voices-celebrities/resolve/57746b866d470be717097b87ba0428f8dd73e4f4"
TTS_RUNTIME_FILES = ("chatterbox-server.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll", "ggml-vulkan.dll")
TTS_RUNTIME_REQUIRED = (*TTS_RUNTIME_FILES, "chatterbox-LICENSE.txt", "ggml-LICENSE.txt")
TTS_RATE, TTS_MAGIC, TTS_VERSION = 24000, 0x32525454, 4
TTS_FRAME = struct.Struct("<7I")
LOG_DIR = ROOT / ".runtime-logs"
TRIDENT_LOG = LOG_DIR / "trident.log"
INSTALL_LOG = LOG_DIR / "install.log"
TTS_LOG = LOG_DIR / "tts.log"
DELIVERABLES = ROOT / ".runtime-deliverables"
PLATFORM, BUILD_RECIPE = "win-x64", ("vulkan", "shared")
KNOBS = {
    "n-gpu-layers": 99, "fastconv": 1, "seed": 42, "max-tokens": 1000,
    "top-k": 1000, "top-p": .95, "min-p": 0.0, "temperature": 0.5,
    "context": 2048, "threads": 4, "repeat-penalty": 1.2,
    "repeat-stop": 16, "cfm-steps": 1,
}
SPEC = {
    "family": "nano", "port": 17933, "output": "tts",
    "models": (ROOT / "models/chatterbox-t3-nano-q4_0.gguf", ROOT / "models/chatterbox-s3gen-nano-q4_0.gguf"),
    "url": "https://huggingface.co/ResembleAI/chatterbox-nano/resolve/71ccd1d0081b430592cea481f4307e764e07bc64",
    "card": "nano-model-card.md",
    "conversions": (
        ("convert-t3-turbo-to-gguf.py", "q4_0",
         ("t3_nano_v1.safetensors", "conds.pt", "ve.safetensors", "vocab.json", "merges.txt", "added_tokens.json")),
        ("convert-s3gen-to-gguf.py", "q4_0",
         ("s3gen_meanflow.safetensors", "conds.pt"))),
}
_RUN_ID = None


def jsonl(event: str, **fields) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    row = {"event": event, "ts": time.time(), "run_id": _RUN_ID, "family": "nano", **fields}
    line = json.dumps({k: v for k, v in row.items() if v is not None}, ensure_ascii=False) + "\n"
    with TRIDENT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _run_logged(cmd, *, step, **kwargs) -> None:
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    with INSTALL_LOG.open("a", encoding="utf-8", errors="replace") as fh:
        print(f"# {step}", file=fh, flush=True)
        subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, check=True, **kwargs)


def _download(url: str, path: Path) -> None:
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


def _source_rev(source: Path) -> str:
    proc = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"git rev-parse failed in {source}")
    head = proc.stdout.strip()
    diff = subprocess.run(
        ["git", "-C", str(source), "diff", "HEAD", "--", "src", "include", "CMakeLists.txt"],
        capture_output=True)
    if diff.returncode != 0:
        raise RuntimeError(f"git diff failed in {source}")
    if diff.stdout:
        return f"{head}+{hashlib.sha256(diff.stdout).hexdigest()[:12]}"
    return head


def _fingerprint(source: Path, patch_path: Path) -> str:
    if not patch_path.is_file():
        raise FileNotFoundError(f"missing ggml vulkan patch: {patch_path}")
    patch_hash = hashlib.sha256(patch_path.read_bytes()).hexdigest()[:12]
    key = "|".join([_source_rev(source), GGML_REV, patch_hash, PLATFORM, *BUILD_RECIPE])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _deliverable_dir(fp: str) -> Path:
    return DELIVERABLES / fp


def _deliverable_complete(fp: str) -> bool:
    return all((_deliverable_dir(fp) / "bin" / name).is_file() for name in TTS_RUNTIME_FILES)


def _install_from_deliverable(fp: str) -> None:
    src = _deliverable_dir(fp)
    TTS_RUNTIME.mkdir(parents=True, exist_ok=True)
    for name in TTS_RUNTIME_FILES:
        shutil.copy2(src / "bin" / name, TTS_RUNTIME / name)
    for lic in (src / "licenses").glob("*.txt"):
        shutil.copy2(lic, TTS_RUNTIME / lic.name)


def _save_deliverable(fp: str, build_bin: Path, source: Path) -> None:
    dest = _deliverable_dir(fp)
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "bin").mkdir(parents=True)
    (dest / "licenses").mkdir()
    for name in TTS_RUNTIME_FILES:
        shutil.copy2(build_bin / name, dest / "bin" / name)
    shutil.copy2(source / "LICENSE", dest / "licenses" / "chatterbox-LICENSE.txt")
    shutil.copy2(source / "ggml/LICENSE", dest / "licenses" / "ggml-LICENSE.txt")
    (dest / "manifest.json").write_text(json.dumps({
        "source_rev": _source_rev(source),
        "chatterbox_rev": CHATTERBOX_REV, "ggml_rev": GGML_REV, "platform": PLATFORM,
        "recipe": list(BUILD_RECIPE), "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": list(TTS_RUNTIME_FILES),
    }, indent=2), encoding="utf-8")


def _server_exe() -> Path:
    path = TTS_RUNTIME / "chatterbox-server.exe"
    if not path.is_file():
        raise RuntimeError("TTS runtime missing")
    return path


def _checkout(url: str, rev: str, path: Path, patterns: tuple) -> None:
    _run_logged(["git", "init", str(path)], step="git-init")
    git = ["git", "-C", str(path)]
    _run_logged([*git, "remote", "add", "origin", url], step="git-remote")
    for step, args in (("git-config", ("config", "remote.origin.promisor", "true")),
                       ("git-filter", ("config", "remote.origin.partialclonefilter", "blob:none")),
                       ("git-fetch", ("fetch", "--depth=1", "--filter=blob:none", "--no-tags", "origin", rev))):
        _run_logged([*git, *args], step=step)
    _run_logged([*git, "sparse-checkout", "set", "--no-cone", "--stdin"], step="git-sparse",
                input="\n".join(patterns) + "\n", text=True)
    _run_logged([*git, "checkout", "--detach", "--force", rev], step="git-checkout")
    _run_logged([*git, "reset", "--hard", "HEAD"], step="git-reset")


def _build_tts(work: Path, source: Path) -> None:
    ggml = source / "ggml"
    patch = source / "src/ggml-vulkan-queue.patch"
    _checkout("https://github.com/ggml-org/ggml.git", GGML_REV, ggml,
              ("/CMakeLists.txt", "/LICENSE", "/cmake/", "/include/", "/src/*", "!/src/*/",
               "/src/ggml-cpu/", "/src/ggml-vulkan/"))
    _run_logged(["git", "-C", str(ggml), "apply", "--whitespace=nowarn", str(patch)], step="ggml-patch")
    build = work / "b"
    _run_logged([
        CMAKE, "-S", str(source), "-B", str(build), "-G", "Visual Studio 17 2022", "-A", "x64",
        "-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF", "-DGGML_NATIVE=ON", "-DGGML_CCACHE=OFF",
        "-DBUILD_SHARED_LIBS=ON", "-DTTS_CPP_BUILD_EXECUTABLES=ON", "-DTTS_CPP_BUILD_TESTS=OFF",
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


def _convert_tts(work: Path, source: Path, missing: list) -> None:
    converter, checkpoint = work / "c", work / "k"
    venv.EnvBuilder(with_pip=True).create(converter)
    python = str(converter / "Scripts/python.exe")
    pip = [python, "-m", "pip", "--isolated", "install", "--no-cache-dir",
           "--disable-pip-version-check", "--progress-bar", "off", "--no-input"]
    _run_logged([*pip, "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cpu"], step="pip-torch")
    _run_logged([*pip, "numpy==1.26.4", "gguf==0.19.0", "safetensors==0.5.3",
                 "scipy==1.15.3", "librosa==0.11.0"], step="pip-convert")
    assets = dict.fromkeys(name for (script, quant, files), output in missing for name in files)
    for name in assets:
        _download(f"{SPEC['url']}/{name}", checkpoint / name)
    TTS_MODELS.mkdir(parents=True, exist_ok=True)
    for (script, quant, files), output in missing:
        converted = work / output.name
        _run_logged([python, str(source / "scripts" / script),
                     "--ckpt-dir", str(checkpoint), "--out", str(converted), "--quant", quant],
                    step=script, cwd=work)
        converted.replace(output)


def install_tts() -> None:
    if len(CHATTERBOX_REV) != 40:
        raise RuntimeError("Set CHATTERBOX_REV to the pushed chatterbox.cpp commit SHA before install")
    source = CHATTERBOX_SOURCE.resolve()
    if not (source / "CMakeLists.txt").is_file():
        raise FileNotFoundError(f"chatterbox source missing CMakeLists.txt: {source}")
    missing = [(c, o) for c, o in zip(SPEC["conversions"], SPEC["models"]) if not o.is_file()]
    card = TTS_MODELS / SPEC["card"]
    voice_card = TTS_VOICE.with_suffix(".md")
    fp = _fingerprint(source, source / "src/ggml-vulkan-queue.patch")
    cached = _deliverable_complete(fp)
    if cached:
        jsonl("tts.install.deliverable", fingerprint=fp, hit=True)
        _install_from_deliverable(fp)
    need_runtime = not cached
    if need_runtime and all((TTS_RUNTIME / name).is_file() for name in TTS_RUNTIME_REQUIRED):
        jsonl("tts.install.stale_runtime", fingerprint=fp)
        for name in TTS_RUNTIME_FILES:
            (TTS_RUNTIME / name).unlink(missing_ok=True)
    if not need_runtime and not missing and card.is_file() and TTS_VOICE.is_file() and voice_card.is_file():
        jsonl("tts.install", skip=True, fingerprint=fp, deliverable_hit=cached)
        return
    jsonl("tts.install", skip=False, fingerprint=fp)
    if need_runtime or missing:
        with tempfile.TemporaryDirectory(prefix=".n-", dir=ROOT) as tmp:
            work = Path(tmp)
            jsonl("tts.install.checkout", rev=CHATTERBOX_REV, source=str(source))
            if need_runtime:
                jsonl("tts.install.build", fingerprint=fp)
                _build_tts(work, source)
                _save_deliverable(fp, work / "b" / "bin", source)
                _install_from_deliverable(fp)
            if missing:
                jsonl("tts.install.convert")
                _convert_tts(work, source, missing)
    if not card.is_file():
        _download(f"{SPEC['url']}/README.md", card)
    if not TTS_VOICE.is_file():
        jsonl("tts.install.voice")
        _download(f"{VOICE_URL}/audio/donald-trump.wav", TTS_VOICE)
    if not voice_card.is_file():
        _download(f"{VOICE_URL}/README.md", voice_card)
    jsonl("tts.install.done", fingerprint=fp)


def _iter_tts_log(response: int):
    if not TTS_LOG.is_file():
        return
    for line in TTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("response") == response:
            yield obj


class TTS:
    def __init__(self) -> None:
        self._proc = self._log_fh = None
        self._response_id = 0
        self.synth_s = 0.0

    def _command(self) -> list:
        t3, s3 = SPEC["models"]
        command = [str(_server_exe()),
                   "--run-id", _RUN_ID or "nano",
                   "--family", "nano", "--model", str(t3), "--s3gen-gguf", str(s3),
                   "--reference", str(TTS_VOICE), "--port", str(SPEC["port"]),
                   "--audit-dir", ""]
        command.extend(arg for name, value in KNOBS.items() for arg in (f"--{name}", str(value)))
        return command

    def start(self) -> "TTS":
        _server_exe()
        if self._proc is not None and self._proc.poll() is None:
            return self
        if _port_in_use(SPEC["port"]):
            raise RuntimeError(f"Port {SPEC['port']} is already in use; run --unload first")
        jsonl("tts.spawn", port=SPEC["port"], chatterbox_rev=CHATTERBOX_REV)
        TTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = TTS_LOG.open("ab", buffering=0)
        self._proc = subprocess.Popen(self._command(), cwd=TTS_RUNTIME, stdin=subprocess.DEVNULL,
                                      stdout=self._log_fh, stderr=self._log_fh)
        _wait_port(self._proc, SPEC["port"], 120)
        jsonl("tts.ready", port=SPEC["port"], pid=self._proc.pid)
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
        jsonl("tts.stop", port=SPEC["port"])
        _kill_port(SPEC["port"])

    def synthesize(self, text: str) -> Path:
        piece = text.strip()
        if not piece:
            raise ValueError("TTS input is empty")
        output = ROOT / f"out_{time.strftime('%d-%m-%y-%H-%M-%S')}_{SPEC['output']}.wav"
        self._response_id += 1
        response_id = self._response_id
        jsonl("synth.begin", response=response_id, pieces=1, total_chars=len(piece))
        synth_t0 = time.perf_counter()
        with socket.create_connection(("127.0.0.1", SPEC["port"]), timeout=300) as sock, sock.makefile("rb") as reader:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._send(sock, 1, response_id, 0, 1, piece)
            pcm_bytes = 0
            with output.open("xb") as target, wave.open(target, "wb") as wav:
                wav.setparams((1, 2, TTS_RATE, 0, "NONE", "not compressed"))
                before = pcm_bytes
                while True:
                    kind, returned_response, returned_piece, chunk, payload = self._receive(reader)
                    if returned_response != response_id or returned_piece != 0:
                        raise RuntimeError("Unexpected TTS piece")
                    if kind == 2:
                        break
                    if kind != 1:
                        raise RuntimeError(f"Unexpected TTS response kind: {kind}")
                    zeros = TTS_RATE // 50 * 2
                    if chunk == 0 and len(payload) > zeros and payload[:zeros] == b"\0" * zeros:
                        payload = payload[zeros:]
                    wav.writeframesraw(payload)
                    pcm_bytes += len(payload)
                if pcm_bytes == before:
                    raise RuntimeError("TTS produced no audio")
            native = [o for o in _iter_tts_log(response_id) if "n_speech_tok" in o]
            jsonl("synth.complete", response=response_id, pieces=1, samples=pcm_bytes // 2, wav=output.name,
                  t1=round(pcm_bytes / 2 / TTS_RATE, 3),
                  wall_ms=int((time.perf_counter() - synth_t0) * 1000),
                  n_text_tok=sum(r.get("n_text_tok") or 0 for r in native) or None,
                  n_speech_tok=sum(r.get("n_speech_tok") or 0 for r in native) or None,
                  stop=native[-1].get("stop") if native else None)
            sock.settimeout(10)
            self._send(sock, 3)
            if self._receive(reader)[0] != 5:
                raise RuntimeError("TTS did not acknowledge close")
        self.synth_s = time.perf_counter() - synth_t0
        return output

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


def main() -> None:
    global _RUN_ID
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("text", nargs="?", help="text to synthesize")
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--unload", action="store_true")
    args = parser.parse_args()
    tts = TTS()
    if args.unload:
        tts.stop()
        return
    if args.text is not None and args.text_file is not None:
        parser.error("give text or --text-file, not both")
    if args.text_file is not None:
        path = args.text_file if args.text_file.is_absolute() else ROOT / args.text_file
        source = path.read_text(encoding="utf-8")
    elif args.text is not None:
        source = args.text
    else:
        parser.error("give text or --text-file")
    install_tts()
    _RUN_ID = uuid.uuid4().hex
    jsonl("tts.run", chatterbox_rev=CHATTERBOX_REV, source_chars=len(source))
    started = time.perf_counter()
    tts.start()
    warmup_s = time.perf_counter() - started
    wav_path = tts.synthesize(source)
    (ROOT / "tts_out.wav").write_bytes(wav_path.read_bytes())
    with wave.open(str(wav_path)) as wav:
        duration = wav.getnframes() / wav.getframerate()
    jsonl("synth.rtf", wav=wav_path.name, warmup_s=round(warmup_s, 3),
          synth_s=round(tts.synth_s, 3), audio_s=round(duration, 3),
          rtf=round(tts.synth_s / duration, 3) if duration else None)


if __name__ == "__main__":
    main()
