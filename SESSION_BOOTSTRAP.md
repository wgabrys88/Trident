# Paste this whole file into a new session (no history)

Grok remote history is off. GitHub is the only memory. Write as if this PC will be destroyed after every push.

Do **not** implement in the first turn. The human clones, installs, **listens**, then code-reviews with you. Their ears beat this file. This file is what we actually agreed, not a GPU-overlap recipe.

---

## Talk to the human like a human

Before any of this overlap work, Trident already spoke on Intel Iris Xe. The voice was good — sometimes emotional, sometimes amazing. Wait was fine: about 16 seconds of audio in about 9 seconds. The pain was **clicks where one chunk is glued to the next**. Chunk size was fiddled until it hurt.

Then a lot of time went into “streaming” and overlapping T3 with S3 on one GPU. That did **not** fix the glue. It made more cuts and made the wait **worse** (~10.4 s instead of ~9 s). Same voice, worse joints, longer wait. That is the scoreboard. Do not pretend we shipped a win.

**What the next session is for**

Same voice they already like. Long text still gets **chunked** (not token-streamed). Each chunk can be a full short synthesis, even from zero, if that keeps the voice honest. The only hard problem is the **seam**. When that is done, waiting should be at least as short as the old sequential run. Faster is optional. Emotion was never missing.

If you start chasing Vulkan queues before the join sounds like one take, you are on the wrong job.

---

## What the human approved and rejected

**Approved**

- **Chunking**, not intra-piece streaming / 25-token S3 hops. Chunking is faster on this machine. Hops made 18 vocoder calls and more clicks. Do not bring hops back.
- Local Iris Xe. Bare metal. Remotes as the only memory.
- Listener quality first. Speed still matters — it must not get worse than the old ~8.5–9.3 s mars-bars wait unless the extra time is an audible win they asked for.

**Rejected**

- **`CHUNK_CHARS=50` as a law.** That number is a speed sweep (`97b4480`: 50 chars beat 60 on RTF, 0.542 vs 0.641). It is not how people talk. It also **creates more seams**, and the same commit already warned shorter chunks make more discontinuities. Do not freeze 50. Do not split only on `. ? ! …`.
- Splitting speech by dots or a character cap. Cuts should follow **meaning**. A human can pause in the middle of a sentence and continue. A regex cannot know if that pause is the end. That needs a small smart model, not punctuation.
- Over-engineering GPU overlap, 20 ms religions, `overlap_ms` as a trophy. The listener does not care how the seam is implemented.

**Their “from zero each chunk” idea is not stupid.** If ten seconds of cold synthesis sounds great, keep that. Chop the long text into meaning-sized pieces, synth each as a good short take, **glue the intersection**. Do not invent a new TTS architecture to avoid a pop.

---

## Smart Turn (the model they meant)

It is **Smart Turn v3.2**, not a TTS vocoder trick.

| | |
|--|--|
| Label in this repo | `SMART TURN V3.2 MULTILINGUAL CPU INT8` |
| File | `smart-turn-v3.2-cpu.onnx` (~8.3 MB, well under 100 MB) |
| Source | HuggingFace `pipecat-ai/smart-turn-v3` @ `f766f81d3cfdf7737ac64aad813d91bbfd56bf93` |
| Where it lived | Trident history, e.g. `55aca13` (logged 61–72 ms/decision, 1 ONNX intra-op thread on this CPU). Wired in old `vad.py` `SmartTurnEndpoint` + `conversation.py`. |
| Dropped | `3e86f63` / `d8071e4` / `15eeb0b` when the stack was stripped to bare TTS/ASR. **Not on `runner-z` now.** |

What it actually does: Silero proposes a pause in **audio**. Smart Turn looks at up to 8 s of audio and says **complete vs continue**. That is exactly “they stopped mid-sentence to think, do not cut them off.”

What it does **not** do: read a text string and emit TTS piece boundaries. Do not pretend the ONNX file splits `tts_nano.py` input.

So two different cuts, both “by meaning”:

1. **Listening (conversation, later):** restore Smart Turn so a breath is not a turn end. North star of this repo was a talk loop so natural you forget the machine. They said they will go bare-metal and then come back to that system.
2. **Speaking (TTS now):** stop wrapping at 50 chars / dots. Piece boundaries should be speakable units of meaning (brain-emitted units, or a small text segmenter — this repo used to have a Python `Segmenter` / spoken-unit splitter, dropped in `b7757f6`). Until that exists, do not treat 50 as sacred; it is only the current code.

Current splitter (replace when you have meaning-cuts): `tts_nano.py` `TTS._chunks` — regex on `[.!?…]` then `textwrap` at `CHUNK_CHARS=50`.

---

## Clone, install, listen (Windows)

```
git clone -b runner-z https://github.com/wgabrys88/Trident.git
cd Trident
python tts_nano.py --install
python tts_nano.py --text "Now let's make my mum's favourite. So three mars bars into the pan. Then we add the tuna and just stir for a bit, just let the chocolate and fish infuse. A sprinkle of olive oil and some tomato ketchup. Now smell that. Oh boy this is going to be incredible."
python parakeet.py tts_out.wav
```

They also clone chatterbox by hand (GitHub Desktop → `main`) so you have a tree to edit. That is optional for install. **Trident pins the latest chatterbox `origin/main`:** `CHATTERBOX_REV = 3593cf22d6d2a8d044e1af8968f1220fe6b03aa1`. Yes, pin == latest pushed native commit. If native moves, bump the pin in the same Trident push.

