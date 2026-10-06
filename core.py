import inspect, json, tomllib
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args, get_origin

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
        properties = {}
        for name, details in parameters.items():
            annotation = method.__annotations__[name]
            choices = get_args(annotation) if get_origin(annotation) is Literal else ()
            properties[name] = {"type": {str: "string", int: "integer"}[type(choices[0]) if choices else annotation],
                                "description": details}
            if choices:
                properties[name]["enum"] = list(choices)
        method.schema = {"type": "function", "function": {"name": method.__name__, "description": description,
                         "parameters": {"type": "object", "properties": properties,
                             "required": [name for name, p in inspect.signature(method).parameters.items() if name in properties and p.default is p.empty],
                             "additionalProperties": False}}}
        return method
    return declare

class Interrupted(Exception):
    pass
