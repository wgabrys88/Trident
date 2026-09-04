import argparse, hashlib, http.client, json, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request, uuid, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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

_PROCESS, _READY, _READER = None, threading.Event(), None


def _download(url: str, path: Path, sha: str = "") -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Parakeet/1"})
    with urllib.request.urlopen(req, timeout=1800) as src, path.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    if sha:
        with path.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != sha:
                raise RuntimeError(f"Download checksum mismatch: {path.name}")


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


def _port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _drain() -> None:
    for line in _PROCESS.stdout:
        if b"server.ready" in line or b"Listening" in line or b"listening on" in line:
            _READY.set()


def _start() -> None:
    global _PROCESS, _READER
    if _port_in_use():
        return
    if _PROCESS is not None and _PROCESS.poll() is None:
        return
    cmd = [str(SERVER), "--model", str(MODEL), "--port", str(PORT),
           "--threads", str(THREADS)]
    _PROCESS = subprocess.Popen(cmd, cwd=RUNTIME, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    _READY.clear()
    _READER = threading.Thread(target=_drain, daemon=True)
    _READER.start()
    deadline = time.monotonic() + 300
    while not _READY.wait(.1):
        if _PROCESS.poll() is not None:
            raise RuntimeError("parakeet-server failed to start")
        if time.monotonic() >= deadline:
            raise TimeoutError("parakeet-server startup timed out")


def _stop() -> None:
    global _PROCESS, _READER
    proc, _PROCESS = _PROCESS, None
    if proc is not None:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if _READER is not None:
        _READER.join(timeout=5)
        _READER = None
    if proc is not None and proc.stdout is not None:
        proc.stdout.close()
    _READY.clear()
    if _port_in_use():
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-NetTCPConnection -LocalPort {PORT} -State Listen -ErrorAction SilentlyContinue | "
             "ForEach-Object { taskkill /F /PID $_.OwningProcess }"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    if _port_in_use():
        return _transcribe_http(wav)
    return _transcribe_cli(wav)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--load", action="store_true")
    p.add_argument("--unload", action="store_true")
    p.add_argument("wav", type=Path, nargs="*")
    args = p.parse_args()
    if args.load:
        if _port_in_use():
            sys.exit(0)
        _install()
        _start()
        sys.exit(0)
    if args.unload:
        _stop()
        sys.exit(0)
    if args.wav:
        for wav in args.wav:
            print(transcribe(wav), end="", flush=True)
    else:
        _install()
        transcript = transcribe(ROOT / "tts_out.wav").strip()
        print(transcript)
