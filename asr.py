import secrets
import subprocess
import time
from pathlib import Path

from config import ASR_RATE, Paths
from journal import finish_cleanup
from runtime import CancelableHTTP, Residents

ensure_venv = lambda: None
if __name__ == "__main__":
    from main import main
    raise SystemExit(main("asr"))


def _ffmpeg_wav(src: Path, dest: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-i", str(src), "-ar", str(ASR_RATE), "-ac", "1", "-sample_fmt", "s16", str(dest)],
                   capture_output=True, check=True)


def _wav_bytes(path: Path) -> bytes:
    return path.read_bytes()


def transcribe(base: str, wav_path: Path, channel: CancelableHTTP) -> str:
    import json
    body = bytearray()
    boundary = "----trident" + secrets.token_hex(8)
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{wav_path.name}\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
    body.extend(_wav_bytes(wav_path))
    body.extend(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nparakeet\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n--{boundary}--\r\n".encode())
    response = channel.open(base + "/v1/audio/transcriptions", bytes(body),
                            {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
    try: return str(json.loads(response.read()).get("text") or "").strip()
    finally: channel.clear(response)


def launch(paths: Paths) -> None:
    residents, failure, http = Residents(paths), None, CancelableHTTP()
    try:
        residents.boot()
        base = residents.require_alive("parakeet")
        paths.journal.emit("main", "ready"); print("trident.ready", flush=True)
        for index, src in enumerate(paths.wavs, 1):
            dest = paths.run_dir / f"{src.stem}-16k.wav"
            _ffmpeg_wav(src, dest)
            duration = dest.stat().st_size / (ASR_RATE * 2)
            started = time.perf_counter()
            text = transcribe(base, dest, http)
            total = time.perf_counter() - started
            paths.journal.emit("asr", "completed", utterance_id=index, accepted=bool(text), input_s=round(duration, 3),
                               total_ms=round(total * 1000, 3), rtf=round(total / max(duration, 1e-9), 3),
                               chars=len(text), text=text, wav=dest.name)
            if text:
                paths.journal.transcript("user", text); print(f"user: {text}", flush=True)
    except BaseException as error:
        failure = (error, error.__traceback__)
    http.close()
    finish_cleanup(paths, failure, [("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))])
