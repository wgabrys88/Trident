import ctypes, json, subprocess, sys, time, urllib.request, wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "workspace"
DONE = WORK / "done"
MODELS = ROOT / "models"
VARIANTS = {
    "nano": {"arch": "gpt2", "flags": {"seed": "42", "temperature": "0.8", "top-k": "1000", "top-p": "0.95", "repeat-penalty": "1.2", "n-predict": "1000", "cfm-steps": "2", "trim-fade-samples": "480"}},
    "turbo": {"arch": "gpt2", "flags": {"seed": "42", "temperature": "0.8", "top-k": "1000", "top-p": "0.95", "repeat-penalty": "1.2", "n-predict": "1000", "cfm-steps": "2", "trim-fade-samples": "480"}},
    "v3": {"arch": "llama", "flags": {"seed": "42", "temperature": "0.8", "top-p": "1.0", "repeat-penalty": "1.2", "n-predict": "1000", "cfm-steps": "10", "trim-fade-samples": "480", "min-p": "0.05", "cfg-weight": "0.5", "exaggeration": "0.5", "cfm-cfg": "0.7"}},
}


def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"


def reexec() -> None:
    py = venv_python()
    if not py.is_file():
        raise RuntimeError("missing " + str(py))
    if Path(sys.executable).resolve() != py.resolve():
        raise SystemExit(subprocess.call([str(py), *sys.argv]))
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def http(server: str, method: str, path: str, payload=None, timeout=70):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(server + path, data=raw, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])
    return data


def retire(path: Path, kind: str) -> Path:
    folder = DONE / kind
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / path.name
    n = 1
    while dest.exists():
        dest = folder / f"{path.stem}-{n}{path.suffix}"
        n += 1
    path.replace(dest)
    return dest


def kill_old_server() -> None:
    path = MODELS / "server.pid"
    if not path.is_file():
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(record["pid"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        path.unlink(missing_ok=True)


def pipe_api():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    k32.WaitNamedPipeW.restype = ctypes.c_int
    return k32


def server_command(name: str, gpu: int) -> tuple[list[str], str]:
    cfg = VARIANTS[name]
    exe = ROOT / "build" / "bin" / "chatterbox-server.exe"
    voice = MODELS / "voices" / name
    t3, s3 = voice / "t3.gguf", voice / "s3.gguf"
    for path in (exe, t3, s3):
        if not path.is_file():
            raise RuntimeError("missing " + str(path))
    pipe = rf"\\.\pipe\chatterbox-{name}"
    flags = dict(cfg["flags"])
    flags["gpu"] = str(gpu)
    if cfg["arch"] == "llama":
        ckpt = ROOT / ".ckpt-v3"
        required = {
            "tokenizer-python": venv_python(),
            "tokenizer-script": ROOT / "scripts" / "mtl_tokenize_runtime.py",
            "tokenizer-source": ckpt / "official_mtl_tokenizer.py",
            "tokenizer-tts-source": ckpt / "official_mtl_tts.py",
            "tokenizer-json": ckpt / "grapheme_mtl_merged_expanded_v1.json",
            "cangjie-json": ckpt / "Cangjie5_TC.json",
            "dicta-model": ckpt / "dicta-1.0.int8.onnx",
        }
        for path in required.values():
            if not Path(path).is_file():
                raise RuntimeError("missing " + str(path))
        flags.update({key: str(value) for key, value in required.items()})
    command = [str(exe), str(t3), str(s3), pipe] + [item for key, value in flags.items() for item in ("--" + key, value)]
    return command, pipe


def start_engine(name: str, gpu: int):
    kill_old_server()
    command, pipe = server_command(name, gpu)
    exe = Path(command[0])
    proc = subprocess.Popen(command, cwd=exe.parent, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NEW_CONSOLE)
    MODELS.mkdir(parents=True, exist_ok=True)
    (MODELS / "server.pid").write_text(json.dumps({"pid": proc.pid}) + "\n", encoding="utf-8")
    k32 = pipe_api()
    deadline = time.monotonic() + 30
    while not k32.WaitNamedPipeW(pipe, 1000):
        if proc.poll() is not None:
            raise RuntimeError(f"mouth engine exited {proc.returncode}")
        if time.monotonic() >= deadline:
            raise TimeoutError("mouth engine startup")
        time.sleep(0.05)
    return proc, pipe


def synthesize(pipe: str, text: str, language: str) -> bytes:
    k32 = pipe_api()
    if not k32.WaitNamedPipeW(pipe, 60000):
        raise RuntimeError("mouth pipe " + pipe)
    payload = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    with open(pipe, "r+b", buffering=0) as stream:
        message = memoryview(f"{language}\n{len(payload)}\n".encode("utf-8") + payload)
        while message:
            sent = stream.write(message)
            if not sent:
                raise BrokenPipeError(pipe)
            message = message[sent:]
        ack = stream.readline().decode("utf-8").strip()
        if not ack.startswith("ok "):
            raise RuntimeError(ack or "mouth server closed pipe")
        pcm, remaining = bytearray(), int(ack[3:]) * 2
        while remaining:
            chunk = stream.read(remaining)
            if not chunk:
                raise BrokenPipeError(pipe)
            pcm += chunk
            remaining -= len(chunk)
    return bytes(pcm)


def write_wav(pcm: bytes) -> Path:
    home = ROOT / "wav"
    home.mkdir(parents=True, exist_ok=True)
    path = home / f"{datetime.now():%y%m%d-%H%M%S%f}.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(pcm)
    return path


def play(pcm: bytes) -> None:
    import numpy as np
    import sounddevice as sd
    ctypes.windll.ole32.CoInitializeEx(None, 0)
    audio = np.frombuffer(pcm, dtype=np.int16)
    sd.play(np.repeat(audio, 2), 48000)
    sd.wait()


def archive_loose_wavs() -> None:
    home = ROOT / "wav"
    if home.is_dir():
        for path in list(home.glob("*.wav")):
            retire(path, "wav")


def serve(name: str, server: str) -> None:
    cfg = VARIANTS[name]
    gpu = int(http(server, "GET", "/config")["vulkan_device"])
    engine, pipe = start_engine(name, gpu)
    archive_loose_wavs()
    http(server, "POST", "/ready", {"name": "mouth"})
    try:
        while True:
            item = http(server, "GET", "/speech/next?timeout=30", timeout=40).get("speech")
            if not item:
                continue
            language, text = item["language"], item["text"]
            spoken_language = language
            if cfg["arch"] == "gpt2" and language not in ("", "en"):
                spoken_language = "en"
                http(server, "POST", "/event", {"type": "mouth_language", "source": "mouth", "data": {"requested": language, "spoken_as": "en", "speech": item["name"]}})
            pcm = synthesize(pipe, text, spoken_language)
            wav = write_wav(pcm)
            print(text, flush=True)
            try:
                play(pcm)
            finally:
                archived = retire(wav, "wav") if wav.is_file() else wav
            http(server, "POST", "/speech/done", {"name": item["name"], "wav": str(archived.relative_to(ROOT))})
    finally:
        if engine.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(engine.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (MODELS / "server.pid").unlink(missing_ok=True)


def main():
    reexec()
    if len(sys.argv) != 3 or sys.argv[1] not in VARIANTS:
        raise SystemExit("usage: python mouth.py <nano|turbo|v3> <server>")
    serve(sys.argv[1], sys.argv[2].rstrip("/"))


if __name__ == "__main__":
    main()
