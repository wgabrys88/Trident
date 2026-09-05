# Cold-start: Trident TTS (paste this whole file into a new session)

You have **no prior session history**. Grok remote history is off. GitHub remotes are the only memory. This file is the bootstrap.

Do **not** start implementing a “fix” in the first turn. The human will clone, `--install`, listen, and then code-review with you. Wait for their listen notes. The plan below is a **hypothesis**, not a decision.

---

## What the human wants (rephrased)

Treat GitHub as if this machine will be destroyed after every push. A fresh clone of **Trident `runner-z`** must install and speak. Quality is judged by a **human listener**, not by constants: not 20 ms, not `overlap_ms`, not fence theory. If the paragraph sounds continuous, in-voice, and finishes in about the old sequential wall time, that is success. If it stutters, holes, clicks, or is slower than sequential for no audible gain, that is failure — even if the pipeline “overlaps” on paper.

---

## Clone, install, listen (Windows)

```
git clone -b runner-z https://github.com/wgabrys88/Trident.git
cd Trident
python tts_nano.py --install
python tts_nano.py --text "Now let's make my mum's favourite. So three mars bars into the pan. Then we add the tuna and just stir for a bit, just let the chocolate and fish infuse. A sprinkle of olive oil and some tomato ketchup. Now smell that. Oh boy this is going to be incredible."
python parakeet.py tts_out.wav
```

`--install` clones `https://github.com/wgabrys88/chatterbox.cpp.git` at `CHATTERBOX_REV`, checkouts ggml `GGML_REV`, applies `src/ggml-vulkan-queue.patch`, builds `chatterbox-server`. Do not push `ggml-org/ggml`.

Host assumptions baked into `tts_nano.py` (not portable): Python 3, Visual Studio 17 2022 x64, CMake `C:/Program Files/CMake/bin/cmake.exe`, Vulkan SDK `C:/VulkanSDK/1.4.357.0`. Models/voice download on first install. Do not commit models, binaries, logs, or WAVs.

WAV: `out_<dd-mm-yy-HH-MM-SS>_tts.wav` and copy `tts_out.wav`. Server log: `.runtime-logs/tts.log` (append-only; read only the new session). Port 17933. Parakeet: `parakeet.py` (port 17934).

Do not change knobs, `CHUNK_CHARS=50`, `GGML_REV`, `NANO_REV`, public Engine callback, or TTR2.

---

## Pins (must match)

| Repo | Remote | Branch | SHA |
|------|--------|--------|-----|
| Trident | `https://github.com/wgabrys88/Trident.git` | `runner-z` | this commit (file lives here) |
| chatterbox.cpp | `https://github.com/wgabrys88/chatterbox.cpp.git` | `main` | `3593cf22d6d2a8d044e1af8968f1220fe6b03aa1` |
| ggml (pinned, patched at build) | do not push | — | `58c3805840b516b2a88ff867ccf7bb41dba79951` |
| Nano weights | HuggingFace ResembleAI/chatterbox-nano | — | `NANO_REV=71ccd1d0081b430592cea481f4307e764e07bc64` |

`tts_nano.py` `CHATTERBOX_REV` **is** `3593cf2`. `_build` runs `git -C ggml apply --whitespace=nowarn src/ggml-vulkan-queue.patch`. Installed stamp `tools/runtime/tts/REVISION` must equal:

```
3593cf22d6d2a8d044e1af8968f1220fe6b03aa1 58c3805840b516b2a88ff867ccf7bb41dba79951
```

Never leave `CHATTERBOX_REV` uncommitted without a matching **pushed** native SHA. Native git: `git -C <chatterbox.cpp clone>`. Trident cwd is the wrong repo for C++ work. PowerShell: no `&&` (use `;`); no nested-quote `python -c`; `Set-Content -Encoding utf8NoBOM` is invalid on the old host (used ascii + strip CRLF for the ggml patch).

Sibling path on the old machine was `C:\Users\eb-wjt\Downloads\chatterbox.cpp`. On a new machine, clone it next to Trident or let `--install` fetch it.

---

## What already landed (do not redo)

Chatterbox `origin/main` (working tree was clean when this was written):

