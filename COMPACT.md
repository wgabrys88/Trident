# Paste this after compact. Say: begin from COMPACT.md

We won the sound. Do not roll that back. The remaining work is **twenty-five duplication** and **speed**. We finish both today. Natural voice flow stays.

No prior Grok history. GitHub is the only memory. After every push, this machine may be gone.

---

## What we won (do not undo)

The listener hears **one take**, not files stuck together. Keep that.

Native chatterbox `origin/main` **`c4e051e82f086b80c5379e4219e2693c15db90f8`**:

- `last_piece` holds `pending_pcm` across pieces so cosine mix runs. `final=true` is dummy pad `4299` × 3 only.
- Do not launch T3(N+1) until S3(N) returns. One Vulkan compute queue. `overlap_ms=0` is success.
- Keep `ggml-vulkan-queue.patch`. Do not add a second queue. Do not push `ggml-org/ggml`.
- ggml pin stays `58c3805840b516b2a88ff867ccf7bb41dba79951`.

Trident `origin/runner-z` **`fafe8abb99b4f7046da179d3e11835a78130bad7`** (this compact file is the next commit on top):

- `chunk.py` — CPU **SaT `sat-3l-sm`** ONNX, `CPUExecutionProvider` only, one ONNX thread. Smart Turn’s text cousin. Smart Turn itself is audio and cannot split a string.
- `tts_nano.py` no longer packs with dots then `CHUNK_CHARS=50`. That packing was the fourteen bug (`f8045d4`): T3 cooked several numbers in one piece and invented extras.
- SaT cuts by **meaning / breath**, size follows the text. Counting is not thirty isolated words. You cannot say thirty digits in one breath. Default SaT on One–Thirty was **4 breath groups**. Mars-bars was **6 sentences**.

Install stamp must stay:

```
c4e051e82f086b80c5379e4219e2693c15db90f8 58c3805840b516b2a88ff867ccf7bb41dba79951
```

`--install` skips native rebuild when that stamp matches. It still installs the chunker venv + ONNX if missing.

---

## What is still broken

1. **Twenty-five duplication.** Same family as fourteen. T3 repeating a number inside a multi-number breath chunk (SaT grouped “Twenty … Twenty-nine” as one piece at default threshold 0.25). Do not “fix” this by going back to 50-character packing or to 30 one-word pieces. Do not restore hop streaming (25-token S3 recooks). Find the root: T3 listing-repeat vs a cut that is still too long for a count list vs join glue leaking a word. Prove it by listening, then change one thing.
2. **Speed is too slow.** Counting last run: `tts_synth=32.372s` `audio_s=26.500s`. Mars-bars: `tts_synth=14.228s` `audio_s=16.220s`. The user said **log RTF is calculated wrongly — trust that, do not treat the `[rtf]` ratio as the pass/fail**. Still make it faster by ear and by `tts_synth` going down. Product target in `SESSION_BOOTSTRAP.md` remains Nano RTF < 0.5 — **do not change 0.5 vs 0.7 until they say**. Do not fake speed with GPU overlap that fights T3/S3 on one queue.

---

## How to work

- PowerShell: no `&&`, use `;`. No nested-quote `python -c`.
- Git for C++: `git -C C:\Users\eb-wjt\Downloads\chatterbox.cpp`. Trident cwd is the wrong repo for native.
- Commit + push after every working step. Never leave `CHATTERBOX_REV` uncommitted. Native SHA on `origin/main` first, then pin `runner-z`.
- No tests, no harnesses, no extra design docs besides `SESSION_BOOTSTRAP.md` / this file.
- Do not commit models, binaries, logs, WAVs, `tools/runtime/chunker` venv, `models/sat-3l-sm`.
- TTR2, knobs, `GGML_REV`, `NANO_REV` unchanged unless a listen proves a knob is the click or the duplicate.
- One TCP session. Queue all pieces, then recv. `TCP_NODELAY`. 300s timeout.
- Gemma, Nano, Parakeet stay GPU-VRAM resident. Only the small chunker on CPU. Do not put SaT on Dml/CUDA.
- Low LOC. Scripts stay small.

Listen: `out_<dd-mm-yy-HH-MM-SS>_tts.wav` and `tts_out.wav`. Log: new tail of `.runtime-logs/tts.log` only.

Mars-bars text (happy prose path):

```
Now let's make my mum's favourite. So three mars bars into the pan. Then we add the tuna and just stir for a bit, just let the chocolate and fish infuse. A sprinkle of olive oil and some tomato ketchup. Now smell that. Oh boy this is going to be incredible.
```

Count text (duplicate hunter):

```
One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. Eleven. Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. Eighteen. Nineteen. Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty.
```

Ears win. If a change kills twenty-five but makes mars-bars clicky or slow in a new way, it is not done.

---

## First move after compact

Do not re-do glue. Do not re-author the Vulkan patch. Do not re-install native if the stamp already matches.

1. Read this file and `SESSION_BOOTSTRAP.md`.
2. Confirm pins: chatterbox `c4e051e`, Trident pin in `tts_nano.py`, chunker SaT CPU.
3. Plan the **root** fix for twenty-five duplication + speed, without throwing away the natural flow. Then wait for the user to say go — unless they already said begin from this file, in which case start at the plan’s step 1.

We will fucking do it today.
