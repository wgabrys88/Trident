import json

from organs import state_dir


class Memory:
    def __init__(self):
        self.path = state_dir() / "memory.json"
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        self.facts: list[str] = list(data.get("facts", []))
        self.task: str = data.get("task", "")

    def save(self):
        self.path.write_text(json.dumps({"facts": self.facts, "task": self.task}, ensure_ascii=False, indent=1), encoding="utf-8")

    def remember(self, fact: str):
        fact = fact.strip()
        if fact and fact not in self.facts:
            self.facts.append(fact)
            self.save()

    def set_task(self, text: str):
        text = text.strip()
        if text and text != self.task:
            self.task = text
            self.save()

    def block(self) -> str:
        lines = [f"Remembered: {fact}" for fact in self.facts]
        if self.task:
            lines.insert(0, "Task: " + self.task)
        return "\n".join(lines)
