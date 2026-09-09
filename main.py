import argparse, hashlib, json, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request, uuid, venv, wave
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TTS_RUNTIME = ROOT / "tools/runtime/tts"
TTS_MODELS = ROOT / "models"
TTS_VOICE = ROOT / "data/ref-trump.wav"
CMAKE = "C:/Program Files/CMake/bin/cmake.exe"
VULKAN_SDK = Path("C:/VulkanSDK/1.4.357.0")
CHATTERBOX_REV = "083ae6b80002cad03d70765a3c3aceacecc2e6c9"
CHATTERBOX_SOURCE = ROOT.parent / "chatterbox.cpp"
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
REPEAT_LAST_N = 4
_RUN_CTX: dict = {"run_id": None, "family": None, "run_dir": None}


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _repair_forensic_json(line: str) -> str:
    """Repair malformed forensic JSON from forensic_head() nesting a second object."""
    if ',{"run_id"' in line:
        line = line.replace(',{"run_id"', ',"run_id"', 1)
    return line.replace(":-inf", ":null").replace(",-inf", ",null")


def _sampler_snapshot(knobs: dict) -> dict:
    return {
        "seed": knobs.get("seed"),
        "temperature": knobs.get("temperature"),
        "min_p": knobs.get("min-p"),
        "top_p": knobs.get("top-p"),
        "top_k": knobs.get("top-k"),
        "repeat_penalty": knobs.get("repeat-penalty"),
        "repeat_last_n": REPEAT_LAST_N,
        "repeat_stop_consecutive": knobs.get("repeat-stop", 16),
    }


def begin_run(family: str) -> str:
    run_id = uuid.uuid4().hex
    run_dir = LOG_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _RUN_CTX.update(run_id=run_id, family=family, run_dir=run_dir)
    return run_id


def end_run(wav_paths: list[Path], spec: dict) -> None:
    run_dir = _RUN_CTX.get("run_dir")
    if not run_dir:
        return
    import analyze
    analyze.finalize_run(run_dir, wav_paths, spec, _RUN_CTX, CHATTERBOX_REV, _sampler_snapshot)
    jsonl("run.end", wav_count=len(wav_paths), run_dir=str(run_dir.relative_to(ROOT)))


def jsonl(event: str, **fields) -> None:
    row = {
        "event": event,
        "ts": time.time(),
        "run_id": _RUN_CTX.get("run_id"),
        "family": fields.get("family", _RUN_CTX.get("family")),
        "response": fields.get("response"),
        "piece": fields.get("piece"),
        **fields,
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with TRIDENT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line)
    run_dir = _RUN_CTX.get("run_dir")
    if run_dir:
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line)


def parse_bench_file(path: Path) -> list[dict]:
    items = []
    section = None
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        items.append({"section": section, "line_no": line_no, "text": line})
    return items


_TEENS = frozenset({"one", "two", "three", "four", "five", "six", "seven", "eight", "nine"})


def _normalize_word(word: str) -> str:
    return word.lower().strip(".,!?;:")


def _compound_numbers(words: list[str]) -> list[str]:
    spoken, i = [], 0
    while i < len(words):
        if words[i] == "twenty" and i + 1 < len(words) and words[i + 1] in _TEENS:
            spoken.append(f"twenty-{words[i + 1]}")
            i += 2
        else:
            spoken.append(words[i])
            i += 1
    return spoken


def _expected_words(text: str) -> list[str]:
    return [_normalize_word(w) for w in text.split() if w.strip()]


def _spoken_words_from_asr(words: list[dict]) -> list[str]:
    return _compound_numbers([_normalize_word(w["w"]) for w in words])


def _asr_diff(expected: list[str], spoken: list[str]) -> dict:
    deletions, insertions, substitutions = [], [], []
    sm = SequenceMatcher(None, expected, spoken)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            deletions.extend(expected[i1:i2])
        elif tag == "insert":
            insertions.extend(spoken[j1:j2])
        elif tag == "replace":
            for e, s in zip(expected[i1:i2], spoken[j1:j2]):
                substitutions.append({"expected": e, "spoken": s})
            if i2 - i1 > j2 - j1:
                deletions.extend(expected[i1 + (j2 - j1):i2])
            elif j2 - j1 > i2 - i1:
                insertions.extend(spoken[j1 + (i2 - i1):j2])
    from collections import Counter
    counts = Counter(spoken)
    duplicates = [w for w, n in counts.items() if n > 1]
    return {
        "expected_words": expected,
        "spoken_words": spoken,
        "deletions": deletions,
        "insertions": insertions,
        "substitutions": substitutions,
        "duplicates": duplicates,
    }


