"""Trident client adapters: Parakeet STT, Gemma LLM, Chatterbox TTS.

TTS protocol (chatterbox.cpp pin, one connection per utterance-stream):

  client -> {u32 piece_count, then for each piece: {u32 text_len, text}}
  resident -> one or more frames {u32 kind, u32 bytes, payload}
    kind=2  int16 little-endian PCM at 24 kHz
    kind=0  metrics, payload is the text "request_id=... samples=... pieces=..."
    kind=1  error, payload is utf-8 message
"""
from __future__ import annotations

import http.client
import json
import select
import socket
import struct
import urllib.parse
from pathlib import Path
from typing import Callable, Iterator


def _connection(base_url: str, timeout: float):
    parsed = urllib.parse.urlsplit(base_url)
    return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout), parsed


def parakeet_transcribe(base_url: str, wav: Path, timeout: float = 3600.0) -> dict:
    import secrets
    boundary = "----------trident" + secrets.token_hex(12)
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


class ChatterboxClient:
    """One TCP connection per turn: send N texts, yield N pieces of int16 PCM.

    The wire on the wire:
      open socket
      send: {u32 piece_count} [{u32 text_len, text}]*
      loop: recv {u32 kind, u32 bytes, payload}
        kind=2 -> yield payload (int16 PCM)
        kind=0 -> store metrics, raise _TtsComplete
        kind=1 -> raise RuntimeError
      close socket
    """

    def __init__(self, base_url: str, timeout: float = 3600.0, cancel: Callable[[], bool] | None = None) -> None:
        self._parsed = urllib.parse.urlsplit(base_url)
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._closed = False
        self._cancel = cancel
        self._sent = 0

    def _recv_exact(self, count: int) -> bytes:
        if self._sock is None:
            raise InterruptedError if self._closed else RuntimeError("ChatterboxClient not open")
        data = bytearray()
        while len(data) < count:
            if self._closed:
                raise InterruptedError
            self._check_cancel()
            if self._sock is None:
                raise InterruptedError
            r, _, _ = select.select([self._sock], [], [], 0.05)
            if not r:
                continue
            part = self._sock.recv(count - len(data))
            if not part:
                if self._closed:
                    raise InterruptedError
                raise RuntimeError("resident TTS closed the connection early")
            data.extend(part)
        return bytes(data)

    def _send_all(self, data: bytes) -> None:
        assert self._sock is not None
        self._sock.sendall(data)

    def open(self) -> None:
        if self._sock is not None:
            raise RuntimeError("ChatterboxClient is already open")
        sock = socket.create_connection(
            (self._parsed.hostname, self._parsed.port or 17933),
            timeout=self._timeout,
        )
        sock.settimeout(self._timeout)
        self._sock = sock
        self._sent = 0

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None

    def send_piece(self, text: str) -> None:
        if self._sock is None:
            raise RuntimeError("ChatterboxClient.open() must be called before send_piece()")
        if self._cancel and self._cancel():
            raise InterruptedError
        text_bytes = text.encode("utf-8")
        if self._sent == 0:
            self._send_all(struct.pack("<I", 1))  # piece_count = 1
        self._send_all(struct.pack("<I", len(text_bytes)) + text_bytes)
        self._sent += 1

    def _check_cancel(self) -> None:
        if self._cancel and self._cancel():
            raise InterruptedError

    def recv_pcm(self) -> bytes:
        if self._sock is None:
            raise RuntimeError("ChatterboxClient not open")
        self._check_cancel()
        kind, length = struct.unpack("<II", self._recv_exact(8))
        payload = self._recv_exact(length) if length else b""
        if kind == 2:
            return payload
        if kind == 0:
            raise _TtsComplete
        if kind == 1:
            message = payload.decode("utf-8", errors="replace")
            raise RuntimeError(f"Chatterbox resident synthesis failed: {message or 'unknown error'}")
        raise RuntimeError(f"unknown Chatterbox frame kind: {kind}")

    def __iter__(self) -> Iterator[bytes]:
        while True:
            try:
                yield self.recv_pcm()
            except _TtsComplete:
                return

    def cancel(self) -> None:
        self._closed = True
        self.close()


class _TtsComplete(Exception):
    pass
