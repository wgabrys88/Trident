import json, re, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "workspace"
DONE = WORK / "done"
MODELS = ROOT / "models"
MODEL = "brain-gemma-4-e2b-it-q4_k_m.gguf"
CURSOR = WORK / "brain.cursor"
MARK = '<|"|>'
OPEN, CLOSE = "<|tool_call>", "<tool_call|>"
BOS = "<bos>"
TEMPLATE = None
TOOLS = [
    {"type": "function", "function": {"name": "speak", "description": "Say something aloud. Use this for all speech, including proactive speech and short progress updates.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "language": {"type": "string", "description": "Two-letter language code such as en, fr, pl."}}, "required": ["text", "language"]}}},
    {"type": "function", "function": {"name": "python", "description": "Run one complete Python script on the local computer. The exact exit code, stdout and stderr return to you as data.", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "remember", "description": "Append durable memory that should survive process restarts and loss of recent event context.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "wake", "description": "Schedule a future wake when you want to reconsider unfinished work without waiting for a person to speak.", "parameters": {"type": "object", "properties": {"seconds": {"type": "string"}, "reason": {"type": "string"}}, "required": ["seconds"]}}},
    {"type": "function", "function": {"name": "stop", "description": "Stop Trident intentionally.", "parameters": {"type": "object", "properties": {}, "required": []}}},
]
SYSTEM = """You are Jarvis, the brain of a local computer assistant.
You receive an ordered event journal. Every heard event is context, not automatically a command. Decide meaning yourself. Being addressed as Jarvis is strong evidence that speech is intended for you, but there is no wake-word rule. Background conversation can be useful context; usually remain silent unless speaking or acting is genuinely useful. Recent speech events tell you what you yourself asked the mouth to say, so use context to recognize likely speaker echo rather than treating every heard sentence as a fresh human instruction.
You own all semantic decisions. Python code outside you does not decide whether something is a request, whether a number needs a tool, or whether you may speak.
Use speak for every audible response. You may initiate speech on clock and wake events when there is a useful reason. Before meaningful computer work, normally speak one short present-tense progress sentence, then use python. Use python freely for computer work and exact calculation. Never invent a tool result. When an exact short Python stdout is the answer, speak those exact characters rather than paraphrasing them. Use remember for durable facts, preferences, commitments, or unfinished work that should survive a restart. Use wake when future attention is useful. Use stop only when ending Trident is actually intended.
After a tool result, continue reasoning until the work is complete. Do not repeat a tool call that the current-cycle transcript already says completed. If nothing useful should happen, make no tool call. Do not emit ordinary assistant prose for the user; audible communication must go through speak."""


def venv_python() -> Path:
    return ROOT / ".venv" / "Scripts" / "python.exe"


def reexec() -> None:
    py = venv_python()
    if not py.is_file():
        raise RuntimeError("missing " + str(py))
    if Path(sys.executable).resolve() != py.resolve():
        raise SystemExit(subprocess.call([str(py), *sys.argv]))
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def http(server: str, method: str, path: str, payload=None, timeout=70):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(server + path, data=raw, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])
    return data


def unique(kind: str, suffix: str) -> Path:
    folder = DONE / kind
    folder.mkdir(parents=True, exist_ok=True)
    n = 1
    while (folder / f"{kind}-{n}{suffix}").exists():
        n += 1
    return folder / f"{kind}-{n}{suffix}"


def record(kind: str, suffix: str, text: str) -> Path:
    path = unique(kind, suffix)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def load_model(server: str):
    global BOS, TEMPLATE
    path = MODELS / MODEL
    if not path.is_file():
        raise RuntimeError("missing " + str(path))
    config = http(server, "GET", "/config")
    from llama_cpp import Llama
    llm = Llama(model_path=str(path), n_ctx=8192, n_batch=1024, n_ubatch=1024, n_threads=4, n_gpu_layers=-1, main_gpu=int(config["vulkan_device"]), swa_full=True, verbose=True, logits_all=False)
    BOS = llm.detokenize([llm.token_bos()], special=True).decode("utf-8") or "<bos>"
    from gguf import GGUFReader
    field = GGUFReader(str(path)).fields["tokenizer.chat_template"]
    TEMPLATE = bytes(field.parts[-1].tolist()).decode("utf-8")
    return llm


def parse_one(call: str) -> dict:
    if not call.startswith("call:") or not call.endswith("}"):
        return {"error": "tool call"}
    name, _, body = call[5:].partition("{")
    body = body[:-1]
    names = {tool["function"]["name"] for tool in TOOLS}
    if name not in names:
        return {"error": "unknown tool " + name}
    args = {key: value for key, value in re.findall(rf"(\w+):{re.escape(MARK)}(.*?){re.escape(MARK)}", body, flags=re.DOTALL)}
    for key, value in re.findall(r"(\w+):([0-9.]+)", body):
        args.setdefault(key, value)
    return {"name": name, "arguments": args}


def parse_calls(text: str) -> list[dict]:
    out, rest = [], text
    while True:
        at = rest.find(OPEN)
        if at < 0:
            return out
        rest = rest[at + len(OPEN):]
        end = rest.find(CLOSE)
        if end < 0:
            out.append({"error": "open tool call"})
            return out
        out.append(parse_one(rest[:end].strip()))
        rest = rest[end + len(CLOSE):]


def format_event(row: dict) -> str:
    data = row.get("data") or {}
    kind = row.get("type", "event")
    at = row.get("at", "")
    if kind == "heard":
        language = data.get("language") or "unknown"
        return f"[{row['id']}] {at} HEARD language={language}: {data.get('text', '')}"
    if kind == "clock":
        return f"[{row['id']}] {at} CLOCK: {data.get('seconds_since_heard', 0)} seconds since heard speech"
    if kind == "wake":
        return f"[{row['id']}] {at} WAKE: {data.get('reason', '')}"
    if kind == "speech_queued":
        return f"[{row['id']}] {at} YOU_ASKED_MOUTH_TO_SAY language={data.get('language', '')}: {data.get('text', '')}"
    if kind == "speech_done":
        return f"[{row['id']}] {at} MOUTH_FINISHED: {data.get('speech', '')} wav={data.get('wav', '')}"
    if kind == "tool_result":
        return f"[{row['id']}] {at} TOOL {data.get('name', '')}: {data.get('result', '')}"
    if kind == "memory":
        return f"[{row['id']}] {at} MEMORY_APPENDED: {data.get('text', '')}"
    if kind == "start":
        return f"[{row['id']}] {at} START variant={data.get('variant', '')}"
    return f"[{row['id']}] {at} {kind.upper()}: {json.dumps(data, ensure_ascii=False)}"


def render(llm, memory: str, events: list[dict], current: dict, cycle: list[str]) -> list[int]:
    import jinja2
    event_text = "\n".join(format_event(row) for row in events)
    cycle_text = "\n".join(cycle) if cycle else "(none yet)"
    content = f"Durable memory:\n{memory.strip() or '(empty)'}\n\nRecent event journal:\n{event_text or '(empty)'}\n\nCurrent trigger:\n{format_event(current)}\n\nCurrent-cycle completed actions and results:\n{cycle_text}"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
    text = jinja2.Environment().from_string(TEMPLATE).render(messages=messages, tools=TOOLS, add_generation_prompt=True, bos_token=BOS, eos_token="")
    if not text.startswith(BOS):
        text = BOS + text
    tokens = llm.tokenize(text.encode("utf-8"), add_bos=False, special=True)
    print("prompt tokens", len(tokens), flush=True)
    return tokens


def run_python(server: str, code: str) -> str:
    if not isinstance(code, str) or not code.strip():
        raise RuntimeError("empty python")
    script = unique("job", ".py")
    script.write_text(code, encoding="utf-8", newline="\n")
    proc = subprocess.run([str(venv_python()), str(script)], cwd=str(WORK), capture_output=True, text=True)
    result = f"exit {proc.returncode}\n{proc.stdout or ''}{proc.stderr or ''}"
    result_path = script.with_name(script.stem + ".result.txt")
    result_path.write_text(result, encoding="utf-8", newline="\n")
    http(server, "POST", "/event", {"type": "tool_result", "source": "brain", "data": {"name": "python", "result": result, "script": str(script.relative_to(ROOT)), "artifact": str(result_path.relative_to(ROOT))}})
    return result


def apply(server: str, calls: list[dict], cycle: list[str]) -> bool:
    acted = False
    for call in calls:
        acted = True
        if "error" in call:
            result = "error: " + call["error"]
            http(server, "POST", "/event", {"type": "tool_result", "source": "brain", "data": {"name": "parser", "result": result}})
            cycle.append("parser -> " + result)
            continue
        name, args = call["name"], call.get("arguments") or {}
        try:
            if name == "speak":
                text = str(args.get("text", "")).strip()
                language = str(args.get("language", "")).strip().lower()
                if not text or not language:
                    raise RuntimeError("speak requires text and language")
                reply = http(server, "POST", "/speech", {"text": text, "language": language})
                result = "queued " + reply["name"]
            elif name == "python":
                result = run_python(server, args.get("code", ""))
            elif name == "remember":
                text = str(args.get("text", "")).strip()
                if not text:
                    raise RuntimeError("empty memory")
                http(server, "POST", "/memory", {"text": text})
                result = "remembered: " + text
            elif name == "wake":
                seconds = float(args.get("seconds", "0"))
                reason = str(args.get("reason", ""))
                http(server, "POST", "/wake", {"seconds": seconds, "reason": reason})
                result = f"wake scheduled in {seconds:g} seconds" + (": " + reason if reason else "")
            elif name == "stop":
                http(server, "POST", "/stop", {"source": "brain"})
                raise SystemExit(0)
            else:
                result = "error: unknown tool " + name
        except SystemExit:
            raise
        except Exception as err:
            result = "error: " + str(err)
            http(server, "POST", "/event", {"type": "tool_result", "source": "brain", "data": {"name": name, "result": result}})
        cycle.append(name + " -> " + result)
    return acted


def think(server: str, llm, current: dict) -> None:
    cycle = []
    while True:
        memory = http(server, "GET", "/memory")["text"]
        events = http(server, "GET", "/events/recent?limit=80")["events"]
        prompt = render(llm, memory, events, current, cycle)
        completion = llm.create_completion(prompt=prompt, stop=["<turn|>"], temperature=1.0, top_p=0.95, top_k=64, min_p=0.0, max_tokens=1024)["choices"][0]["text"]
        print(completion, flush=True)
        decision = record("decision", ".txt", completion if completion.endswith("\n") else completion + "\n")
        http(server, "POST", "/event", {"type": "decision", "source": "brain", "data": {"artifact": str(decision.relative_to(ROOT)), "text": completion}})
        calls = parse_calls(completion)
        if not calls:
            return
        if not apply(server, calls, cycle):
            return


def cursor() -> int:
    try:
        return int(CURSOR.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return 0


def set_cursor(value: int) -> None:
    CURSOR.parent.mkdir(parents=True, exist_ok=True)
    tmp = CURSOR.with_name(CURSOR.name + ".tmp")
    tmp.write_text(str(value) + "\n", encoding="utf-8")
    tmp.replace(CURSOR)


def serve(server: str) -> None:
    llm = load_model(server)
    http(server, "POST", "/ready", {"name": "brain"})
    position = cursor()
    while True:
        rows = http(server, "GET", "/events?after=" + str(position) + "&timeout=30", timeout=40)["events"]
        if not rows:
            continue
        for row in rows:
            position = int(row["id"])
            set_cursor(position)
            if row.get("trigger"):
                think(server, llm, row)


def main():
    reexec()
    if len(sys.argv) != 2:
        raise SystemExit("usage: python brain.py <server>")
    serve(sys.argv[1].rstrip("/"))


if __name__ == "__main__":
    main()
