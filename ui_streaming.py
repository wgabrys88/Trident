from __future__ import annotations


def _cut(text: str, start: int, limit: int) -> int:
    end = min(len(text), start + limit)
    if end < len(text):
        split = max(text.rfind(" ", start + limit // 2, end), text.rfind("\n", start + limit // 2, end))
        if split > start:
            end = split + 1
    return end


def text_batches(text: str, first_chars: int, chunk_chars: int, group_chunks: int) -> list[tuple[str, int]]:
    text = text.strip()
    if not text:
        return []
    first_limit = first_chars + max(0, group_chunks - 1) * chunk_chars
    later_limit = group_chunks * chunk_chars
    out: list[tuple[str, int]] = []
    start = 0
    limit = first_limit
    while start < len(text):
        end = _cut(text, start, limit)
        piece = text[start:end].strip()
        if piece:
            out.append((piece, end))
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
        limit = later_limit
    return out


def highlighted_progress(text: str, sent_end: int, buffered_end: int | None = None):
    text = text.strip()
    sent_end = max(0, min(len(text), sent_end))
    buffered_end = sent_end if buffered_end is None else max(sent_end, min(len(text), buffered_end))
    spans = []
    if sent_end:
        spans.append((text[:sent_end], "sent"))
    if buffered_end > sent_end:
        spans.append((text[sent_end:buffered_end], "buffered"))
    if buffered_end < len(text):
        spans.append((text[buffered_end:], "pending"))
    return spans or [(text, "pending")]


def pcm16_lookahead(chunks, sample_rate: int, min_seconds: float):
    minimum = int(sample_rate * min_seconds) * 2
    bucket = bytearray()
    pending = None
    for raw in chunks:
        if raw:
            bucket.extend(raw)
        if len(bucket) < minimum:
            continue
        ready = bytes(bucket)
        bucket.clear()
        if pending is None:
            pending = ready
        else:
            yield pending
            pending = ready
    if bucket:
        ready = bytes(bucket)
        if pending is None:
            pending = ready
        else:
            yield pending
            pending = ready
    if pending:
        yield pending
