# Qwen3-0.6B on CPU. Its only limit is its own memory file.
# A window of original transcripts arrives together. yes passes that window on unchanged.

from pathlib import Path


class Gate:
    def __init__(self, model: Path, threads: int, ctx: int, n_predict: int, memory: Path, memory_max: int):
        from llama_cpp import Llama

        self.n_predict = n_predict
        self.memory_path = memory
        self.memory_max = memory_max
        self.room = []
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
            "You remember the earlier lines. The lines marked NEW just arrived. "
            "Reply yes if those new lines should be passed on. Reply no if they should not. "
            "Never rewrite them. One word: yes or no.\n"
            "/no_think<|im_end|>\n"
            "<|im_start|>user\n"
            + (("Earlier\n" + earlier + "\n") if earlier else "")
            + "NEW\n"
            + heard
            + "<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n"
        )
        out = self.llm(
            prompt,
            max_tokens=max(self.n_predict, 4),
            temperature=0.0,
            top_k=20,
            top_p=0.8,
            stop=["<|im_end|>"],
        )
        word = out["choices"][0]["text"].strip().split()
        if word and word[0].lower().strip(".,!") == "yes":
            return heard
        return ""
