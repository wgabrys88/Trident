# Trident v3-only campaign — fresh-agent briefing

You are a coding agent with **no prior chat**. This file is the entire memory. Work like a mixture of experts: keep a tokenizer person, a Pascal-Vulkan person, a GGUF-convert person, a sampling person, and an audio-forensics person in your head. They argue. You only ship what survives that argument. **Do not touch turbo or nano except as frozen gold audio.** Master **Chatterbox Multilingual V3 English on this Pascal box first.** German and Polish come after English is boringly good.

Repo: `C:\Users\px-wjt\Downloads\Trident`  
Branch: `runner-x` (pushed). Latest relevant commit isolates three family CLIs and adds the MTL start/stop text pad. **Git tracks source, patches, and C++. Binaries are not in git. Always rebuild after patch or wrapper changes.**  
OS: Windows x64. GPU: NVIDIA GeForce GTX 1060 6GB, Pascal, compute 6.1.  
Vulkan banner you must treat as law: `fp16=0 | bf16=0 | shared=48KB | warp=32 | matrix cores=none`. Every fp16 tensor on this card is **fp32 math with an f16 file**. That is not a maybe.

---

## How to think (the MoE loop)

Each cycle is short. Full Rainbow Passage is a **graduation exam**, not a daily driver. One sentence, then a paragraph, then Rainbow.

1. **Tokenizer expert.** Before you believe audio, print encode ids. For English you must see start-text `255`, then language marker `[en]=708`, then content, then stop-text `0`. German is `[de]=636`, Polish `[pl]=717`. If you see token `1` (`[UNK]`) at the front, you wrote a UTF-8 BOM (PowerShell `Set-Content -Encoding utf8` does this). Python `write_text` utf-8 without BOM is the only safe write.
2. **Vulkan/Pascal expert.** Ask: did this change actually run on GPU? Did ggml_vulkan print a CPU fallback? Is S3Gen f16 GGUF silently doing fp32? Never “enable fp16” on this GPU. Never assume flash-attn / matrix cores exist. `n_gpu_layers=99` means “try to put the whole T3 on Vulkan0.” Confirm in stderr, do not assume.
3. **GGUF expert.** The v3 T3 file is `models/chatterbox-t3-mtl-v3-q4_0.gguf` from ResembleAI/chatterbox revision `5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18`. Convert script is `convert-t3-mtl-to-gguf.py`. **The checkpoint on disk is v3 weights renamed to v2 filenames** because the converter still looks for `t3_mtl23ls_v2.safetensors` and `s3gen.pt`. That copy map is intentional, not a typo — but you must verify the GGUF metadata actually says multilingual / t3_mtl, tokenizer JSON is embedded, start_text=255, stop_text=0, speech start/stop around 6561/6562. S3Gen is `chatterbox-s3gen-mtl-v3-f16.gguf`, variant `mtl`, quant f16. On Pascal that f16 is a storage format. If English is garbage, **re-dump GGUF keys and compare to the convert script and the HuggingFace model card before you twiddle temperature.**
4. **Sampling expert.** Official Python multilingual `generate()`: temperature 0.8, repetition_penalty 1.2, min_p 0.05, **top_p 1.0**, **no top_k**, cfg_weight 0.5, exaggeration 0.5 by their README, we currently ship exaggeration 0.3 and cfm_steps 7 (chatterbox.cpp quality knee vs 10). max tokens 768 (NVIGI) not 1000. **top_k=1000 on v3 is a bug, not a default.** The v3 binary forces top_k=0. Do not pass `--top-k` to it. cfg_weight=0 is the official **cross-language** trick (English speaker, German text). On this machine it **looped to six minutes**. Do not use cfg=0 until English-on-English is solid, and then only on a one-sentence probe.
5. **Audio forensics expert (the judge).** **Do not use Parakeet for this campaign.** It already called German 94% WER on audio that may have been English-accented mush, and it hid real failures behind a single number. Use the project `.venv` (create if missing) with `librosa scipy matplotlib numpy soundfile tokenizers`. Run `rainbow/audio_report.py`. Gold for English duration/speech density is **nano English Rainbow: about 102.5 s, ~93.5% speech, ~31 gaps, RMS ~0.081, no clipping.** v3 English will be longer than nano (different model); treat nano as a **sanity floor**, not a clone target. Trust: duration, speech fraction, gap count, max gap, clip fraction, spectral centroid, onset rate. Listen with your eyes on a **seconds-axis** energy plot, not a postage-stamp mel dump.

