# Trident

Windows x64 speech stack: Parakeet ASR, Gemma (llama.cpp Vulkan), Chatterbox TTS. This file is the handover. A new agent with no chat history should be able to install, probe, and know what is still broken.

Git tracks **source, patches, C++, and this README**. Not binaries, GGUFs, wavs, venv, or `third_party/`. A clone is supposed to become compilable through `python main.py install`.

## Machine this branch was tuned on

- OS: Windows x64
- GPU: NVIDIA GeForce GTX 1060 6GB, Pascal, compute 6.1
- Vulkan banner you must treat as law: `fp16=0 | bf16=0 | warp size: 32 | matrix cores: none`
- Every f16 GGUF on this card is **storage**. Math is fp32. Do not "enable fp16". Do not assume flash-attn tensor cores exist.
- Branch: `runner-x`

## Clone to a new directory

```
git clone <this-repo> Trident
cd Trident
git checkout runner-x
python main.py install --family v3
python main.py probe --family v3 --language en
```

Install clones pinned `chatterbox.cpp` + ggml, applies `patches/chatterbox-*.patch` in sorted order, builds `tts-cpp` and the three family CLIs (`trident-tts-v3`, `trident-tts-turbo`, `trident-tts-nano`), fetches GGUFs (v3 T3 is a convert: v3 weights copied onto v2 filenames because the converter still looks for `t3_mtl23ls_v2.safetensors`), copies runtime binaries to `tools/runtime/tts/`, and writes `tools/runtime/tts/.build-stamp`.

That stamp is a hash of the chatterbox revision, ggml revision, every `patches/chatterbox-*.patch`, and every `tts/` `.cpp`/`.hpp`/`CMakeLists.txt`. If you change a patch or wrapper, the next `install` rebuilds even if the old `.exe` is sitting there. Stale-binary skip is a past bug.

Do not run leftover `tools/runtime/tts/trident-tts.exe`. That is the old unified binary. Family isolation is three names.

## Are we better than yesterday?

**No. Not on the metric that matters (v3 English Rainbow), and we did not re-run Rainbow today.** Be honest in the next review.

What got **correct**:

- Language IDs. v3 encode is now `255 708 … 0` for English (`[de]=636`, `[pl]=717`). Engine used to skip SOT/EOT that Python and tts-cli pad. German without that pad was English-accented mush.
- v3 `top_k=0`. Passing `top_k=1000` into MTL sampling is a bug. The v3 CLI rejects `--top-k`.
- Logging. Stderr now tells the pipeline from text file to last sample. Rainbow capture reprints the whole log, not two prefixes.
- Install stamp. A clone plus `install` is supposed to compile the wrappers and patches you actually have.

What got **worse or not recovered**:

- Official pad + official `top_p=1.0` + `top_k` off made v3 English Rainbow **worse** than the old illegal combo (top_k 1000, no SOT/EOT): about 149 s / 81% speech / 97 gaps vs the previous ~123 s / ~86% speech. Parakeet WER ~4% on the old file is gossip, not a target.
- Today's one-sentence English, same reference, seed 42, exag 0.3, cfm 7, chunk 180, SOT/EOT on:

  | run | seconds | speech% | gaps | gapMax | peak |
  |---|---:|---:|---:|---:|---:|
  | nano (frozen gold for density) | 5.16 | 97.6 | 1 | 0.13 | 0.43 |
  | v3 top_p 1.0 | 7.24 | 82.8 | 3 | 0.66 | 0.41 |
  | v3 top_p 0.95 | 6.84 | 85.5 | 3 | 0.40 | 0.38 |
  | v3 top_p 0.95, three sentences | 25.81 vs nano 17.08 | 87.6 | 10 | 0.58 | **0.99** |

