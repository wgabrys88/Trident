import json, re, subprocess, sys, time
from pathlib import Path
from runtime import MODELS, reexec, venv_python, vulkan
from settings import (
    BRAIN, CHUNK, IDLE, MEMORY, SPEAK, STOP, TOOLS, TURNS, VARIANTS, WORK,
    bus, next_path, parts, put, read_memory, ready, retire, take, waiting,
)

MARK = '<|"|>'
OPEN, CLOSE = "<|tool_call>", "<tool_call|>"
NAMES = {"python", "remember", "wake", "stop"}
CAP = 6144
SPOKEN = []
LANG = "en"
WAKE_AT = None
LAST_HUMAN = None
LAST_CODE = ""
LAST_COMPLETION = ""
PRINTED = False
BOS = "<bos>"
TEMPLATE = None


def load():
    global BOS, TEMPLATE
    path = MODELS / BRAIN["file"]
    if not path.is_file():
        raise RuntimeError("missing " + str(path))
    device = vulkan()
    from llama_cpp import Llama
    llm = Llama(
        model_path=str(path), n_ctx=BRAIN["n_ctx"], n_batch=BRAIN["n_batch"], n_ubatch=BRAIN["n_ubatch"],
        n_threads=BRAIN["n_threads"], n_gpu_layers=BRAIN["n_gpu_layers"], main_gpu=device,
        swa_full=BRAIN["swa_full"], verbose=True, logits_all=False,
    )
    BOS = llm.detokenize([llm.token_bos()], special=True).decode("utf-8") or "<bos>"
    TEMPLATE = chat_template(path)
    return llm


def chat_template(path: Path) -> str:
    from gguf import GGUFReader
    field = GGUFReader(str(path)).fields["tokenizer.chat_template"]
    return bytes(field.parts[-1].tolist()).decode("utf-8")


def read_turns() -> list:
    bus()
    if not TURNS.is_file():
        return []
    rows = []
    for line in TURNS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_turns(rows: list) -> None:
    bus()
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    put(TURNS, text)


