from __future__ import annotations

import http.client
import json
import secrets
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


def chatterbox_stream(base_url: str, text: str, output: Path, streaming: bool = True, join: str = "crossfade", timeout: float = 3600.0, cancel=None):
    import select
    import socket
    import struct

    parsed = urllib.parse.urlsplit(base_url)
    text_bytes = text.encode("utf-8")
    output_bytes = str(output.resolve()).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)

    def recv_exact(sock: socket.socket, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            if cancel and cancel():
                raise InterruptedError
            if cancel and not select.select([sock], [], [], 0.05)[0]:
                continue
            part = sock.recv(count - len(data))
            if not part:
                raise RuntimeError("resident TTS closed the connection early")
            data.extend(part)
        return bytes(data)

    try:
        if cancel and cancel():
            return
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(struct.pack("<IIII", len(text_bytes), len(output_bytes), int(streaming), {"chunks": 0, "crossfade": 1}[join]))
            sock.sendall(text_bytes)
            sock.sendall(output_bytes)
            while True:
                kind, length = struct.unpack("<II", recv_exact(sock, 8))
                payload = recv_exact(sock, length) if length else b""
                if kind == 2:
                    yield payload
                    continue
                message = payload.decode("utf-8", errors="replace")
                if kind == 0:
                    return message
                raise RuntimeError(f"Chatterbox resident synthesis failed: {message or 'unknown error'}")
    except InterruptedError:
        return
    except OSError as exc:
        raise RuntimeError(f"Chatterbox resident request failed: {exc}") from exc