- `top_p=0.95` is a small win on one sentence. It is **not shipped** in `cfg.py` (`_V3_SAMPLE` still has official `top_p=1.0`). Three-sentence v3 is still ~1.5× nano and slammed 0.99. Do not call that mastered.
- cfg_weight=0 (Resemble's cross-language trick) looped German to ~372 s. Do not use it until English-on-English is boring.
- We did not replace `data/default-reference.wav`. It is 15 s / 48 kHz / mono / 85% speech / no clip. Usable, not proven optimal.

Nano English Rainbow (~102.5 s, ~93.5% speech) is the **density floor**, not a clone target. v3 is a different model. It may be longer. It must not double, hole, or clip.

## Logging contract

Every family binary prints `tts …` lines on stderr. `python main.py rainbow` and `probe` dump the whole stream.

A healthy v3 English probe must contain:

```
tts text ... bom=0
tts family=v3
ggml_vulkan: 0 = NVIDIA GeForce GTX 1060 6GB ... fp16: 0 ... matrix cores: none
tts backend=Vulkan gpu_layers=99
tts engine variant=t3_mtl ... voice_overridden=1 ... ref=<your wav>
tts mtl encode lang=en ... ids=255 708 ...
tts t3 speech_tokens=... eos=1 cap=0 max=768
tts wav ... samples=...
family=v3 tts done ... rtf=...
```

Poison in that log:

- `bom=1` then token `1` (`[UNK]`) — PowerShell `Set-Content -Encoding utf8`. The CLI now strips a leading UTF-8 BOM, but write files with Python `utf-8` no BOM.
- `tts backend=CPU fallback`
- `voice_overridden=0` while you passed a reference
- encode first ids not `255 708` for English
- `cap=1` (hit max_tokens, likely looping)

Gemma/llama-cli (brain path) is **not** quiet anymore: `--verbosity 0 --show-timings --perf --log-prefix --log-timestamps`. `--verbose` on llama.cpp is infinity debug; do not use it as the daily driver. `--list-devices` shows `Vulkan0`. The repo talks to `llama-cli.exe`, not `llama-server.exe`. Same flags exist on the server binary.

## One-sentence probe (do this, not Rainbow)

Full Rainbow is a graduation exam. Daily driver:

```
python main.py probe --family v3 --language en
```

That writes UTF-8 no BOM, runs `trident-tts-v3.exe`, and fails if Vulkan/encode/voice/cap lines are missing. Compare duration to nano:

```
python main.py probe --family nano --language en
.venv\Scripts\python.exe rainbow\audio_report.py
```

`audio_report.py` looks up names under `rainbow/runs/` unless you copy the probe wavs there. Gold for density: `nano-en-iso-p.wav` if present.

One-factor sampling only. Keep SOT/EOT. Candidate still on the table: ship `top_p=0.95` in `cfg.py` after a three-sentence win without 0.99 peak. Do not change pack/glue/sampling in the same commit.

Judge: `rainbow/audio_report.py` (librosa/scipy). **Not Parakeet.**

## Patches (sorted apply)

1. `patches/chatterbox-trident.patch` — host integration
2. `patches/chatterbox-z-mtl-sot-eot.patch` — pad `start_text` + ids + `stop_text`
3. `patches/chatterbox-z-pipeline-log.patch` — always-on backend/voice/T3-cap logs + Vulkan banner without kernel spam

If you edit `third_party/chatterbox.cpp` and skip updating the patch, the next install `git reset --hard` + `clean -fdx` wipes you.

## Code review — next agent, start here

Run `/review` or `/code-review` on `runner-x` vs `origin/main`. Do not rubber-stamp "it works".

Open quality problems:

1. `tts/src/main_turbo.cpp` and `main_nano.cpp` are the same CLI copied twice. Isolation of the **allowlist** is justified; triplicating `main()` is not. v3 is the only different surface.
2. `main.py` `synthesize()` still dispatches `--language`/`cfg`/`exag` on the literal exe name `trident-tts-v3.exe`. Policy should live in `cfg.py` only.
3. Dead Parakeet rainbow scorer: `resample_16k`, `score`, unused `transcribe(..., out=)`. `cfg.py` still holds `RAINBOW` next to model tables.
4. `EngineOptions` defaults are turbo-shaped (`top_k=1000`, `top_p=0.95`). Wrappers paper over it. A caller that forgets `top_k=0` on v3 is wrong again.
5. Quiet-edge glue (`amp 0.02`) can bite soft English onsets at chunk joins. Three-sentence v3 hole around 8–10 s was **inside** a chunk (T3 ramble), not glue. Still audit glue if "the / There / These" vanish.
6. Official Python `exaggeration=0.5`; we ship `0.3`. Official `top_p=1.0`; the better short-run today was `0.95`. Do not "fix" both at once.
7. GGUF convert still copies v3 tensors onto v2 filenames. Metadata on disk is correct (`chatterbox.variant=t3_mtl`, `start_text_token=255`, `stop_text_token=0`, `start_speech_token=6561`, `stop_speech_token=6562`, llama 520M 30×1024). If English is garbage, dump keys before twiddling temperature.
8. CFM `< 5` on v3 is clamped back up. `--cfm-steps 2` does not speed v3 up.
9. `python main.py install --family v3` is the only supported rebuild. Do not hand-copy a random `trident-tts.exe`.

## Poison

- Parakeet as the optimization target
- Full Rainbow as the first run of the day
- Grid-searching turbo/nano "while you are there"
- PowerShell UTF-8 files
- Re-converting GGUF with the wrong revision or skipping the v3→v2 copy map
- Assuming f16 GGUF means f16 Vulkan compute on a 1060
- cfg_weight=0 on long text
- Changing pack_text, glue, and sampling in one commit

## Campaign brief

Long-form v3 English instructions: `rainbow/V3_ENGLISH_PASCAL_AGENT_PROMPT.md`. Master English on Pascal first. German/Polish after English is boring.

## Commands

```
python main.py install --family v3
python main.py probe --family v3 --language en
python main.py rainbow rainbow/runs/v3-en.wav --family v3 --language en
python main.py run input.wav output.wav --family v3 --language en
```

Eval venv (create if missing): librosa scipy matplotlib numpy soundfile tokenizers. GGUF dump lives in `tools/convert/Scripts/gguf-dump.exe` after convert venv exists.
