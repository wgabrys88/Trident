from config import ensure_venv

ensure_venv()
if __name__ == "__main__":
    from main import main

    raise SystemExit(main("tts"))


import json
import socket
import struct
import time
import wave
from pathlib import Path

from config import CHATTERBOX_REV, PORTS, TTS_RATE, Paths
from journal import finish_cleanup
from runtime import PROTOCOL_MAGIC, PROTOCOL_VERSION, REQ_CLOSE, REQ_SYNTH, RESP_CANCELLED, RESP_CLOSED, RESP_DONE, RESP_ERROR, RESP_PCM, Residents

_REQUEST_HEADER = struct.Struct("<IIIIIII")
_RESPONSE_HEADER = struct.Struct("<IIIIIIII")
_INT16_MAX = 32767


def _send(sock: socket.socket, kind: int, epoch: int = 0, response_id: int = 0, piece_id: int = 0, text: str = "") -> None:
    raw = text.encode("utf-8")
    sock.sendall(_REQUEST_HEADER.pack(PROTOCOL_MAGIC, PROTOCOL_VERSION, kind, epoch, response_id, piece_id, len(raw)) + raw)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise RuntimeError("unexpected TTS socket EOF")
        buf.extend(chunk)
    return bytes(buf)


def _recv_frame(sock: socket.socket) -> tuple[int, int, int, int, int, bytes]:
    header = _recv_exact(sock, _RESPONSE_HEADER.size)
    magic, version, kind, epoch, response_id, piece_id, chunk_id, length = _RESPONSE_HEADER.unpack(header)
    if magic != PROTOCOL_MAGIC or version != PROTOCOL_VERSION:
        raise RuntimeError("unsupported TTS response protocol")
    payload = _recv_exact(sock, length) if length else b""
    return kind, epoch, response_id, piece_id, chunk_id, payload


def _write_wav(path: Path, pcm: bytes, rate: int = TTS_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        out.writeframes(pcm)


def cook(paths: Paths, text: str) -> dict:
    residents = Residents(paths)
    failure = None
    try:
        paths.journal.emit("tts", "start", text=text, chars=len(text))
        residents.boot("nano", "en")
        client = residents.chatterbox_client()
        sock = client.sock
        assert sock is not None
        sock.settimeout(3600)

        started = time.perf_counter()
        _send(sock, REQ_SYNTH, 0, 0, 0, text)
        paths.journal.emit("tts", "synthesize.sent", text=text, bytes=len(text.encode("utf-8")))

        pcm = bytearray()
        frames: list[dict] = []
        first_pcm_at: float | None = None
        terminal: dict | None = None

        while True:
            kind, epoch, response_id, piece_id, chunk_id, payload = _recv_frame(sock)
            if kind == RESP_PCM:
                if not pcm:
                    first_pcm_at = time.perf_counter()
                pcm.extend(payload)
                frames.append({"kind": "pcm", "epoch": epoch, "response_id": response_id,
                               "piece_id": piece_id, "chunk_id": chunk_id, "bytes": len(payload)})
            elif kind == RESP_DONE:
                terminal = {"kind": "done", "epoch": epoch, "response_id": response_id, "piece_id": piece_id}
                break
            elif kind == RESP_CANCELLED:
                terminal = {"kind": "cancelled", "epoch": epoch, "response_id": response_id, "piece_id": piece_id}
                break
            elif kind == RESP_ERROR:
                message = payload.decode("utf-8", errors="replace")
                paths.journal.emit("tts", "error", epoch=epoch, response_id=response_id, piece_id=piece_id, message=message)
                raise RuntimeError(f"native TTS error: {message}")
            elif kind == RESP_CLOSED:
                terminal = {"kind": "closed", "epoch": epoch, "response_id": response_id, "piece_id": piece_id}
                break
            else:
                raise RuntimeError(f"unknown TTS response kind {kind}")

        if terminal is None:
            terminal = {"kind": "unknown"}
        if terminal.get("kind") not in {"done", "closed"}:
            raise RuntimeError(f"cooked PCM without terminal ACK: {terminal}")
        if first_pcm_at is not None:
            paths.journal.emit("tts", "first_pcm", elapsed_ms=round((first_pcm_at - started) * 1000, 3), bytes=0)
        paths.journal.emit("tts", "terminal", **terminal, frames=len(frames), pcm_bytes=len(pcm))

        _send(sock, REQ_CLOSE)
        closed_deadline = time.monotonic() + 10
        while time.monotonic() < closed_deadline:
            kind, *_ = _recv_frame(sock)
            if kind == RESP_CLOSED:
                break
        else:
            raise RuntimeError("native close handshake timed out")
        paths.journal.emit("tts", "closed")
        residents.chatterbox_closed.set()

        duration_s = len(pcm) / (TTS_RATE * 2)
        out_wav = paths.run_dir / "out.wav"
        _write_wav(out_wav, bytes(pcm))
        paths.journal.emit("tts", "wav", path=out_wav.name, bytes=len(pcm), duration_s=round(duration_s, 3))

        from utils import spectrogram
        spec = spectrogram(out_wav)
        paths.journal.emit("tts", "spectrogram", path=spec.name)

        (paths.run_dir / "tokens.json").write_text(
            json.dumps({"text": text, "chatterbox_pin": CHATTERBOX_REV, "frames": frames, "terminal": terminal,
                        "pcm_bytes": len(pcm), "duration_s": duration_s, "wav": out_wav.name, "spectrogram": spec.name},
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        paths.journal.emit("tts", "completed", elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                           frames=len(frames), pcm_bytes=len(pcm), duration_s=round(duration_s, 3),
                           wav=out_wav.name, spectrogram=spec.name)
        return {"wav": out_wav, "spectrogram": spec, "frames": len(frames), "pcm_bytes": len(pcm), "duration_s": duration_s}
    except BaseException as error:
        failure = (error, error.__traceback__)
        raise
    finally:
        finish_cleanup(paths, failure, [("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))])


def launch(paths: Paths, family: str = "nano", language: str = "en", primary: str | None = None,
           replacement=None, interrupt_after=None) -> None:
    if family != "nano":
        raise RuntimeError("TTS-only mode supports nano family only")
    if language != "en":
        raise RuntimeError("TTS-only mode supports English only")
    if replacement is not None or interrupt_after is not None:
        raise RuntimeError("interrupt flags are not available in TTS-only mode")
    if not primary:
        raise RuntimeError("TTS requires --text or --text-file")
    cook(paths, primary)
