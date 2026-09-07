# Trident TTS — Production Knowledge Base & Execution Plan

**Current authoritative code:** Trident `3ee379918342a38265145e0233055d0b23723f72` → chatterbox.cpp `a20e18d09a4066de97c502b2ffaf9026eb5a66c2` → GGML `58c3805840b516b2a88ff867ccf7bb41dba79951`, branch `v3-optimization`.

## Repository / host contract

Windows 11, PowerShell, Python 3.11, VS2022 x64, CMake `C:/Program Files/CMake/bin/cmake.exe`, Vulkan SDK `C:/VulkanSDK/1.4.357.0`. Repos are siblings: `.\Trident` and `.\chatterbox.cpp`.

Verified from the pushed archives: both HEADs equal `origin/v3-optimization`; Git object databases pass `git fsck`; Trident pins exact chatterbox HEAD; Python sources compile; protocol-v4 client/server layouts match; `server.cpp` and public headers pass C++17 syntax review; the failed `check()` call was removed in `a20e18d`.

**Not yet verified:** a complete Windows/MSVC/Vulkan build and real post-fix synthesis run from this exact pin.

## North Star

A lean local speech loop where:
1. Gemma writes for speech, not Markdown.
2. CPU SaT preserves intended breath lines and makes meaning-aware cuts.
3. One Python connection owns one complete TTS request.
4. One native Engine call owns one acoustic session.
5. T3 runs before S3 for every piece, in order.
6. S3 history is context only; emitted PCM is never replayed.
7. WAV is mono PCM16 at 24 kHz.
8. Every failure can be traced text → tokens → S3 → PCM → WAV.
9. Complete warm Nano RTF stays **<0.7**.

## Current pipeline

`prompt → brain.py/Gemma → one breath per line → chunk.py/SaT CPU → main.py protocol v4 → chatterbox-server → T3 → S3Gen → PCM16/24k → WAV → parakeet.py + listening`

### Brain
`brain.py` uses pinned llama.cpp/Gemma. System output rules: natural speech only, short sentences, **one breath per line**, split long similar runs, expand numbers/abbreviations when useful, no Markdown/code/URLs/meta-reasoning.

### Chunker
`chunk.py` uses `sat-3l-sm`, ONNX Runtime CPU only, 1+1 threads, sequential execution. It normalizes whitespace, preserves brain newlines, then SaT may meaning-cut inside each breath line. No project environment-variable steering.

### TTS request ownership
Protocol v4 request header = **7 little-endian uint32 / 28 bytes**:

`magic | version | kind | response | piece | total | payload_bytes`

Responses reuse the layout; field 6 is chunk index. Request kinds: synth=1, close=3. Response kinds: PCM=1, done=2, error=4, closed=5.

One connection sends pieces `0..total-1` contiguously with one response ID. Native `Engine::synthesize_pieces_streaming()` resets acoustics once, derives the final piece internally, and executes synchronous `T3(N) → S3(N)`.

## Model matrix

| Model | Context | Threads | CFM | Repeat | CFG | Exaggeration |
|---|---:|---:|---:|---:|---:|---:|
| Nano | 2048 | 4 | **1** | 1.2 | 0 | 0 |
| Turbo | 8196 | 4 | **2** | 1.2 | 0 | 0 |
| V3 | 2048 | 6 | **5** | 1.5 | .3 | .5 |

All use seed 42, GPU layers 99, top-k 1000, top-p .95, min-p 0, temperature .8, max tokens 1000, Q4_0 T3 + S3Gen GGUFs.

Current official Turbo/Nano execution uses CFM=2. Treat Nano CFM=1 as an explicit experiment until Windows A/B evidence proves whether 1 or 2 is preferable. Do not bulk-copy upstream V3 defaults; test one dimension at a time.

## S3 acoustic contract

Current constants:
- speech history: **25 tokens**
- lookahead: **3 tokens**
- waveform scale: **960 samples/token at 24 kHz**
- boundary hold: **480 samples / 20 ms** when another piece follows

History is prepended to the next S3 window. S3 replaces historical mel frames with cached frames, regenerates the window, crossfades the held previous PCM into the regenerated boundary, then emits from:

`history_tokens*960 - pending_pcm` to `wav_size - hold`

The next investigation must prove those ranges at runtime. Never conflate text overlap, compute concurrency, acoustic history, and PCM crossfade.

## Logging / traceback

Read `.runtime-logs\tts.log` **completely after every behavioral run**.

Native identity: `seq`, `conn`, `response`, `piece`, `event`.

Important events:
- `server.start`, `server.ready`
- `synthesis.request`, `synthesis.queued`
- `t3.begin`, `t3.text`, `t3.end`
- `s3.begin`, `s3.end`
- `synthesis.first_result`, `synthesis.completed`, `synthesis.failed`
- `session`

