# Qwen3-0.6B on CPU. It only answers yes or no.
# yes forwards the recognizer text unchanged. It never rewrites.

from pathlib import Path

ROOM = 8


class Gate:
    def __init__(self, model: Path, threads: int, ctx: int, n_predict: int):
        from llama_cpp import Llama

        self.n_predict = n_predict
        self.room = []
        self.llm = Llama(
            model_path=str(model),
            n_ctx=ctx,
            n_threads=threads,
            n_gpu_layers=0,
            verbose=False,
        )

    def pass_original(self, heard: str) -> str:
        heard = heard.strip()
        if not heard:
            return ""
        self.room.append(heard)
        del self.room[:-ROOM]
        earlier = "\n".join(self.room[:-1])
        body = (earlier + "\n" if earlier else "") + "NEW " + heard
        prompt = (
            "<|im_start|>system\n"
            "You stand in front of Gemma. She is the assistant. "
            "The line marked NEW is what the microphone just transcribed. "
            "Earlier lines are only context. "
            "Reply yes if Gemma should hear that new line. Reply no if she should not. "
            "Never rewrite, translate, or correct the line. One word: yes or no.\n"
            "/no_think<|im_end|>\n"
            "<|im_start|>user\n"
            + body
            + "<|im_end|>\n"
            "<|im_start|>assistant\n<think>\n\n</think>\n"
        )
        out = self.llm(
            prompt,
            max_tokens=self.n_predict,
            temperature=0.2,
            top_k=20,
            top_p=0.8,
            stop=["<|im_end|>"],
        )
        word = out["choices"][0]["text"].strip().split()
        if word and word[0].lower().strip(".,!") == "yes":
            return heard
        return ""