You are allowed to web-search whenever a knob, GGUF field, Vulkan flag, or Resemble tokenizer behavior is in doubt. Search ResembleAI chatterbox, chatterbox.cpp, ggml vulkan Pascal, NVIGI Chatterbox 768 tokens, official cfg_weight cross-language.

---

## Machine and pipeline, start to last sample

Command that matters:

`tools/runtime/tts/trident-tts-v3.exe`  
`--model models/chatterbox-t3-mtl-v3-q4_0.gguf`  
`--s3gen-gguf models/chatterbox-s3gen-mtl-v3-f16.gguf`  
`--reference` a **mono, >=5 s** wav (today: `data/default-reference.wav`, bundled from `assets/default-reference.wav`, English speaker)  
`--text-file` UTF-8 **no BOM**  
`--language en` (must be the two-letter code the tokenizer prefixes as `[en]`)  
runtime: `--n-gpu-layers 99 --context 2048 --threads 4`  
sample: seed 42, max-tokens 768, top-p 1.0, min-p 0.05, temp 0.8, repeat 1.2, cfg 0.5, exaggeration 0.3, cfm-steps 7, chunk-chars 180.

**Orchard path (preferred):** `python main.py rainbow <out.wav> --family v3 --language en`  
That binary is family-specific. It will **reject** `--top-k`. Good.

What happens inside, in order. Audit this if quality is wrong:

1. CLI parses **only v3 knobs**. Missing language is fatal.
2. Text is read as UTF-8. Packer splits on `.?!` then commas, counting **UTF-8 codepoints**, not bytes. Chunks glue if they fit 180. English ASCII is unchanged vs old byte counts; umlaut languages were short-changed before.
3. One `Engine` is constructed: load T3 GGUF onto Vulkan, preload S3Gen GGUF, bake voice from the reference (VoiceEncoder at 16 kHz after resample, CAMPPlus embedding, S3TokenizerV2 prompt tokens, prompt mel). If any of those are empty, it throws. Soft failure used to silently fall back to the built-in speaker — that was patched away. Empty embedding = hard error. Good. Still confirm the bake actually used **your** reference, not conds.pt builtin, by watching stderr.
4. For **each text chunk**, T3 runs. Multilingual tokenizer: lowercase, NFKD (this **is** what Python does; do not “fix” NFKD by deleting it), prefix `[en]`, spaces become `[SPACE]`, BPE. Then **pad 255 + ids + 0**. That pad was missing in Engine and present in tts-cli. It is now a second patch, `patches/chatterbox-z-mtl-sot-eot.patch`, applied **after** `patches/chatterbox-trident.patch`. If you `git apply` on a dirty third_party tree you will double-pad. Clean checkout + both patches, or you are not running the code you think you are.
5. Look at stderr: `tts mtl encode lang=en raw=N padded=N+2 sot=255 eot=0 ids=255 708 ...`  
   First two ids **must** be 255 then 708 for English. Then content. Also required: `tts backend=Vulkan`, `voice_overridden=1`, `tts t3 ... cap=0`. If language is empty, there is no `[en]` and you will get English-ish drift and “wrong language” audio. The binary requires `--language`. Do not invent a default inside Engine for “empty means en” unless you prove Python does that.