def append_turn(row: dict) -> None:
    row = dict(row)
    row["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    bus()
    with TURNS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def seed_memory() -> None:
    text = read_memory().strip()
    if text and not read_turns():
        append_turn({"role": "user", "content": "memory:\n" + text})


def render(rows: list) -> str:
    import jinja2
    messages = [{"role": "system", "content": SPEAK}]
    for turn in rows:
        item = {"role": turn["role"], "content": turn.get("content") or ""}
        if turn.get("tool_calls"):
            item["tool_calls"] = []
            for call in turn["tool_calls"]:
                name = call["name"]
                arguments = call.get("arguments") or {}
                item["tool_calls"].append({"function": {"name": name, "arguments": arguments}})
        if turn["role"] == "tool":
            item["name"] = turn.get("name") or "unknown"
        messages.append(item)
    text = jinja2.Environment().from_string(TEMPLATE).render(
        messages=messages, tools=TOOLS, add_generation_prompt=True, bos_token=BOS, eos_token="",
    )
    if not text.startswith(BOS):
        text = BOS + text
    return text


def trim(llm) -> None:
    rows = read_turns()
    changed = False
    while rows:
        prompt = render(rows)
        count = len(llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True))
        if count <= CAP:
            break
        index = next((i for i, row in enumerate(rows) if not str(row.get("content", "")).startswith("memory:")), None)
        if index is None:
            break
        end = index + 1
        if rows[index]["role"] == "assistant" and rows[index].get("tool_calls"):
            end += len(rows[index]["tool_calls"])
        dropped = rows[index:end]
        del rows[index:end]
        changed = True
        folder = bus() / "done" / "turns"
        folder.mkdir(parents=True, exist_ok=True)
        n = 1
        while (folder / f"turns-{n}.jsonl").exists():
            n += 1
        put(folder / f"turns-{n}.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in dropped))
    if changed:
        write_turns(rows)


def letters(value: str) -> str:
    value = re.sub(r"<[^>]*>", " ", value)
    return " ".join("".join(ch for ch in value.casefold() if ch.isalpha() or ch.isspace()).split())


def own(heard: str) -> bool:
    heard_words = letters(heard)
    said = letters(" ".join(SPOKEN[-3:]))
    return bool(heard_words) and bool(said) and heard_words in said


def note_language(text: str) -> None:
    global LANG
    match = re.search(r"<([A-Za-z]{2,3})-[A-Za-z]{2}>", text)
    if match:
        LANG = match.group(1).lower()[:2]


SPEAK_THIS = True


def is_request(words: str) -> bool:
    text = " ".join(re.sub(r"<[^>]*>", " ", words).casefold().split())
    return "?" in words or "remember" in text or text.startswith("please say aloud the following text")


def number_question(words: str) -> bool:
    if "?" not in words:
        return False
    text = words.casefold()
    if re.search(r"\d", text):
        return True
    return any(word in text.split() for word in ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"))


def emit(sentence: str, always: bool = False) -> bool:
    if not always and not SPEAK_THIS:
        return False
    sentence = re.sub(r"^\d{1,2}:\d{2}:\d{2}\s+\S+\.?\s*", "", sentence.strip()).strip()
    bare = sentence.strip(".,")
    if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", bare) or re.fullmatch(r"[A-Za-z]{2,3}-[A-Za-z]{2}", bare):
        return False
    if not sentence:
        return False
    put(next_path("speech"), LANG + "\n" + sentence)
    SPOKEN.append(sentence)
    del SPOKEN[:-3]
    return True


def cut(buf: str, final: bool = False, first: bool = False):
    words = 0
    i = 0
    while i < len(buf):
        while i < len(buf) and buf[i].isspace():
            i += 1
        if i >= len(buf):
            break
        j = i
        while j < len(buf) and not buf[j].isspace():
            j += 1
        words += 1
        ended = buf[j - 1] in ".?!" or (j < len(buf) and buf[j] == "\n")
        if first and j < len(buf):
            return buf[:j].strip(), buf[j:]
        if words >= 8 and ended:
            return buf[:j].strip(), buf[j:]
        if words >= CHUNK:
            return buf[:j].strip(), buf[j:]
        i = j
    if final and buf.strip():
        return buf.strip(), ""
    return None, buf


class Speaker:
    def __init__(self):
        self.hold = ""
        self.buf = ""
        self.inside = False
        self.pending_first = True
        self.pre = ""
        self.drop = False

    def feed(self, text: str) -> None:
        self.hold += text
        while self.hold:
            if not self.inside:
                at = self.hold.find(OPEN)
                if at < 0:
                    keep = next((OPEN[:n] for n in range(len(OPEN) - 1, 0, -1) if self.hold.endswith(OPEN[:n])), "")
                    body, self.hold = (self.hold[:-len(keep)], keep) if keep else (self.hold, "")
                    self.buf += body
                    self.release()
                    return
                self.pre = (self.buf + self.hold[:at]).strip()
                self.buf = ""
                self.hold = self.hold[at + len(OPEN):]
                self.inside = True
            else:
                at = self.hold.find(CLOSE)
                if at < 0:
                    return
                call = self.hold[:at].strip()
                self.hold = self.hold[at + len(CLOSE):]
                self.inside = False
                name = call[5:].partition("{")[0] if call.startswith("call:") else ""
                if name == "python":
                    self.pre = ""
                    self.buf = ""
                    self.drop = True
                elif name == "remember":
                    self.pre = ""
                elif self.pre:
                    self.buf = (self.pre + "\n" + self.buf).strip()
                    self.pre = ""
                self.release()

    def release(self) -> None:
        if self.drop:
            self.buf = ""
            return
        while True:
            sentence, rest = cut(self.buf, False, first=False)
            if sentence is None:
                return
            if not rest.lstrip() or rest.lstrip().startswith("<"):
                return
            if emit(sentence):
                self.pending_first = False
            self.buf = rest

    def flush(self, final: bool = False) -> None:
        if self.drop:
            self.buf = ""
            return
        while True:
            sentence, rest = cut(self.buf, final, first=self.pending_first and not final)
            if sentence is None:
                return
            if emit(sentence):
                self.pending_first = False
            self.buf = rest

    def close(self) -> None:
        self.feed("")
        if self.inside:
            return
        if self.pre:
            self.buf = (self.pre + "\n" + self.buf).strip()
            self.pre = ""
        self.flush(final=True)


def parse_one(call: str) -> dict:
    if not call.startswith("call:") or not call.endswith("}"):
        return {"error": "tool call"}
    name, _, body = call[5:].partition("{")
    body = body[:-1]
    if name not in NAMES:
        return {"error": "unknown tool " + name}
    args = {key: value for key, value in re.findall(rf"(\w+):{re.escape(MARK)}(.*?){re.escape(MARK)}", body, flags=re.DOTALL)}
    for key, value in re.findall(r"(\w+):(\d+)", body):
        args.setdefault(key, value)
    return {"name": name, "arguments": args}


def parse_calls(text: str) -> list:
    found, rest = [], text
    while True:
        at = rest.find(OPEN)
        if at < 0:
            return found
        rest = rest[at + len(OPEN):]
        end = rest.find(CLOSE)
        if end < 0:
            found.append({"error": "open tool call"})
            return found
        found.append(parse_one(rest[:end].strip()))
        rest = rest[end + len(CLOSE):]


def tool_line(name: str, content: str) -> None:
    append_turn({"role": "tool", "name": name, "content": content})


def remember(text: str) -> None:
    if not text.strip():
        raise RuntimeError("remember")
    bus()
    prev = MEMORY.read_text(encoding="utf-8")
    fact = text.strip()
    MEMORY.write_text((prev.rstrip() + "\n" if prev.strip() else "") + fact + "\n", encoding="utf-8")
    read_memory()
    tool_line("remember", "remembered\n" + fact)


def arm(seconds: str) -> None:
    global WAKE_AT
    digits = "".join(ch for ch in str(seconds) if ch.isdigit())
    if not digits:
        raise RuntimeError("wake")
    n = min(86400, max(5, int(digits)))
    WAKE_AT = time.monotonic() + n


def run_python(code: str) -> str:
    global LAST_CODE
    if not isinstance(code, str) or not code.strip():
        raise RuntimeError("python")
    body = code.strip()
    if body == LAST_CODE:
        tool_line("python", "already ran, see above")
        return ""
    LAST_CODE = body
    script = next_path("job", ".py")
    script.write_text(code, encoding="utf-8")
    try:
        proc = subprocess.run([str(venv_python()), str(script)], cwd=str(bus()), capture_output=True, text=True, timeout=60)
        printed = proc.stdout or ""
        result = f"exit {proc.returncode}\n{printed}{proc.stderr or ''}"
    except subprocess.TimeoutExpired as err:
        printed = ""
        result = f"exit timeout\n{err.stdout or ''}{err.stderr or ''}"
    record = script.with_name(script.stem + ".result.txt")
    record.write_text(result, encoding="utf-8")
    retire(script, "job")
    retire(record, "job")
    tool_line("python", result)
    return printed.strip()


def drain() -> None:
    put(STOP, "")
    deadline = time.monotonic() + 60
    quiet = None
    while time.monotonic() < deadline:
        if waiting("speech"):
            quiet = None
        elif quiet is None:
            quiet = time.monotonic()
        elif time.monotonic() - quiet >= 2:
            return
        time.sleep(0.05)


def apply(found: list) -> bool:
    again = False
    for tool in found:
        if "error" in tool and "name" not in tool:
            tool_line("error", "error: " + tool["error"])
            again = True
            continue
        name, args = tool["name"], tool.get("arguments") or {}
        try:
            if name == "python":
                printed = run_python(args.get("code", ""))
                if printed:
                    emit(printed, always=True)
            elif name == "remember":
                remember(args.get("text", ""))
                again = True
            elif name == "wake":
                arm(args.get("seconds", ""))
            elif name == "stop":
                drain()
                raise SystemExit
            else:
                tool_line("error", "error: unknown tool " + name)
                again = True
        except SystemExit:
            raise
        except Exception as err:
            tool_line(name, "error: " + str(err))
            again = True
    return again


def store_assistant(content: str, calls: list) -> None:
    spoken = speech_of(content)
    if any(call.get("name") in {"python", "remember"} for call in calls):
        spoken = ""
    row = {"role": "assistant", "content": spoken}
    stored = []
    for call in calls:
        if "name" in call:
            stored.append({"name": call["name"], "arguments": call.get("arguments") or {}})
        else:
            stored.append({"name": "error", "arguments": {"text": call.get("error", "tool call")}})
    if stored:
        row["tool_calls"] = stored
    append_turn(row)


def speech_of(content: str) -> str:
    parts_out, rest = [], content
    while True:
        at = rest.find(OPEN)
        if at < 0:
            parts_out.append(rest)
            break
        parts_out.append(rest[:at])
        rest = rest[at + len(OPEN):]
        end = rest.find(CLOSE)
        if end < 0:
            break
        rest = rest[end + len(CLOSE):]
    return "".join(parts_out).strip()


def think(llm) -> None:
    global LAST_COMPLETION, PRINTED
    while True:
        trim(llm)
        rows = read_turns()
        prompt = render(rows)
        if not PRINTED:
            print(prompt, flush=True)
            PRINTED = True
        tokens = llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
        print("prompt tokens", len(tokens), flush=True)
        speaker, pieces = Speaker(), []
        for chunk in llm.create_completion(prompt=tokens, stream=True, stop=["<turn|>"], **BRAIN["decode"]):
            text = chunk["choices"][0].get("text") or ""
            pieces.append(text)
            speaker.feed(text)
        speaker.close()
        content = "".join(pieces)
        print(content, flush=True)
        calls = parse_calls(content)
        path = put(next_path("decision"), content if content.endswith("\n") else content + "\n")
        retire(path, "decision")
        store_assistant(content, calls)
        if content == LAST_COMPLETION:
            return
        LAST_COMPLETION = content
        if not apply(calls):
            return


def clock_line(kind_seconds: int) -> None:
    append_turn({"role": "user", "content": time.strftime("%H:%M:%S") + " silence for " + str(kind_seconds) + " s"})


def serve():
    global LAST_HUMAN, WAKE_AT
    bus()
    seed_memory()
    for row in read_turns():
        if row["role"] == "assistant" and row.get("content"):
            SPOKEN.append(row["content"])
    del SPOKEN[:-3]
    llm = load()
    ready("brain")
    started = time.monotonic()
    idle_mark = None
    while True:
        files = waiting("transcription")
        if files:
            name = files[0].name
            heard = take(files[0], "transcription").strip()
            print("read", name, flush=True)
            if not heard:
                continue
            words = heard.split("\n", 1)[0].strip()
            if own(words):
                continue
            global SPEAK_THIS
            SPEAK_THIS = is_request(words) and not number_question(words)
            note_language(words)
            LAST_HUMAN = time.monotonic()
            idle_mark = None
            append_turn({"role": "user", "content": time.strftime("%H:%M:%S") + " " + words})
            think(llm)
            continue
        now = time.monotonic()
        if WAKE_AT is not None and now >= WAKE_AT:
            base = LAST_HUMAN or started
            WAKE_AT = None
            clock_line(int(now - base))
            think(llm)
            continue
        base = LAST_HUMAN or started
        if WAKE_AT is None and now - base >= IDLE and (idle_mark is None or now - idle_mark >= IDLE):
            idle_mark = now
            clock_line(int(now - base))
            think(llm)
            continue
        time.sleep(0.05)


def read_payload(value: str) -> str:
    path = Path(value)
    if path.is_file():
        return path.read_text(encoding="utf-8-sig")
    return value


if __name__ == "__main__":
    reexec()
    argv = sys.argv
    if len(argv) == 1:
        serve()
    elif len(argv) == 2 and argv[1] not in VARIANTS:
        bus()
        seed_memory()
        heard = read_payload(argv[1]).strip()
        note_language(heard)
        LAST_HUMAN = time.monotonic()
        append_turn({"role": "user", "content": time.strftime("%H:%M:%S") + " " + heard})
        think(load())
    elif len(argv) == 4 and argv[1] in VARIANTS and argv[2] == "--say" and argv[3]:
        bus()
        for piece in parts(read_payload(argv[3]).strip()):
            put(next_path("speech"), "en\n" + piece)
    else:
        raise SystemExit("usage: python brain.py [text|file] | python brain.py <variant> --say <text|file>")
