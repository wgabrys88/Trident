# Turbo pipeline

This document describes Trident branch `turbo` and its pinned Chatterbox source.
The source audit was performed on 2026-09-11.

## Working milestone and pins

The user reported all three pipelines working before the milestone tags were
created. No new Turbo synthesis or benchmark was run for this source audit.

| Item | Value |
| --- | --- |
| Lightweight tag in both repositories | `MILESTONE-SET-turbo` |
| Trident milestone | `35bee5e1cf491ed4e31998389e635a13385a645d` |
| Chatterbox pin and milestone | `aec122a248b1fcc6670c0ef7b130d42671beb459` |
| ggml requested revision | `7840aaba1989c6deeefede1d77d5aaf8f52b947e` |
| Hugging Face repository | `ResembleAI/chatterbox-turbo` |
| Hugging Face revision | `749d1c1a46eb10492095d68fbcf55691ccf137cd` |

The tag identifies the pre-audit state, not the latest documentation commit.
The C++ pin is in `tts_turbo.py`. Source paths below refer to that pin.

## Launch and files

Run `python tts_turbo.py "The billing issue is resolved."` on Trident `turbo`,
with `reference.wav` in the Trident root and sibling `chatterbox-turbo.cpp`
on Chatterbox `turbo` at the pin above. No language argument is passed.

| Artifact | Path or construction |
| --- | --- |
| T3 / S3Gen | `models/chatterbox-t3-turbo-q8_0.gguf` / `models/chatterbox-s3gen-turbo-q4_0.gguf` |
| Checkpoints / conversion environment | `.ckpt-turbo` / `.venv-convert-turbo` |
| Voice stamp / build revision / PID | `models/turbo.voice.sha256` / `models/turbo.rev` / `models/turbo.pid` |
| Output | `tts_out_turbo.wav` |
| Pipe | `\\.\pipe\chatterbox-turbo-` + first 12 hex characters of SHA256 of `str(ROOT).encode() + b"turbo"` |
| Executables | sibling checkout's `build/bin/chatterbox-server.exe` and `chatterbox-bake.exe` |

The launcher checks C++ branch and HEAD only when rebuilding because an
executable/revision stamp is missing or the stamp differs from the pin.
It clones and pins missing ggml, but does not verify an existing ggml checkout.
Build commands use Visual Studio 2022 x64 Release, CMake at
`C:/Program Files/CMake/bin/cmake.exe`, and Vulkan SDK `C:/VulkanSDK/1.4.357.0`.

Missing assets are downloaded and missing GGUFs converted. Bake runs after
conversion or a change to the reference SHA256 stamp. Existing checkpoint and
GGUF contents are not hash-verified. Conversion uses CPU PyTorch 2.6.0, NumPy
1.26.4, gguf 0.19.0, safetensors 0.5.3, SciPy 1.15.3 and librosa 0.11.0.

The launcher waits for the pipe in one-second calls while the recorded PID is
alive. The server holds one Engine, handles text lines sequentially, writes
mono 24 kHz PCM16 through a temporary WAV, replaces the destination, then
acknowledges `ok\n`. It remains alive after synthesis; launcher `kill()`
terminates the recorded PID and removes its file. PID checks do not verify
executable identity. There is no TCP service, PCM streaming, or text chunker.

## Model path

CMake compiles `src/t3_nano.cpp` and `src/gpt2_bpe.cpp` for Turbo.
Despite the filename, that GPT-2 graph reads its dimensions from Turbo GGUF
metadata and its learned position table. It uses LayerNorm, fused QKV,
GELU and flash attention. It has no Llama RoPE, Perceiver or T3 CFG path.

The Turbo converter derives layer count and width from checkpoint tensors,
heads as width / 64, and context from the position table. It does not read
a model YAML. It enforces a 50276-entry tokenizer, writes speech vocabulary
6563 and start/stop IDs 6561/6562, skips `tfmr.wte.weight` and
`text_head.weight`, and rejects other unmapped names.
The GPT-2 tokenizer normalizes punctuation and applies byte mapping, regex
splitting, added-token matching and BPE.

`include/tts-cpp/chatterbox/turbo.h` sets seed 42, maximum predictions 1000,
top-k 1000, top-p 0.95, temperature 0.8, repetition penalty 1.2 over the last
1000 tokens, two CFM steps and three silence tokens of ID 4299.
T3 resets RNG/context position each utterance, requires EOS, filters speech
IDs and appends three silence tokens. S3Gen separately adds three lookahead
tokens, then removes six encoder frames. Meanflow uses a two-step linear
schedule, both endpoint time embeddings and `time_embed_mixer`, without CFG.

## Conversion and reference bake

The launcher invokes `scripts/convert-t3-turbo-to-gguf.py` and
`scripts/convert-s3gen-to-gguf.py`. Inputs are `t3_turbo_v1.safetensors`
and `s3gen_meanflow.safetensors`, plus `conds.pt`, `ve.safetensors`,
`vocab.json`, `merges.txt` and `added_tokens.json`.
Selected T3 matrices use Q8_0 and selected S3Gen weights use Q4_0;
other tensors remain floating-point or integer.
Both Turbo converters print checkpoint keys/shapes. The S3Gen converter
checks allowed top-level prefixes; it is not an exhaustive shape validator.

Bake rewrites the supplied T3/S3Gen GGUF paths, without a family-name check.
It replaces T3 speaker embedding/conditioning tokens and S3Gen prompt tokens,
mel features and speaker embedding. T3 conditioning uses up to 15 seconds at
16 kHz, capped by GGUF metadata; S3Gen prompt and CAMPPlus windows are at most
10 seconds. VoiceEncoder input is capped at 30 seconds after 16 kHz resampling.
See `src/bake.cpp` and `src/main.cpp`.

## Execution boundaries and isolation

CMake forces ggml Vulkan on and CPU/CUDA/OpenMP off; backend initialization
selects Vulkan device 0. Host C++ still handles tokenization, sampling,
resampling, loudness normalization, portions of CAMPPlus, speech quantization,
CFM integration and harmonic-source calculations. The implementation therefore
does not support the old claim that host RAM holds only text and WAV bytes.

Turbo has separate model, checkpoint, environment, PID, pipe, output and build
paths. Its own `tts_turbo.py` is the pipeline entry point. The older
`tts_nano.py` retained on this branch checks out its Nano SHA during rebuild;
it does not use the branch-checking Nano launcher found on Trident `nano`.

## Source reduction findings

These opportunities were identified without changing runtime source:

- `include/tts-cpp/chatterbox/nano.h` and
  `scripts/convert-t3-nano-to-gguf.py` are not used by the Turbo build/launcher:
  140 lines, 7382 bytes in the milestone Git blobs.
- The retained Trident `tts_nano.py` adds another 118 lines outside this
  pipeline. Removing this obsolete entry point would leave Turbo independent.
- `t3_nano.cpp` and `gpt2_bpe.cpp` are active Turbo sources and are not
  removable leftovers.
- The only estimator call passes `f16_kv_attn=false`; its option plumbing,
  pass-through `s3_*` wrappers, unused counters and the tokenizer's identical
  mel-buffer copy are reduction candidates within this branch.
- True-or-throw bake helper chains contain redundant boolean propagation.
  Functions with real false-return paths must be distinguished before editing.

These counts exclude documentation and generated artifacts. No claim of
runtime speedup follows from deleting unused source.