| SHA | What |
|-----|------|
| `c46535b677ef23be72ac2a225a7bb4fa6251dee8` | Logging: one `t3` line, one aggregated `s3` line, one `session` line. No per-line thread hash. No 16k Vulkan firehose. `src/overlap-vulkan-trace.patch` deleted. |
| `0f4ab055d0f1e265925f2844d2bf04b64e4306c7` | One `s3gen_synthesize` per piece (`final=true`, dummy pad `4299` × lookahead). Session-persistent `acoustic` + `speech_history` on `Engine::Impl`. `begin_synthesis` resets acoustics (second TCP client must not inherit). Recv `pending.empty()` locked. T3(N+1) fail during S3(N) throws original T3 error after join. Server same-epoch batch + socket coalesce. **Also launches T3(N+1) while S3(N) runs.** |
| `3593cf22d6d2a8d044e1af8968f1220fe6b03aa1` | `src/ggml-vulkan-queue.patch`: last real submit uses this context’s fence; `ggml_vk_synchronize` does **not** empty-submit a fence; transfer leftover host-waits this backend’s timeline; `ggml_vk_sync_buffers` uses buffer barriers on unsynced+prealloc, global only if empty. Counters `wait_us`/`submit_n`/`barrier_n` via `ggml_vk_overlap_counters` (`dllexport` from ggml-vulkan, `dllimport` from chatterbox). `queue_mutex` still only around `vkQueueSubmit`. |

Trident `6de62fb` pinned that SHA and the patch apply. This bootstrap commit does not change the pin.

Keep: producer/consumer of **pieces** (client queues all pieces before first recv; one TCP session). One S3 graph per piece. Persistent acoustics. Honest logs. Scoped-fence patch. Do **not** restore 25-token S3 hops. The S3 encoder is full-sequence Conformer (`ggml_soft_max` T×T, not causal). Prompt-activation concat cache is invalid. A real prompt cache is KV/prefix inside Conformer+CFM (graph rewrite, later).

---

## Mars-bars already measured on this pin (2026-09-05 20:38:50)

Client: `tts_synth=10.400s`, `audio_s=15.820s`, `tts_start=0.001s` (server already up). WAV `out_05-09-26-20-38-50_tts.wav`.

Server session line: `tts_synth=10392 first_audio_ms=1418 overlap_ms=7426`.

Protocol: one session, pieces 0–6 **queued before first T3**, one `terminal=done` per piece, `server.close.requested` + `server.closed`, no dummy `client_closed`. 7 S3 lines, `prompt_tokens=250` each. ~53 new log lines, not 16k.

Tokens: 50+53+77+50+75+37+54 = **396** (old sequential was ~380-class; same knobs; sampling drift — record it, do not chase it).

| | This pin | Old sequential | Hop prototype |
|--|----------|----------------|---------------|
| `tts_synth` | **10.4s worse** | 8.5–9.3s | ~15.5s |
| `first_audio_ms` | 1418 | ~1207 | ~967 |
| S3 calls | 7 | ~7 | 18 |
| `audio_s` | 15.82s | ~15.8s | — |

Parakeet recovered the paragraph: “Now let's make my mum's favourite so three Mars bars into the pan then we add the tuna and just stir for a bit just let the chocolate and fish infuse as sprinkle of olive oil and some tomato catch up now smell that oh boy this is going to be incredible”.

### GPU: overlap is CPU-real, GPU-fake

T3 `ms` sum 9280; T3 `wait_us` sum **2.96s**. S3 stage fields sum ~8.36s; S3 `wait_us` sum **7.00s**. S3 `submit_n` 254–255/piece, `barrier_n=2921`.

Two ggml backends (`Engine` T3 + cached S3) share **one** Vulkan compute queue. `overlap_ms=7426` is two CPU threads in flight. A fence on submit N still covers earlier queue work, so they host-wait on each other. The scoped-fence patch stopped empty-submit-*later* (do not wait for work after our last submit). It cannot create GPU concurrency on one queue. That is the measured reason wall time lost to sequential, **not** “we forgot to cache prompt `mu`” (sequential also re-encoded 250 prompt tokens × 7).

Iris Xe compute `queueCount` was **not** probed. Do not assume two queues exist.

### Audio: holes at piece joins, not impulse clicks

`final=true` is required for dummy pad, and it also does `end = wav.size()`, so `pending_pcm` is always empty. The cosine OLA in `s3gen_synthesize` never runs across pieces.

