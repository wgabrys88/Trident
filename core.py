import json, tomllib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
STATE, MODELS, BIN = (ROOT / CONFIG["paths"][key] for key in ("state", "models", "bin"))
SYSTEM = (ROOT / "organism.txt").read_bytes().decode("utf-8")

def timestamp():
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S_%f%z")

def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2)

def tool(description, **parameters):
    def declare(method):
        properties = {name: {"type": {str: "string", int: "integer"}[method.__annotations__[name]],
                            "description": details} for name, details in parameters.items()}
        if "how" in properties:
            properties["how"]["enum"] = ["left", "right", "double"]
        method.schema = {"type": "function", "function": {"name": method.__name__, "description": description,
                         "parameters": {"type": "object", "properties": properties,
                                        "required": list(properties), "additionalProperties": False}}}
        return method
    return declare

class Interrupted(Exception):
    pass
