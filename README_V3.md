# V3 multilingual pipeline

This document describes Trident branch `v3` and its pinned Chatterbox source.
The source audit was performed on 2026-09-11.

## Working milestone and pins

On 2026-09-11 the real launcher rebuilt the pinned C++ source and generated
"The billing issue is resolved." with language `en`. The user listened and
confirmed it worked. The build completed before the output WAV was written;
the build stamp recorded the C++ SHA below. The daemon was subsequently
confirmed stopped. This is evidence for that utterance and local baked models,
not a claim that every language, reference or text has been validated.

| Item | Value |
| --- | --- |
| Lightweight tag in both repositories | `MILESTONE-SET-v3` |
| Trident milestone | `0d95e514c1471f811632f66ccd19035a91c5a430` |
| Chatterbox pin and milestone | `eec82c24e13c8afb7d492127b3f8464378486d76` |
| ggml revision | `7840aaba1989c6deeefede1d77d5aaf8f52b947e` |
| Hugging Face repository | `ResembleAI/chatterbox` |
| Hugging Face revision | `5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18` |

The tag records the working state before the README audit. The C++ pin is in
`tts_v3.py`; source paths below refer to that pin.
The remaining C++ change relative to `cbbbb8c` is the S3Gen convolution
batch-layout fix: matmul preserves the batch axis, then permutes to [T,C,B].
Trident's working correction points the launcher at sibling `chatterbox.cpp`.
The prior README's active garble diagnosis and token-ID-jump heuristic are
superseded by the successful run. Numeric codebook distance alone does not
establish whether generated speech tokens are correct.

## Launch and files

Run `python tts_v3.py "The billing issue is resolved." en` from Trident with
`reference.wav` in the root. The launcher checks out Chatterbox `v3` at the
full pin above and builds into `build/v3`.

| Artifact | Path or construction |
| --- | --- |
| T3 / S3Gen | `models/chatterbox-t3-v3-q8_0.gguf` / `models/chatterbox-s3gen-v3-q4_0.gguf` |
| Checkpoints / conversion environment | `.ckpt-v3` / `.venv-convert-v3` |
| Voice stamp / build revision / PID | `models/v3.voice.sha256` / `models/v3.rev` / `models/v3.pid` |
| Output / token dump | `tts_out_v3.wav` / `models/v3_t3_dump.txt` |
| Pipe | `\\.\pipe\chatterbox-v3-` + first 12 hex characters of SHA256 of `str(ROOT).encode() + b"v3"` |
| Executables | sibling checkout's `build/v3/bin/chatterbox-server.exe` and `chatterbox-bake.exe` |

The launcher always checks out Chatterbox `v3` at the pin. It rebuilds when an
executable or revision stamp is missing, or the stamp differs from the pin.
On rebuild it verifies existing ggml HEAD or clones/checks out missing ggml.
The stamp is not a hash of executable or source contents.
Build commands use Visual Studio 2022 x64 Release, CMake at
`C:/Program Files/CMake/bin/cmake.exe`, and Vulkan SDK `C:/VulkanSDK/1.4.357.0`.

Missing assets are downloaded and missing GGUFs converted. Bake runs after
conversion or a reference SHA256 change. Existing models/checkpoints are reused
without content-hash verification. Conversion installs CPU PyTorch 2.6.0,
NumPy 1.26.4, gguf 0.19.0, safetensors 0.5.3, SciPy 1.15.3 and librosa 0.11.0.

The server holds one Engine and accepts text lines sequentially over a local
named pipe. It writes mono 24 kHz PCM16 through a temporary file, replaces
the destination, then acknowledges `ok\n`. The launcher waits in one-second
pipe calls while the recorded PID is alive and leaves it running after speech.
`kill()` terminates that PID and removes its file; PID checks do not verify
executable identity. Language is fixed when the daemon starts: a later launcher
argument does not change an already-running daemon's language.
There is no TCP service, PCM streaming protocol, or text chunker.

## T3 and tokenizer

CMake links `src/t3_v3.cpp`, `src/mtl_bpe.cpp`,
`src/chatterbox_engine.cpp`, and `src/chatterbox_tts.cpp`.
T3 uses Llama attention, RMSNorm, SwiGLU, learned text/speech positions, NEOX
RoPE with converted Llama3 frequency factors, and a four-head Perceiver.
Layer count, width, feed-forward width and Perceiver length come from GGUF.
The converter derives context as
`1 + perceiver_len + 1 + text_position_table_length + 2 + 1000`.
The prompt includes speaker, Perceiver and baked emotion embeddings, text and
two speech BOS embeddings. CFG runs as batch two with shared token choices;
host logits combine as `cond + 0.5 * (cond - uncond)`.

