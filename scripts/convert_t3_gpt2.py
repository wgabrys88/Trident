import argparse
import json
import re

import gguf
from quant_policy import QuantPolicy, T3Converter


class Gpt2Converter(T3Converter):
    LAYER = re.compile(r"^tfmr\.h\.(\d+)\.(.+)$")
    DIRECT = {
        "tfmr.wpe.weight": "model/wpe", "tfmr.ln_f.weight": "model/ln_f/g",
        "tfmr.ln_f.bias": "model/ln_f/b", "text_emb.weight": "chatterbox/text_emb",
        "speech_emb.weight": "chatterbox/speech_emb", "speech_head.weight": "chatterbox/speech_head",
        "speech_head.bias": "chatterbox/speech_head_bias",
        "cond_enc.spkr_enc.weight": "chatterbox/cond_spkr/w", "cond_enc.spkr_enc.bias": "chatterbox/cond_spkr/b",
    }
    BLOCK = {
        "ln_1.weight": "ln_1/g", "ln_1.bias": "ln_1/b", "ln_2.weight": "ln_2/g", "ln_2.bias": "ln_2/b",
        "attn.c_attn.weight": "attn/c_attn/w", "attn.c_attn.bias": "attn/c_attn/b",
        "attn.c_proj.weight": "attn/c_proj/w", "attn.c_proj.bias": "attn/c_proj/b",
        "mlp.c_fc.weight": "mlp/c_fc/w", "mlp.c_fc.bias": "mlp/c_fc/b",
        "mlp.c_proj.weight": "mlp/c_proj/w", "mlp.c_proj.bias": "mlp/c_proj/b",
    }

    def tokenizer(self):
        vocab = json.loads((self.checkpoint / "vocab.json").read_text(encoding="utf-8"))
        added = json.loads((self.checkpoint / "added_tokens.json").read_text(encoding="utf-8"))
        by_id = {int(index): token for token, index in (vocab | added).items()}
        tokens = [by_id[index] for index in range(max(by_id) + 1)]
        types = [int(gguf.TokenType.USER_DEFINED if token in added else gguf.TokenType.NORMAL) for token in tokens]
        merges = [line for line in (self.checkpoint / "merges.txt").read_text(encoding="utf-8").splitlines()
                  if line and not line.startswith("#")]
        self.writer.add_tokenizer_model("gpt2")
        self.writer.add_token_list(tokens)
        self.writer.add_token_types(types)
        self.writer.add_token_merges(merges)

    def convert(self):
        width = self.state["tfmr.ln_f.weight"].shape[0]
        depth = 1 + max(int(match[1]) for name in self.state if (match := self.LAYER.match(name)))
        self.metadata(dict(n_embd=width, n_head=width // 64,
                           n_layer=depth, text_vocab_size=self.state["text_emb.weight"].shape[0], speech_vocab_size=self.state["speech_emb.weight"].shape[0],
                           start_speech_token=self.speech_tokens, stop_speech_token=self.speech_tokens + 1, speaker_embed_size=self.conditions["speaker_emb"].shape[-1]),
                      dict(layer_norm_eps=1e-5))
        self.tokenizer()
        for name, tensor in self.state.items():
            if name in self.DIRECT:
                self.tensor(self.DIRECT[name], tensor)
            elif (match := self.LAYER.match(name)) and match[2] in self.BLOCK:
                suffix = self.BLOCK[match[2]]
                matrix = suffix.endswith("/w")
                self.tensor(f"model/h{int(match[1])}/{suffix}", tensor, matrix=matrix, transpose=matrix)
        self.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    parser.add_argument("output")
    parser.add_argument("safetensors")
    parser.add_argument("--matrix-type", required=True, choices=QuantPolicy.WEIGHT_TYPES)
    args = parser.parse_args()
    Gpt2Converter(args.checkpoint, args.output, args.safetensors, args.matrix_type).convert()
