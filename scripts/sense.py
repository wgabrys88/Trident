# Qwen3-0.6B on CPU. Its only limit is its own memory file.
# A window of original transcripts arrives together. yes passes that window on unchanged.

from pathlib import Path


class Gate:
    def __init__(self, model: Path, threads: int, ctx: int, n_predict: int, memory: Path, memory_max: int, system: str, temperature: float, top_k: int, top_p: float):
        from llama_cpp import Llama

        self.n_predict = n_predict
        self.system = system
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.memory_path = memory
        self.memory_max = memory_max
        self.room = []
        self.said = ""
        if memory.exists():
            raw = memory.read_text(encoding="utf-8")
            self.room = [part.strip() for part in raw.split("\n\n") if part.strip()]
            self._compact()
        self.llm = Llama(
            model_path=str(model),
            n_ctx=ctx,
            n_threads=threads,
            n_gpu_layers=0,
            verbose=False,
        )

    def _compact(self) -> None:
        while len(self.room) > 1 and sum(len(part) + 2 for part in self.room) > self.memory_max:
            self.room.pop(0)

    def _save(self) -> None:
        self._compact()
        text = "\n\n".join(self.room)
        self.memory_path.write_text(text + ("\n" if text else ""), encoding="utf-8")

    def pass_original(self, heard: str) -> str:
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
            + (("Earlier\n" + earlier + "\n") if earlier else "")
            + "NEW\n"
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
        self.said = out["choices"][0]["text"].strip()
        for line in self.said.splitlines():
            if line.strip().lower().strip(".,!") == "tool pass":
                return heard
        return ""
