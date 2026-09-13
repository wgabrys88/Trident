# Trident

Windows front for sibling `chatterbox.cpp`. Three ResembleAI Chatterbox families, three launchers, one tree. Clone this repo next to `chatterbox.cpp`. Put `reference.wav` in this root.

```
python tts_nano.py "Your text."
python tts_turbo.py "Your text."
python tts_v3.py "Your text." en
```

`experimental` is the one-tree rehearsal. All three launchers pin the same chatterbox `experimental` SHA and checkout that branch, not `nano`/`turbo`/`v3`. CMake `-DTTS_FAMILY=<name>` selects the GPT-2 or Llama backend into `chatterbox.cpp/build/<family>` (Visual Studio 2022 x64 Release, Vulkan). Wait on first run. Production family branches are unchanged.

Same family again: the named-pipe daemon stays loaded and the next sentence is immediate. A different family: the launcher kills the other two daemons first so only one model occupies VRAM. V3 language is fixed when that daemon starts.

WAV path is `{YYYYMMDD-HHMMSS}-{nano|turbo|v3}.wav` in this root, named at speak time. Each speak writes a new file.

No TCP, no chunker, no dispatcher. Fail hard.

## Two graphs, not three

Official Python: Nano is Turbo with a smaller GPT-2. V3 is a different stack. Treat Nano and Turbo as one architecture at two sizes. Do not treat V3 as bigger Turbo.

| | Nano | Turbo | V3 (Multilingual) |
| --- | --- | --- | --- |
| Official size | 110M | 350M | 500M |
| Official class | `ChatterboxTurboTTS.from_pretrained(..., nano=True)` | `ChatterboxTurboTTS.from_pretrained(...)` | `ChatterboxMultilingualTTS.from_pretrained(..., t3_model="v3")` |
| HF repo | `ResembleAI/chatterbox-nano` | `ResembleAI/chatterbox-turbo` | `ResembleAI/chatterbox` |
| T3 backbone | GPT-2 small: 12 layers, hidden 768, 12 heads, GELU, learned GPT-2 positions, fused QKV, flash attn | GPT-2 medium: 24 layers, hidden 1024, 16 heads, same GPT-2 rules | Llama 520M: 30 layers, hidden 1024, 16 heads, SwiGLU, RMSNorm, Llama3 RoPE (theta 500000, factor 8, high_freq 4) |
| T3 config | `GPT2_small` | `GPT2_medium` | `Llama_520M` |
| Tokenizer | GPT-2 byte BPE, text vocab 50276 | same | MTL BPE, text vocab 2454, language prefix, ASCII lower, NFKD |
| Speech vocab | 6563, start/stop 6561/6562 | same | 8194, start/stop 6561/6562, text start/stop 255/0 |
| Perceiver | no | no | yes, 4-head, baked emotion embedding |
| T3 CFG | no (dropped for speed; batch-2 doubles compute) | no | yes, `cond + 0.5*(cond-uncond)`, cfg_weight 0.5 |
| Exaggeration | ignored | ignored | baked tensor; local `EXAGGERATION=0.5` unused at runtime |
| S3Gen | meanflow | meanflow | classic CFM, no `time_embed_mixer` |
| CFM steps | 2 Euler (`n_cfm_timesteps=2`; marketing “1-step” is wrong) | same 2 | 10 Euler, `t[i]=1-cos((i/10)*pi/2)`, CFM CFG 0.7 |
| Silence tokens | T3 appends 15×4299; S3Gen adds 3 lookahead and drops 6 encoder frames | T3 appends 3×4299; S3Gen same lookahead | T3 does not append SILENCE_COUNT; S3Gen still has 3×4299 lookahead; audio resized to `max(1, n_tokens-1)*960` |
| Languages | English | English | Official 23. This C++ tree rejects zh ja he ko ru. Accepted: en ar da de el es fi fr hi it ms nl no pl pt sv sw tr |
| Local sampler | seed 42, N_PREDICT 2000, top-k 1000, top-p 0.95, temp 0.8, rep 1.2, REPEAT_LAST_N 200, RAS 10/0.1, CFM 2, SILENCE_COUNT 15 | seed 42, N_PREDICT 1000, top-k 1000, top-p 0.95, temp 0.8, rep 1.2, REPEAT_LAST_N 1000, no RAS, CFM 2, SILENCE_COUNT 3 | seed 42, N_PREDICT 1000, top-k 0, top-p 1, min-p 0.05, temp 0.8, rep 1.2, REPEAT_LAST_N 1000, T3 CFG 0.5, CFM 10, CFM CFG 0.7. No SILENCE_COUNT |
| Local C++ | `t3_nano.cpp` + `gpt2_bpe`, `nano.h`, RAS, KV clear, pause-vs-stop | same GPT-2 graph, `turbo.h`, no RAS | `t3_v3.cpp` + `mtl_bpe`, `v3.h`. Do not compile Nano T3 into a v3 build. |

Isolation on disk: `build/nano`, `build/turbo`, `build/v3`; own GGUFs, pipes, pid files, voice/rev stamps. Never share `build/bin`. This README lives on `experimental` for the one-tree rehearsal. `main` still documents the three family forks.
