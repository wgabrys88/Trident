import os
import time
from pathlib import Path

from trident_runtime import read_cfg

ROOT = Path(__file__).resolve().parents[1]


class Gate:
    def __init__(self, model, threads, ctx, n_predict, memory, memory_max, system, temperature, top_k, top_p):
        from llama_cpp import Llama

        self.n_predict = n_predict
        self.system = system
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.memory_path = memory
        self.memory_max = memory_max
        self.room = []
        if memory.exists():
            raw = memory.read_text(encoding="utf-8")
            self.room = [part.strip() for part in raw.split("\n\n") if part.strip()]
            self._compact()
        self.llm = Llama(model_path=str(model), n_ctx=ctx, n_threads=threads, n_gpu_layers=0, verbose=False)

    def _compact(self):
        while len(self.room) > 1 and sum(len(part) + 2 for part in self.room) > self.memory_max:
            self.room.pop(0)

    def _save(self):
        self._compact()
        text = "\n\n".join(self.room)
        self.memory_path.write_text(text + ("\n" if text else ""), encoding="utf-8")

    def pass_original(self, heard):
        heard = heard.strip()
        if not heard:
            return ""
        earlier = "\n\n".join(self.room)
        self.room.append(heard)
        self._save()
        prompt = (
            "<|im_start|>system\n"
            + self.system.strip()
            + "\n/no_think<|im_end|>\n"
            "<|im_start|>user\n"
            + ((earlier + "\n") if earlier else "")
            + heard
            + "<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n"
        )
        out = self.llm(
            prompt,
            max_tokens=self.n_predict,
            temperature=self.temperature,
            top_k=self.top_k,
            top_p=self.top_p,
            stop=["<|im_end|>"],
        )
        for line in out["choices"][0]["text"].splitlines():
            if line.strip().lower().strip(".,!") == "tool pass":
                return heard
        return ""


def main():
    cfg = read_cfg()
    gate = Gate(
        ROOT / cfg["sense.model"],
        int(cfg["sense.threads"]),
        int(cfg["sense.ctx"]),
        int(cfg["sense.n-predict"]),
        ROOT / cfg["sense.memory-file"],
        int(cfg["sense.memory-max"]),
        cfg["sense.system"],
        float(cfg["sense.temp"]),
        int(cfg["sense.top-k"]),
        float(cfg["sense.top-p"]),
    )
    heard_path = ROOT / cfg["ear.response-file"]
    out_path = ROOT / cfg["gemma.prompt-file"]
    poll = int(cfg["sense.poll-ms"]) / 1000
    stop = ROOT / "sense.stop"
    pid = ROOT / "sense.pid"
    stop.unlink(missing_ok=True)
    pid.write_text(str(os.getpid()), encoding="ascii")
    seen = heard_path.stat().st_mtime if heard_path.exists() else None
    while not stop.exists():
        if heard_path.exists():
            stamp = heard_path.stat().st_mtime
            if stamp != seen:
                seen = stamp
                original = gate.pass_original(heard_path.read_text(encoding="utf-8"))
                if original:
                    out_path.write_text(original, encoding="utf-8")
        time.sleep(poll)
    pid.unlink(missing_ok=True)
    stop.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