6. MTL sampling is CFG: logits = cond + cfg_weight * (cond − uncond). min_p then top_p. top_k only if >0. Repeat penalty on generated speech tokens. If all logits die, it returns stop-speech instead of token 0 (that used to desync). Watch for `degenerate logits`.
7. Official MTL pops EOS, then pops one more speech token (~40 ms of noise). Leave that alone unless you have a spectrogram proving you are eating real phones.
8. Speech tokens go to S3Gen. Meanflow vs standard CFM: v3 is **not** turbo meanflow. cfm_steps 7 is the documented knee (log-mel cosine ~0.995 vs 10). The library **clamps CFM < 5 back up to n_timesteps** on the non-meanflow path. Passing `--cfm-steps 2` on v3 does **not** do 2 steps. Do not “speed up” v3 that way.
9. Chunks are equal-power glued. Quiet-edge trim uses amplitude 0.02. Soft onsets can get bitten at chunk boundaries. If English drops “the / There / These” at joins, this is the suspect, not the tokenizer.
10. WAV write is 24 kHz mono PCM16. Last sample on disk is the last glued sample. RTF = (t3_ms + s3gen_ms) / audio_ms. Pascal v3 English Rainbow was about **0.9 RTF**. If RTF explodes, you are looping (max-tokens 768 × many chunks) or CPU-falling back.

Install/rebuild truth: `python main.py install --family v3` clones chatterbox.cpp to a pinned revision, applies **sorted** `patches/chatterbox-*.patch` (`trident`, then `z-mtl-sot-eot`, then `z-pipeline-log`), builds `tts-cpp` + `mtl_tokenizer`, then CMake in `tts/` builds three executables. Copy lands in `tools/runtime/tts/` with a `.build-stamp` of patch+wrapper hashes. **Stale binaries skip is gone.** If you edit `third_party/chatterbox.cpp` and do not update the matching patch, the next install wipes you. After install, `python main.py probe --family v3 --language en` is the one-sentence smoke. Full Rainbow is still the graduation exam. See the repo `README.md`.

---

## What already worked, what got worse, what is poison

**Nano English Rainbow is the gold recording**, not the gold model. 102.5 s, 93.5% speech, identical after CLI isolation. If your v3 English is 150 s with 97 gaps, you did not “improve expressiveness.” You added holes and rambling.

**Old v3 English (chunk 180, exaggeration 0.3, cfm 7, but still top_k 1000 and no SOT/EOT)** was the best v3 English we had: about 123 s, 86% speech, Parakeet WER around 4% (treat WER as gossip). **After SOT/EOT + official top_p=1.0 + top_k disabled, v3 English Rainbow got worse: 149 s, 81% speech, 97 gaps, peak hitting 0.99.** So the “correct” Python pad is necessary for language IDs, but the **sampling change is not free**. Your first job is to get English **back to ~123 s / ~86%+ speech / few long gaps**, with encode still `255 708 ... 0`. Likely: keep SOT/EOT, try top_p 0.95 again (the old winner), keep top_k=0, keep exaggeration 0.3, chunk 180, cfm 7. One-factor changes. Short text first.

**v3 German** after SOT/EOT is no longer the 372 s cfg=0 monster (230 gaps). It sits ~149 s. Still not mastered. **Do not go back to German until English is stable.** Cross-language with this English reference is a different experiment (cfg=0). Resemble says: reference language should match the language tag, or set cfg_weight to 0 to reduce accent bleed. We learned cfg=0 can also mean “please hallucinate for six minutes.”

**Poison:**
- Parakeet as the optimization target.
- Full Rainbow as the first run of the day.
- Grid-searching turbo and nano “while you’re there.”
- PowerShell UTF-8 files.
- Re-converting GGUF with the wrong revision or skipping the v3→v2 filename copy.
- Assuming f16 GGUF means f16 Vulkan compute on a 1060.
- Changing pack_text, glue, and sampling in the same commit.

---

## English first — reference audio is half the model

Today’s clone source is `data/default-reference.wav` (copy of `assets/default-reference.wav`). It is English. Engine requires **>= 5 seconds**, mono. Resemble’s own docs want **~10 s+** of clean speech. Official multilingual README: if the reference clip’s language does not match the language tag, output inherits the reference accent; mitigate with cfg_weight 0.

