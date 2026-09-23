import hashlib, json, os, re, subprocess
from pathlib import Path

from components.runtime import DONE, MODELS, ROOT, WORK, Http, venv_python

MODEL = "brain-gemma-4-e2b-it-q4_k_m.gguf"
MARK = '<|"|>'
OPEN, CLOSE = "<|tool_call>", "<tool_call|>"
TOOL_RESPONSE = "<|tool_response>"
MAX_TOOL_ROUNDS = 32
LEDGER = WORK / "brain.tool-ledger.jsonl"
CURSOR = WORK / "brain.cursor"
TOOLS = [
    {"type": "function", "function": {"name": "speak", "description": "Say something aloud.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "language": {"type": "string"}}, "required": ["text", "language"]}}},
    {"type": "function", "function": {"name": "python", "description": "Run one Python script; stdout/stderr return as data.", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "remember", "description": "Append durable memory.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "wake", "description": "Schedule a future wake.", "parameters": {"type": "object", "properties": {"seconds": {"type": "string"}, "reason": {"type": "string"}}, "required": ["seconds"]}}},
    {"type": "function", "function": {"name": "stop", "description": "Stop Trident.", "parameters": {"type": "object", "properties": {}, "required": []}}},
]
SYSTEM = """You are Jarvis, the brain of a local computer assistant.
You receive an ordered event journal. Every heard event is context, not automatically a command.
Use speak for every audible response. Use python for computer work. Use remember for durable facts. Use wake when future attention is useful. Use stop only to end Trident.
After a tool result, continue until work is complete. If nothing useful should happen, make no tool call.
Tool calls must use the Gemma form only: <|tool_call>call:toolname{key:<|"|>value<|"|>}<tool_call|>."""


class ToolParser:
    @staticmethod
    def parse_one(call: str) -> dict:
        if not call.startswith("call:") or not call.endswith("}"):
            return {"error": "tool call"}
        name, _, body = call[5:].partition("{")
        names = {t["function"]["name"] for t in TOOLS}
        if name not in names:
            return {"error": "unknown tool " + name}
        args = {k: v for k, v in re.findall(rf"(\w+):{re.escape(MARK)}(.*?){re.escape(MARK)}", body[:-1], flags=re.DOTALL)}
        return {"name": name, "arguments": args}

    @classmethod
    def parse_calls(cls, text: str) -> list[dict]:
        out, rest = [], text
        while True:
            at = rest.find(OPEN)
            if at < 0:
                break
            rest = rest[at + len(OPEN) :]
            end = rest.find(CLOSE)
            if end < 0:
                return [{"error": "open tool call"}]
            out.append(cls.parse_one(rest[:end].strip()))
            rest = rest[end + len(CLOSE) :]
        if out:
            return out
        for pattern in (
            r'speak\(\s*text\s*=\s*"((?:\\.|[^"\\])*)"\s*,\s*language\s*=\s*"([a-z]{2})"\s*\)',
            r"speak\{\s*\"text\"\s*:\s*\"((?:\\.|[^\"\\])*)\"\s*,\s*\"language\"\s*:\s*\"([a-z]{2})\"\s*\}",
        ):
            for match in re.finditer(pattern, text, flags=re.DOTALL):
                text_val = bytes(match.group(1), "utf-8").decode("unicode_escape")
                out.append({"name": "speak", "arguments": {"text": text_val, "language": match.group(2)}})
        return out


class ToolLedger:
    def __init__(self, path: Path = LEDGER):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(trigger_id: int, call: dict) -> str:
        payload = json.dumps({"id": trigger_id, "call": call}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def seen(self, trigger_id: int, call: dict) -> bool:
        key = self.key(trigger_id, call)
        if not self.path.is_file():
            return False
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip() == key:
                return True
        return False

    def record(self, trigger_id: int, call: dict) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(self.key(trigger_id, call) + "\n")


class Brain:
    def __init__(self, server: str):
        self.http = Http(server)
        self.ledger = ToolLedger()
        self.bos = "<bos>"
        self.template = ""
        self.llm = None
        self.python_enabled = os.environ.get("TRIDENT_ALLOW_PYTHON", "1").strip().lower() not in ("0", "false", "no")

    def load(self) -> None:
        path = MODELS / MODEL
        if not path.is_file():
            raise RuntimeError("missing " + str(path))
        flash = os.environ.get("TRIDENT_FLASH_ATTN", "0").strip().lower() in ("1", "true", "yes")
        from llama_cpp import Llama
        from gguf import GGUFReader

        cfg = self.http.call("GET", "/config")
        print("brain flash_attn", flash, flush=True)
        self.llm = Llama(
            model_path=str(path),
            n_ctx=8192,
            n_batch=1024,
            n_ubatch=1024,
            n_threads=4,
            n_gpu_layers=-1,
            main_gpu=int(cfg["vulkan_device"]),
            swa_full=True,
            flash_attn=flash,
            verbose=False,
            logits_all=False,
        )
        self.bos = self.llm.detokenize([self.llm.token_bos()], special=True).decode("utf-8") or "<bos>"
        field = GGUFReader(str(path)).fields["tokenizer.chat_template"]
        self.template = bytes(field.parts[-1].tolist()).decode("utf-8")

    @staticmethod
    def format_event(row: dict) -> str:
        data = row.get("data") or {}
        kind = row.get("type", "event")
        at = row.get("at", "")
        if kind == "heard":
            return f"[{row['id']}] {at} HEARD language={data.get('language') or 'unknown'}: {data.get('text', '')}"
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

    def journal_user_content(self, current: dict) -> str:
        memory = self.http.call("GET", "/memory")["text"]
        events = self.http.call("GET", "/events/recent?limit=80")["events"]
        lines = "\n".join(self.format_event(row) for row in events)
        return f"Durable memory:\n{memory.strip() or '(empty)'}\n\nRecent event journal:\n{lines or '(empty)'}\n\nCurrent trigger:\n{self.format_event(current)}"

    def render(self, messages: list[dict]) -> list[int]:
        import jinja2

        text = jinja2.Environment().from_string(self.template).render(
            messages=messages, tools=TOOLS, add_generation_prompt=True, bos_token=self.bos, eos_token=""
        )
        if not text.startswith(self.bos):
            text = self.bos + text
        tokens = self.llm.tokenize(text.encode("utf-8"), add_bos=False, special=True)
        print("prompt tokens", len(tokens), flush=True)
        return tokens

    def tool_event(self, name: str, result: str, trigger_id: int, extra: dict | None = None) -> None:
        data = {"name": name, "result": result, "trigger_id": trigger_id}
        if extra:
            data.update(extra)
        self.http.call("POST", "/event", {"type": "tool_result", "source": "brain", "data": data})

    def next_job(self, suffix: str) -> Path:
        n = 1
        while True:
            path = WORK / f"job-{n}{suffix}"
            if not path.exists() and not (DONE / "job" / path.name).exists():
                return path
            n += 1

    def apply_call(self, call: dict, trigger_id: int) -> tuple[str, bool]:
        if "error" in call:
            result = "error: " + call["error"]
            self.tool_event("parser", result, trigger_id)
            return result, False
        if self.ledger.seen(trigger_id, call):
            return "skipped duplicate tool call", False
        name, args = call["name"], call.get("arguments") or {}
        try:
            if name == "speak":
                text, language = str(args.get("text", "")).strip(), str(args.get("language", "")).strip().lower()
                if not text or not language:
                    raise RuntimeError("speak requires text and language")
                reply = self.http.call("POST", "/speech", {"text": text, "language": language})
                result = "queued " + reply["name"]
            elif name == "python":
                if not self.python_enabled:
                    raise RuntimeError("python tool disabled (set TRIDENT_ALLOW_PYTHON=1 to enable)")
                code = args.get("code", "")
                if not isinstance(code, str) or not code.strip():
                    raise RuntimeError("empty python")
                script = self.next_job(".py")
                script.write_text(code, encoding="utf-8", newline="\n")
                proc = subprocess.run([str(venv_python()), str(script)], cwd=str(WORK), capture_output=True, text=True)
                result = f"exit {proc.returncode}\n{proc.stdout or ''}{proc.stderr or ''}"
                result_path = script.with_name(script.stem + ".result.txt")
                result_path.write_text(result, encoding="utf-8", newline="\n")
                script_art = self.http.call("POST", "/archive", {"path": str(script.relative_to(ROOT)), "kind": "job"})["artifact"]
                result_art = self.http.call("POST", "/archive", {"path": str(result_path.relative_to(ROOT)), "kind": "job"})["artifact"]
                self.tool_event("python", result, trigger_id, {"script": script_art, "artifact": result_art})
                self.ledger.record(trigger_id, call)
                return result, False
            elif name == "remember":
                text = str(args.get("text", ""))
                if not text.strip():
                    raise RuntimeError("empty memory")
                self.http.call("POST", "/memory", {"text": text})
                result = "remembered: " + text
            elif name == "wake":
                seconds = float(args.get("seconds", "0"))
                self.http.call("POST", "/wake", {"seconds": seconds, "reason": str(args.get("reason", ""))})
                result = f"wake scheduled in {seconds:g} seconds"
            elif name == "stop":
                self.http.call("POST", "/stop", {"source": "brain"})
                result = "stop requested"
                self.tool_event("stop", result, trigger_id)
                self.ledger.record(trigger_id, call)
                return result, True
            else:
                result = "error: unknown tool " + name
                self.tool_event(name, result, trigger_id)
            if name not in ("python", "parser"):
                self.tool_event(name, result, trigger_id)
            self.ledger.record(trigger_id, call)
        except Exception as err:
            result = "error: " + str(err)
            self.tool_event(name, result, trigger_id)
        return result, False

    @staticmethod
    def append_tool_turn(messages: list[dict], calls: list[dict], results: list[str]) -> None:
        tool_calls, tool_msgs = [], []
        base = len(messages)
        for index, (call, result) in enumerate(zip(calls, results)):
            name = "parser" if "error" in call else call.get("name", "unknown")
            args = {} if "error" in call else (call.get("arguments") or {})
            tool_id = f"t{base}_{index}"
            tool_calls.append({"id": tool_id, "type": "function", "function": {"name": name, "arguments": args}})
            tool_msgs.append({"role": "tool", "tool_call_id": tool_id, "content": result})
        messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
        messages.extend(tool_msgs)

    def think(self, current: dict) -> None:
        trigger_id = int(current["id"])
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": self.journal_user_content(current)}]
        for round_index in range(MAX_TOOL_ROUNDS):
            if self.http.call("GET", "/health")["stop"]:
                return
            completion = self.llm.create_completion(
                prompt=self.render(messages),
                stop=[TOOL_RESPONSE, "<turn|>"],
                temperature=1.0,
                top_p=0.95,
                top_k=64,
                min_p=0.0,
                max_tokens=1024,
            )["choices"][0]["text"]
            print(completion, flush=True)
            calls = ToolParser.parse_calls(completion)
            if not calls:
                return
            results, stopped = [], False
            for call in calls:
                result, stop_flag = self.apply_call(call, trigger_id)
                results.append(result)
                stopped = stopped or stop_flag
            self.append_tool_turn(messages, calls, results)
            self._set_cursor(trigger_id, round_index + 1)
            if stopped:
                return
        self.tool_event("brain", "error: tool round limit", trigger_id)

    @staticmethod
    def _cursor() -> int:
        try:
            return int(CURSOR.read_text(encoding="utf-8").strip().split()[0])
        except (FileNotFoundError, ValueError):
            return 0

    @staticmethod
    def _set_cursor(event_id: int, round_index: int = 0) -> None:
        CURSOR.parent.mkdir(parents=True, exist_ok=True)
        tmp = CURSOR.with_name(CURSOR.name + ".tmp")
        tmp.write_text(f"{event_id} {round_index}\n", encoding="utf-8")
        tmp.replace(CURSOR)

    def run(self) -> None:
        self.load()
        self.http.call("POST", "/ready", {"name": "brain"})
        position = self._cursor()
        while not self.http.call("GET", "/health")["stop"]:
            rows = self.http.call("GET", f"/events?after={position}&timeout=30", timeout=40)["events"]
            if not rows:
                continue
            for row in rows:
                if row.get("trigger"):
                    self.think(row)
                position = int(row["id"])
                self._set_cursor(position)
                if self.http.call("GET", "/health")["stop"]:
                    return
