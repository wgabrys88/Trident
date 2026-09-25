# Gemma 4 E2B-it turn strings, tool loop, speech text. Matches Google native tokens.

import re

Q = '<|"|>'

SYSTEM = (
    "<|turn>system\n<|think|>You are Trident, a concise voice assistant."
    f"<|tool>declaration:add{{a:{Q}number{Q},b:{Q}number{Q}}}<tool|><turn|>\n"
)


def append_history(history: list, user: str, model_raw: str) -> list:
    body = strip_thought(normalize_model(model_raw)).rstrip()
    if not body.endswith("<turn|>"):
        body += "<turn|>"
    history.append(user_turn(user) + model_open() + body + "\n")
    return history


def prompt_with_history(history: list, user: str) -> str:
    return SYSTEM + "".join(history) + user_turn(user) + model_open()


def normalize_model(text: str) -> str:
    t = text.replace("<|tool_call|>", "<tool_call|>")
    t = t.replace("<tool_call|>", "<tool_call|>")
    t = t.replace("<|tool_response|>", "<tool_response|>")
    return t


def user_turn(text: str) -> str:
    return f"<|turn>user\n{text.strip()}<turn|>\n"


def model_open() -> str:
    return "<|turn>model\n"


def prompt_continue(user: str, model_so_far: str, tool_responses: str, history=None) -> str:
    head = SYSTEM + ("".join(history) if history else "") + user_turn(user) + model_open()
    return head + normalize_model(model_so_far) + tool_responses


def prompt_for_user(user: str, history=None) -> str:
    if history:
        return prompt_with_history(history, user)
    return SYSTEM + user_turn(user) + model_open()


def format_tool_response(name: str, fields: dict) -> str:
    body = ",".join(f"{k}:{v}" for k, v in fields.items())
    return f"<|tool_response>response:{name}{{{body}}}<tool_response|>"


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
    return {"error": "unknown_tool"}


def converse(gemma_proc, user_text: str) -> str:
    from trident_runtime import gemma_ask

    user_text = user_text.strip()
    model = gemma_ask(gemma_proc, prompt_for_user(user_text))
    for _ in range(4):
        calls = parse_calls(model)
        if not calls:
            break
        chunks = [format_tool_response(n, run_tool(n, a)) for n, a in calls]
        model = gemma_ask(gemma_proc, prompt_continue(user_text, model, "".join(chunks)))
    line = speakable(model)
    if not line:
        raise SystemExit("gemma produced no speakable text")
    return line
