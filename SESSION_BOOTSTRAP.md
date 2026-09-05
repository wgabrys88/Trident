# Paste this whole file into a new session

No prior Grok history. GitHub is the only memory. After every push, this machine may be gone.

Read this as a decision, not a menu. There is no “try A, if not then B.”

---

## What we want

A local voice stack on this PC (Iris Xe now, NVIDIA RTX later) that sounds like one person talking, not like files stuck together.

The voice is already good. Sometimes it is emotional. Keep that. Do not replace the model to chase speed.

Long answers are **chunked**. Not streamed token-by-token. Chunking is faster here. We already tried streaming hops inside a piece: more cuts, more clicks, slower. That is finished. Do not bring it back.

Chunks are cut by **meaning**, not by fifty characters and not by dots. People pause in the middle of a sentence and then continue. A regex cannot hear that.

The only ugly part of the old working system was the **join**: a click or a hole where chunk A meets chunk B. That is the TTS problem. Fix the glue. Do not invent a new engine.

Waiting must not get worse than the old sequential run (mars-bars about **8.5–9.3 seconds** of wait for about **16 seconds** of audio). Speed matters. Faster than that is welcome after the joins are clean. Worse wait for a paper “overlap” is failure.

---

## How the machine is supposed to run

After install, three engines are **started and left running**, like a car already warm:

1. **Gemma** — brain (`brain.py`)
2. **Chatterbox Nano** — speak (`tts_nano.py` / chatterbox-server)
3. **Parakeet** — transcribe (`parakeet.py`)

They live in **GPU memory (VRAM)**. Iris Xe shared GPU memory is fine. RTX dedicated VRAM is the same idea. A speak / think / transcribe request must **not** load weights again. The process is already up. The model is already on the GPU.

Those three do **not** run on CPU as their home. Vulkan is GPU. Do not “help” them by putting the nano / Gemma / Parakeet inference on CPU.

The **only** thing that belongs on CPU is a **small** helper, in parallel, that does not steal VRAM:

- **Now (speaking):** a small text model (Smart Turn’s cousin, or Smart Turn if it can be used on text — it cannot; Smart Turn is audio). It reads the text that will be spoken and cuts it into meaning-sized pieces for TTS. Tiny. CPU. Parallel with the GPU engines.
- **Later (listening, when the talk loop comes back):** Smart Turn v3.2 on **audio**, also CPU, also tiny. Silero may propose a pause; Smart Turn says “they finished” vs “they only breathed.” That is how a human pause in the middle of a sentence is not treated as the end of the turn.

Smart Turn identity (already used in this repo, then stripped):

- `smart-turn-v3.2-cpu.onnx` (~8.3 MB)
- HuggingFace `pipecat-ai/smart-turn-v3` @ `f766f81d3cfdf7737ac64aad813d91bbfd56bf93`
- Label: SMART TURN V3.2 MULTILINGUAL CPU INT8
- History: `55aca13` (61–72 ms per decision, one ONNX thread). Dropped in `3e86f63` / `d8071e4`. Not on `runner-z` today.

Do not put Smart Turn on the GPU. Do not put Gemma / Nano / Parakeet on the CPU.

---

## How speech is made (TTS)

1. Text comes in (from the human, or from Gemma).
2. The **CPU** small model cuts it into meaning pieces. Not `CHUNK_CHARS=50`. Not split-on-`.?!`. Fifty was a speed sweep (`97b4480`) and it makes **more** seams. It is not how people talk.
3. Each piece is a full short synthesis on the **GPU** Nano server that is already loaded. Starting a piece “from zero” is allowed if that keeps the voice honest. The user already heard ten seconds of cold synthesis sound great.
4. Pieces are glued so the listener hears **one take**. Hold a little tail of A and mix it into the start of B, or whatever actually sounds continuous. Twenty milliseconds is not a religion; it is an old constant in the vocoder (`n_trim = 24000/50`). If the join still pops, change the glue until it does not.
5. One TCP session. Queue every piece, then receive audio in order. TTR2 stays. One vocoder graph per piece. No 25-token hops inside a piece.

T3 and S3 both sit on the same GPU. They must not fight each other for that GPU in a way that makes the user wait longer. The overlap experiment did that. So: keep both on the GPU, keep the server hot, run the work so the queue is not stolen out from under a running graph. Do not celebrate `overlap_ms` if the clock got worse.

---

## What we refuse

- Token-streaming / hops inside a piece
- Fifty characters or punctuation as the meaning of a “sentence”
- Reloading Gemma, Nano, or Parakeet on every request
- Parking those three on CPU
- Parking the small chunker/turn model on GPU
- Pushing to `ggml-org/ggml`
- Tests, harnesses, extra design docs besides this file
- Committing models, binaries, logs, WAVs
- Leaving `CHATTERBOX_REV` uncommitted while native is only local
- A process-wide inference lock
- “Try two Vulkan queues, else sequential” as the story. The story is: hot GPU engines, clean joins, meaning chunks, wait not worse than sequential.

---

## Clone and run (Windows)

```
git clone -b runner-z https://github.com/wgabrys88/Trident.git
cd Trident
python tts_nano.py --install
python tts_nano.py --text "Now let's make my mum's favourite. So three mars bars into the pan. Then we add the tuna and just stir for a bit, just let the chocolate and fish infuse. A sprinkle of olive oil and some tomato ketchup. Now smell that. Oh boy this is going to be incredible."
python parakeet.py tts_out.wav
```

