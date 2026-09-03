from config import ensure_venv

ensure_venv()
if __name__ == "__main__":
    from main import main

    raise SystemExit(main("tts"))


import json
import re
import textwrap
import time
import wave

from config import CHATTERBOX_REV, TTS_RATE, Paths
from runtime import REQ_CLOSE, REQ_SYNTH, RESP_CANCELLED, RESP_CLOSED, RESP_DONE, RESP_ERROR, RESP_PCM, Residents

_TEXT_CHUNK_CHARS = 60
_SENTENCE_BREAK = re.compile(r"(?<=[.!?\u2026])\s+")
# chatterbox s3gen_synthesize zeros the first 20 ms of every chunk_id=0 piece.
# We strip this for piece_id==0 only; subsequent pieces keep zeros + fade-in.
_S3GEN_OPENING_ZEROS = (TTS_RATE // 50) * 2


def _text_chunks(text: str, limit: int = _TEXT_CHUNK_CHARS) -> list[str]:
    """Pack sentences into chunks of up to limit chars. A sentence longer than limit
    is split by textwrap. Joining short sentences into one chunk reduces per-piece
    S3Gen overhead (see chatterbox-tts CFM+HiFT pipeline at ~500ms/piece)."""
    normalized = " ".join(text.split())
    sentences = [s.strip() for s in _SENTENCE_BREAK.split(normalized) if s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(textwrap.wrap(sentence, width=limit, break_long_words=True,
                                        break_on_hyphens=False, replace_whitespace=True,
                                        drop_whitespace=True))
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    if not chunks:
        raise RuntimeError("TTS input contains no speakable text")
    return chunks


def _pcm_for_wav(payload: bytes, piece_id: int) -> bytes:
    """Only strip start-of-stream zeros for the first piece. Subsequent pieces keep
    zeros so the fade-in ramp smooths the join naturally (no abrupt onset artifacts)."""
    if piece_id != 0:
        return payload
    if len(payload) <= _S3GEN_OPENING_ZEROS or payload[:_S3GEN_OPENING_ZEROS] != b"\x00" * _S3GEN_OPENING_ZEROS:
        return payload
    return payload[_S3GEN_OPENING_ZEROS:]


def cook(paths: Paths, text: str) -> dict:
    residents = Residents(paths)
    failure = None
    try:
        pieces = _text_chunks(text)
        paths.journal.emit("tts", "start", text=text, chars=len(text), pieces=len(pieces),
                           piece_chars=[len(piece) for piece in pieces])
        residents.boot("nano", "en")
        client = residents.chatterbox_client()
        proto = client.proto
        assert proto is not None and proto.sock is not None
        proto.sock.settimeout(3600)

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
                proto.send(REQ_SYNTH, piece_id=piece_id, text=piece)
                paths.journal.emit("tts", "piece.sent", piece_id=piece_id, pieces=len(pieces),
                                   text=piece, chars=len(piece), bytes=len(piece.encode("utf-8")))
                terminal: dict | None = None
                while terminal is None:
                    kind, epoch, response_id, returned_piece, chunk_id, payload = proto.recv_frame()
                    identity = (epoch, response_id, returned_piece)
                    if kind != RESP_CLOSED and identity != (0, 0, piece_id):
                        raise RuntimeError(f"unexpected TTS response identity {identity}, expected {(0, 0, piece_id)}")
                    if kind == RESP_PCM:
                        wav_payload = _pcm_for_wav(payload, piece_id)
                        if first_pcm_at is None:
                            first_pcm_at = time.perf_counter()
                            paths.journal.emit("tts", "first_pcm",
                                               elapsed_ms=round((first_pcm_at - started) * 1000, 3),
                                               bytes=len(wav_payload), piece_id=piece_id, chunk_id=chunk_id)
                        out.writeframesraw(wav_payload)
                        pcm_bytes += len(wav_payload)
                        frames.append({"kind": "pcm", "epoch": epoch, "response_id": response_id,
                                       "piece_id": returned_piece, "chunk_id": chunk_id,
                                       "bytes": len(wav_payload)})
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

        proto.send(REQ_CLOSE)
        closed_deadline = time.monotonic() + 10
        while time.monotonic() < closed_deadline:
            kind, *_ = proto.recv_frame()
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
        try:
            residents.stop()
        except BaseException as cleanup_error:
            paths.journal.failure("cleanup.residents", cleanup_error)
        if failure is not None:
            raise failure[0].with_traceback(failure[1])


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