The converter writes text vocabulary 2454, speech vocabulary 8194, text
start/stop 255/0 and speech start/stop 6561/6562.
The tokenizer applies punctuation normalization, ASCII lowercasing, Windows
NFKD, a language prefix, space markers, added-token matching and BPE.
Its regex splitting is byte-string based; this audit does not establish
Unicode-tokenizer parity for arbitrary multilingual text.
Accepted language IDs are `en ar da de el es fi fr hi it ms nl no pl pt sv sw tr`.
Other IDs, including `zh ja he ko ru`, throw "language extras unread".
Acceptance of an ID is not evidence of intelligible output in that language.

`v3.h` sets seed 42, maximum predictions 1000, temperature 0.8, minimum
probability 0.05, repetition penalty 1.2 over 1000 tokens, T3 CFG 0.5,
ten CFM steps and CFM CFG 0.7. Top-k is 0 and top-p is 1, so their filter
branches are inactive. `EXAGGERATION=0.5` is declared but not read by runtime
code; the graph uses the baked emotion tensor.
T3 resets RNG/context position per utterance, requires EOS, strips SOS/EOS
boundaries, and writes its token dump on every utterance. Dump-write errors
can abort synthesis. The implementation is not free of diagnostic file output.

## S3Gen, conversion and bake

S3Gen uses ten Euler steps with
`t[i] = 1 - cos((i/10) * pi/2)`. Each estimator call has batch two:
both halves receive x; only the conditional half receives mu, speaker and
prompt conditioning. The update uses `1.7 * conditional - 0.7 * unconditional`.
The active path calls the time MLP without the meanflow time mixer.

The working source still appends three 4299 lookahead tokens inside S3Gen and
uses `T_mu = 2 * n_total - 6`. T3 itself does not append silence.
After S3Gen, Engine resizes audio to `max(1, token_count - 1) * 960` samples.
The removed full-length encoder and trim/fade edits are not part of this pin.

The launcher calls `scripts/convert-t3-v3-to-gguf.py` and
`scripts/convert-s3gen-v3-to-gguf.py`, using `t3_mtl23ls_v3.safetensors`
and `s3gen.safetensors`. Other assets are `conds.pt`, `ve.safetensors`,
`grapheme_mtl_merged_expanded_v1.json` and `Cangjie5_TC.json`.
T3 skips `tfmr.embed_tokens.weight` and `text_head.weight`, rejects unmapped
names and checks tokenizer length. S3Gen checks tokenizer size and top-level
prefixes and rejects any `time_embed_mixer` key.
Selected matrices use Q8_0 for T3 and Q4_0 for S3Gen; both contain other types.
The successful run's actual GGUF tensor metadata confirmed these mixed types.

Bake rewrites supplied GGUF paths without enforcing a family name.
It replaces T3 speaker/conditioning tensors and S3Gen prompt tokens, mel
features and speaker embedding. T3 conditioning uses up to six seconds at
16 kHz, capped by GGUF metadata. S3Gen prompt and CAMPPlus use up to ten
seconds. V3's VoiceEncoder path has no 30-second input cap.
The reference stamp matched the reference used for the successful run;
this documentation audit did not rebake it.

## Execution boundaries and reduction findings

CMake forces ggml Vulkan on and CPU/CUDA/OpenMP off; device 0 is selected.
Host C++ still performs tokenization, sampling, CFG-logit combination, CFM
integration, harmonic-source calculation and parts of reference processing.
The pinned code therefore does not perform every arithmetic operation on GPU.

Nano, Turbo, and V3 share the sibling checkout `chatterbox.cpp` and keep
separate model, pipe, stamp, and CMake build paths (`build/nano`, `build/turbo`,
`build/v3`). Each launcher checks out its C++ branch. The three Trident
launchers live on one tree; do not switch Trident branches to pick a family.

Unused Nano leftovers (`t3_nano.cpp`, `gpt2_bpe.cpp/.h`, `nano.h`,
`convert-t3-nano-to-gguf.py`, `convert-s3gen-to-gguf.py`) were deleted
from this branch. Their active equivalents remain on Nano's branch.
Remaining reduction opportunities, not applied:
- `compute_time_mixed` and its cache are uncalled V3 leftovers. They are
  active in Nano/Turbo and cannot be removed there on the same grounds.
- Top-k/top-p sampler branches and F16 CFM KV option plumbing are inactive
  under V3's fixed constants/call sites. The unused exaggeration constant
  and tokenizer's unused reverse-token vector are further candidates.
- Forwarding `s3_*` wrappers, unused load counters, true-or-throw helper
  boolean chains and the tokenizer's identical mel-buffer copy can be reduced.
- Removing the mandatory T3 dump would reduce source and filesystem dependency,
  but also remove an existing diagnostic. That is a behavior change.

Inference graphs, model pins, and the V3 launcher contract are otherwise
unchanged. No dispatcher was introduced. The milestone tags retain the known
working state.
