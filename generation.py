from config import ensure_venv
ensure_venv()
if __name__ == "__main__":
    from main import main
    raise SystemExit(main("generation"))

import json
import subprocess
import sys

from config import GEMMA_GEN, ROOT, Paths, system_prompt
from journal import finish_cleanup
from runtime import CancelableHTTP, Residents

SPEAKABLE_MIN_WORDS = 8
SPOKEN_TURN_WORDS = 60
SPOKEN_TURN_CHARS = 480
HELLO_TOOL = {"type": "function", "function": {"name": "hello_world", "description": "Run the local hello_world.py script with one text argument.", "parameters": {"type": "object", "properties": {"text": {"type": "string", "description": "Text passed as the script's single argument."}}, "required": ["text"], "additionalProperties": False}}}


def fit_spoken_unit(text: str, max_words: int, max_chars: int) -> tuple[str, bool]:
    parts = text.split(); normalized = " ".join(parts)
    fitted = " ".join(parts[:max(0, max_words)])[:max(0, max_chars)].rstrip()
    return fitted, len(parts) > max_words or len(normalized) > max_chars


def spoken(text: str) -> str:
    return text.replace("\r", "").strip()


def _payload(messages: list[dict], gen: dict, thinking: str, thinking_budget: int, **extra) -> dict:
    payload = {"model": "gemma", "messages": messages, "cache_prompt": True, **gen, **extra}
    if thinking == "off": payload["reasoning_effort"] = "none"
    elif thinking == "on": payload["reasoning_effort"] = "high"
    if thinking_budget >= 0: payload["thinking_budget_tokens"] = thinking_budget
    return payload


def _choice(data, what: str) -> dict:
    choices = data.get("choices") if type(data) is dict else None
    if type(choices) is not list or not choices or type(choices[0]) is not dict:
        raise RuntimeError(f"Gemma {what} is missing a valid choices envelope")
    return choices[0]


def _open_json(base: str, channel: CancelableHTTP, payload: dict, accept: str = "application/json"):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    response = channel.open(base + "/v1/chat/completions", raw, {"Content-Type": "application/json", "Accept": accept})
    if response.status >= 400:
        try: detail = response.read(4096).decode("utf-8", "replace")
        finally: channel.clear(response)
        raise RuntimeError(f"Gemma HTTP {response.status}: {detail}")
    return response


def gemma_stream(base: str, messages: list[dict], channel: CancelableHTTP, gen: dict,
                 thinking: str = "off", thinking_budget: int = -1, tools: list[dict] | None = None,
                 tool_choice=None):
    payload = _payload(messages, dict(gen), thinking, thinking_budget, stream=True)
    if tools is not None:
        payload.update(tools=tools, parallel_tool_calls=False)
        if tool_choice is not None: payload["tool_choice"] = tool_choice
    response = _open_json(base, channel, payload, "text/event-stream")
    try:
        while line := response.readline():
            if not line.startswith(b"data:"): continue
            chunk = line[5:].strip()
            if chunk == b"[DONE]": return
            if text := str((_choice(json.loads(chunk), "SSE chunk").get("delta") or {}).get("content") or ""):
                yield text
    finally:
        channel.clear(response)


def gemma_complete(base: str, messages: list[dict], channel: CancelableHTTP, gen: dict,
                   thinking: str = "off", thinking_budget: int = -1, **extra) -> dict:
    response = _open_json(base, channel, _payload(messages, dict(gen), thinking, thinking_budget, stream=False, **extra))
    try: data = json.loads(response.read())
    finally: channel.clear(response)
    if type(data) is not dict: raise RuntimeError("Gemma completion is not an object")
    return data


def gemma_prefill(base: str, messages: list[dict], channel: CancelableHTTP, thinking: str = "off") -> dict:
    # llama-server interprets max_tokens=0 as prompt evaluation only; cache_prompt keeps the evaluated prefix.
    gen = {**GEMMA_GEN, "max_tokens": 0}
    return gemma_complete(base, messages, channel, gen, thinking, -1)