`--install` fetches chatterbox at that SHA, ggml `58c3805840b516b2a88ff867ccf7bb41dba79951`, applies `src/ggml-vulkan-queue.patch`, builds `chatterbox-server`. Do not push `ggml-org/ggml`. Do not commit models, binaries, logs, WAVs.

Host paths in `tts_nano.py`: VS 2022 x64, CMake `C:/Program Files/CMake/bin/cmake.exe`, Vulkan SDK `C:/VulkanSDK/1.4.357.0`. WAV `out_<dd-mm-yy-HH-MM-SS>_tts.wav` and `tts_out.wav`. Log `.runtime-logs/tts.log` (append-only; read the new tail). TTS port 17933, Parakeet 17934.

PowerShell: no `&&` (use `;`). Native git: `git -C <chatterbox clone>`. Trident cwd is the wrong repo for C++.

---

## Pins

| | Remote | Branch | SHA |
|--|--|--|--|
| Trident | `https://github.com/wgabrys88/Trident.git` | `runner-z` | this commit |
| chatterbox.cpp | `https://github.com/wgabrys88/chatterbox.cpp.git` | `main` | `3593cf22d6d2a8d044e1af8968f1220fe6b03aa1` |
| ggml | do not push | — | `58c3805840b516b2a88ff867ccf7bb41dba79951` |
| Nano weights | ResembleAI/chatterbox-nano | — | `71ccd1d0081b430592cea481f4307e764e07bc64` |

`tools/runtime/tts/REVISION` after install:

```
3593cf22d6d2a8d044e1af8968f1220fe6b03aa1 58c3805840b516b2a88ff867ccf7bb41dba79951
```

Never leave `CHATTERBOX_REV` uncommitted without that SHA already on `origin/main`.

Keep: TTR2, one TCP session, queue all pieces then recv, `TCP_NODELAY`, 300s timeout, bind port probe, public Engine callback, `GGML_REV`, `NANO_REV`, knobs unless a listen+review says a knob is the click. No tests, no harnesses, no process-wide inference lock, no third backend, no extra design docs besides this file.

---

## What is already on GitHub (do not redo)

Chatterbox `origin/main` was clean at `3593cf2`:

- `c46535b` — honest logs (one t3, one s3, one session). No 16k Vulkan firehose.
- `0f4ab05` — one S3 call per piece (dummy pad `4299` × 3 lookahead). Acoustics + last 25 speech tokens persist across pieces. `begin_synthesis` resets so a second client does not inherit. Recv `pending.empty()` locked. Server batches same-epoch pieces. **Also starts T3 of piece N+1 while S3 of N runs** (this is the overlap that lost time).
- `3593cf2` — ggml patch: fence on this context’s last real submit, no empty-submit queue-wide wait, buffer barriers, `wait_us` / `submit_n` / `barrier_n`. Still one compute queue. `queue_mutex` only around `vkQueueSubmit`.

Trident `6de62fb` pinned that SHA and applies the patch at `_build`. This bootstrap does not change the pin.

Do not restore hops. S3 encoder is full-sequence Conformer (T×T softmax, not causal). You cannot cache prompt by concatenating activations. A real prompt cache is KV inside Conformer+CFM — later, and only after the seam and wait are fixed.

---

## Measured mars-bars on this pin (2026-09-05 20:38:50)

`tts_synth=10.400s` `audio_s=15.820s` `first_audio_ms=1418` `overlap_ms=7426`. Seven pieces queued before first T3, seven S3 calls, close ack. Tokens 396 (old sequential ~380; same knobs; drift, do not chase). Parakeet still gets the paragraph (ketchup → “catch up”).

Old sequential: wait ~8.5–9.3 s, first audio ~1207 ms, ~7 S3. Hop prototype: wait ~15.5 s, 18 S3.

S3 `wait_us` summed to ~7 s. Two backends on one Vulkan queue. CPU threads overlap; GPU work does not. That is why wait got worse. Iris Xe `queueCount` was never probed.

Joins: `final=true` (needed for dummy pad) also sets `end = wav.size()`, so `pending_pcm` is always empty and the cosine mix never runs. At the six seams, 30–70 ms of digital zeros. Not a spike click — a hole. `n_trim = 24000/50 = 480` samples (~20 ms) is an old fade constant. It is **not** a quality target.

Glue lives here: `chatterbox_tts.cpp` `s3gen_synthesize` (pending / `final` / history mel). Pipeline overlap: `chatterbox_engine.cpp` `pipeline_pieces`. Piece 0 leading-zero strip: `tts_nano.py` synthesize.

---

## First turn of the new session

1. Confirm remotes. Pin must still be latest chatterbox `main` or a later SHA already pushed and pinned.
2. Listen with the human. Code-review against **this** brief, not the old overlap plan.
3. Propose the smallest change that makes the paragraph sound like one take and does not make wait worse. Stop for approval if it is large.
4. Chatterbox commit+push first, bump pin, Trident commit+push, `--install` once, mars-bars, Parakeet, listen again. Numbers in the Trident message.

**Likely shape (hypothesis, not a ticket):** sequential GPU again (stop launching next T3 until this S3 finishes) if they still share one queue; actually mix the tail of chunk A into the head of chunk B; replace 50-char/dot packing with meaning-sized pieces when a splitter exists; bring Smart Turn back when the talk loop comes back. Throw any of that away if the ears disagree.

Done when a cold clone installs, the voice still sounds like the one they already loved, the joins do not pop or drop, and waiting is not worse than the old sequential run.
