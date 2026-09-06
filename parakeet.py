import argparse, http.client, json, shutil, struct, subprocess, sys, time, uuid, wave, zipfile
from pathlib import Path

from main import ROOT, _download, _port_in_use, _kill_port

RUNTIME = ROOT / "tools/runtime/parakeet"
EXE = RUNTIME / "parakeet-cli.exe"
SERVER = RUNTIME / "parakeet-server.exe"
MODEL = ROOT / "models/nemotron-3.5-asr-streaming-0.6b-q4_k.gguf"
MODEL_CARD = MODEL.with_suffix(".md")
ARCHIVE = "parakeet-v0.5.0-bin-win-vulkan-x64.zip"
RUNTIME_URL = f"https://github.com/mudler/parakeet.cpp/releases/download/v0.5.0/{ARCHIVE}"
MODEL_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9"
RUNTIME_SHA = "717c416fab299755e8140137e3a0115121ce1acb6379d13c60f2f0613f6c13a3"
MODEL_SHA = "5ad85eb3f3014c1a300d67b7ccbd23c38c4c952405cbe33a861e19fb2775e84b"
PARAKEET_REV = "e75de9b6b9b688fd293aa22f7e27aa724ea286f8"
THREADS = 6
LANGUAGE = "auto"
PORT = 17934
TIMESTAMP_FORMAT = "%d-%m-%y-%H-%M-%S"

_PROCESS = None


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