`t3.end` carries exact `speech_seq`, speech hash/count, stop reason, KV and speech positions.  
`s3.begin/end` carries history/new/window hashes, CFM, caches, pending PCM, emission range, samples and stage timings.  
Python `[synth]` carries exact source/piece hashes, byte/sample intervals, per-piece PCM hash and final WAV SHA-256.

Find the **first wrong boundary**, not the loudest symptom.

## Current unresolved work

1. **Rerun installation after `a20e18d`.** Previous build reached project C++ and failed only on the now-removed stale `check()` call. A successful later build is not assumed.
2. Prove fresh EXE/DLL/GGUF/reference hashes match current pins.
3. Reproduce canonical 1–30 on Nano/Turbo/V3 and full pipeline.
4. Reproduce isolated Nano 20–30. This is required because within-piece repetition cannot be explained solely by cross-piece history.
5. Nano A/B: same seed/text/voice, CFM=1 vs CFM=2. Compare exact T3 sequences, S3 traces, PCM hashes, ASR and listening.
6. Verify V3 learned speech-position behavior on real runtime before changing V3 sampling/CFM.
7. Prove every S3 history/hold interval is emitted exactly once.
8. Verify WAV header/sample arithmetic and independent ASR + human listening.
9. Measure cold startup separately. Warm RTF = request→complete WAV wall / `(samples/24000)`. Nano must be <0.7.
10. Exercise installer resume: valid completed phases skip; only missing T3/S3 conversion work repeats; temp conversion environments disappear.
11. **Hardening:** Turbo checkpoint URL currently uses Hugging Face `resolve/main`; pin an immutable revision/checksums before calling Turbo artifacts fully reproducible.
12. **Hardening:** `TTS.start()` trusts an already-listening model port. Before upgrades/runs, unload stale model servers. Future code may bind running-process identity to the runtime pin.

## Canonical PowerShell run

```powershell
Set-Location C:\Users\px-wjt\Downloads\Trident
python .\main.py
Get-Content .\.runtime-logs\tts.log

$Numbers = 'One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. Eleven. Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. Eighteen. Nineteen. Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty.'
$Tail = 'Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty.'

python .\tts_nano.py --text $Numbers --seed 42 --cfm-steps 1
python .\parakeet.py .\tts_out.wav
python .\tts_nano.py --text $Tail --seed 42 --cfm-steps 1

python .\tts_nano.py --unload
python .\tts_nano.py --load --cfm-steps 2
# in another PowerShell:
python .\tts_nano.py --text $Numbers --seed 42 --cfm-steps 2
python .\parakeet.py .\tts_out.wav

python .\tts_turbo.py --text $Numbers --seed 42 --cfm-steps 2
python .\tts_v3.py --language en --text $Numbers --seed 42
python .\main.py $Numbers
```

Preserve every generated WAV and complete log used as evidence.

## Project rules

- PowerShell only on the production host; workspace-relative paths.
- Read current source/logs before editing behavior.
- Preserve user edits/deletions.
- No tests/harnesses/mocks for synthesis proof; use real application runs.
- No retries, fallback mazes, catch-and-ignore, defensive wrappers or permanent log spam.
- No post-hoc audio deduplication or number-specific hacks.
- Do not hide defects by blindly changing repeat penalties.
- Change one causal variable at a time.
- Prefer deletion and single ownership contracts.
- Fail hard on real errors.
- Runtime evidence outranks comments/docs/issues.
- Issues/PRs are leads, not proof.

## Git contract

Native changes always land first:

`chatterbox.cpp edit → build/verify → commit → push v3-optimization → copy exact SHA → update CHATTERBOX_REV in Trident → rebuild/verify → commit Trident → push`

Never pin an unpushed native commit. Never use “latest” as the native dependency contract.

Current pin chain is already aligned:
- chatterbox `a20e18d09a4066de97c502b2ffaf9026eb5a66c2`
- Trident `3ee379918342a38265145e0233055d0b23723f72`

## Acceptance / confidence

Source and integration state are verified. Acoustic correctness is **not** closed until the real Windows runtime passes.

Do not declare 100/100 until:
- exact pinned native build succeeds;
- all model artifacts/binaries have proven provenance;
- Nano/Turbo/V3 + full pipeline speak requested content once and in order;
- first wrong boundary for any remaining repetition is proven and corrected;
- S3 context/hold and Python PCM ranges are once-only;
- PCM16 mono 24 kHz WAV is exact;
- ASR and human listening agree;
- Nano warm RTF <0.7;
- installer resume is proven;
- final repositories are pushed and clean.

If any gate is missing, report the exact blocker and current confidence rather than claiming completion.
