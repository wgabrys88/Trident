# Paste this whole file into a new session

No prior Grok history. GitHub is the only memory. After every push, this machine may be gone.

Read this as a decision, not a menu. There is no “try A, if not then B.”

Scripts stay **small (low LOC)**, independent, bare metal. That is how they stay maintainable and how they will be wired later. Wire them with **RTF**, not with a stopwatch for one paragraph.

---

## RTF (this is the speed language)

Do not aim at “about nine seconds.” That is one text on one machine. It does not travel.

**Nano (speak)**  
RTF = time the already-loaded TTS server spent synthesizing ÷ duration of the audio it produced.  
Target: **RTF &lt; 0.5**. This repo has already shown Nano can sit there. Below 0.5 means speech is made more than twice as fast as it plays.

**Brain (think)**  
RTF = time Gemma spent generating the answer ÷ time Nano spent synthesizing that same answer.  
Think versus speak. **Not** ÷ audio-file length. Audio length is how long the listener hears. Synthesis time is how long the hot Vulkan engine took to make the voice. The engines sit in VRAM already, so synthesis time is the real “how long did speak take.” Dividing think time by audio length would mix a file duration into a pipeline that is waiting on synthesis. Nonsense for wiring.

When the small scripts are connected, RTF is how you know they fit: if brain RTF is below 1, thinking finishes before speaking does; if Nano RTF is below 0.5, speak is cheap enough to sit next to the others.

---

## What we want

A local voice stack on this PC (Iris Xe now, NVIDIA RTX later) that sounds like one person talking, not like files stuck together.

The voice is already good. Sometimes it is emotional. Keep that. Do not replace the model to chase RTF.

Long answers are **chunked**. Not streamed token-by-token. Chunking is faster here. We already tried streaming hops inside a piece: more cuts, more clicks, worse RTF. That is finished. Do not bring it back.

Chunks are cut by **meaning**, not by fifty characters and not by dots. People pause in the middle of a sentence and then continue. A regex cannot hear that.

The only ugly part of the old working system was the **join**: a click or a hole where chunk A meets chunk B. That is the TTS problem. Fix the glue. Do not invent a new engine.

Nano RTF must get **below 0.5**. A fake GPU “overlap” that raises RTF is failure, even if `overlap_ms` looks pretty.

---

## How the machine is supposed to run

After install, three engines are **started and left running**, like a car already warm:

1. **Gemma** — brain (`brain.py`)
2. **Chatterbox Nano** — speak (`tts_nano.py` / chatterbox-server)
3. **Parakeet** — transcribe (`parakeet.py`)

They live in **GPU memory (VRAM)**. Iris Xe shared GPU memory is fine. RTX dedicated VRAM is the same idea. A speak / think / transcribe request must **not** load weights again. The process is already up. The model is already on the GPU. That is also why brain RTF uses synthesis time in the denominator: speak is a hot engine, not a cold load plus a file length.

Those three do **not** run on CPU as their home. Vulkan is GPU. Do not “help” them by putting Nano / Gemma / Parakeet inference on CPU.

The **only** thing that belongs on CPU is a **small** helper, in parallel, that does not steal VRAM:

- **Now (speaking):** a small text model (Smart Turn’s cousin — Smart Turn itself is audio, it cannot split a string). It reads the text that will be spoken and cuts it into meaning-sized pieces for TTS. Tiny. CPU. Parallel with the GPU engines. Low LOC.
- **Later (listening, when the talk loop comes back):** Smart Turn v3.2 on **audio**, also CPU, also tiny. Silero may propose a pause; Smart Turn says “they finished” vs “they only breathed.” That is how a human pause in the middle of a sentence is not treated as the end of the turn.

Smart Turn identity (already used in this repo, then stripped):

- `smart-turn-v3.2-cpu.onnx` (~8.3 MB)
- HuggingFace `pipecat-ai/smart-turn-v3` @ `f766f81d3cfdf7737ac64aad813d91bbfd56bf93`
- Label: SMART TURN V3.2 MULTILINGUAL CPU INT8
- History: `55aca13` (~60–70 ms per decision, one ONNX thread). Dropped in `3e86f63` / `d8071e4`. Not on `runner-z` today.

Do not put Smart Turn on the GPU. Do not put Gemma / Nano / Parakeet on the CPU.

---

## How speech is made (TTS)

1. Text comes in (from the human, or from Gemma).
2. The **CPU** small model cuts it into meaning pieces. Not `CHUNK_CHARS=50`. Not split-on-`.?!`. Fifty was a speed sweep (`97b4480`, Nano RTF 0.542 on that text) and it makes **more** seams. It is not how people talk.
3. Each piece is a full short synthesis on the **GPU** Nano server that is already loaded. Starting a piece “from zero” is allowed if that keeps the voice honest. A short cold take already sounded great.
4. Pieces are glued so the listener hears **one take**. Hold a little tail of A and mix it into the start of B, or whatever actually sounds continuous. Twenty milliseconds is not a religion; it is an old constant in the vocoder (`n_trim = 24000/50`). If the join still pops, change the glue until it does not.
5. One TCP session. Queue every piece, then receive audio in order. TTR2 stays. One vocoder graph per piece. No 25-token hops inside a piece.

T3 and S3 both sit on the same GPU. They must not fight each other for that GPU in a way that raises Nano RTF. The overlap experiment did that. Keep both on the GPU, keep the server hot, do not steal the queue out from under a running graph. Do not celebrate `overlap_ms` if Nano RTF got worse.

---

## What we refuse