def _build(work: Path) -> None:
    import tempfile
    source = work / "parakeet"
    _checkout("https://github.com/mudler/parakeet.cpp.git", PARAKEET_REV, source,
              ("/CMakeLists.txt", "/LICENSE", "/src/", "/include/", "/examples/",
               "/third_party/", "/scripts/apply_ggml_patches.sh", "/scripts/requirements.txt"))
    subprocess.run(["git", "-C", str(source), "submodule", "update", "--init", "--depth=1",
                    "--filter=blob:none", "third_party/ggml"], check=True)
    build = work / "build"
    subprocess.run(["C:/Program Files/CMake/bin/cmake.exe", "-S", str(source), "-B", str(build),
                    "-G", "Visual Studio 17 2022", "-A", "x64", "-DPARAKEET_BUILD_TESTS=OFF",
                    "-DPARAKEET_BUILD_CLI=ON", "-DPARAKEET_BUILD_SERVER=ON",
                    "-DGGML_NATIVE=ON", "-DGGML_LLAMAFILE=ON"], check=True)
    subprocess.run(["C:/Program Files/CMake/bin/cmake.exe", "--build", str(build),
                    "--config", "Release", "--target", "parakeet-server", "--parallel", "4"], check=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for name in ("parakeet-server.exe", "parakeet-cli.exe"):
        for sub in ("bin", "bin/Release", "examples/cli", "examples/cli/Release", "examples/server", "examples/server/Release"):
            src = build / sub / name
            if src.is_file():
                shutil.copy2(src, RUNTIME / name)
                break
    for dll in build.rglob("bin/Release/*.dll"):
        shutil.copy2(dll, RUNTIME / dll.name)
    for dll in build.rglob("bin/*.dll"):
        shutil.copy2(dll, RUNTIME / dll.name)
    shutil.copy2(source / "LICENSE", RUNTIME / "parakeet-LICENSE.txt")


def _install() -> None:
    import tempfile
    required = [EXE, SERVER, RUNTIME / "parakeet-LICENSE.txt", MODEL, MODEL_CARD]
    if all(p.is_file() for p in required):
        return
    with tempfile.TemporaryDirectory(prefix=".parakeet-install-", dir=ROOT) as tmp:
        work = Path(tmp)
        if not (EXE.is_file() and (RUNTIME / "parakeet-LICENSE.txt").is_file()):
            archive = work / ARCHIVE
            _download(RUNTIME_URL, archive, RUNTIME_SHA)
            RUNTIME.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as z:
                for name in (EXE.name, "LICENSE"):
                    member = f"{ARCHIVE.removesuffix('.zip')}/{name}"
                    with z.open(member) as src, (RUNTIME / {"LICENSE": "parakeet-LICENSE.txt"}.get(name, name)).open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        if not SERVER.is_file():
            _build(work)
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        if not MODEL.is_file():
            downloaded = work / MODEL.name
            _download(f"{MODEL_URL}/{MODEL.name}", downloaded, MODEL_SHA)
            downloaded.replace(MODEL)
        if not MODEL_CARD.is_file():
            downloaded = work / MODEL_CARD.name
            _download(f"{MODEL_URL}/README.md", downloaded)
            downloaded.replace(MODEL_CARD)


def _start() -> None:
    global _PROCESS
    if _PROCESS is not None and _PROCESS.poll() is None:
        return
    if _port_in_use(PORT):
        return
    _PROCESS = subprocess.Popen([str(SERVER), "--model", str(MODEL),
                                 "--port", str(PORT), "--threads", str(THREADS)],
                                cwd=RUNTIME, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if _PROCESS.poll() is not None:
            raise RuntimeError(f"parakeet-server died with code {_PROCESS.poll()}")
        if _port_in_use(PORT):
            return
        time.sleep(0.1)
    _PROCESS.kill()
    raise TimeoutError("parakeet-server failed to open port 17934")


def _stop() -> None:
    global _PROCESS
    proc, _PROCESS = _PROCESS, None
    if proc is not None:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    _kill_port(PORT)


def _transcribe_cli(wav: Path) -> str:
    cmd = [str(EXE), "transcribe", "--model", str(MODEL), "--input", str(wav),
           "--lang", LANGUAGE, "--threads", str(THREADS)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", check=True)
    return result.stdout


def _transcribe_http(wav: Path) -> str:
    boundary = uuid.uuid4().hex
    with wav.open("rb") as f:
        wav_bytes = f.read()
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="file"; filename="{wav.name}"\r\n'
        f'Content-Type: audio/wav\r\n\r\n'
    ).encode() + wav_bytes + (
        f'\r\n--{boundary}--\r\n'
    ).encode()
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=300)
    try:
        conn.request("POST", "/v1/audio/transcriptions", body=body,
                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"parakeet HTTP {resp.status}: {body.decode('utf-8', 'replace')[-2000:]}")
        return json.loads(body)["text"]
    finally:
        conn.close()


def transcribe(wav: Path) -> str:
    wav = (wav if wav.is_absolute() else ROOT / wav).resolve()
    if _port_in_use(PORT):
        return _transcribe_http(wav)
    return _transcribe_cli(wav)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--install", action="store_true")
    p.add_argument("--load", action="store_true")
    p.add_argument("--unload", action="store_true")
    p.add_argument("wav", type=Path, nargs="*")
    args = p.parse_args()
    if args.install:
        _install()
        _start()
        sys.exit(0)
    if args.load:
        _install()
        _start()
        try:
            input("[parakeet] ready. Press Enter to stop...\n")
        except EOFError:
            while True:
                time.sleep(3600)
        _stop()
        sys.exit(0)
    if args.unload:
        _stop()
        sys.exit(0)
    if args.wav:
        for wav_path in args.wav:
            t0 = time.perf_counter()
            transcript = transcribe(wav_path).strip()
            t1 = time.perf_counter()
            with wave.open(str(wav_path)) as wf:
                dur = wf.getnframes() / wf.getframerate()
            print(transcript, flush=True)
            print(f"[rtf] parakeet_total={t1-t0:.3f}s", file=sys.stderr)
            print(f"[rtf] audio_s={dur:.3f}s", file=sys.stderr)
    else:
        _install()
        wav = ROOT / "tts_out.wav"
        with wave.open(str(wav)) as wf:
            dur = wf.getnframes() / wf.getframerate()
        t0 = time.perf_counter()
        transcript = transcribe(wav).strip()
        t1 = time.perf_counter()
        print(transcript)
        print(f"[rtf] parakeet_total={t1-t0:.3f}s", file=sys.stderr)
        print(f"[rtf] audio_s={dur:.3f}s", file=sys.stderr)
