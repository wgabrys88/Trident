import ctypes, json, subprocess, sys, time, wave
from datetime import datetime
from pathlib import Path
from install import MODELS, ROOT, alive, kill, launch_args, venv_python
from settings import ARCHITECTURES, VARIANTS
DETACH = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.WaitNamedPipeW.argtypes, K32.WaitNamedPipeW.restype = [ctypes.c_wchar_p, ctypes.c_uint], ctypes.c_int
LIMIT = 300
def cable():
    import sounddevice as sd
    return next(i for i, item in enumerate(sd.query_devices())
                if str(item["name"]).startswith("CABLE Input") and item["max_output_channels"] and item["default_samplerate"] == 48000)
def play(pcm):
    import numpy as np
    import sounddevice as sd
    ctypes.windll.ole32.CoInitializeEx(None, 0)
    sd.play(np.repeat(pcm, 2), 48000, device=cable())
    sd.wait()
def write_wav(pcm: bytes, stem: str) -> Path:
    home = ROOT / "wav"
    home.mkdir(parents=True, exist_ok=True)
    path = home / f"{datetime.now():%y%m%d-%H%M%S}_{stem}.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(pcm)
    return path
def synthesize(pipe: str, text: str) -> bytes:
    if not K32.WaitNamedPipeW(pipe, 60000):
        raise RuntimeError("mouth: pipe " + pipe)
    payload = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    with open(pipe, "r+b", buffering=0) as stream:
        message = memoryview(f"\n{len(payload)}\n".encode("utf-8") + payload)
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
def say(pipe: str, text, n: int) -> int:
    import numpy as np
    if not isinstance(text, list):
        raise RuntimeError("say text is not an array")
    clips = []
    for piece in text:
        if not isinstance(piece, str) or len(piece) > LIMIT:
            raise RuntimeError("say piece exceeds 300 characters")
        pcm = np.frombuffer(synthesize(pipe, piece), dtype=np.int16)
        n += 1
        print(write_wav(pcm.tobytes(), str(n)), flush=True)
        print(piece, flush=True)
        clips.append(pcm)
    if clips:
        play(np.concatenate(clips))
    return n
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
    for legacy in MODELS.glob("*.pid"):
        if legacy != pid:
            kill(legacy)
    if pid.is_file():
        record = json.loads(pid.read_text(encoding="utf-8"))
        if record["contract"] == wanted and alive(record["pid"]):
            if K32.WaitNamedPipeW(pipe, 3000):
                return pipe
    kill(pid)
    proc = subprocess.Popen(command, cwd=exe.parent, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACH)
    from install import Contract
    Contract({"pid": proc.pid, "contract": wanted}).write(pid)
    deadline = time.monotonic() + 30
    while not K32.WaitNamedPipeW(pipe, 1000):
        if proc.poll() is not None:
            raise RuntimeError(f"server exited: {proc.returncode}")
        if time.monotonic() >= deadline:
            raise TimeoutError("server startup")
        time.sleep(0.05)
    return pipe
def stay():
    pid = MODELS / "server.pid"
    print("ready", flush=True)
    while pid.is_file():
        record = json.loads(pid.read_text(encoding="utf-8"))
        if not alive(record["pid"]):
            return
        time.sleep(1)
if __name__ == "__main__":
    from install import reexec
    reexec()
    argv = sys.argv
    if len(argv) == 4 and argv[2] == "--hear":
        import numpy as np
        play(np.frombuffer(synthesize(serve(argv[1]), argv[3]), dtype=np.int16))
    elif len(argv) == 2:
        serve(argv[1])
        stay()
    else:
        raise SystemExit("usage: python tts.py <variant> [--hear <text>]")
