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
