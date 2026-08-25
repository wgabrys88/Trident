from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechUnit:
    text: str
    end: int


class SpeechSegmenter:
    def __init__(self, minimum: int, hard_limit: int) -> None:
        if minimum < 1 or hard_limit < 1 or minimum > hard_limit:
            raise ValueError("speech segmentation limits are invalid")
        self.minimum = minimum
        self.hard_limit = hard_limit
        self.sent = 0

    def update(self, text: str, flush: bool = False) -> list[SpeechUnit]:
        units: list[SpeechUnit] = []
        while self.sent < len(text):
            pending = text[self.sent:]
            stop = min(len(pending), self.hard_limit)
            cut = 0
            for i in range(self.minimum - 1, stop):
                if pending[i] in ".?!" and (i + 1 == len(pending) or pending[i + 1].isspace()):
                    cut = i + 1
                    break
            if not cut and len(pending) >= self.hard_limit:
                split = max(
                    pending.rfind(" ", self.minimum, self.hard_limit),
                    pending.rfind("\n", self.minimum, self.hard_limit),
                    pending.rfind("\t", self.minimum, self.hard_limit),
                )
                cut = split + 1 if split >= self.minimum else self.hard_limit
            if not cut and flush:
                cut = len(pending)
            if not cut:
                break
            unit = pending[:cut].strip()
            self.sent += cut
            while self.sent < len(text) and text[self.sent].isspace():
                self.sent += 1
            if unit:
                units.append(SpeechUnit(unit, self.sent))
        return units


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
