import json
import math
from pathlib import Path

import gguf
import librosa
import numpy as np
import torch
from safetensors.torch import load_file
from safetensors import safe_open


class QuantPolicy:
    WEIGHT_TYPES = tuple(json.loads(Path(__file__).with_name("precision_policy.json").read_text())["weight_types"])

    def __init__(self, weight_type):
        if weight_type not in self.WEIGHT_TYPES:
            raise ValueError(f"unsupported weight type: {weight_type}")
        self.weight_type = weight_type

    def add(self, writer, name, array, *, force_f32=False):
        array = np.ascontiguousarray(array)
        if array.dtype.kind in "iu":
            writer.add_tensor(name, array)
        elif force_f32 or self.weight_type == "f32":
            writer.add_tensor(name, array.astype(np.float32))
        elif self.weight_type == "q4_0" and array.ndim == 2 and math.prod(array.shape) >= 1024 and array.shape[-1] % gguf.GGML_QUANT_SIZES[gguf.GGMLQuantizationType.Q4_0][0] == 0:
            kind = gguf.GGMLQuantizationType.Q4_0
            packed = gguf.quants.quantize(array.astype(np.float32), kind)
            writer.add_tensor(name, packed, raw_shape=packed.shape, raw_dtype=kind)
        else:
            writer.add_tensor(name, array.astype(np.float16))


class T3Converter:
    def __init__(self, checkpoint, output, safetensors, matrix_type):
        self.checkpoint = Path(checkpoint)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        self.state = load_file(self.checkpoint / safetensors)
        self.conditions = torch.load(self.checkpoint / "conds.pt", map_location="cpu", weights_only=True)["t3"]
        self.writer = gguf.GGUFWriter(str(output), "chatterbox")
        s3 = "s3gen_meanflow.safetensors" if "tfmr.wpe.weight" in self.state else "s3gen.safetensors"
        with safe_open(self.checkpoint / s3, framework="pt") as source:
            self.speech_tokens = source.get_slice("flow.input_embedding.weight").get_shape()[0]
        self.policy = QuantPolicy(matrix_type)
        self.writer.add_string("chatterbox.conversion.matrix_type", matrix_type)

    def metadata(self, integers, floats):
        for name, value in integers.items():
            self.writer.add_uint32("chatterbox." + name, int(value))
        for name, value in floats.items():
            self.writer.add_float32("chatterbox." + name, value)

    def tensor(self, name, tensor, *, matrix=False, transpose=False):
        array = tensor.detach().cpu().float().numpy()
        self.policy.add(self.writer, name, array.T if transpose else array, force_f32=not matrix)

    def finish(self):
        tokens = self.conditions["cond_prompt_speech_tokens"].reshape(-1).to(torch.int32)
        self.metadata({"cond_prompt_max": tokens.numel(), "cond_prompt_length": tokens.numel()}, {})
        self.writer.add_tensor("chatterbox/builtin/cond_prompt_speech_tokens", tokens.numpy())
        self.tensor("chatterbox/builtin/speaker_emb", self.conditions["speaker_emb"].reshape(1, 256))
        integers = dict(n_mels=40, hidden_size=256, num_layers=3, embedding_size=256,
                        partial_frames=160, sample_rate=16000, n_fft=400, hop_size=160, win_size=400)
        for name, value in integers.items():
            self.writer.add_uint32("voice_encoder." + name, value)
        for name, value in dict(overlap=0.5, rate=1.3, min_coverage=0.8).items():
            self.writer.add_float32("voice_encoder." + name, value)
        for name, tensor in load_file(self.checkpoint / "ve.safetensors").items():
            if not name.startswith("similarity_"):
                self.tensor("voice_encoder/" + name.replace(".", "/"), tensor)
        self.writer.add_tensor("voice_encoder/mel_fb", np.ascontiguousarray(librosa.filters.mel(
            sr=16000, n_fft=400, n_mels=40, fmin=0, fmax=8000).astype(np.float32)))
        self.writer.write_header_to_file()
        self.writer.write_kv_data_to_file()
        self.writer.write_tensors_to_file()
        self.writer.close()
