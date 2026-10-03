"""state/memory.json, the only memory that lasts across turns.

facts are the lines she asked to keep.
turns are what he said and she said, thoughts already removed.
quiet is her promise not to ring until he speaks.
"""

import json

from organs import state_dir


class Memory:
    def __init__(self):
        self.path = state_dir() / "memory.json"
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        self.facts: list[str] = data.get("facts", [])
        self.turns: list[list[str]] = data.get("turns", [])
        self.quiet: bool = data.get("quiet", False)

    def save(self):
        self.path.write_text(json.dumps({"facts": self.facts, "turns": self.turns, "quiet": self.quiet}, ensure_ascii=False, indent=1), encoding="utf-8")

    def remember(self, fact: str):
        fact = fact.strip()
        if fact and fact not in self.facts:
            self.facts.append(fact)
            self.save()

    def add_turn(self, user: str, model: str):
        self.turns.append([user, model])
        self.save()

    def set_quiet(self, value: bool):
        if self.quiet != value:
            self.quiet = value
            self.save()

    def facts_block(self) -> str:
        return ("\nRemembered:\n" + "\n".join(f"- {f}" for f in self.facts)) if self.facts else ""

    def history(self) -> list[tuple[str, str]]:
        return [(u, m) for u, m in self.turns]