Your English-mastery setup:
- Language tag **en**, English text, English reference. That is the matched case. cfg 0.5 is the default for that case.
- Probe whether `default-reference.wav` is actually a good clone source: duration, clipping, silence, SNR, sample rate, mono, how much leading/trailing hush. If it is noisy, compressed, or too short, **find a clean public-domain English read** (LibriVox / LibriSpeech / Fairbanks Rainbow read by a clear native speaker, or Resemble’s own demo voice if you can fetch it legally). Mono, 16-bit, several seconds of continuous speech, no music. Put it in `data/` under a new name. Do not overwrite the bundled default until the new one wins on audio_report **and** a listen.
- Search the web for: Chatterbox multilingual reference wav recommendations, Resemble demo voice, LibriVox rainbow passage, “cfg_weight 0 cross language chatterbox”. Bring citations into your notes.

Short English texts to use, in order:
1. One sentence: “When the sunlight strikes raindrops in the air, they act as a prism and form a rainbow.”
2. Three sentences from the Fairbanks Rainbow (already in `cfg.py` RAINBOW["en"]).
3. Full Rainbow only when (1) and (2) look like speech, not Swiss cheese.

Compare every short run to the same sentence synthesized by **nano** (frozen CLI, frozen knobs) as a duration/speech-density reference. v3 can be slower and slightly longer. It must not double the length or drop clauses.

---

## GGUF and convert — do this once, early, if English is still messy

Checkpoint cache: `tools/convert/checkpoints/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18/` with `t3_mtl23ls_v3.safetensors` copied to `t3_mtl23ls_v2.safetensors`, `s3gen_v3.pt` copied to `s3gen.pt`, plus `grapheme_mtl_merged_expanded_v1.json`, `ve.pt`, `conds.pt`. Tokenizer JSON `normalizer` is null because **Python preprocesses outside** the HuggingFace tokenizer. C++ reimplements that preprocess. To compare ids, use `.venv` `tokenizers`: lowercase, NFKD, `[en]` prefix, replace spaces with `[SPACE]`, then `Tokenizer.from_file(grapheme json).encode`. C++ stderr must match that sequence **plus** the 255/0 pad.

Dump GGUF metadata (`gguf-dump` lives in the convert venv). Confirm variant is MTL, tokenizer blob present, embedding sizes match 520M llama (n_embd 1024, 30 layers). If someone converted with the turbo script, you will get a fluent disaster.

S3Gen f16 on Pascal: expect fp32 kernels. Correctness should hold; speed will not match an RTX. If audio is metallic or silent, check CAMPPlus / prompt_feat bake, not “need fp16.”

---

## Working rules

- Rebuild `tts-cpp` then `trident-tts-v3` after any chatterbox patch. Copy exe+ggml dlls into `tools/runtime/tts/`. Run **that** exe, not an old `trident-tts.exe` leftover.
- One change per short run. Name outputs like `rainbow/runs/v3-en-sent1-topp95.wav`.
- Always keep the text sidecar utf-8 no BOM.
- Always keep stderr (`mtl encode`, `family=v3`, `tts done samples=...`). If `main.py rainbow` swallows encode lines, it is supposed to reprint lines starting with `mtl encode` and `family=`. If it does not, fix that before tuning.
- Eval: `.venv\Scripts\python.exe rainbow\audio_report.py <wav>...` and a seconds-axis RMS / onset plot if gaps look weird. No Parakeet, no new test harnesses, no pytest. Run tools.
- Do not commit binaries, venv, GGUFs, or wavs. Commit source, patches, C++. Push `runner-x`.
- Web-search when stuck. Do not invent Resemble parameter ranges. top_k for turbo is 1–10000 default 1000. top_k for v3 is unused. max_tokens 768 is a cap, not a target length.
- Quiet-edge, pack_text, OOV filter on the **turbo** path, and the extra MTL pop_back are secondary. English first. Tokenizer ids first. Then sampling. Then glue. Then GGUF. Then German.

## Done looks like

English v3, English reference, language `en`, encode starts `255 708`, Rainbow (when you finally run it) speech fraction in the mid-80s or better, no 0.99 full-scale slamming, no 40+ extra seconds of tail versus the previous good v3 English, gen RTF not much worse than ~1 on this 1060, and a short note of which **one** knob recovered quality after SOT/EOT. Only then may you open German, with a German reference or a proven cfg strategy that does not loop.

If you are unsure, run a smaller sentence. Time spent on full Rainbow without encode logs is wasted time.
