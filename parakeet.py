import argparse, http.client, json, shutil, subprocess, sys, time, uuid, wave, zipfile
from pathlib import Path

from main import CMAKE, ROOT, _checkout, _download, _kill_port, _port_in_use, _run_logged, _wait_port, jsonl

RUNTIME = ROOT / "tools/runtime/parakeet"
EXE = RUNTIME / "parakeet-cli.exe"
SERVER = RUNTIME / "parakeet-server.exe"
MODEL = ROOT / "models/nemotron-3.5-asr-streaming-0.6b-q4_k.gguf"
MODEL_CARD = MODEL.with_suffix(".md")
ARCHIVE = "parakeet-v0.5.0-bin-win-vulkan-x64.zip"
RUNTIME_URL = f"https://github.com/mudler/parakeet.cpp/releases/download/v0.5.0/{ARCHIVE}"
MODEL_URL = "https://huggingface.co/mudler/parakeet-cpp-gguf/resolve/bf0af9f425fa01809cadec671b3cb672709d13e9"
PARAKEET_REV = "e75de9b6b9b688fd293aa22f7e27aa724ea286f8"
THREADS = 6
LANGUAGE = "auto"
PORT = 17934
TIMESTAMP_FORMAT = "%d-%m-%y-%H-%M-%S"

_PROCESS = None


def _build(work: Path) -> None:
    source = work / "s"
    _checkout("https://github.com/mudler/parakeet.cpp.git", PARAKEET_REV, source,
              ("/CMakeLists.txt", "/LICENSE", "/src/", "/include/", "/examples/",
               "/third_party/", "/scripts/apply_ggml_patches.sh", "/scripts/requirements.txt"))
    _run_logged(["git", "-C", str(source), "submodule", "update", "--init", "--depth=1",
                 "--filter=blob:none", "third_party/ggml"], step="parakeet-submodule")
    build = work / "b"
    _run_logged([CMAKE, "-S", str(source), "-B", str(build),
                 "-G", "Visual Studio 17 2022", "-A", "x64", "-DPARAKEET_BUILD_TESTS=OFF",
                 "-DPARAKEET_BUILD_CLI=ON", "-DPARAKEET_BUILD_SERVER=ON",
                 "-DGGML_NATIVE=ON", "-DGGML_LLAMAFILE=ON"], step="parakeet-cmake")
    _run_logged([CMAKE, "--build", str(build),
                 "--config", "Release", "--target", "parakeet-server", "--parallel", "4"],
                step="parakeet-msbuild")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build / "examples/server/Release/parakeet-server.exe", SERVER)
    for dll in (build / "bin/Release").glob("*.dll"):
        shutil.copy2(dll, RUNTIME / dll.name)
    shutil.copy2(source / "LICENSE", RUNTIME / "parakeet-LICENSE.txt")


def _install() -> None:
    import tempfile
    required = [EXE, SERVER, RUNTIME / "parakeet-LICENSE.txt", MODEL, MODEL_CARD]
    if all(p.is_file() for p in required):
        return
    with tempfile.TemporaryDirectory(prefix=".p-", dir=ROOT) as tmp:
        work = Path(tmp)
        if not (EXE.is_file() and (RUNTIME / "parakeet-LICENSE.txt").is_file()):
            archive = work / ARCHIVE
            _download(RUNTIME_URL, archive)
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
            _download(f"{MODEL_URL}/{MODEL.name}", downloaded)
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
    _wait_port(_PROCESS, PORT, 300)


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


def transcribe_json(wav: Path) -> dict:
    wav = (wav if wav.is_absolute() else ROOT / wav).resolve()
    cmd = [str(EXE), "transcribe", "--model", str(MODEL), "--input", str(wav),
           "--lang", LANGUAGE, "--threads", str(THREADS), "--json"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", check=True)
    return json.loads(result.stdout)


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
    p.add_argument("--json", action="store_true", help="emit text plus per-word/per-token timestamps")
    p.add_argument("wav", type=Path, nargs="*")
    args = p.parse_args()
    if args.unload:
        _stop()
        sys.exit(0)
    _install()
    if args.install:
        _start()
        sys.exit(0)
    if args.load:
        _start()
        try:
            input("[parakeet] ready. Press Enter to stop...\n")
        except EOFError:
            while True:
                time.sleep(3600)
        _stop()
        sys.exit(0)
    if args.wav:
        for wav_path in args.wav:
            t0 = time.perf_counter()
            result = transcribe_json(wav_path) if args.json else transcribe(wav_path).strip()
            t1 = time.perf_counter()
            with wave.open(str(wav_path)) as wf:
                dur = wf.getnframes() / wf.getframerate()
            print(json.dumps(result, ensure_ascii=False) if args.json else result, flush=True)
            jsonl("parakeet.rtf", total_s=round(t1 - t0, 3), audio_s=round(dur, 3))
    else:
        wav = ROOT / "tts_out.wav"
        with wave.open(str(wav)) as wf:
            dur = wf.getnframes() / wf.getframerate()
        t0 = time.perf_counter()
        transcript = transcribe(wav).strip()
        t1 = time.perf_counter()
        print(transcript)
        jsonl("parakeet.rtf", total_s=round(t1 - t0, 3), audio_s=round(dur, 3))
