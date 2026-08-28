from __future__ import annotations

import http.client
import json
import secrets
import select
import socket
import struct
import urllib.parse
import urllib.request
from pathlib import Path


def _connection(base_url: str, timeout: float):
    parsed = urllib.parse.urlsplit(base_url)
    return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout), parsed


def parakeet_transcribe(base_url: str, wav: Path, timeout: float = 3600.0) -> dict:
    boundary = "----------------trident" + secrets.token_hex(12)
    filename = wav.name.replace('"', "_")
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("ascii")
    fields = (
        f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="model"\r\n\r\n'
        "parakeet\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="response_format"\r\n\r\n'
        "verbose_json\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="timestamp_granularities[]"\r\n\r\n'
        "word\r\n"
        f"--{boundary}--\r\n"
    ).encode("ascii")
    content_length = len(head) + wav.stat().st_size + len(fields)
    conn, parsed = _connection(base_url, timeout)
    try:
        path = (parsed.path.rstrip("/") if parsed.path else "") + "/v1/audio/transcriptions"
        conn.putrequest("POST", path)
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(content_length))
        conn.putheader("Accept", "application/json")
        conn.putheader("Connection", "close")
        conn.endheaders()
        conn.send(head)
        with wav.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                conn.send(block)
        conn.send(fields)
        response = conn.getresponse()
        body = response.read()
        if not 200 <= response.status < 300:
            raise RuntimeError(
                f"Parakeet server HTTP {response.status}: "
                + body.decode("utf-8", errors="replace")[:1000]
            )
        return json.loads(body.decode("utf-8"))
    finally:
        conn.close()


def gemma_chat(base_url: str, payload: dict, timeout: float = 3600.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    url = base_url.rstrip("/") + "/v1/chat/completions"
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": "trident/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body.decode("utf-8"))


def gemma_chat_stream(base_url: str, payload: dict, timeout: float = 3600.0):
    body = dict(payload)
    body["stream"] = True
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    conn, parsed = _connection(base_url, timeout)
    try:
        path = (parsed.path.rstrip("/") if parsed.path else "") + "/v1/chat/completions"
        conn.putrequest("POST", path)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Accept", "text/event-stream")
        conn.putheader("Content-Length", str(len(data)))
        conn.putheader("Connection", "close")
        conn.endheaders()
        conn.send(data)
        response = conn.getresponse()
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Gemma server HTTP {response.status}: " + response.read().decode("utf-8", errors="replace")[:1000])
        while line := response.readline():
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data_line = line[5:].strip()
            if data_line == b"[DONE]":
                break
            event = json.loads(data_line.decode("utf-8"))
            text = str((event.get("choices") or [{}])[0].get("delta", {}).get("content") or "")
            if text:
                yield text
    finally:
        conn.close()


_PIECE_END = 0xFFFFFFFF
_PIECE_CANCEL = 0xFFFFFFFE
_PIECE_FRAME_KIND = 3


