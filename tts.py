from config import ensure_venv

ensure_venv()
if __name__ == "__main__":
    from main import main

    raise SystemExit(main("tts"))


import json
import re
import socket
import struct
import textwrap
import time
import wave

from config import CHATTERBOX_REV, TTS_RATE, Paths
from journal import finish_cleanup
from runtime import PROTOCOL_MAGIC, PROTOCOL_VERSION, REQ_CLOSE, REQ_SYNTH, RESP_CANCELLED, RESP_CLOSED, RESP_DONE, RESP_ERROR, RESP_PCM, Residents

_REQUEST_HEADER = struct.Struct("<IIIIIII")
_RESPONSE_HEADER = struct.Struct("<IIIIIIII")
_TEXT_CHUNK_CHARS = 120
_SENTENCE_BREAK = re.compile(r"(?<=[.!?\u2026])\s+")


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


def _text_chunks(text: str, limit: int = _TEXT_CHUNK_CHARS) -> list[str]:
    """Return bounded, non-overlapping speech pieces in their original order."""
    normalized = " ".join(text.split())
    units: list[str] = []
    for sentence in _SENTENCE_BREAK.split(normalized):
        units.extend(textwrap.wrap(sentence, width=limit, break_long_words=True,
                                   break_on_hyphens=False, replace_whitespace=True,
                                   drop_whitespace=True))
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}" if current else unit
        if current and len(candidate) > limit:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    if not chunks:
        raise RuntimeError("TTS input contains no speakable text")
    return chunks


def cook(paths: Paths, text: str) -> dict:
    residents = Residents(paths)
    failure = None
    try:
        pieces = _text_chunks(text)
        paths.journal.emit("tts", "start", text=text, chars=len(text), pieces=len(pieces),
                           piece_chars=[len(piece) for piece in pieces])
        residents.boot("nano", "en")
        client = residents.chatterbox_client()
        sock = client.sock
        assert sock is not None
        sock.settimeout(3600)

        started = time.perf_counter()
        out_wav = paths.run_dir / "out.wav"
        frames: list[dict] = []
        terminals: list[dict] = []
        pcm_bytes = 0
        first_pcm_at: float | None = None
        with wave.open(str(out_wav), "wb") as out:
            out.setparams((1, 2, TTS_RATE, 0, "NONE", "not compressed"))
            for piece_id, piece in enumerate(pieces):
                piece_started = time.perf_counter()
                before_bytes, before_frames = pcm_bytes, len(frames)
                _send(sock, REQ_SYNTH, 0, 0, piece_id, piece)
                paths.journal.emit("tts", "piece.sent", piece_id=piece_id, pieces=len(pieces),
                                   text=piece, chars=len(piece), bytes=len(piece.encode("utf-8")))
                terminal: dict | None = None
                while terminal is None:
                    kind, epoch, response_id, returned_piece, chunk_id, payload = _recv_frame(sock)
                    identity = (epoch, response_id, returned_piece)
                    if kind != RESP_CLOSED and identity != (0, 0, piece_id):
                        raise RuntimeError(f"unexpected TTS response identity {identity}, expected {(0, 0, piece_id)}")
                    if kind == RESP_PCM:
                        if first_pcm_at is None:
                            first_pcm_at = time.perf_counter()
                            paths.journal.emit("tts", "first_pcm",
                                               elapsed_ms=round((first_pcm_at - started) * 1000, 3),
                                               bytes=len(payload), piece_id=piece_id, chunk_id=chunk_id)
                        out.writeframesraw(payload)
                        pcm_bytes += len(payload)
                        frames.append({"kind": "pcm", "epoch": epoch, "response_id": response_id,
                                       "piece_id": returned_piece, "chunk_id": chunk_id,
                                       "bytes": len(payload)})
                    elif kind == RESP_DONE:
                        terminal = {"kind": "done", "epoch": epoch, "response_id": response_id,
                                    "piece_id": returned_piece}
                    elif kind == RESP_CANCELLED:
                        terminal = {"kind": "cancelled", "epoch": epoch, "response_id": response_id,
                                    "piece_id": returned_piece}
                    elif kind == RESP_ERROR:
                        message = payload.decode("utf-8", errors="replace")
                        paths.journal.emit("tts", "error", epoch=epoch, response_id=response_id,
                                           piece_id=returned_piece, message=message)
                        raise RuntimeError(f"native TTS error: {message}")
                    elif kind == RESP_CLOSED:
                        terminal = {"kind": "closed", "epoch": epoch, "response_id": response_id,
                                    "piece_id": returned_piece}
                    else:
                        raise RuntimeError(f"unknown TTS response kind {kind}")
                if terminal["kind"] != "done":
                    raise RuntimeError(f"cooked PCM without terminal ACK: {terminal}")
                piece_pcm = pcm_bytes - before_bytes
                if piece_pcm == 0:
                    raise RuntimeError(f"TTS piece {piece_id} produced no PCM")
                terminals.append(terminal)
                paths.journal.emit("tts", "piece.completed", piece_id=piece_id,
                                   elapsed_ms=round((time.perf_counter() - piece_started) * 1000, 3),
                                   frames=len(frames) - before_frames, pcm_bytes=piece_pcm)

        synthesis_finished = time.perf_counter()
        paths.journal.emit("tts", "terminal", kind="done", pieces=len(terminals),
                           frames=len(frames), pcm_bytes=pcm_bytes)

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

        duration_s = pcm_bytes / (TTS_RATE * 2)
        elapsed_ms = (synthesis_finished - started) * 1000
        rtf = elapsed_ms / (duration_s * 1000) if duration_s else 0.0
        paths.journal.emit("tts", "wav", path=out_wav.name, bytes=pcm_bytes,
                           duration_s=round(duration_s, 3))

        (paths.run_dir / "tokens.json").write_text(
            json.dumps({"text": text, "pieces": pieces, "chatterbox_pin": CHATTERBOX_REV,
                        "frames": frames, "terminals": terminals, "pcm_bytes": pcm_bytes,
                        "duration_s": duration_s, "wav": out_wav.name},
                       indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        paths.journal.emit("tts", "completed", elapsed_ms=round(elapsed_ms, 3), rtf=round(rtf, 3),
                           pieces=len(pieces), frames=len(frames), pcm_bytes=pcm_bytes,
                           duration_s=round(duration_s, 3), wav=out_wav.name)
        return {"wav": out_wav, "pieces": len(pieces), "frames": len(frames),
                "pcm_bytes": pcm_bytes, "duration_s": duration_s, "rtf": rtf}
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
