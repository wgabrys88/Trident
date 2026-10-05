import inspect
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
STATE = ROOT / CONFIG["paths"]["state"]
MODELS = ROOT / CONFIG["paths"]["models"]
BIN = ROOT / CONFIG["paths"]["bin"]


def contract(body):
    document = (ROOT / "README.md").read_bytes().decode("utf-8")
    section = document.split("## Organism\n", 1)[1]
    system = section.split("```text\n", 1)[1].split("```", 1)[0]
    tools = []
    for name, signature, description in re.findall(r"^- (\w+)\(([^\n]*?)\): (.+)$", system, re.M):
        method = getattr(body, name)
        properties = {key: {"type": {str: "string", int: "integer"}[param.annotation]}
                      for key, param in inspect.signature(method).parameters.items()}
        for parameter in signature.split(","):
            if "|" in parameter:
                key, choices = parameter.strip().split(" ", 1)
                properties[key]["enum"] = choices.split("|")
        tools.append({"type": "function", "function": {"name": name, "description": description,
                      "parameters": {"type": "object", "properties": properties, "required": list(properties)}}})
    return system, tools
