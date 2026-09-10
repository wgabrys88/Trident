# Nano pipeline

This document describes Trident branch `nano` and its pinned Chatterbox source.
The source audit was performed on 2026-09-11. The same family documents are
kept on all three pipeline branches; each describes its named branch.

## Working milestone and pins

The user reported all three pipelines working before the milestone tags were
created. This audit reads source; it does not establish new audio-quality or
performance results.

| Item | Value |
| --- | --- |
| Lightweight tag in both repositories | `MILESTONE-SET-nano` |
| Trident milestone | `43ad6be7e2da6b30e4193f3d6ac257bded6023b2` |
| Chatterbox pin and milestone | `56b80874a8fcd61e312d25ddc49da38685a4771c` |
| ggml requested revision | `7840aaba1989c6deeefede1d77d5aaf8f52b947e` |
| Hugging Face repository | `ResembleAI/chatterbox-nano` |
| Hugging Face revision | `71ccd1d0081b430592cea481f4307e764e07bc64` |

The tag identifies the pre-audit state, not the latest documentation commit.
The C++ pin is in `tts_nano.py`. Source references below are relative to the
pinned Chatterbox repository.

## Launch and files

Run `python tts_nano.py "The billing issue is resolved."` on Trident `nano`,
with `reference.wav` in the Trident root and sibling `chatterbox.cpp` on
Chatterbox `nano` at the pin above. No language argument is passed.

| Artifact | Path or construction |
| --- | --- |
| T3 / S3Gen | `models/chatterbox-t3-nano-q8_0.gguf` / `models/chatterbox-s3gen-nano-q4_0.gguf` |
| Checkpoints / conversion environment | `.ckpt` / `.venv-convert` |
| Voice stamp / build revision / PID | `models/voice.sha256` / `models/rev` / `models/server.pid` |
| Output | `tts_out.wav` |
| Pipe | `\\.\pipe\chatterbox-` + first 12 hex characters of SHA256 of `str(ROOT).encode()` |
| Executables | sibling checkout's `build/bin/chatterbox-server.exe` and `chatterbox-bake.exe` |

The launcher builds when an executable or revision stamp is missing, or the
stamp differs from the pin. Only on this path does it check C++ branch and HEAD.
It clones and checks out ggml if missing; it does not check an existing ggml
checkout's revision. A matching stamp is not a source or executable hash check.

Build commands target Visual Studio 2022 x64 Release, CMake at
`C:/Program Files/CMake/bin/cmake.exe`, and Vulkan SDK `C:/VulkanSDK/1.4.357.0`.
The launcher downloads missing assets, converts missing GGUFs, and bakes when
conversion occurred or the reference SHA256 differs from the voice stamp.
Existing checkpoints and GGUFs are reused without content-hash verification.
The conversion environment installs CPU PyTorch 2.6.0, NumPy 1.26.4, gguf 0.19.0,
safetensors 0.5.3, SciPy 1.15.3, and librosa 0.11.0.

The server holds one Engine and handles complete text lines sequentially over
a local named pipe. It writes mono 24 kHz PCM16 through a temporary WAV and
replaces the output before acknowledging `ok\n`. The launcher makes one
`WaitNamedPipeW(..., 120000)` call. It leaves the daemon running after speaking.
Its `kill()` function terminates the recorded PID and removes the PID file.

## Model path

CMake links `src/t3_nano.cpp`, `src/gpt2_bpe.cpp`,
`src/chatterbox_engine.cpp`, and `src/chatterbox_tts.cpp` into the server library.
T3 uses GPT-2 learned positions, LayerNorm, fused QKV, GELU, and flash attention.
Layer count and width come from GGUF; context length comes from the position
table. The converter derives these dimensions from checkpoint tensors.
There is no Perceiver or T3 classifier-free guidance path.

The GPT-2 tokenizer uses punctuation normalization, byte-to-Unicode mapping,
regex splitting, added tokens and BPE merges. The converter writes text-vocab
metadata 50276 and speech-vocab metadata 6563; speech start/stop are 6561/6562.
It does not validate tokenizer length against 50276 and silently skips unmapped
checkpoint names. Earlier documentation claimed checks that are absent.

`include/tts-cpp/chatterbox/nano.h` sets seed 42, maximum predictions 1000,
top-k 1000, top-p 0.95, temperature 0.8, repetition penalty 1.2 over the last
1000 tokens, two CFM steps, and three silence tokens of ID 4299.
T3 resets its RNG and context position for each utterance and throws without EOS.
It filters speech IDs and appends three silence tokens. S3Gen adds three more
lookahead tokens internally and discards the corresponding six encoder frames.
The two-step meanflow schedule is linear, uses both endpoint time embeddings
and `time_embed_mixer`, and has no CFM guidance batch.

## Conversion and reference bake

The launcher uses `scripts/convert-t3-nano-to-gguf.py` and
`scripts/convert-s3gen-to-gguf.py`. T3 selected matrices are Q8_0; S3Gen selected
weights are Q4_0 under `quant_policy.py`. Both files also contain other types.
S3Gen reads `s3gen_meanflow.safetensors`; T3 reads `t3_nano_v1.safetensors`.
Other downloaded assets are `conds.pt`, `ve.safetensors`, `vocab.json`,
`merges.txt`, and `added_tokens.json`.

Bake rewrites the two GGUF paths supplied on its command line. It has no
family-name check. It replaces the speaker embedding and conditioning tokens
in T3, and prompt tokens, mel features and speaker embedding in S3Gen.
Conditioning tokens use up to 15 seconds at 16 kHz, capped by GGUF metadata.
S3Gen prompt tokens/features and CAMPPlus use up to 10 seconds.
VoiceEncoder input is capped at 30 seconds after resampling to 16 kHz.
These windows are implemented in `src/main.cpp` and `src/bake.cpp`.

## Execution boundaries and isolation

CMake forces the ggml Vulkan backend on and CPU/CUDA/OpenMP off. Backend
initialization selects Vulkan device 0. This does not mean all computation is
on the GPU: tokenization, sampling, resampling, loudness normalization, parts
of CAMPPlus, speech quantization, CFM updates and harmonic-source calculations
contain host C++ loops. Conversion also uses host PyTorch.
There is no TCP service, PCM streaming protocol, or text chunker in this code.

Nano and V3 both name their sibling checkout `chatterbox.cpp`. Their model,
pipe and stamp names differ, but that shared checkout/build directory is not
independent if branches are switched in place. Separate workspace pairs keep
their binaries separate. The retained `tts_nano.py` on Trident `turbo` and
`v3` is an older copy that checks out a SHA during rebuild; it is not the
Nano launcher described here. Trident `main` also retains that older copy.

## Source reduction findings

These are inspected opportunities, not changes applied by this audit:

- `src/chatterbox_tts.cpp` passes `false` at the only estimator call, so its
  `f16_kv_attn` option and conditional copies are unused by this pipeline.
- Four `s3_*` tensor/allocation wrappers only forward to ggml. Unused load
  counters and unused parameters add source without changing computation.
- Some bake loaders return true or throw, while callers still propagate an
  impossible false result. These particular boolean chains can be simplified;
  other functions have real false returns and require separate treatment.
- `src/s3tokenizer.cpp` copies `mel` into `mel_time_major` using identical
  indices before upload. The copy performs no transpose.
- Keeping each branch's launcher, model graph, sampler and bake windows local
  preserves independence; a shared runtime dispatcher is not needed for these
  reductions.

No runtime source, model, build artifact, or pin was changed for this audit.
