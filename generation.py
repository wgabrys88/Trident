from config import ensure_venv
ensure_venv()
if __name__ == "__main__":
    from main import main
    raise SystemExit(main("generation"))

import json

from config import GEMMA_GEN, Paths, load_settings, system_prompt
from journal import finish_cleanup
from runtime import CancelableHTTP, Residents

SPEAKABLE_MIN_WORDS = 8
SPOKEN_TURN_WORDS = 60


def fit_spoken_unit(text: str, max_words: int) -> tuple[str, bool]:
    parts = text.split()
    return " ".join(parts[:max(0, max_words)]), len(parts) > max_words


def spoken(text: str) -> str:
    text, marker = text.replace("\r", "").strip(), "Assistant:\n"
    return text.rsplit(marker, 1)[-1].strip() if marker in text else text


def gemma_stream(base: str, messages: list[dict[str, str]], channel: CancelableHTTP):
    payload = {"model": "gemma", "messages": messages, "stream": True, "cache_prompt": True, **GEMMA_GEN, "chat_template_kwargs": {"enable_thinking": False}}
    response = channel.open(base + "/v1/chat/completions", json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
                            {"Content-Type": "application/json", "Accept": "text/event-stream"})
    try:
        while line := response.readline():
            if not line.startswith(b"data:"): continue
            chunk = line[5:].strip()
            if chunk == b"[DONE]": return
            if text := str((json.loads(chunk).get("choices") or [{}])[0].get("delta", {}).get("content") or ""):
                yield text
    finally:
        channel.clear(response)


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
        messages = [{"role": "system", "content": system_prompt(language, str(load_settings(paths.data_dir).get("system_prompt") or ""))},
                    {"role": "user", "content": primary}]
        paths.journal.emit("gemma", "start", chars=len(primary)); print("trident.ready", flush=True)
        raw = []
        for delta in gemma_stream(residents.require_alive("gemma"), messages, http):
            print(delta, end="", flush=True); raw.append(delta)
        answer = spoken("".join(raw)); print()
        if answer:
            paths.journal.transcript("assistant", answer)
        paths.journal.emit("gemma", "completed", chars=len(answer), generated_chars=len("".join(raw)))
    except BaseException as error:
        failure = (error, error.__traceback__)
    http.close()
    finish_cleanup(paths, failure, [("residents", residents.stop), ("supervisor", lambda: paths.supervisor.join(1))])
