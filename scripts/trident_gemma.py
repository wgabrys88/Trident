# Gemma 4 E2B-it turn strings, tool loop, speech text. Matches Google native tokens.

import re

Q = '<|"|>'


def tool_decls(spec: str) -> str:
    blocks = []
    for line in spec.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, rest = line.partition(" ")
        fields = []
        for part in rest.split():
            key, typ = part.split(":")
            fields.append(f"{key}:{Q}{typ}{Q}")
        body = "{" + ",".join(fields) + "}" if fields else "{}"
        blocks.append(f"<|tool>declaration:{name}{body}<tool|>")
    return "".join(blocks)


def system_turn(spec: str) -> str:
    return (
        "<|turn>system\n<|think|>You are Trident, a concise voice assistant."
        + tool_decls(spec)
        + "<turn|>\n"
    )


def append_history(history: list, user: str, model_raw: str) -> list:
    body = strip_thought(normalize_model(model_raw)).rstrip()
    if not body.endswith("<turn|>"):
        body += "<turn|>"
    history.append(user_turn(user) + model_open() + body + "\n")
    return history


def normalize_model(text: str) -> str:
    t = text.replace("<|tool_call|>", "<tool_call|>")
    t = t.replace("<tool_call|>", "<tool_call|>")
    t = t.replace("<|tool_response|>", "<tool_response|>")
    return t


def user_turn(text: str) -> str:
    return f"<|turn>user\n{text.strip()}<turn|>\n"


def model_open() -> str:
    return "<|turn>model\n"


def prompt_body(spec: str, history, user: str, model_body: str) -> str:
    return system_turn(spec) + "".join(history or []) + user_turn(user) + model_open() + model_body


def format_tool_response(name: str, fields: dict) -> str:
    parts = []
    for key, value in fields.items():
        if isinstance(value, str):
            parts.append(f"{key}:{Q}{value}{Q}")
        else:
            parts.append(f"{key}:{value}")
    return f"<|tool_response>response:{name}{{{','.join(parts)}}}<tool_response|>"


def strip_thought(text: str) -> str:
    return re.sub(r"<\|channel>thought\n.*?<channel\|>", "", text, flags=re.DOTALL)


def speakable(text: str) -> str:
    t = strip_thought(normalize_model(text))
    t = re.sub(r"<\|tool_call>.*?<tool_call\|>", "", t, flags=re.DOTALL)
    t = re.sub(r"<\|tool_response>.*?<tool_response\|>", "", t, flags=re.DOTALL)
    t = re.sub(r"<\|tool_response>.*?$", "", t, flags=re.DOTALL)
    return t.strip()


def parse_calls(text: str):
    norm = normalize_model(text)
    out = []
    for m in re.finditer(r"<\|tool_call>call:(\w+)\{(.*?)\}<tool_call\|>", norm, flags=re.DOTALL):
        name = m.group(1)
        raw = m.group(2)
        args = {}
        for part in re.finditer(r"(\w+):(?:<\|\"\|>(.*?)<\|\"\|>|([^,}]+))", raw):
            key = part.group(1)
            val = part.group(2) if part.group(2) is not None else part.group(3)
            val = val.strip()
            try:
                args[key] = int(val)
            except ValueError:
                try:
                    args[key] = float(val)
                except ValueError:
                    args[key] = val
        out.append((name, args))
    return out


def run_tool(name: str, args: dict):
    if name == "add":
        return {"sum": int(args["a"]) + int(args["b"])}
    if name == "sub":
        return {"difference": int(args["a"]) - int(args["b"])}
    if name == "now":
        from datetime import datetime
        return {"time": datetime.now().strftime("%H:%M")}
    return {"error": "unknown_tool"}


def converse(gemma_proc, user_text: str, spec: str, history: list) -> str:
    from trident_runtime import gemma_ask

    user_text = user_text.strip()
    latest = normalize_model(gemma_ask(gemma_proc, prompt_body(spec, history, user_text, "")))
    body = strip_thought(latest)
    for _ in range(4):
        calls = parse_calls(latest)
        if not calls:
            break
        body += "".join(format_tool_response(n, run_tool(n, a)) for n, a in calls)
        latest = normalize_model(gemma_ask(gemma_proc, prompt_body(spec, history, user_text, body)))
        body += strip_thought(latest)
    append_history(history, user_text, body)
    line = speakable(body)
    if not line:
        raise SystemExit("gemma produced no speakable text")
    return line
