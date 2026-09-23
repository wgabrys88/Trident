import re, subprocess, sys, time
from pathlib import Path
from runtime import MODELS, reexec, venv_python
from settings import (
    BRAIN, LIVE, MEMORY, SAID, SPEAK, TOOLS, VARIANTS, append_live, bus, clear_live,
    next_path, parts, put, ready, retire, slot, take, user_text, waiting,
)

MARK = '<|"|>'
RULES = r"""
call ::= say | idle | noted | distilled | py
say ::= "<|tool_call>call:say{" saybody "}" "<tool_call|>"
idle ::= "<|tool_call>call:" ("pass" | "quit") "{}" "<tool_call|>"
noted ::= "<|tool_call>call:note{text:" piece "}" "<tool_call|>"
distilled ::= "<|tool_call>call:distill{text:" piece "}" "<tool_call|>"
py ::= "<|tool_call>call:run_python{code:" piece "}" "<tool_call|>"
saybody ::= "text:" piece ",language:" lang | "language:" lang ",text:" piece
piece ::= mark chars mark
lang ::= mark ("en" | "pl") mark
mark ::= "<|\"|>"
chars ::= [^<]*
"""

def load():
    path = MODELS / BRAIN["file"]
    if not path.is_file():
        raise RuntimeError("missing " + str(path))
    from llama_cpp import Llama, LlamaGrammar
    llm = Llama(model_path=str(path), n_ctx=BRAIN["n_ctx"], n_batch=BRAIN["n_batch"],
                n_threads=BRAIN["n_threads"], n_gpu_layers=BRAIN["n_gpu_layers"],
                chat_format="chat_template.default", verbose=False, logits_all=False)
    grammar = LlamaGrammar.from_string("root ::= call+\n" + RULES)
    return llm, grammar

def ask(llm, grammar, text: str) -> str:
    if not text.strip():
        return '<|tool_call>call:pass{}<tool_call|>'
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

def speak(text: str, language: str):
    if language not in ("en", "pl") or not text.strip():
        raise RuntimeError("say")
    slot(SAID, text)
    for piece in parts(text):
        put(next_path("speech"), language + "\n" + piece)

def run_python(code: str) -> str:
    if not isinstance(code, str) or not code.strip():
        raise RuntimeError("run_python")
    script = next_path("job", ".py")
    script.write_text(code, encoding="utf-8")
    try:
        proc = subprocess.run([str(venv_python()), str(script)], cwd=str(bus()), capture_output=True, text=True, timeout=60)
        result = f"exit {proc.returncode}\n{proc.stdout or ''}{proc.stderr or ''}"
    except subprocess.TimeoutExpired as err:
        result = f"exit timeout\n{err.stdout or ''}{err.stderr or ''}"
    record = script.with_name(script.stem + ".result.txt")
    record.write_text(result, encoding="utf-8")
    append_live(result)
    retire(script, "job")
    retire(record, "job")
    return result

def clean_exit(text: str) -> bool:
    for line in text.splitlines():
        piece = line.split()
        if line.startswith("exit") and len(piece) > 1 and piece[1] == "0":
            return True
    return False

def apply(found):
    again = False
    spoke = False
    blocked = clean_exit(LIVE.read_text(encoding="utf-8") if LIVE.is_file() else "")
    for tool in found:
        name = tool["name"]
        if name == "pass":
            pass
        elif name == "say":
            speak(tool.get("text", ""), tool.get("language") or "en")
            spoke = True
        elif name == "note":
            if not tool.get("text"):
                raise RuntimeError("note")
            bus()
            prev = MEMORY.read_text(encoding="utf-8")
            MEMORY.write_text((prev.rstrip() + "\n" if prev.strip() else "") + tool["text"].strip() + "\n", encoding="utf-8")
        elif name == "distill":
            if not tool.get("text"):
                raise RuntimeError("distill")
            slot(MEMORY, tool["text"].strip() + "\n")
            clear_live()
        elif name == "run_python":
            if blocked:
                continue
            run_python(tool.get("code", ""))
            again = True
        elif name == "quit":
            raise SystemExit
        else:
            raise RuntimeError("unknown tool " + name)
    if spoke:
        clear_live()
        return False
    return again

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
    hops = 0
    while True:
        blob = user_text()
        print(blob, flush=True)
        content = ask(llm, grammar, blob)
        print(content, flush=True)
        again = decide(content)
        hops += 1
        if not again or hops >= 3:
            return

def read_payload(value: str) -> str:
    path = Path(value)
    if path.is_file():
        return path.read_text(encoding="utf-8-sig")
    return value

def serve():
    bus()
    clear_live()
    llm, grammar = load()
    ready("brain")
    while True:
        files = waiting("transcription")
        if not files:
            time.sleep(0.05)
            continue
        name = files[0].name
        heard = take(files[0], "transcription").strip()
        print("read", name, flush=True)
        if not heard:
            continue
        said = SAID.read_text(encoding="utf-8") if SAID.exists() else ""
        if own(heard, said):
            continue
        append_live(heard)
        think(llm, grammar)

if __name__ == "__main__":
    reexec()
    argv = sys.argv
    if len(argv) == 1:
        serve()
    elif len(argv) == 2 and argv[1] not in VARIANTS:
        bus()
        append_live(read_payload(argv[1]))
        llm, grammar = load()
        think(llm, grammar)
    elif len(argv) == 4 and argv[1] in VARIANTS and argv[2] == "--say" and argv[3]:
        bus()
        speak(read_payload(argv[3]).strip(), "en")
    else:
        raise SystemExit("usage: python brain.py [text|file] | python brain.py <variant> --say <text|file>")