`--install` on each script builds if needed **and starts the server**. `python main.py` with no args installs all three in order. `--load` keeps them up. `--unload` stops them. After install, they should stay up so the next speak/think/transcribe is a request, not a load.

You may also clone `https://github.com/wgabrys88/chatterbox.cpp.git` on `main` in GitHub Desktop so the native tree is there to edit. Install does not require that clone. Trident **already points at the latest pushed chatterbox `main`.**

Need on the PC: Python 3, Visual Studio 17 2022 x64, CMake at `C:/Program Files/CMake/bin/cmake.exe`, Vulkan SDK at `C:/VulkanSDK/1.4.357.0`. PowerShell: no `&&`, use `;`. Native git: `git -C <chatterbox clone>`. Do not git from Trident to commit C++.

WAV: `out_<dd-mm-yy-HH-MM-SS>_tts.wav` and `tts_out.wav`. Log: `.runtime-logs/tts.log` (read only the new tail). TTS port 17933. Parakeet 17934.

---

## Pins (must match)

| | Remote | Branch | SHA |
|--|--|--|--|
| Trident | `https://github.com/wgabrys88/Trident.git` | `runner-z` | this commit |
| chatterbox.cpp | `https://github.com/wgabrys88/chatterbox.cpp.git` | `main` | `3593cf22d6d2a8d044e1af8968f1220fe6b03aa1` |
| ggml (patched at build, never pushed) | | | `58c3805840b516b2a88ff867ccf7bb41dba79951` |
| Nano weights | ResembleAI/chatterbox-nano | | `71ccd1d0081b430592cea481f4307e764e07bc64` |

`tts_nano.py` `CHATTERBOX_REV` is that chatterbox SHA. `_build` applies `src/ggml-vulkan-queue.patch` onto ggml. After install, `tools/runtime/tts/REVISION` is:

```
3593cf22d6d2a8d044e1af8968f1220fe6b03aa1 58c3805840b516b2a88ff867ccf7bb41dba79951
```

If you change chatterbox, push `origin/main` first, then bump this pin on `runner-z`, then `--install` once.

Keep TTR2, public Engine callback, `GGML_REV`, `NANO_REV`, knobs unless a listen proves a knob is the click.

---

## Where the code is today (not the destination)

On chatterbox `3593cf2` (this is latest `origin/main`):

- Honest logs (`c46535b`): one t3 line, one s3 line, one session line.
- One S3 call per piece (`0f4ab05`), dummy pad `4299` × 3, acoustics persist, session reset on `begin_synthesis`. Server queues all pieces in one TCP session.
- It also starts T3 of the next piece while S3 of this piece is still using the GPU. That overlap **lost** time. Remove that fight; do not decorate it.
- Vulkan patch (`3593cf2`): wait scoped to this backend’s last submit, counters `wait_us` / `submit_n` / `barrier_n`. Still one compute queue. Keep the patch. It is not a second GPU.

On Trident `runner-z`, `tts_nano.py` still packs with dots then 50 characters (`TTS._chunks`). That packing is **not** the product. Replace it with the CPU meaning-cutter.

Glue bug, concrete: `s3gen_synthesize` in `chatterbox_tts.cpp` uses `final=true` for the dummy pad **and** for “emit everything, empty pending.” So `pending_pcm` never mixes across pieces. Six mars-bars joins were 30–70 ms of digital zeros. That is the hole you hear. Split “dummy pad” from “hold a tail for the next piece.” `pipeline_pieces` in `chatterbox_engine.cpp` is where next T3 is launched too early.

---

## What we already measured (so we do not relearn it)

Mars-bars on this pin, 2026-09-05 20:38:50, server already up:

- Wait **10.400 s** (worse than 8.5–9.3 s)
- Audio 15.820 s, first audio 1418 ms (old sequential ~1207 ms)
- 7 pieces, 7 S3 calls, 396 tokens (old sequential ~380, same knobs, ignore the drift)
- Parakeet still understands the paragraph
- S3 `wait_us` summed to ~7 s — GPU queue fight, not “missing prompt cache”
- Hops were 18 S3 and ~15.5 s wait — worse in every way

S3 encoder is full-sequence attention. You cannot cache the voice prompt by copying activations. A real prompt cache is future work, after joins and wait are right.

---

## What the new session does, in this order

1. Clone `runner-z`, install, **listen** with the human. Confirm the three servers can stay up. Confirm the pin is still latest chatterbox `main`.
2. Code-review against **this** file. If it disagrees with the ears, the ears win, then you change this file and push it.
3. Work, in this order, one working push at a time (chatterbox `main` first if C++ changed, then Trident pin, then one `--install`):
   1. Make the join inaudible. Same voice. Meaning-sized or current pieces, does not matter until the glue works.
   2. Stop T3 and S3 stealing the same GPU from each other so wait returns to ≤ ~9 s.
   3. Put meaning-cuts on CPU (small text model). Delete 50/dots as the law.
   4. Keep Gemma, Nano, Parakeet resident on GPU. Prove a second request does not reload.
   5. When the talk loop returns: Smart Turn on CPU for audio complete/continue.

Done when a cold clone installs, the three engines stay hot on the GPU, the paragraph sounds like one take, chunks follow meaning, and waiting is not worse than the old sequential run.
