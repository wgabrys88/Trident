import re, subprocess, sys, time
from pathlib import Path
from runtime import MODELS, reexec, venv_python, vulkan
from settings import (
    BRAIN, MEMORY, SAID, SPEAK, TOOLS, VARIANTS, append_live, bus, clear_live,
    next_path, parts, put, read_live, read_memory, ready, retire, slot, take, waiting,
)

MARK = '<|"|>'
RULES = r"""
call ::= speak | wait | remember | py | stop
speak ::= "<|tool_call>call:speak{" speakbody "}" "<tool_call|>"
wait ::= "<|tool_call>call:wait{seconds:" piece "}" "<tool_call|>"
remember ::= "<|tool_call>call:remember{text:" piece "}" "<tool_call|>"
py ::= "<|tool_call>call:python{code:" piece "}" "<tool_call|>"
stop ::= "<|tool_call>call:stop{}" "<tool_call|>"
speakbody ::= "text:" piece ",language:" lang | "language:" lang ",text:" piece
piece ::= mark chars mark
lang ::= mark ("en" | "pl") mark
mark ::= "<|\"|>"
chars ::= ([^<] | "<" [^|])*
"""
DEADLINE = 0.0
LAST_HUMAN = None
LAST_CODE = ""

def load():
    path = MODELS / BRAIN["file"]
    if not path.is_file():
        raise RuntimeError("missing " + str(path))
    vulkan()
    from llama_cpp import Llama, LlamaGrammar
    llm = Llama(model_path=str(path), n_ctx=BRAIN["n_ctx"], n_batch=BRAIN["n_batch"],
                n_threads=BRAIN["n_threads"], n_gpu_layers=BRAIN["n_gpu_layers"],
                chat_format="chat_template.default", verbose=False, logits_all=False)
    grammar = LlamaGrammar.from_string("root ::= call+\n" + RULES)
    return llm, grammar

