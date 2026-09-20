import argparse
import re
from pathlib import Path

import gguf
import librosa
import numpy as np
import torch
from safetensors.torch import load_file
from quant_policy import QuantPolicy


class S3Converter:
    CONFORMER = {
        **{f"{layer}.{suffix}": f"{layer}/{target}" for layer in ("norm_mha", "norm_ff")
           for suffix, target in (("weight", "w"), ("bias", "b"))},
        **{f"self_attn.linear_{layer}.{suffix}": f"attn/{target}/{ending}"
           for layer, target in (("q", "q"), ("k", "k"), ("v", "v"), ("out", "o"))
           for suffix, ending in (("weight", "w"), ("bias", "b"))},
        "self_attn.linear_pos.weight": "attn/pos/w", "self_attn.pos_bias_u": "attn/pos_bias_u",
        "self_attn.pos_bias_v": "attn/pos_bias_v",
        **{f"feed_forward.w_{index}.{suffix}": f"ff/w{index}/{target}" for index in (1, 2)
           for suffix, target in (("weight", "w"), ("bias", "b"))},
    }

    def __init__(self, directory, output, checkpoint, weight_type):
        directory = Path(directory)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        self.state = load_file(directory / checkpoint)
        self.conditions = torch.load(directory / "conds.pt", map_location="cpu", weights_only=True)["gen"]
        self.writer = gguf.GGUFWriter(str(output), "chatterbox-s3gen")
        self.policy = QuantPolicy(weight_type)
        self.writer.add_string("s3gen.conversion.weight_type", weight_type)
        prefixes = {match[1] for name in self.state
                    if (match := re.fullmatch(r"(.+)\.parametrizations\.weight\.original0", name))}
        for prefix in prefixes:
            gain = self.state.pop(prefix + ".parametrizations.weight.original0")
            vector = self.state.pop(prefix + ".parametrizations.weight.original1")
            norm = vector.flatten(1).norm(dim=1).view(-1, *([1] * (vector.ndim - 1)))
            self.state[prefix + ".weight"] = gain * vector / norm

    def add(self, name, value):
        array = value.detach().cpu().numpy() if isinstance(value, torch.Tensor) else value
        force = array.ndim <= 1 or any(part in name for part in (
            "flow/input_embedding", "flow/spk_embed_affine/", "/builtin/", "s3gen/mel_fb/",
            "campplus/", "s3tokv2/", "cfm/", "hift/"))
        self.policy.add(self.writer, name, array, force_f32=force)

    def metadata(self, prefix, integers, floats=None):
        for name, value in integers.items():
            self.writer.add_uint32(prefix + "." + name, int(value))
        for name, value in (floats or {}).items():
            self.writer.add_float32(prefix + "." + name, value)

    def affine(self, source, target):
        self.add(target + "/w", self.state[source + ".weight"])
        self.add(target + "/b", self.state[source + ".bias"])

    def encoder(self):
        self.add("flow/input_embedding", self.state["flow.input_embedding.weight"])
        for source, target in (
            ("flow.spk_embed_affine_layer", "flow/spk_embed_affine"),
            ("flow.encoder_proj", "flow/encoder_proj"),
            ("flow.encoder.embed.out.0", "flow/encoder/embed/linear"),
            ("flow.encoder.embed.out.1", "flow/encoder/embed/norm"),
            ("flow.encoder.pre_lookahead_layer.conv1", "flow/encoder/pre_lookahead/conv1"),
            ("flow.encoder.pre_lookahead_layer.conv2", "flow/encoder/pre_lookahead/conv2"),
            ("flow.encoder.up_layer.conv", "flow/encoder/up_layer/conv"),
            ("flow.encoder.up_embed.out.0", "flow/encoder/up_embed/linear"),
            ("flow.encoder.up_embed.out.1", "flow/encoder/up_embed/norm"),
            ("flow.encoder.after_norm", "flow/encoder/after_norm"),
        ):
            self.affine(source, target)
        for source, target, count in (("encoders", "block", 6), ("up_encoders", "up_block", 4)):
            for index in range(count):
                for suffix, destination in self.CONFORMER.items():
                    self.add(f"flow/encoder/{target}{index}/{destination}",
                             self.state[f"flow.encoder.{source}.{index}.{suffix}"].float())

    def mel(self, name, rate, fft, channels):
        self.add(name, librosa.filters.mel(sr=rate, n_fft=fft, n_mels=channels, fmin=0, fmax=8000).astype(np.float32))

    def campplus(self):
        state = {name.removeprefix("speaker_encoder."): value for name, value in self.state.items()
                 if name.startswith("speaker_encoder.")}
        groups = {name.removesuffix(".running_mean") for name in state if name.endswith(".running_mean")}
        for name, value in state.items():
            prefix, suffix = name.rsplit(".", 1)
            if suffix == "num_batches_tracked":
                continue
            target = "campplus/" + prefix.replace(".", "/")
            if prefix in groups:
                if suffix != "running_mean":
                    continue
                mean = value.float()
                variance = state[prefix + ".running_var"].float()
                gamma = state[prefix + ".weight"].float() if prefix + ".weight" in state else torch.ones_like(mean)
                beta = state[prefix + ".bias"].float() if prefix + ".bias" in state else torch.zeros_like(mean)
                scale = gamma / torch.sqrt(variance + 1e-5)
                self.add(target + "/s", scale)
                self.add(target + "/b", beta - mean * scale)
            else:
                self.add("campplus/" + name.replace(".", "/"), value.float())
        self.metadata("campplus", dict(feat_dim=80, embedding_size=192, growth_rate=32, bn_size=4,
            init_channels=128, block1_layers=12, block2_layers=24, block3_layers=16,
            block1_dilation=1, block2_dilation=2, block3_dilation=2, kernel_size=3,
            seg_pool_len=100, sample_rate=16000))
        low = 1127.0 * np.log(1 + 20.0 / 700.0)
        high = 1127.0 * np.log(1 + 8000.0 / 700.0)
        delta = (high - low) / 81
        bins = 1127.0 * np.log(1 + np.arange(257, dtype=np.float64) * 16000 / 512 / 700.0)
        filters = np.zeros((80, 257), dtype=np.float32)
        for channel in range(80):
            center = low + (channel + 1) * delta
            left, right = center - delta, center + delta
            for index, frequency in enumerate(bins):
                if left <= frequency <= right:
                    filters[channel, index] = ((frequency - left) / (center - left) if frequency <= center
                                                else (right - frequency) / (right - center))
        self.add("campplus/mel_fb_kaldi_80", filters)

    def tokenizer(self):
        for name, value in self.state.items():
            if name.startswith("tokenizer."):
                suffix = name.removeprefix("tokenizer.")
                if suffix not in ("window", "_mel_filters"):
                    self.add("s3tokv2/" + suffix.replace(".", "/"), value.float())
        self.mel("s3tokv2/mel_fb", 16000, 400, 128)
        self.metadata("s3tokv2", dict(n_mels=128, n_audio_state=1280, n_audio_head=20,
            n_audio_layer=6, head_dim=64, mlp_ratio=4, fsmn_kernel=31, fsq_levels=3,
            fsq_dim=8, codebook_size=3 ** 8, conv_stride=2, n_fft=400, hop=160,
            sample_rate=16000, rope_max_pos=2048), dict(rope_theta=10000.0))

    def convert(self):
        self.metadata("s3gen", {"speech_vocab_size": self.state["flow.input_embedding.weight"].shape[0], "input_size": 512, "output_size": 80,
            "encoder.n_blocks": 6, "encoder.up_n_blocks": 4, "encoder.attention_heads": 8,
            "encoder.head_dim": 64, "encoder.ff_size": 2048, "encoder.token_mel_ratio": 2,
            "encoder.pre_lookahead_len": 3, "spk_embed_dim": 192}, {"layer_norm_eps": 1e-12})
        tokens = self.conditions["prompt_token"].reshape(-1).to(torch.int32)
        features = self.conditions["prompt_feat"].squeeze(0).float()
        self.metadata("s3gen.builtin", dict(prompt_token_len=tokens.numel(), prompt_feat_frames=features.shape[0]))
        self.add("s3gen/builtin/prompt_token", tokens)
        self.add("s3gen/builtin/prompt_feat", features)
        self.add("s3gen/builtin/embedding", self.conditions["embedding"].squeeze(0).float())
        self.encoder()
        for prefix, target in (("flow.decoder.estimator.", "cfm/"), ("mel2wav.", "hift/")):
            for name in sorted(name for name in self.state if name.startswith(prefix)):
                self.add(target + name.removeprefix(prefix).replace(".", "/"), self.state[name].float())
        self.mel("s3gen/mel_fb/24k_80", 24000, 1920, 80)
        self.campplus()
        self.tokenizer()
        self.writer.write_header_to_file()
        self.writer.write_kv_data_to_file()
        self.writer.write_tensors_to_file()
        self.writer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("output")
    parser.add_argument("--checkpoint", required=True, choices=("s3gen_meanflow.safetensors", "s3gen.safetensors"))
    parser.add_argument("--weight-type", required=True, choices=QuantPolicy.WEIGHT_TYPES)
    args = parser.parse_args()
    S3Converter(args.directory, args.output, args.checkpoint, args.weight_type).convert()