def _run_asr(wav_path: Path, prompt_text: str, response_id: int, piece_id: int = 0) -> None:
    import parakeet
    parakeet._install()
    jsonl("asr.begin", response=response_id, piece=piece_id, wav=wav_path.name)
    t0 = time.perf_counter()
    try:
        data = parakeet.transcribe_json(wav_path)
    except Exception as exc:
        jsonl("asr.error", response=response_id, piece=piece_id, wav=wav_path.name, error=str(exc))
        return
    wall_s = round(time.perf_counter() - t0, 3)
    words = [w for w in data.get("words", []) if not w.get("w", "").startswith("<")]
    for idx, word in enumerate(words):
        jsonl("asr.word", response=response_id, piece=piece_id, idx=idx,
              word=word["w"], word_norm=_normalize_word(word["w"]),
              start=word["start"], end=word["end"], conf=word.get("conf"))
    jsonl("asr.complete", response=response_id, piece=piece_id, wav=wav_path.name,
          word_count=len(words), wall_s=wall_s, transcript=data.get("text", ""))
    expected = _expected_words(prompt_text)
    spoken = _spoken_words_from_asr(words)
    diff = _asr_diff(expected, spoken)
    jsonl("asr.diff", response=response_id, piece=piece_id, wav=wav_path.name, **diff)


def _iter_tts_log(*, event: str | None = None, response: int | None = None,
                  piece: int | None = None, reverse: bool = False):
    if not TTS_LOG.is_file():
        return
    lines = TTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines) if reverse else lines:
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
        yield obj


def _read_tts_log_json(*, event: str | None = None, response: int | None = None,
                       piece: int | None = None) -> dict | None:
    return next(_iter_tts_log(event=event, response=response, piece=piece, reverse=True), None)


def _ingest_t3_steps(response_id: int, piece_id: int = 0, audit_dir: str | Path | None = None) -> None:
    run_id = _RUN_CTX.get("run_id")
    sources = []
    if audit_dir:
        ledger = Path(audit_dir)
        if not ledger.is_absolute():
            ledger = ROOT / ledger
        steps = ledger / "04-t3-step.jsonl"
        if steps.is_file():
            sources.append(steps.read_text(encoding="utf-8").splitlines())
    if not sources:
        sources.append([json.dumps(obj) for obj in _iter_tts_log(response=response_id, piece=piece_id)
                        if obj.get("event") in ("t3.step", "t3.repeat_abort", "t3.text_tokens")])
    for line in sources[0]:
        if not line.strip():
            continue
        obj = json.loads(_repair_forensic_json(line))
        if run_id and obj.get("run_id") not in (run_id, None):
            continue
        if obj.get("response") not in (response_id, None):
            continue
        jsonl(obj["event"], **{k: v for k, v in obj.items() if k != "event"})


def _run_logged(cmd, *, step, **kwargs) -> None:
    jsonl("run", step=step)
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    with INSTALL_LOG.open("a", encoding="utf-8", errors="replace") as fh:
        print(f"# {step}", file=fh, flush=True)
        subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, check=True, **kwargs)