class _TtsConnection:
    def __init__(self, base_url: str, timeout: float) -> None:
        self._parsed = urllib.parse.urlsplit(base_url)
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._closed = False
        self._metrics: str = ""

    def _recv_exact(self, count: int) -> bytes:
        assert self._sock is not None
        data = bytearray()
        while len(data) < count:
            if self._closed:
                raise InterruptedError
            if not select.select([self._sock], [], [], 0.05)[0]:
                continue
            part = self._sock.recv(count - len(data))
            if not part:
                raise RuntimeError("resident TTS closed the connection early")
            data.extend(part)
        return bytes(data)

    def connect(self) -> None:
        self._sock = socket.create_connection(
            (self._parsed.hostname, self._parsed.port), timeout=self._timeout
        )
        self._sock.settimeout(self._timeout)

    def send_piece(self, index: int, text: str, wav_path: Path | None) -> None:
        assert self._sock is not None
        text_bytes = text.encode("utf-8")
        path_bytes = str(wav_path.resolve()).encode("utf-8") if wav_path is not None else b""
        header = struct.pack("<III", int(index) & 0xFFFFFFFF, len(path_bytes), len(text_bytes))
        self._sock.sendall(header)
        self._sock.sendall(text_bytes)
        self._sock.sendall(path_bytes)

    def cancel_piece(self) -> None:
        assert self._sock is not None
        header = struct.pack("<III", _PIECE_CANCEL, 0, 0)
        self._sock.sendall(header)

    def end_stream(self) -> str:
        assert self._sock is not None
        self._sock.sendall(struct.pack("<III", _PIECE_END, 0, 0))
        kind, length = struct.unpack("<II", self._recv_exact(8))
        payload = self._recv_exact(length) if length else b""
        if kind == 0:
            self._metrics = payload.decode("utf-8", errors="replace")
            return self._metrics
        message = payload.decode("utf-8", errors="replace")
        raise RuntimeError(f"Chatterbox resident synthesis failed: {message or 'unknown error'}")

    def recv_pcm(self) -> bytes:
        kind, length = struct.unpack("<II", self._recv_exact(8))
        payload = self._recv_exact(length) if length else b""
        if kind == 2:
            return payload
        if kind == _PIECE_FRAME_KIND:
            raise _PieceComplete()
        message = payload.decode("utf-8", errors="replace")
        if kind == 0:
            self._metrics = message
            raise _TtsComplete(message)
        raise RuntimeError(f"Chatterbox resident synthesis failed: {message or 'unknown error'}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    @property
    def metrics(self) -> str:
        return self._metrics


class _TtsComplete(Exception):
    def __init__(self, metrics: str) -> None:
        super().__init__(metrics)
        self.metrics = metrics


class _PieceComplete(Exception):
    pass


class ChatterboxClient:
    def __init__(self, base_url: str, timeout: float = 3600.0, cancel=None) -> None:
        self._conn = _TtsConnection(base_url, timeout)
        self._cancel = cancel
        self._opened = False
        self._piece_index = 0
        self._wav_sent = False

    def open(self, wav_path: Path) -> None:
        if self._opened:
            raise RuntimeError("ChatterboxClient is already open")
        self._conn.connect()
        self._opened = True
        self._piece_index = 0
        self._wav_sent = False
        self._wav_path = wav_path

    def _check_cancel(self) -> None:
        if self._cancel and self._cancel():
            raise InterruptedError

    def send_piece(self, text: str) -> None:
        if not self._opened:
            raise RuntimeError("ChatterboxClient.open() must be called before send_piece()")
        self._check_cancel()
        self._piece_index += 1
        path = self._wav_path if not self._wav_sent else None
        try:
            self._conn.send_piece(self._piece_index, text, path)
        except OSError as exc:
            raise RuntimeError(f"Chatterbox resident request failed: {exc}") from exc
        self._wav_sent = True

    def recv_pcm(self) -> bytes:
        try:
            return self._conn.recv_pcm()
        except _PieceComplete:
            raise StopIteration("piece_complete")
        except _TtsComplete as done:
            raise StopIteration(done.metrics)
        except OSError as exc:
            raise RuntimeError(f"Chatterbox resident request failed: {exc}") from exc

    def cancel_piece(self) -> None:
        if self._opened and self._conn._sock:
            try:
                self._conn.cancel_piece()
            except OSError:
                pass

    def end(self) -> str:
        try:
            return self._conn.end_stream()
        except OSError as exc:
            raise RuntimeError(f"Chatterbox resident request failed: {exc}") from exc
        finally:
            self.close()

    def close(self) -> None:
        self._conn.close()
        self._opened = False

    def __iter__(self):
        if not self._opened:
            raise RuntimeError("ChatterboxClient.open() must be called before iteration")
        while True:
            try:
                yield self._conn.recv_pcm()
            except _PieceComplete:
                return
            except _TtsComplete as done:
                return done.metrics
            except OSError as exc:
                raise RuntimeError(f"Chatterbox resident request failed: {exc}") from exc

    @property
    def metrics(self) -> str:
        return self._conn.metrics


def chatterbox_stream(base_url: str, text: str, output: Path, cancel=None):
    client = ChatterboxClient(base_url, cancel=cancel)
    if not (cancel and cancel()):
        client.open(output)
        client.send_piece(text)
    try:
        yield from client
    finally:
        client.close()
