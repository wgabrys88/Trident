# Gemma's tools and turns. The tool list is trident.txt. Sound is the speak tool.

import re

Q = '<|"|>'


def read_tools(spec: str) -> list:
    tools = []
    name = ""
    description = ""
    params = []
    def flush():
        if name:
            tools.append((name, description, params.copy()))
    for line in spec.splitlines():
        line = line.strip()
        if not line:
            flush()
            name = ""
            description = ""
            params = []
            continue
        if not name:
            name = line
            continue
        if ":" not in line:
            description = line
            continue
        key, note = line.split(":", 1)
        params.append((key.strip(), note.strip()))
    flush()
    return tools


def tool_decls(spec: str) -> str:
    blocks = []
    for name, description, params in read_tools(spec):
        fields = [f"{key}:{Q}string{Q}" for key, _ in params]
        body = "{" + ",".join(fields) + "}" if fields else "{}"
        note = description
        if params:
            note += " " + " ".join(key + " is " + text for key, text in params)
        blocks.append(f"<|tool>declaration:{name}{body}<tool|>{note}")
    return "".join(blocks)


def system_turn(spec: str) -> str:
    return (
        "<|turn>system\n<|think|>You are Gemma, the assistant in this room. "
        "A line reaches you only when the small model answered yes, and the line is the original speech. "
        "Keep that memory. From its meaning, do the thing. "
        "Before you act, call speak and say what you are doing and what you plan. "
        "When you finish, call speak with the result. "
        "You are heard only through your tools."
        + tool_decls(spec)
        + "<turn|>\n"
    )


def user_turn(text: str) -> str:
    return f"<|turn>user\n{text.strip()}<turn|>\n"


def model_open() -> str:
    return "<|turn>model\n"


def prompt_body(spec: str, history, user: str, model_body: str) -> str:
    return system_turn(spec) + "".join(history or []) + user_turn(user) + model_open() + model_body


def format_tool_response(name: str, fields: dict) -> str:
    parts = [f"{key}:{Q}{value}{Q}" for key, value in fields.items()]
    return f"<|tool_response>response:{name}{{{','.join(parts)}}}<tool_response|>"


def parse_calls(text: str):
    out = []
    for match in re.finditer(r"<\|tool_call>call:(\w+)\{(.*?)\}<tool_call\|>", text, flags=re.DOTALL):
        args = {}
        for part in re.finditer(r"(\w+):(?:<\|\"\|>(.*?)<\|\"\|>|([^,}]+))", match.group(2)):
            value = part.group(2) if part.group(2) is not None else part.group(3)
            args[part.group(1)] = value.strip()
        out.append((match.group(1), args))
    return out


def compact_history(history: list, limit: int) -> list:
    while len(history) > 1 and sum(len(part) for part in history) > limit:
        history.pop(0)
    return history


def write_memory(history: list, limit: int, path) -> None:
    compact_history(history, limit)
    path.write_text("".join(history), encoding="utf-8")


def converse(gemma_proc, user_text: str, spec: str, history: list, limit: int, path, act) -> None:
    from trident_runtime import gemma_ask

    write_memory(history, limit, path)
    user_text = user_text.strip()
    latest = gemma_ask(gemma_proc, prompt_body(spec, history, user_text, ""))
    body = latest
    seen = set()
    while True:
        calls = parse_calls(latest)
        fresh = []
        for name, args in calls:
            mark = (name, tuple(sorted(args.items())))
            if mark in seen:
                continue
            seen.add(mark)
            fresh.append((name, args))
        if not fresh:
            break
        body += "".join(format_tool_response(name, act(name, args)) for name, args in fresh)
        write_memory(history, limit, path)
        latest = gemma_ask(gemma_proc, prompt_body(spec, history, user_text, body))
        body += latest
    stored = body.rstrip()
    if not stored.endswith("<turn|>"):
        stored += "<turn|>"
    history.append(user_turn(user_text) + model_open() + stored + "\n")
    write_memory(history, limit, path)