def tts_knobs(context: int, threads: int, cfm_steps: int, repeat_penalty: float = 1.2,
              repeat_stop: int = 16, cfg_weight: float = 0.0, exaggeration: float = 0.0,
              min_p: float = 0.0, n_gpu_layers: int = 99, fastconv: int = 1, seed: int = 42,
              max_tokens: int = 1000, top_k: int = 1000, top_p: float = .95,
              temperature: float = .8) -> dict:
    return {
        "n-gpu-layers": n_gpu_layers, "fastconv": fastconv, "seed": seed, "max-tokens": max_tokens,
        "top-k": top_k, "top-p": top_p, "min-p": min_p, "temperature": temperature,
        "context": context, "threads": threads, "repeat-penalty": repeat_penalty,
        "repeat-stop": repeat_stop, "cfm-steps": cfm_steps, "cfg-weight": cfg_weight,
        "exaggeration": exaggeration,
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


def _purge_tts_runtime() -> None:
    for name in TTS_RUNTIME_FILES:
        (TTS_RUNTIME / name).unlink(missing_ok=True)


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


def _chatterbox_source(work: Path) -> Path:
    local = CHATTERBOX_SOURCE
    if local.is_dir() and (local / "CMakeLists.txt").is_file():
        jsonl("tts.install.local_source", path=str(local))
        return local.resolve()
    source = work / "s"
    _checkout("https://github.com/wgabrys88/chatterbox.cpp.git", CHATTERBOX_REV, source,
              ("/CMakeLists.txt", "/LICENSE", "/src/", "/include/"))
    return source


def _checkout(url: str, rev: str, path: Path, patterns: tuple) -> None:
    _run_logged(["git", "init", str(path)], step="git-init")
    git = ["git", "-C", str(path)]
    remote_cmd = ("remote", "set-url", "origin", url) if subprocess.run(
        [*git, "remote", "get-url", "origin"], capture_output=True).returncode == 0 else ("remote", "add", "origin", url)
    for step, args in (("git-remote", remote_cmd),
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


def install_tts(spec: dict, *, force_rebuild: bool = False) -> None:
    import deliverable as dlvr
    if len(CHATTERBOX_REV) != 40:
        raise RuntimeError("Set CHATTERBOX_REV to the pushed chatterbox.cpp commit SHA before install")
    family = spec["family"]
    missing = _missing_conversions(spec)
    card = TTS_MODELS / spec["card"]
    voice_card = TTS_VOICE.with_suffix(".md")
    source = dlvr.resolve_source(spec.get("chatterbox_source"), CHATTERBOX_SOURCE)
    fp = dlvr.fingerprint(source, GGML_REV, source / "src/ggml-vulkan-queue.patch", CHATTERBOX_REV)
    spec["deliverable_fingerprint"] = fp
    if spec.get("chatterbox_exe"):
        jsonl("tts.install.exe_override", family=family, path=str(Path(spec["chatterbox_exe"]).resolve()))
    elif force_rebuild:
        jsonl("tts.install.force_rebuild", family=family, fingerprint=fp)
        dlvr.purge_deliverable(fp)
        if _tts_runtime_present():
            _purge_tts_runtime()
    need_runtime = not spec.get("chatterbox_exe") and not _tts_runtime_present()
    deliverable_hit = (
        not spec.get("chatterbox_exe")
        and not force_rebuild
        and dlvr.is_complete(fp, TTS_RUNTIME_FILES)
    )
    if deliverable_hit and not _tts_runtime_present():
        jsonl("tts.install.deliverable", family=family, fingerprint=fp, hit=True)
        dlvr.install_from_deliverable(fp, TTS_RUNTIME, TTS_RUNTIME_FILES)
        lic_src = dlvr.deliverable_dir(fp) / dlvr.LICENSES
        for lic in lic_src.glob("*.txt"):
            shutil.copy2(lic, TTS_RUNTIME / lic.name)
        need_runtime = False
    if (
        not spec.get("chatterbox_exe")
        and _tts_runtime_present()
        and not dlvr.is_complete(fp, TTS_RUNTIME_FILES)
    ):
        jsonl("tts.install.deliverable_seed", family=family, fingerprint=fp)
        dlvr.save_from_runtime(fp, TTS_RUNTIME, source, TTS_RUNTIME_FILES,
                               chatterbox_rev=CHATTERBOX_REV, ggml_rev=GGML_REV)
    if not need_runtime and not missing and card.is_file() and TTS_VOICE.is_file() and voice_card.is_file():
        jsonl("tts.install", family=family, skip=True, force_rebuild=force_rebuild, fingerprint=fp,
              deliverable_hit=deliverable_hit)
    else:
        jsonl("tts.install", family=family, skip=False, force_rebuild=force_rebuild, fingerprint=fp)
        if need_runtime or missing:
            with tempfile.TemporaryDirectory(prefix=f".{family[0]}-", dir=ROOT) as tmp:
                work = Path(tmp)
                checkout_source = work / "s"
                jsonl("tts.install.checkout", family=family, rev=CHATTERBOX_REV, source=str(source))
                patterns = []
                if need_runtime:
                    patterns += ["/CMakeLists.txt", "/LICENSE", "/src/", "/include/"]
                if missing:
                    patterns += [*(f"/scripts/{conversion[0]}" for conversion, output in missing),
                                 "/scripts/quant_policy.py"]
                if need_runtime:
                    build_source = source if source.is_dir() else _chatterbox_source(work)
                    jsonl("tts.install.build", family=family, fingerprint=fp)
                    _build_tts(work, build_source)
                    dlvr.save_deliverable(fp, work / "b" / "bin", build_source, TTS_RUNTIME_FILES,
                                          chatterbox_rev=CHATTERBOX_REV, ggml_rev=GGML_REV)
                    dlvr.install_from_deliverable(fp, TTS_RUNTIME, TTS_RUNTIME_FILES)
                    for lic in (dlvr.deliverable_dir(fp) / dlvr.LICENSES).glob("*.txt"):
                        shutil.copy2(lic, TTS_RUNTIME / lic.name)
                else:
                    _checkout("https://github.com/wgabrys88/chatterbox.cpp.git", CHATTERBOX_REV,
                              checkout_source, patterns)
                if missing:
                    jsonl("tts.install.convert", family=family)
                    _convert_tts(spec, work, source if source.is_dir() else checkout_source, missing)
        if not card.is_file():
            _download(f"{spec['url']}/README.md", card)
        if not TTS_VOICE.is_file():
            jsonl("tts.install.voice", family=family)
            _download(f"{VOICE_URL}/audio/donald-trump.wav", TTS_VOICE)
        if not voice_card.is_file():
            _download(f"{VOICE_URL}/README.md", voice_card)
        jsonl("tts.install.done", family=family, fingerprint=fp)
    import analyze, chunk as chunker
    chunker.install()
    analyze.install()


class TTS:
    def __init__(self, spec: dict) -> None:
        self.spec, self._proc, self._log_fh, self._response_id = spec, None, None, 0
        self.chunk_s = self.synth_s = 0.0

    def _command(self, language: str) -> list:
        import deliverable as dlvr
        spec = self.spec
        t3, s3 = spec["models"]
        command = [str(dlvr.server_exe(spec, TTS_RUNTIME)),
                   "--run-id", _RUN_CTX.get("run_id") or spec["family"],
                   "--family", spec["family"], "--model", str(t3), "--s3gen-gguf", str(s3),
                   "--reference", str(TTS_VOICE), "--language", language, "--port", str(spec["port"]),
                   "--audit-dir", str(spec.get("audit_dir", ""))]
        if spec.get("forensics"):
            command.extend(["--forensics", "1"])
        command.extend(arg for name, value in spec["knobs"].items()
                       for arg in (f"--{name}", str(value)))
        return command

    def start(self, language: str = None) -> "TTS":
        import deliverable as dlvr
        dlvr.server_exe(self.spec, TTS_RUNTIME)
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

    def synthesize(self, text: str, *, pieces: list | None = None,
                   bench_file: str | None = None, bench_section: str | None = None,
                   bench_line: int | None = None) -> Path:
        chunk_t0 = time.perf_counter()
        one_piece = pieces is not None
        if pieces is None:
            pieces = self._chunks(text)
        self.chunk_s = time.perf_counter() - chunk_t0
        output = ROOT / f"out_{time.strftime('%d-%m-%y-%H-%M-%S')}_{self.spec['output']}.wav"
        self._response_id += 1
        response_id = self._response_id
        prompt_text = pieces[0] if len(pieces) == 1 else text
        fields = dict(
            response=response_id, pieces=len(pieces),
            total_chars=sum(len(p) for p in pieces), chunk_s=round(self.chunk_s, 3),
            one_piece=one_piece, text_sha=_text_sha(prompt_text),
            sampler=_sampler_snapshot(self.spec["knobs"]),
        )
        if one_piece:
            fields["chunk_bypassed"] = True
        if bench_file is not None:
            fields["bench_file"] = bench_file
        if bench_section is not None:
            fields["bench_section"] = bench_section
        if bench_line is not None:
            fields["bench_line"] = bench_line
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
                  native_pieces=native_pieces or None,
                  bench_file=bench_file, bench_section=bench_section, bench_line=bench_line,
                  text_sha=_text_sha(prompt_text))
            sock.settimeout(10)
            self._send(sock, 3)
            if self._receive(reader)[0] != 5:
                raise RuntimeError("TTS did not acknowledge close")
        self.synth_s = time.perf_counter() - synth_t0
        if self.spec.get("forensics") or self.spec.get("audit_dir"):
            _ingest_t3_steps(response_id, audit_dir=self.spec.get("audit_dir"))
        _run_asr(output, prompt_text, response_id)
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
    parser.add_argument("--force-rebuild", action="store_true",
                        help="purge deliverable cache for current source and rebuild native binaries")
    parser.add_argument("--chatterbox-exe", type=Path,
                        help="use a pre-built chatterbox-server.exe (skip install/build)")
    parser.add_argument("--chatterbox-source", type=Path,
                        help="chatterbox.cpp tree for fingerprinting and local builds")
    parser.add_argument("--deliverable-status", action="store_true",
                        help="list cached native deliverables and exit")
    parser.add_argument("--audit", action="store_true",
                        help="capture replayable native stage artifacts; do not use for RTF")
    parser.add_argument("--forensics", action="store_true",
                        help="emit per-step T3 sampling logs (enabled automatically with --audit)")
    parser.add_argument("--one-piece", action="store_true",
                        help="bypass SaT chunking; synthesize the supplied text as exactly one piece")
    parser.add_argument("--bench-section",
                        help="synthesize only lines from this bench section (requires --text-file)")
    parser.add_argument("--bench-line", type=int,
                        help="synthesize only this bench file line number (requires --text-file)")
    if spec["multilingual"]:
        parser.add_argument("--language", default=spec["language"],
                            help="ISO 639-1 language code (e.g. en, fr, zh)")
    text = parser.add_mutually_exclusive_group()
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    for name, default in spec["knobs"].items():
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=type(default), default=None)
    args = parser.parse_args()
    if args.deliverable_status:
        import deliverable as dlvr
        dlvr.print_status(TTS_RUNTIME_FILES)
        return
    spec = {**spec, "knobs": dict(spec["knobs"])}
    if args.chatterbox_exe:
        spec["chatterbox_exe"] = str(args.chatterbox_exe.resolve())
    if args.chatterbox_source:
        spec["chatterbox_source"] = str(args.chatterbox_source.resolve())
    spec["forensics"] = args.forensics or args.audit
    if args.audit:
        audit_dir = ROOT / ".runtime-logs" / "audit" / (
            f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}-{spec['family']}"
        )
        audit_dir.mkdir(parents=True, exist_ok=False)
        spec["audit_dir"] = audit_dir
        jsonl("tts.audit", dir=str(audit_dir.relative_to(ROOT)), forensics=spec["forensics"])
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
    install_tts(spec, force_rebuild=args.force_rebuild)
    if args.install:
        tts.start(language)
        return
    if args.load:
        tts.start(language)
        jsonl("tts.loaded", family=spec["family"])
        input()
        tts.stop()
        return
    if (args.bench_section or args.bench_line) and not args.text_file:
        parser.error("--bench-section and --bench-line require --text-file")
    begin_run(spec["family"])
    bench_file = str(args.text_file) if args.text_file else None
    bench_items: list[dict] | None = None
    def _bench_rows(items: list[dict]) -> list[dict]:
        return [{"section": i["section"], "line_no": i["line_no"], "chars": len(i["text"]),
                 "text_sha": _text_sha(i["text"])} for i in items]

    if args.text_file:
        all_items = parse_bench_file(ROOT / args.text_file)
        if args.bench_line:
            bench_items = [item for item in all_items if item["line_no"] == args.bench_line]
            if not bench_items:
                raise ValueError(f"bench line {args.bench_line} not found in {bench_file}")
        elif args.bench_section:
            bench_items = [item for item in all_items if item["section"] == args.bench_section]
            if not bench_items:
                raise ValueError(f"bench section {args.bench_section!r} not found in {bench_file}")
        elif all_items and any(item["section"] for item in all_items):
            jsonl("bench.matrix", bench_file=bench_file, items=_bench_rows(all_items))
    if bench_items:
        jsonl("bench.matrix", bench_file=bench_file, items=_bench_rows(bench_items))
        source = "\n".join(item["text"] for item in bench_items)
    else:
        source = (args.text if args.text is not None else
                  (ROOT / args.text_file).read_text(encoding="utf-8") if args.text_file else
                  (ROOT / "brain_out.txt").read_text(encoding="utf-8"))
    jsonl("tts.run", family=spec["family"], one_piece=args.one_piece,
          chatterbox_rev=CHATTERBOX_REV, knobs=spec["knobs"],
          sampler=_sampler_snapshot(spec["knobs"]),
          source_chars=len(source), text_file=bench_file,
          bench_section=args.bench_section, bench_line=args.bench_line)
    started = time.perf_counter()
    tts.start(language)
    warmup_s = time.perf_counter() - started
    wav_paths: list[Path] = []
    if bench_items:
        for item in bench_items:
            if args.one_piece:
                pieces = TTS._one_piece(item["text"])
                jsonl("synth.chunk_bypass", pieces=1, chars=len(pieces[0]), text=pieces[0],
                      bench_section=item["section"], bench_line=item["line_no"])
                wav_path = tts.synthesize(
                    item["text"], pieces=pieces,
                    bench_file=bench_file, bench_section=item["section"], bench_line=item["line_no"],
                )
            else:
                wav_path = tts.synthesize(
                    item["text"],
                    bench_file=bench_file, bench_section=item["section"], bench_line=item["line_no"],
                )
            wav_paths.append(wav_path)
    elif args.one_piece:
        pieces = TTS._one_piece(source)
        jsonl("synth.chunk_bypass", pieces=1, chars=len(pieces[0]), text=pieces[0])
        wav_path = tts.synthesize(source, pieces=pieces, bench_file=bench_file)
        wav_paths.append(wav_path)
    else:
        wav_path = tts.synthesize(source, bench_file=bench_file)
        wav_paths.append(wav_path)
    wav_path = wav_paths[-1]
    (ROOT / "tts_out.wav").write_bytes(wav_path.read_bytes())
    with wave.open(str(wav_path)) as wav:
        duration = wav.getnframes() / wav.getframerate()
    synth_s = tts.synth_s
    audit = bool(spec.get("audit_dir"))
    jsonl("synth.rtf", family=spec["family"], wav=wav_path.name,
          warmup_s=round(warmup_s, 3), chunk_s=round(tts.chunk_s, 3),
          synth_s=round(synth_s, 3), audio_s=round(duration, 3),
          rtf=round(synth_s / duration, 3) if duration else None,
          audit=audit, rtf_valid=not audit, wav_count=len(wav_paths))
    if audit and spec.get("audit_dir"):
        audit_dir = Path(spec["audit_dir"])
        ledger_files = sorted(audit_dir.glob("*.jsonl")) if audit_dir.is_dir() else []
        jsonl("audit.ready", audit_dir=str(audit_dir.relative_to(ROOT)),
              files=[p.name for p in ledger_files])
        jsonl("audit.ingest", audit_dir=str(audit_dir.relative_to(ROOT)),
              ledger_counts={p.name: sum(1 for _ in p.open(encoding="utf-8")) for p in ledger_files})
    import analyze
    analyze.report(wav_path)
    end_run(wav_paths, spec)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="No arguments installs and loads Brain, Nano TTS, and Parakeet.", allow_abbrev=False)
    command = parser.add_mutually_exclusive_group()
    command.add_argument("prompt", nargs="?", help="run Brain, TTS and Parakeet without installation")
    command.add_argument("--unload", action="store_true", help="stop all three model servers")
    parser.add_argument("--audit", action="store_true",
                        help="run Nano with replayable diagnostic artifacts; not for RTF")
    parser.add_argument("--force-rebuild", action="store_true",
                        help="rebuild native chatterbox-server before install/pipeline")
    args = parser.parse_args()
    mode = "unload" if args.unload else "install" if args.prompt is None else "pipeline"
    install_models = (("brain", "brain.py"), ("tts_nano", "tts_nano.py"), ("parakeet", "parakeet.py"))
    unload_models = (("brain", "brain.py"), ("tts_nano", "tts_nano.py"), ("tts_turbo", "tts_turbo.py"),
                     ("tts_v3", "tts_v3.py"), ("parakeet", "parakeet.py"))
    models = unload_models if mode == "unload" else install_models
    nano_flags = []
    if args.audit:
        nano_flags.append("--audit")
    if args.force_rebuild:
        nano_flags.append("--force-rebuild")
    stages = ((("brain", "brain.py", (f"--request={args.prompt}",)),
               ("tts_nano", "tts_nano.py", tuple(nano_flags)),
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