def execute_tool(call: dict) -> tuple[str, str]:
    function = call.get("function") or {}; name = str(function.get("name") or "")
    if name != "hello_world": return name, f"tool error: unsupported tool {name!r}"
    try: args = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError as error: return name, f"tool error: invalid arguments: {error}"
    text = str(args.get("text") or "")[:1000]
    try:
        result = subprocess.run([sys.executable, str(ROOT / "hello_world.py"), text], cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error: return name, f"tool error: {error}"
    output = result.stdout.strip()[:4000]
    return name, output if result.returncode == 0 else f"tool exit {result.returncode}: {output}"


def tool_round(base: str, messages: list[dict], channel: CancelableHTTP, gen: dict, thinking: str,
               thinking_budget: int) -> tuple[str | None, list[dict], list[tuple[str, str]]]:
    data = gemma_complete(base, messages, channel, gen, thinking, thinking_budget, tools=[HELLO_TOOL], tool_choice="auto", parallel_tool_calls=False)
    message = _choice(data, "completion").get("message")
    if type(message) is not dict: raise RuntimeError("Gemma completion is missing a message")
    calls = message.get("tool_calls") or []
    if not calls: return str(message.get("content") or ""), messages, []
    augmented = [*messages, {k: v for k, v in message.items() if k in ("role", "content", "reasoning_content", "tool_calls")}]
    results = []
    for call in calls[:1]:
        name, result = execute_tool(call); results.append((name, result))
        augmented.append({"role": "tool", "tool_call_id": str(call.get("id") or "tool"), "name": name, "content": result})
    return None, augmented, results


class Segmenter:
    def __init__(self) -> None:
        self.sent = 0; self.buffer: list[str] = []

    def take(self, text: str, flush: bool = False) -> list[str]:
        out: list[str] = []
        while self.sent < len(text):
            pending, cut = text[self.sent:], 0
            for i, char in enumerate(pending):
                if char in ".?!" and (i + 1 == len(pending) or pending[i + 1].isspace()):
                    cut = i + 1; break
            if not (cut := cut or (len(pending) if flush else 0)): break
            unit = pending[:cut].strip(); self.sent += cut
            while self.sent < len(text) and text[self.sent].isspace(): self.sent += 1
            if unit: self.buffer.append(unit)
            if sum(len(part.split()) for part in self.buffer) >= SPEAKABLE_MIN_WORDS:
                out.append(" ".join(self.buffer)); self.buffer.clear()
        if flush and self.buffer:
            out.append(" ".join(self.buffer)); self.buffer.clear()
        return out


def launch(paths: Paths, family: str = "nano", language: str = "en", primary: str | None = None, replacement=None, interrupt_after=None) -> None:
    if not primary: raise RuntimeError("generation requires --text or --text-file")
    residents, http, failure = Residents(paths), CancelableHTTP(), None
    try:
        residents.boot(family, language)
        messages = [{"role": "system", "content": system_prompt(language, paths.system_prompt)}, {"role": "user", "content": primary}]
        paths.journal.emit("gemma", "start", chars=len(primary), thinking=paths.thinking, tools=paths.tools_enabled); print("trident.ready", flush=True)
        raw: list[str] = []; tools = choice = None; gemma = residents.require_alive("gemma"); chunks = None
        if paths.tools_enabled:
            direct, messages, results = tool_round(gemma, messages, http, paths.gemma_gen, paths.thinking, paths.thinking_budget)
            for name, result in results: paths.journal.emit("gemma", "tool.completed", name=name, result_chars=len(result))
            if direct is not None: chunks = (direct,)
            else: tools, choice = [HELLO_TOOL], "none"
        for delta in chunks if chunks is not None else gemma_stream(gemma, messages, http, paths.gemma_gen, paths.thinking, paths.thinking_budget, tools, choice):
            print(delta, end="", flush=True); raw.append(delta)
        answer = spoken("".join(raw)); print()
        if answer: paths.journal.transcript("assistant", answer)
        paths.journal.emit("gemma", "completed", chars=len(answer), generated_chars=len("".join(raw)))
    except BaseException as error:
        failure = (error, error.__traceback__)
    http.close()
    finish_cleanup(paths, failure, [("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))])
