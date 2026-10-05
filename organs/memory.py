import json
from organs import state_dir
class Memory:
    def __init__(self):
        self.path = state_dir() / "memory.json"
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        self.note = data.get("note", "") if isinstance(data, dict) else ""
    def save(self):
        self.path.write_text(json.dumps({"note": self.note}, ensure_ascii=False, indent=1), encoding="utf-8")
    def replace(self, note: str):
        self.note = str(note)
        self.save()
