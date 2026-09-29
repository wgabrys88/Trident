import argparse
import json
import math
import re
from pathlib import Path

from quant import Policy, TYPES
import gguf
import librosa
import numpy as np
import torch
from safetensors.torch import load_file
from safetensors import safe_open
from tokenizers import Tokenizer


class T3Converter:
    def __init__(self, state, checkpoint, output, safetensors, s3_checkpoint, matrix_type, quant_policy):
        self.checkpoint = Path(checkpoint)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        self.state = state
        self.conditions = torch.load(self.checkpoint / "conds.pt", map_location="cpu", weights_only=True)["t3"]
        self.writer = gguf.GGUFWriter(str(output), self.ARCHITECTURE)
        with safe_open(self.checkpoint / s3_checkpoint, framework="pt") as source:
            self.speech_tokens = source.get_slice("flow.input_embedding.weight").get_shape()[0]
        self.policy = Policy(matrix_type, json.loads(Path(quant_policy).read_text())["rules"])

    def metadata(self, integers, floats):
        for name, value in integers.items():
            self.writer.add_uint32("chatterbox." + name, int(value))
        for name, value in floats.items():
            self.writer.add_float32("chatterbox." + name, value)

    def tensor(self, name, tensor, *, transpose=False):
        array = tensor.detach().cpu().float().numpy()
        self.policy.add(self.writer, name, array.T if transpose else array)

    def finish(self):
        tokens = self.conditions["cond_prompt_speech_tokens"].reshape(-1).to(torch.int32)
        self.metadata({"cond_prompt_max": tokens.numel()}, {})
        self.policy.add(self.writer, "chatterbox/builtin/cond_prompt_speech_tokens", tokens.numpy())
        self.tensor("chatterbox/builtin/speaker_emb", self.conditions["speaker_emb"].reshape(1, -1))
        voice = load_file(self.checkpoint / "ve.safetensors")
        integers = dict(n_mels=voice["lstm.weight_ih_l0"].shape[1], hidden_size=voice["lstm.weight_hh_l0"].shape[1],
                        num_layers=sum(name.startswith("lstm.weight_ih_l") for name in voice), embedding_size=voice["proj.weight"].shape[0],
                        partial_frames=160, sample_rate=16000)
        for name, value in integers.items():
            self.writer.add_uint32("voice_encoder." + name, value)
        for name, value in dict(rate=1.3, min_coverage=0.8).items():
            self.writer.add_float32("voice_encoder." + name, value)
        for name, tensor in voice.items():
            if not name.startswith("similarity_"):
                self.tensor("voice_encoder/" + name.replace(".", "/"), tensor)
        self.policy.add(self.writer, "voice_encoder/mel_fb", np.ascontiguousarray(librosa.filters.mel(
            sr=16000, n_fft=400, n_mels=integers["n_mels"], fmin=0, fmax=8000).astype(np.float32)))
        self.writer.write_header_to_file()
        self.writer.write_kv_data_to_file()
        self.writer.write_tensors_to_file()
        self.writer.close()


class Gpt2Converter(T3Converter):
    ARCHITECTURE = "chatterbox-gpt2"
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
                           n_layer=depth, speech_vocab_size=self.state["speech_emb.weight"].shape[0],
                           start_speech_token=self.speech_tokens, stop_speech_token=self.speech_tokens + 1,
                           speech_pad_token=4299, speech_pad_count=3),
                      dict(layer_norm_eps=1e-5))
        self.tokenizer()
        for name, tensor in self.state.items():
            if name in self.DIRECT:
                self.tensor(self.DIRECT[name], tensor)
            elif (match := self.LAYER.match(name)) and match[2] in self.BLOCK:
                suffix = self.BLOCK[match[2]]
                matrix = suffix.endswith("/w")
                self.tensor(f"model/h{int(match[1])}/{suffix}", tensor, transpose=matrix)
        self.finish()


class LlamaConverter(T3Converter):
    ARCHITECTURE = "chatterbox-llama"
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
        self.policy.add(self.writer, "model/rope_freq_factors", factors)

    def tokenizer(self, vocab):
        data = json.loads((self.checkpoint / "grapheme_mtl_merged_expanded_v1.json").read_text(encoding="utf-8"))
        by_id = {int(index): token for token, index in vocab.items()}
        added = set()
        for item in data.get("added_tokens", []):
            by_id[int(item["id"])] = item["content"]
            added.add(item["content"])
        tokens = [by_id.get(index, "[UNK]") for index in range(max(by_id) + 1)]
        types = [int(gguf.TokenType.CONTROL if token in added else gguf.TokenType.NORMAL) for token in tokens]
        merges = []
        for merge in data["model"]["merges"]:
            merges.append(merge if isinstance(merge, str) else merge[0] + " " + merge[1])
        self.writer.add_tokenizer_model("bpe")
        self.writer.add_token_list(tokens)
        self.writer.add_token_types(types)
        self.writer.add_token_merges(merges)

    def convert(self):
        state = self.state
        vocab = Tokenizer.from_file(str(self.checkpoint / "grapheme_mtl_merged_expanded_v1.json")).get_vocab()
        self.tokenizer(vocab)
        width = state["tfmr.norm.weight"].shape[0]
        perceiver = state["cond_enc.perceiver.pre_attention_query"].shape[1]
        text_positions = state["text_pos_emb.emb.weight"].shape[0]
        depth = 1 + max(int(match[1]) for name in state if (match := self.LAYER.match(name)))
        self.metadata(dict(text_positions=text_positions,
                           n_embd=width, n_layer=depth,
                           perceiver_len=perceiver, perceiver_heads=4,
                           speech_vocab_size=state["speech_emb.weight"].shape[0], start_text_token=vocab["[START]"], stop_text_token=vocab["[STOP]"],
                           start_speech_token=self.speech_tokens, stop_speech_token=self.speech_tokens + 1,
                           rope_orig_ctx=8192, n_ff=int(state["tfmr.layers.0.mlp.gate_proj.weight"].shape[0])),
                      dict(layer_norm_eps=1e-5, rope_theta=500000.0))
        self.writer.add_string("chatterbox.tokenizer.language_tokens", ",".join(sorted(t for t in vocab if re.fullmatch(r"\[[a-z]{2,3}\]", t))))
        for name, tensor in state.items():
            if name in self.DIRECT:
                self.tensor(self.DIRECT[name], tensor)
            elif (match := self.LAYER.match(name)) and match[2] in self.BLOCK:
                suffix = self.BLOCK[match[2]]
                self.tensor(f"model/h{int(match[1])}/{suffix}", tensor)
        self.rope()
        self.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    parser.add_argument("output")
    parser.add_argument("safetensors")
    parser.add_argument("--matrix-type", required=True, choices=TYPES)
    parser.add_argument("--quant-policy", required=True)
    parser.add_argument("--s3-checkpoint", required=True)
    args = parser.parse_args()
    state = load_file(Path(args.checkpoint) / args.safetensors)
    (Gpt2Converter if "tfmr.wpe.weight" in state else LlamaConverter)(state, **vars(args)).convert()