- Token-streaming / hops inside a piece
- Fifty characters or punctuation as the meaning of a “sentence”
- Reloading Gemma, Nano, or Parakeet on every request
- Parking those three on CPU
- Parking the small chunker/turn model on GPU
- Pushing to `ggml-org/ggml`
- Tests, harnesses, extra design docs besides this file
- Fat scripts (low LOC is a requirement)
- Committing models, binaries, logs, WAVs
- Leaving `CHATTERBOX_REV` uncommitted while native is only local
- A process-wide inference lock
- A wall-clock “about N seconds” as the speed goal
- Brain RTF ÷ audio-file length
- “Try two Vulkan queues, else sequential” as the story. The story is: hot GPU engines, clean joins, meaning chunks, Nano RTF &lt; 0.5, small scripts.

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

WAV: `out_<dd-mm-yy-HH-MM-SS>_tts.wav` and `tts_out.wav`. Log: `.runtime-logs/tts.log` (read only the new tail). TTS port 17933. Parakeet 17934. Scripts already print `[rtf]` lines; use those, not a paragraph stopwatch.

---

## Pins (must match)

| | Remote | Branch | SHA |
|--|--|--|--|
| Trident | `https://github.com/wgabrys88/Trident.git` | `runner-z` | this commit |
| chatterbox.cpp | `https://github.com/wgabrys88/chatterbox.cpp.git` | `main` | `c4e051e82f086b80c5379e4219e2693c15db90f8` |
| ggml (patched at build, never pushed) | | | `58c3805840b516b2a88ff867ccf7bb41dba79951` |
| Nano weights | ResembleAI/chatterbox-nano | | `71ccd1d0081b430592cea481f4307e764e07bc64` |

`tts_nano.py` `CHATTERBOX_REV` is that chatterbox SHA. `_build` applies `src/ggml-vulkan-queue.patch` onto ggml. After install, `tools/runtime/tts/REVISION` is:

```
c4e051e82f086b80c5379e4219e2693c15db90f8 58c3805840b516b2a88ff867ccf7bb41dba79951
```

CPU meaning-cutter: `chunk.py`, SaT `sat-3l-sm` ONNX (`model_optimized.onnx` sha `8573277b4dbea9c5fb1b4cfd8c21e5aa628069ac8258d1342ba664e1b64ada6d`), `CPUExecutionProvider` only, one ONNX thread. Never pack sentences. Never 50-character wrap.

If you change chatterbox, push `origin/main` first, then bump this pin on `runner-z`, then `--install` once.

Keep TTR2, public Engine callback, `GGML_REV`, `NANO_REV`, knobs unless a listen proves a knob is the click.

---

## Where the code is today

On chatterbox `c4e051e` (latest `origin/main`):

- Honest logs (`c46535b`): one t3 line, one s3 line, one session line.
- One S3 call per piece (`0f4ab05`), dummy pad `4299` × 3, acoustics persist, session reset on `begin_synthesis`. Server queues all pieces in one TCP session.
- `last_piece` holds `pending_pcm` across pieces so cosine mix can run. `final` is dummy pad only.
- Next T3 starts after this S3 returns. One Vulkan compute queue. Keep `ggml-vulkan-queue.patch`.
- Vulkan wait scoped to this backend’s last submit, counters `wait_us` / `submit_n` / `barrier_n`.

On Trident `runner-z`, `tts_nano.py` asks `chunk.py` (SaT `sat-3l-sm`, CPU) for pieces. No `CHUNK_CHARS`. No regex packing. Fourteen duplication was T3 cooking several numbers in one packed piece (`f8045d4`).

---

## What we already measured (so we do not relearn it)

Mars-bars on this pin, 2026-09-05, server already up:

- Nano RTF **0.657** (`tts_synth / audio_s` = 10.400 / 15.820). Target is **&lt; 0.5**. This is worse.
- Old sequential Nano RTF sat about **0.54–0.59**. Sweep `97b4480` hit **0.542** at 50-char packing. Hops were worse in RTF and in clicks (18 S3 calls).
- 7 pieces, 7 S3 calls, 396 tokens (old sequential ~380, same knobs, ignore the drift)
- Parakeet still understands the paragraph
- S3 `wait_us` was huge — GPU queue fight, not “missing prompt cache”
- First audio also got worse on this pin; RTF does not replace listening for a late start

S3 encoder is full-sequence attention. You cannot cache the voice prompt by copying activations. A real prompt cache is future work, after joins and Nano RTF are right.

---

## What the new session does, in this order

1. Clone `runner-z`, install, **listen** with the human. Confirm the three servers stay up on the GPU. Confirm the pin is still latest chatterbox `main`.
2. Code-review against **this** file. If it disagrees with the ears, the ears win, then you change this file and push it.
3. Work, in this order, one working push at a time (chatterbox `main` first if C++ changed, then Trident pin, then one `--install`). Keep LOC down.
   1. Make the join inaudible. Same voice.
   2. Stop T3 and S3 stealing the same GPU from each other until Nano RTF is **&lt; 0.5**.
   3. Put meaning-cuts on CPU (small text model). Delete 50/dots as the law.
   4. Keep Gemma, Nano, Parakeet resident on GPU. Prove a second request does not reload. Print brain RTF as think ÷ synthesis, Nano RTF as synthesis ÷ audio.
   5. When the talk loop returns: Smart Turn on CPU for audio complete/continue.

Done when a cold clone installs, the three engines stay hot on the GPU, the paragraph sounds like one take, chunks follow meaning, scripts stay small, Nano RTF is below 0.5, and brain RTF is think ÷ speak-synthesis.
