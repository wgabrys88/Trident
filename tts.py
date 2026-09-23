import ctypes, json, subprocess, sys, time, wave
from datetime import datetime
from pathlib import Path
from runtime import MODELS, ROOT, Contract, alive, kill, launch_args, reexec, venv_python
from settings import ARCHITECTURES, VARIANTS, WAV, ready, retire, waiting

K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.WaitNamedPipeW.argtypes, K32.WaitNamedPipeW.restype = [ctypes.c_wchar_p, ctypes.c_uint], ctypes.c_int

def play(pcm):
    import numpy as np
    import sounddevice as sd
    ctypes.windll.ole32.CoInitializeEx(None, 0)
    seconds = len(pcm) / 2 / 24000
    sd.play(np.repeat(pcm, 2), 48000)
    callback = sd._last_callback
    if callback.event.wait(seconds + 120):
        callback.stream.close()
        return
    callback.stream.stop()
    callback.stream.close()
    raise RuntimeError("play timeout")

def write_wav(pcm: bytes) -> Path:
    home = ROOT / WAV
    home.mkdir(parents=True, exist_ok=True)
    path = home / f"{datetime.now():%y%m%d-%H%M%S%f}.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(pcm)
    return path

def synthesize(pipe: str, text: str, language: str = "") -> bytes:
    if "\n" in language or "\r" in language:
        raise RuntimeError("say language")
    if not K32.WaitNamedPipeW(pipe, 60000):
        raise RuntimeError("mouth: pipe " + pipe)
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
            raise RuntimeError(ack or "server closed the pipe")
        pcm, remaining = bytearray(), int(ack[3:]) * 2
        while remaining:
            chunk = stream.read(remaining)
            if not chunk:
                raise BrokenPipeError(pipe)
            pcm += chunk
            remaining -= len(chunk)
    return bytes(pcm)

def loose_wavs() -> None:
    home = ROOT / WAV
    if not home.is_dir():
        return
    for path in list(home.glob("*.wav")):
        retire(path, "wav")

def say(pipe: str, text: str, language: str = "", architecture: str = ""):
    if architecture == "gpt2" and language != "en":
        raise RuntimeError("Unsupported language: " + language)
    import numpy as np
    loose_wavs()
    pcm = np.frombuffer(synthesize(pipe, text, language), dtype=np.int16)
    wav = write_wav(pcm.tobytes())
    print(text, flush=True)
    try:
        play(pcm)
    except Exception:
        if wav.is_file():
            retire(wav, "wav")
        raise
    print(retire(wav, "wav"), flush=True)

def serve(name: str) -> str:
    if name not in VARIANTS:
        raise SystemExit("variant is nano, turbo, or v3")
    cfg = VARIANTS[name]
    args = launch_args(cfg)
    exe = ROOT / "build" / "bin" / "chatterbox-server.exe"
    voice_dir = MODELS / "voices" / cfg.name
    t3, s3, baked = voice_dir / "t3.gguf", voice_dir / "s3.gguf", voice_dir / "bake.json"
    py, ckpt = venv_python(), ROOT / ARCHITECTURES[cfg.architecture]["ckpt"]
    for path in (exe, t3, s3, baked, py):
        if not path.is_file():
            raise RuntimeError("missing " + str(path))
    pipe, pid, flags = rf"\\.\pipe\chatterbox-{cfg.name}", MODELS / "server.pid", dict(args.knobs)
    if cfg.architecture == "llama":
        for path in (ckpt / "official_mtl_tokenizer.py", ckpt / "official_mtl_tts.py", ckpt / "grapheme_mtl_merged_expanded_v1.json",
                     ckpt / "Cangjie5_TC.json", ckpt / "dicta-1.0.int8.onnx"):
            if not path.is_file():
                raise RuntimeError("missing " + str(path))
        flags.update({"tokenizer-python": str(py), "tokenizer-script": str(ROOT / "scripts/mtl_tokenize_runtime.py"),
                      "tokenizer-source": str(ckpt / "official_mtl_tokenizer.py"), "tokenizer-tts-source": str(ckpt / "official_mtl_tts.py"),
                      "tokenizer-json": str(ckpt / "grapheme_mtl_merged_expanded_v1.json"), "cangjie-json": str(ckpt / "Cangjie5_TC.json"),
                      "dicta-model": str(ckpt / "dicta-1.0.int8.onnx")})
    command = [str(exe), str(t3), str(s3), pipe] + [item for pair in flags.items() for item in (f"--{pair[0]}", pair[1])]
    wanted = {"command": command, "voice": json.loads(baked.read_text(encoding="utf-8"))}
    keep = {pid, MODELS / "trident.pid"}
    for legacy in MODELS.glob("*.pid"):
        if legacy not in keep:
            kill(legacy)
    if pid.is_file():
        record = json.loads(pid.read_text(encoding="utf-8"))
        if record["contract"] == wanted and alive(record["pid"]):
            if K32.WaitNamedPipeW(pipe, 3000):
                return pipe
    kill(pid)
    proc = subprocess.Popen(command, cwd=exe.parent, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NEW_CONSOLE)
    Contract({"pid": proc.pid, "contract": wanted}).write(pid)
    deadline = time.monotonic() + 30
    while not K32.WaitNamedPipeW(pipe, 1000):
        if proc.poll() is not None:
            raise RuntimeError(f"server exited: {proc.returncode}")
        if time.monotonic() >= deadline:
            raise TimeoutError("server startup")
        time.sleep(0.05)
    return pipe

def watch(pipe: str, architecture: str):
    ready("mouth")
    while True:
        files = waiting("speech")
        if not files:
            time.sleep(0.05)
            continue
        path = files[0]
        body = path.read_text(encoding="utf-8-sig")
        language, _, text = body.partition("\n")
        language, text = language.strip(), text.strip()
        if not text:
            retire(path, "speech")
            continue
        say(pipe, text, language, architecture)
        retire(path, "speech")

if __name__ == "__main__":
    reexec()
    argv = sys.argv
    if len(argv) == 2 and argv[1] in VARIANTS:
        watch(serve(argv[1]), VARIANTS[argv[1]].architecture)
    elif len(argv) in (3, 4) and argv[1] in VARIANTS and argv[2]:
        say(serve(argv[1]), argv[2], argv[3] if len(argv) == 4 else "en", VARIANTS[argv[1]].architecture)
    else:
        raise SystemExit("usage: python tts.py <variant> [text] [language]")