def ask(llm, grammar, text: str) -> str:
    decode = dict(BRAIN["decode"])
    content = llm.create_chat_completion(
        messages=[{"role": "system", "content": SPEAK}, {"role": "user", "content": text}],
        tools=TOOLS, stop=["<turn|>"], grammar=grammar, **decode
    )["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise RuntimeError("brain completion")
    return content

def tools(text: str):
    found = []
    while True:
        at = text.find("<|tool_call>")
        if at < 0:
            if text.strip() or not found:
                raise RuntimeError("tool call")
            return found
        if text[:at].strip():
            raise RuntimeError("tool call")
        rest = text[at + len("<|tool_call>"):]
        end = rest.find("<tool_call|>")
        if end < 0:
            raise RuntimeError("open tool call")
        call = rest[:end].strip()
        text = rest[end + len("<tool_call|>"):]
        if not call.startswith("call:") or not call.endswith("}"):
            raise RuntimeError("tool call")
        name, body = call[5:].split("{", 1)
        body = body[:-1]
        args = {key: value for key, value in re.findall(
            rf"(\w+):{re.escape(MARK)}(.*?){re.escape(MARK)}", body, flags=re.DOTALL)}
        found.append({"name": name, **args})

def own(heard: str, said: str) -> bool:
    def words(value):
        return " ".join("".join(ch for ch in value.casefold() if ch.isalpha() or ch.isspace()).split())
    a, b = words(heard), words(said)
    return bool(a) and bool(b) and a in b

def journal(kind: str, text: str) -> None:
    append_live(time.strftime("%Y-%m-%d %H:%M:%S") + " " + kind + ": " + text.strip())

def scene() -> str:
    memory = read_memory().strip()
    live = read_live().strip()
    silent = "none" if LAST_HUMAN is None else str(int(time.monotonic() - LAST_HUMAN))
    clock = "now: " + time.strftime("%Y-%m-%d %H:%M:%S") + "\nsilent: " + silent
    return "\n\n".join(part for part in (memory, clock, live) if part)

def arm(seconds: str) -> None:
    global DEADLINE
    digits = "".join(ch for ch in seconds if ch.isdigit())
    n = int(digits) if digits else 60
    if n < 1:
        n = 60
    DEADLINE = time.monotonic() + min(3600, n)

def speak(text: str, language: str, record: bool = True):
    if language not in ("en", "pl") or not text.strip():
        raise RuntimeError("speak")
    slot(SAID, text)
    if record:
        journal("said", text)
    for piece in parts(text):
        put(next_path("speech"), language + "\n" + piece)

def remember(text: str) -> None:
    if not text.strip():
        raise RuntimeError("remember")
    bus()
    prev = MEMORY.read_text(encoding="utf-8")
    MEMORY.write_text((prev.rstrip() + "\n" if prev.strip() else "") + text.strip() + "\n", encoding="utf-8")

def run_python(code: str) -> bool:
    global LAST_CODE
    if not isinstance(code, str) or not code.strip():
        raise RuntimeError("python")
    body = code.strip()
    if body == LAST_CODE:
        journal("python", "already ran")
        return False
    LAST_CODE = body
    script = next_path("job", ".py")
    script.write_text(code, encoding="utf-8")
    try:
        proc = subprocess.run([str(venv_python()), str(script)], cwd=str(bus()), capture_output=True, text=True, timeout=60)
        result = f"exit {proc.returncode}\n{proc.stdout or ''}{proc.stderr or ''}"
    except subprocess.TimeoutExpired as err:
        result = f"exit timeout\n{err.stdout or ''}{err.stderr or ''}"
    record = script.with_name(script.stem + ".result.txt")
    record.write_text(result, encoding="utf-8")
    journal("python", result)
    retire(script, "job")
    retire(record, "job")
    return True

def apply(found):
    again = False
    waited = False
    for tool in found:
        name = tool["name"]
        if name == "wait":
            arm(tool.get("seconds", ""))
            waited = True
        elif name == "speak":
            speak(tool.get("text", ""), tool.get("language") or "en")
        elif name == "remember":
            remember(tool.get("text", ""))
        elif name == "python":
            if run_python(tool.get("code", "")):
                again = True
        elif name == "stop":
            raise SystemExit
        else:
            raise RuntimeError("unknown tool " + name)
    if again:
        return True
    if not waited:
        arm("60")
    return False

def decide(content: str):
    path = put(next_path("decision"), content if content.endswith("\n") else content + "\n")
    try:
        again = apply(tools(content))
    except SystemExit:
        retire(path, "decision")
        raise
    retire(path, "decision")
    return again

def think(llm, grammar):
    while True:
        blob = scene()
        print(blob, flush=True)
        content = ask(llm, grammar, blob)
        print(content, flush=True)
        if not decide(content):
            return

def read_payload(value: str) -> str:
    path = Path(value)
    if path.is_file():
        return path.read_text(encoding="utf-8-sig")
    return value

def serve():
    global DEADLINE, LAST_HUMAN
    bus()
    clear_live()
    llm, grammar = load()
    ready("brain")
    DEADLINE = time.monotonic()
    while True:
        files = waiting("transcription")
        if files:
            name = files[0].name
            heard = take(files[0], "transcription").strip()
            print("read", name, flush=True)
            if not heard:
                continue
            said = SAID.read_text(encoding="utf-8") if SAID.exists() else ""
            if own(heard, said):
                journal("echo", heard)
            else:
                journal("heard", heard)
                LAST_HUMAN = time.monotonic()
            think(llm, grammar)
            continue
        if time.monotonic() >= DEADLINE:
            think(llm, grammar)
            continue
        time.sleep(min(0.05, DEADLINE - time.monotonic()))

if __name__ == "__main__":
    reexec()
    argv = sys.argv
    if len(argv) == 1:
        serve()
    elif len(argv) == 2 and argv[1] not in VARIANTS:
        bus()
        journal("heard", read_payload(argv[1]))
        LAST_HUMAN = time.monotonic()
        llm, grammar = load()
        think(llm, grammar)
    elif len(argv) == 4 and argv[1] in VARIANTS and argv[2] == "--say" and argv[3]:
        bus()
        speak(read_payload(argv[3]).strip(), "en", False)
    else:
        raise SystemExit("usage: python brain.py [text|file] | python brain.py <variant> --say <text|file>")