PCM at the six joins (piece sample counts from S3 `samples=`; piece 0 minus 480 client-stripped leading zeros): **30–70 ms of digital zeros** (trailing ~22–47 ms + leading ~10–27 ms). No sample-to-sample jump ≥8000 within 20 ms of a join. Listener effect is a **dropout/stutter at sentence boundaries**, not a spike click.

`n_trim = sr/50 = 480 samples ≈ 20 ms` is an existing fade/HiFT holdback constant in `chatterbox_tts.cpp`. It is **not** a quality target and not a rule to enshrine. If a join needs a different overlap, different history, or a different vocoder boundary, do that because it **sounds** better, not because a plan said 20 ms.

---

## Code map (where to look)

Native, chatterbox `3593cf2`:

- `src/chatterbox_engine.cpp` `pipeline_pieces` — launches T3(N+1) before `run_s3` of N; `overlap_ms` accumulated here.
- `src/chatterbox_engine.cpp` `run_s3` — `s.final = true` every piece; prepends `speech_history` (25 tokens); one synthesize call.
- `src/chatterbox_engine.cpp` `begin_synthesis` / `reset_acoustics` — must clear `acoustic` + `speech_history` per session.
- `src/chatterbox_tts.cpp` `s3gen_synthesize` — dummy pad if `final`; history mel overwrite on **history** frames; `n_trim`; pending OLA; emit `[begin, end)`.
- `src/s3gen_pipeline.h` — `kSpeechHistoryTokens=25`, `kSpeechLookaheadTokens=3`, `kSamplesPerToken=960`, `s3gen_piece_state`.
- `src/ggml-vulkan-queue.patch` — fence/barrier/counter patch applied at Trident `_build` onto ggml `58c3805`.
- `include/tts-cpp/chatterbox/log.h` — session `overlap_ms`, piece lines.

Trident:

- `tts_nano.py` — pin, patch apply, TTR2 client, `CHUNK_CHARS=50`, queue-all-then-recv, `TCP_NODELAY`, 300s timeout, bind port probe, strip 20 ms zeros on piece 0 chunk 0.
- `parakeet.py` — ASR check.

---

## Constraints still in force

- No tests, no harnesses, no extra design docs beyond this bootstrap.
- No process-wide inference lock. No third backend object. No ggml-org push.
- Commit+push after every **working** step: chatterbox `main` first (full SHA), then Trident `runner-z` pin if SHA moved, then `--install` once.
- Public Engine callback and TTR2 unchanged.

---

## Hypotheses (not ordered work — review may throw them out)

These were written after the 10.4 s run. They may be wrong. The human’s ears beat this list.

1. **If Iris Xe `queueCount < 2`:** stop launching T3(N+1) until S3(N) returns. Keep piece batching and one S3/piece. `overlap_ms=0` would then be honest, not a regression. Do not revert the scoped-fence patch.
2. **If `queueCount >= 2`:** distinct compute queues for T3 and S3 so fences do not cover each other. Only then is T3\|\|S3 real.
3. **Joins:** split “dummy pad” (`final`) from “hold some tail PCM into the next piece so the join is continuous.” Length should be whatever sounds continuous, not a hardcoded 20 ms religion. Fade-in only on the true start of the utterance.
4. **After wall time is at least sequential and joins sound good:** beat sequential. Valid: two queues if we sequentialized and the device has them; Conformer+CFM prompt KV (not memcpy of `mu`). Do not bring hops back unless prompt work is actually cheap.

Do not implement 1–4 before the human has listened on the new clone and you have done the code review they asked for.

---

## First turn of the new session (checklist)

1. Confirm remotes: Trident `runner-z` has this file; chatterbox `origin/main` is `3593cf2` (or a later SHA the human already pinned).
2. Code-review the pinned native+runner against this brief. Ask where the listener will hear a problem (joins, pacing, latency-to-first-audio) and where the GPU story is fake.
3. Incorporate the human’s listen notes and comments. Rephrase disagreements. Then propose a plan. Stop for approval if the change is large.
4. Implement only what the review+ears support. Chatterbox commit+push first, bump `CHATTERBOX_REV`, Trident commit+push, `--install` once, mars-bars, Parakeet, listen again. Put numbers in the Trident commit message.

Done when a cold clone installs, the mars-bars paragraph sounds like one take, and `tts_synth` is not worse than sequential unless the extra time is an audible quality win the human wants.
