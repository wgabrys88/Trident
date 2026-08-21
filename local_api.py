from __future__ import annotations

import http.client
import json
import secrets
import urllib.parse
import urllib.request
from pathlib import Path


def _connection(base_url: str, timeout: float):
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise RuntimeError(f"resident endpoint must be local http: {base_url}")
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    return conn, parsed


def parakeet_transcribe(base_url: str, wav: Path, timeout: float = 3600.0) -> dict:
    """POST a WAV without copying the whole file into a Python bytes object."""
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
        "json\r\n"
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
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("Parakeet server returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Parakeet server returned non-object JSON")
        return payload
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
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except Exception as exc:
        raise RuntimeError(f"Gemma resident request failed: {exc}") from exc
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("Gemma server returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Gemma server returned non-object JSON")
    return value


def chatterbox_synthesize(base_url: str, text: str, output: Path, timeout: float = 3600.0) -> str:
    """Ask the resident native TTS process to synthesize directly to *output*.

    The process-local Engine owns all model/backend/reference-conditioning state;
    only UTF-8 text and the destination path cross the localhost socket.
    """
    import socket
    import struct

    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "tcp" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(f"resident TTS endpoint must be local tcp: {base_url}")
    port = parsed.port
    if port is None:
        raise RuntimeError(f"resident TTS endpoint has no port: {base_url}")
    text_bytes = text.encode("utf-8")
    output_bytes = str(output.resolve()).encode("utf-8")
    if not text_bytes:
        raise RuntimeError("resident TTS text is empty")
    if len(text_bytes) > 4 * 1024 * 1024 or len(output_bytes) > 32768:
        raise RuntimeError("resident TTS request is too large")
    output.parent.mkdir(parents=True, exist_ok=True)

    def recv_exact(sock: socket.socket, count: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < count:
            part = sock.recv(count - len(chunks))
            if not part:
                raise RuntimeError("resident TTS closed the connection early")
            chunks.extend(part)
        return bytes(chunks)

    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(struct.pack("<II", len(text_bytes), len(output_bytes)))
            sock.sendall(text_bytes)
            sock.sendall(output_bytes)
            status, length = struct.unpack("<II", recv_exact(sock, 8))
            if length > 1024 * 1024:
                raise RuntimeError("resident TTS returned an oversized status message")
            message = recv_exact(sock, length).decode("utf-8", errors="replace") if length else ""
    except OSError as exc:
        raise RuntimeError(f"Chatterbox resident request failed: {exc}") from exc
    if status != 0:
        raise RuntimeError(f"Chatterbox resident synthesis failed: {message or 'unknown error'}")
    return message
