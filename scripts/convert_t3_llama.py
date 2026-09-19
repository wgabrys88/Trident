import argparse
import math
import re

import numpy as np
from tokenizers import Tokenizer
from quant_policy import QuantPolicy, T3Converter


class LlamaConverter(T3Converter):
    LAYER = re.compile(r"^tfmr\.layers\.(\d+)\.(.+)$")
    DIRECT = {
        "tfmr.norm.weight": "model/norm/g", "text_emb.weight": "chatterbox/text_emb",
        "speech_emb.weight": "chatterbox/speech_emb", "speech_head.weight": "chatterbox/speech_head",
        "text_pos_emb.emb.weight": "chatterbox/text_pos_emb", "speech_pos_emb.emb.weight": "chatterbox/speech_pos_emb",
        "cond_enc.spkr_enc.weight": "chatterbox/cond_spkr/w", "cond_enc.spkr_enc.bias": "chatterbox/cond_spkr/b",
        "cond_enc.emotion_adv_fc.weight": "chatterbox/emotion_adv_fc/w",
        "cond_enc.perceiver.pre_attention_query": "chatterbox/perceiver/pre_attention_query",
        "cond_enc.perceiver.attn.norm.weight": "chatterbox/perceiver/attn/norm/g",
        "cond_enc.perceiver.attn.norm.bias": "chatterbox/perceiver/attn/norm/b",
        **{f"cond_enc.perceiver.attn.{layer}.{suffix}": f"chatterbox/perceiver/attn/{layer}/{target}"
           for layer in ("to_q", "to_k", "to_v", "proj_out") for suffix, target in (("weight", "w"), ("bias", "b"))},
    }
    BLOCK = {
        "input_layernorm.weight": "attn_norm/g", "post_attention_layernorm.weight": "ffn_norm/g",
        **{f"self_attn.{projection}_proj.weight": f"attn/{projection}/w" for projection in ("q", "k", "v", "o")},
        **{f"mlp.{projection}_proj.weight": f"ffn/{projection}/w" for projection in ("gate", "up", "down")},
    }

    def rope(self):
        factors = np.zeros(32, dtype=np.float32)
        for index in range(32):
            inverse = 500000.0 ** (-2.0 * index / 64)
            wavelength = 2.0 * math.pi / inverse
            if wavelength < 8192 / 4:
                scaled = inverse
            elif wavelength > 8192:
                scaled = inverse / 8
            else:
                blend = (8192 / wavelength - 1) / 3
                scaled = (1 - blend) * inverse / 8 + blend * inverse
            factors[index] = np.float32(inverse / scaled)
        self.writer.add_tensor("model/rope_freq_factors", factors)

    def convert(self):
        state = self.state
        vocab = Tokenizer.from_file(str(self.checkpoint / "grapheme_mtl_merged_expanded_v1.json")).get_vocab()
        languages = sorted(token for token in vocab if re.fullmatch(r"\[[a-z]{2,3}\]", token))
        width = state["tfmr.norm.weight"].shape[0]
        perceiver = state["cond_enc.perceiver.pre_attention_query"].shape[1]
        text_positions = state["text_pos_emb.emb.weight"].shape[0]
        depth = 1 + max(int(match[1]) for name in state if (match := self.LAYER.match(name)))
        self.metadata(dict(n_ctx=1 + perceiver + 1 + text_positions + 2 + 4096,
                           n_embd=width, n_head=width // 64, n_layer=depth,
                           n_ff=state["tfmr.layers.0.mlp.gate_proj.weight"].shape[0], n_batch=2,
                           perceiver_len=perceiver, text_vocab_size=state["text_emb.weight"].shape[0],
                           speech_vocab_size=8194, start_text_token=vocab["[START]"], stop_text_token=vocab["[STOP]"],
                           start_speech_token=6561, stop_speech_token=6562, speaker_embed_size=256,
                           rope_orig_ctx=8192, text_frontend_version=4),
                      dict(layer_norm_eps=1e-5, rope_theta=500000.0))
        self.writer.add_string("chatterbox.tokenizer.language_tokens", ",".join(languages))
        for name, tensor in state.items():
            if name in self.DIRECT:
                self.tensor(self.DIRECT[name], tensor)
            elif (match := self.LAYER.match(name)) and match[2] in self.BLOCK:
                suffix = self.BLOCK[match[2]]
                self.tensor(f"model/h{int(match[1])}/{suffix}", tensor, matrix=suffix.endswith("/w"))
        self.rope()
        self.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    parser.add_argument("output")
    parser.add_argument("safetensors")
    parser.add_argument("--matrix-type", required=True, choices=QuantPolicy.WEIGHT_TYPES)
    args = parser.parse_args()
    LlamaConverter(args.checkpoint, args.output, args.safetensors, args.matrix_type).convert()
