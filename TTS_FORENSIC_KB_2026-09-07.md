# Trident TTS Production/Forensic Knowledge Base — 2026-09-07 — Pass 2

## Status and authority

This document records the second production pass built from the supplied Trident HEAD `7ebbfd20bb579259f55a6fba5fa2b88df4977c31` and chatterbox.cpp HEAD `335a94eb9d1211bee1b31e9df8bae24c684494dc` on branch `v3-optimization`.

The pre-existing deletion of `TTS_INVESTIGATION_KB.md` is intentionally preserved. This new KB is explicitly whitelisted by `.gitignore` so it can be committed normally.

**Current forensic confidence: 63/100.** The source architecture is substantially simpler and two control/parity defects are corrected in source, but this Linux investigation environment cannot perform the required Windows 11/MSVC/Vulkan/GGUF audio intervention runs. Do not call the overall repeated/scrambled-speech problem solved until those runs pass.

`main.py` deliberately contains:

```python
CHATTERBOX_REV = "PUSH_CHATTERBOX_FIRST_AND_SET_SHA"
```

This is a production safety gate, not an unfinished guess. The chatterbox commit must be pushed first. Only its exact pushed 40-hex SHA may replace that placeholder. `install_tts()` and direct TTS startup fail hard until the runtime stamp matches that exact chatterbox SHA plus the pinned GGML SHA.

## Repository and runtime baseline

- Trident branch: `v3-optimization`
- Trident supplied HEAD: `7ebbfd20bb579259f55a6fba5fa2b88df4977c31`
- chatterbox.cpp branch: `v3-optimization`
- chatterbox.cpp supplied HEAD / historical Trident pin: `335a94eb9d1211bee1b31e9df8bae24c684494dc`
- GGML pin: `58c3805840b516b2a88ff867ccf7bb41dba79951`
- target host: Windows 11, VS 2022 x64, CMake at `C:/Program Files/CMake/bin/cmake.exe`, Vulkan SDK `C:/VulkanSDK/1.4.357.0`
- observed GPU: GTX 1060 6 GB
- observed runtime capabilities: integer dot available; fp16/bf16/matrix-core capability unavailable on this device despite compiler/SDK feature probes
- output contract: mono PCM16, 24,000 Hz

## Preserved reproduction evidence

### Nano canonical 1–30

Historical fresh run retained from the first investigation pass:

- output: `out_07-09-26-11-55-26_tts.wav`
- cold/server startup: 16.228 s
- warm synthesis: 10.376 s
- audio duration: 26.980 s
- warm RTF: **0.385**
- four pieces
- response bytes: `236160 / 90240 / 362880 / 605760`
- T3 speech tokens: `124 / 47 / 189 / 315`
- all pieces stopped at EOS
- Parakeet independently transcribed duplicated 23, 25 and 28 in the last piece
- no callable listening tool was available in the investigation environment

This establishes the historical Nano performance margin under the required `<0.7` RTF ceiling, but it is **not** a post-change measurement.

### Nano isolated 20–30

Historical fresh run:

- output: `out_07-09-26-11-57-09_tts.wav`
- SaT split: `20–29` / `30`
- synthesis: 7.034 s
- audio: 12.620 s
- Parakeet transcribed repeated 24/25 **inside external piece 0**

Therefore cross-piece acoustic history cannot be the sole cause of Nano repetition. The unresolved within-piece branch remains T3 versus S3/meanflow/CFM versus conversion/quantization/runtime behavior.

## Proven historical defect: request acoustic lifetime followed opportunistic batches

At the supplied chatterbox HEAD the server queued pieces and drained opportunistic batches. `begin_synthesis()` was called from the batch path and cleared acoustic/session state. The old final-piece decision was also derived from the current batch rather than the complete external request.

The preserved baseline runtime log showed:

1. external pieces 0–3 were queued for one synthesis;
2. piece 0 was synthesized in one drain;
3. `s3.history_cleared` appeared after that piece;
4. pieces 1–3 were handled by a later drain;
5. internal T3 piece numbering restarted;
6. the first drain therefore had finalization semantics inconsistent with the complete request.

That is a **PROVEN CAUSE of broken cross-piece continuity/finalization semantics**. It is not sufficient to explain the isolated within-piece Nano repetition.

## Pass-2 production architecture: one request owns one acoustic session

The previous queue/batch/epoch correction was simplified further so the architecture itself makes the old bug class difficult to recreate.

### Protocol v4

One TCP connection now carries one complete synthesis request.

Request frame: seven little-endian `uint32` values:

```text
magic | version | kind | response | piece | total | payload_bytes
```

Response frame uses the same 28-byte physical header:

```text
magic | version | kind | response | piece | chunk | payload_bytes
```

Kinds:

```text
request 1 = synthesize
request 3 = close
response 1 = PCM
response 2 = done
response 4 = error
response 5 = closed
```

Removed from the production protocol:

- epoch control;
- `advance_epoch`;
- cancellation messages;
- queue ownership state;
- batch IDs and batch grouping;
- worker condition variables;
- queued request cancellation semantics.

Python already knows and sends the entire SaT piece set before reading audio, so the native server now receives the exact contiguous `piece=0..total-1` request before synthesis begins.

### Engine ownership contract

Public `Engine` no longer exposes `begin_synthesis()` or `cancel()`.

`Engine::synthesize_pieces_streaming()` now owns the complete acoustic lifetime:

```text
reset acoustic state once
for each supplied external piece in order:
    generate T3 speech tokens synchronously
    synthesize S3 synchronously
    derive last_piece internally from vector position
```

`SynthesisPiece` contains only:

```text
external piece id
text
```

The caller cannot reset S3 in the middle of a request and cannot lie about which piece is final.

### Removed fake concurrency

The prior engine created a T3 worker thread and immediately joined it before S3. That provided no T3(N+1) || S3(N) overlap but retained worker/atomic/mutex state and obscured execution order.

The production path is now explicit synchronous:

```text
T3(piece N) -> S3(piece N) -> T3(piece N+1) -> S3(piece N+1)
```

S3 model preload may still overlap with voice baking during engine initialization because that is real independent startup work. Synthesis-time fake concurrency is gone.

### Cancellation removed from S3

Because one local synchronous request is now the ownership unit, the unused atomic cancellation pointer and repeated cancellation checks were removed from S3Gen. Real errors throw and fail the request directly.

## V3 parity correction: KV position is not learned speech position

Current official Resemble T3 inference uses two different coordinates:

- transformer/KV sequence position;
- learned speech-position embedding position.

Speech BOS uses learned speech position 0 and generated speech tokens advance by generated speech index (`i+1`), independent of prompt-inclusive KV length.

The local V3 path previously used prompt-inclusive `n_past` for both. Pass 2 preserves the source correction:

```text
kv_pos      -> transformer position / cache placement
speech_pos  -> learned speech position embedding
```

`eval_step_mtl()` now receives both coordinates and range-checks `speech_pos` against the speech-position table.

Classification: **upstream-parity defect corrected in source; audible causal intervention still requires Windows before/after execution.**

## September 2026 upstream facts rechecked

Authoritative current Resemble source was rechecked on 2026-09-07.

- Turbo/Nano use the same Turbo model family; Nano uses the smaller GPT-2 backbone.
- Turbo/Nano use meanflow S3Gen.
- Current executable Turbo/Nano generation passes `n_cfm_timesteps=2`.
- Current Turbo defaults are approximately temperature 0.8, top-p 0.95, top-k 1000 and repetition penalty 1.2; CFG/min-p/exaggeration are not active model controls there.
- Current multilingual defaults are approximately exaggeration 0.5, CFG 0.5, temperature 0.8, repetition penalty 1.2, min-p 0.05 and top-p 1.0.
- Current S3Gen uses 16 kHz reference/speaker/tokenizer processing and 24 kHz synthesized audio.
- Current S3Gen default step count is 2 for meanflow and 10 for non-meanflow when not overridden.
- The upstream CFM solver creates `n_timesteps + 1` time coordinates and executes one estimator/Euler update for each interval. Therefore `n_cfm_timesteps=2` means **two estimator passes**.

This resolves an important terminology contradiction: upstream marketing describes Turbo/Nano as a “single-step decoder,” while the current executable source drives two CFM/Euler estimator intervals. For this investigation executable source and runtime evidence outrank marketing terminology.

Current official source references:

- `https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/tts_turbo.py`
- `https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py`
- `https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/models/t3/t3.py`
- `https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/models/s3gen/s3gen.py`

### Consequence for Nano CFM

Local production Nano still defaults to `cfm_steps=1`; Turbo defaults to 2.

The local S3 implementation uses the same interval semantics: CFM `N` creates `N+1` time coordinates and performs `N` estimator updates. Therefore Nano=1 is a real one-pass local deviation from current official Turbo/Nano execution.

It is a strong controlled candidate for the **within-piece** Nano repetition because the isolated 20–30 defect occurred before any cross-piece history. It is **not** promoted to the default yet because the required Windows A/B has not established quality or post-change RTF. Use `--cfm-steps 2` as a single-dimensional experiment.

## S3 acoustic boundary contract

Current local constants must be verified in the fresh runtime log, but source currently defines:

```text
speech history      = 25 tokens
speech lookahead    = 3 tokens
samples/token       = 960 at 24 kHz
boundary hold       = 480 samples (20 ms)
```

For each new piece the S3 input window is:

```text
last <=25 prior speech tokens + new T3 speech tokens
```

The emitted interval is derived from history regeneration and pending PCM. Current source records:

- history token count/hash;
- new token count/hash;
- full window hash;
- CFM steps actually used;
- `pending_in` and `pending_out`;
- mel-cache frames;
- source cache length;
- phase state length;
- `emit_begin` / `emit_end`;
- held tail size;
- emitted sample count.

The fresh Windows run must prove that regenerated history before `emit_begin` is not emitted twice and that the 480-sample held boundary is crossfaded exactly once.

## Forensic logging contract

The objective is one causal record per boundary, not token-by-token spam.

Every native event has:

```text
seq | conn | event | response | external piece | monotonic timestamp where needed | run identity
```

### T3

`t3.begin` records:

- session piece;
- text size/hash;
- max tokens;
- temperature/top-k/top-p/min-p;
- repetition penalty;
- CFG.

`t3.text` records exact text-token count/hash.

`t3.end` records:

- exact generated speech-token count;
- hash;
- **complete generated speech-token sequence**;
- MTL pending-tail drop/token state;
- EOS versus repeat stop;
- KV position;
- learned speech position;
- elapsed T3 time;
- Vulkan wait/submit/barrier counters where available.

The earlier O(n²) repeated-subsequence scan was removed from the latency-critical path. Because the complete speech sequence is retained, repeated-subsequence analysis can be done exactly offline without synthesis overhead.

### S3

`s3.begin` records session piece, true request-final flag, history count/hash, new-token count/hash and complete window hash.

`s3.end` records actual CFM steps, acoustic caches, emission arithmetic, stage timings, audio duration/RTF and Vulkan counters.

The obsolete `overlap_ms` session field was removed. It had no active producer and conflated compute concurrency with acoustic history/crossfade semantics.

### Python PCM assembly

For every external piece Python records:

```text
response id
piece/total
source text and SHA
PCM byte start/end
PCM sample start/end
leading silence trim bytes
PCM SHA
```

The final WAV SHA-256 is emitted after close acknowledgement.

## Installer/runtime contract after pass 2

### Exact runtime provenance

TTS direct startup accepts the native runtime only if all required binaries/licenses exist and `tools/runtime/tts/REVISION` equals:

```text
<exact pushed chatterbox SHA> <exact pinned GGML SHA>
```

Old unstamped binaries are no longer blessed with the requested pin. Protocol-v4 Python therefore cannot intentionally start an on-disk runtime that does not match the requested source pin.

### Short paths

Temporary TTS install paths are intentionally short (`s`, `b`, `c`, `k`) to reduce Windows nested-path depth. This addresses the preserved MSVC C1083 failure during the nested Vulkan shader-generator compiler check.

### Phase-aware conversion

Each GGUF conversion now declares its exact checkpoint assets. `_missing_conversions()` determines which outputs are absent before the converter venv is created.

If only one GGUF is missing:

- only that converter script is included in the sparse source checkout;
- only the checkpoint/assets needed by that conversion are downloaded;
- only that conversion runs;
- already completed GGUF output is left untouched.

Examples:

```text
Nano/Turbo T3 -> T3 safetensors + conds + voice encoder + GPT-2 tokenizer assets
Nano/Turbo S3 -> s3gen_meanflow.safetensors + conds
V3 T3         -> t3_mtl23ls_v3 + conds + ve + grapheme JSON + Cangjie JSON
V3 S3         -> s3gen.pt + conds
```

Temporary converter/checkpoint/build state stays inside `TemporaryDirectory` and is removed afterward.

### Model cards and voice

Model cards and voice/reference assets are independent phases. A missing card does not create a converter environment. A complete runtime/model/card/voice state is skipped.

The actual completed/interrupted installer exercise still must run on Windows before this contract earns full evidence credit.

## Trident reductions and alignment

Pass 2 also reduces duplicated Python plumbing:

- Nano/Turbo/V3 share one `TTS` implementation and one common knob contract;
- model wrapper files only define model-specific data/defaults;
- duplicate SHA helper removed from `brain.py`;
- checkout and port-readiness helpers are shared;
- `chunk.py` no longer mutates `CUDA_VISIBLE_DEVICES`; CPU SaT remains selected by ONNX Runtime provider;
- TTS and Parakeet readiness are defined by the listening port; chatterbox binds only after model warm-up completes;
- V3 warm/synthesis timing uses elapsed durations, not an absolute `perf_counter()` value;
- redundant `start()` inside `synthesize()` is removed;
- all zero-valued floating TTS controls are true floats, so CLI fractional experiments parse correctly;
- Parakeet uses the single build layout proven in the preserved Windows installation log instead of path-search/copy fallback branches.

## Code-size result

Raw tracked source line counts relative to the supplied repository HEADs:

```text
Trident tracked Python:       1040 -> 1030   (-10)
chatterbox tracked cpp/h/py:  8591 -> 8267  (-324)
combined source delta:                     -334 lines
```

Largest reductions:

```text
chatterbox src/server.cpp               413 -> 244  (-169)
chatterbox src/chatterbox_engine.cpp    493 -> 408   (-85)
chatterbox include/.../log.h            133 ->  97   (-36)
Trident parakeet.py                     211 -> 183   (-28)
Trident model wrappers combined          85 ->  50   (-35)
```

`main.py` grows because it now centralizes the code removed from three TTS wrappers plus provenance, protocol, resumable conversion and deterministic assembly tracing. Total runtime source is nevertheless smaller.

## Static verification completed in this environment

Completed after the latest source changes:

- all Trident Python modules compile with `py_compile`;
- all three model specs import and expose intended defaults;
- Python `TTS_FRAME.size == 28` and protocol version is 4;
- native protocol version is 4 and native request header is seven `uint32` values;
- `git diff --check` passes in both repositories;
- `server.cpp` passes C++17 syntax checking using temporary Windows socket declarations;
- stale runtime references to old epoch/advance/cancel/batch APIs were searched and removed;
- `CUDA_VISIBLE_DEVICES` steering is absent from runtime source;
- the V3 public/internal evaluator signature mismatch found during pass 1 is fixed;
- a pass-2 logging-context member-name regression was found by interface audit and fixed before packaging.

### Native-build blocker

A complete chatterbox native compile is **not** claimed. The exact pinned GGML source is not present in this Linux environment, and direct Git access fails DNS resolution for `github.com`. Compiling the GGML-dependent translation units therefore stops at the expected missing `ggml-alloc.h` dependency.

The Windows/MSVC/Vulkan host remains the authoritative build and execution environment.

## PowerShell production integration sequence

Run these commands after replacing files while preserving each repository's `.git` directory.

### 1. chatterbox first

```powershell
Set-Location C:\Users\px-wjt\Downloads\chatterbox.cpp

git status --short --branch
git diff --check
git diff

git add include/tts-cpp/chatterbox/engine.h `
        include/tts-cpp/chatterbox/log.h `
        src/chatterbox_engine.cpp `
        src/chatterbox_t3_internal.h `
        src/chatterbox_tts.cpp `
        src/main.cpp `
        src/s3gen_pipeline.h `
        src/server.cpp `
        src/t3_mtl.cpp

git commit
git push origin v3-optimization
$ChatterboxSha = (git rev-parse HEAD).Trim()
$ChatterboxSha
```

Do not continue unless the push succeeds.

### 2. pin the pushed SHA in Trident

```powershell
Set-Location C:\Users\px-wjt\Downloads\Trident
$Path = (Resolve-Path .\main.py).Path
$Text = [IO.File]::ReadAllText($Path)
$Text = $Text.Replace('PUSH_CHATTERBOX_FIRST_AND_SET_SHA', $ChatterboxSha)
[IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))

python -m py_compile .\main.py .\brain.py .\chunk.py .\parakeet.py `
    .\tts_nano.py .\tts_turbo.py .\tts_v3.py
git diff --check
```

### 3. real installer/build provenance

```powershell
python .\tts_nano.py --install
python .\tts_nano.py --provenance
Get-Content .\.runtime-logs\tts.log
```

Record/hash the resulting EXE, DLLs, GGUFs, reference voice and runtime revision before detailed trace interpretation.

## Required causal experiments

Canonical text:

```powershell
$Numbers = 'One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. Eleven. Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. Eighteen. Nineteen. Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty.'
$Tail = 'Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty.'
```

### Nano local CFM=1 baseline on the new runtime

Terminal A:

```powershell
python .\tts_nano.py --load --cfm-steps 1
```

Terminal B:

```powershell
python .\tts_nano.py --text $Numbers --seed 42 --cfm-steps 1
python .\parakeet.py .\tts_out.wav
Copy-Item .\.runtime-logs\tts.log .\.runtime-logs\nano-cfm1.log

python .\tts_nano.py --text $Tail --seed 42 --cfm-steps 1
python .\parakeet.py .\tts_out.wav
Copy-Item .\.runtime-logs\tts.log .\.runtime-logs\nano-tail-cfm1.log
```

### Nano current-upstream execution parity: CFM=2

```powershell
python .\tts_nano.py --unload
python .\tts_nano.py --load --cfm-steps 2
```

Then:

```powershell
python .\tts_nano.py --text $Numbers --seed 42 --cfm-steps 2
python .\parakeet.py .\tts_out.wav
Copy-Item .\.runtime-logs\tts.log .\.runtime-logs\nano-cfm2.log

python .\tts_nano.py --text $Tail --seed 42 --cfm-steps 2
python .\parakeet.py .\tts_out.wav
Copy-Item .\.runtime-logs\tts.log .\.runtime-logs\nano-tail-cfm2.log
```

Because CFM lies downstream of T3, identical `t3.end speech_seq` values with different semantic/PCM results localize the changed behavior to S3/decoder execution rather than T3 sampling.

### Turbo

```powershell
python .\tts_turbo.py --load --cfm-steps 2
python .\tts_turbo.py --text $Numbers --seed 42 --cfm-steps 2
python .\parakeet.py .\tts_out.wav
python .\tts_turbo.py --unload
```

### V3 learned-position intervention first

Run the corrected learned-position source using the existing local knobs before changing any sampling/CFM value:

```powershell
python .\tts_v3.py --load --language en
python .\tts_v3.py --text $Numbers --language en --seed 42
python .\parakeet.py .\tts_out.wav
python .\tts_v3.py --unload
```

Only then vary one upstream-parity dimension per server run:

```text
repeat penalty: 1.5 -> 1.2
min-p/top-p:    0/.95 -> .05/1.0
CFG:            .3 -> .5
CFM:            5 -> 10
```

Never change all dimensions together when causal attribution is required.

### Full Gemma -> CPU SaT -> TTS -> Parakeet

After direct model synthesis is correct:

```powershell
python .\main.py $Numbers
```

Verify Gemma breath-line structure, SaT concatenation, request piece identity, native T3/S3 identity, Python PCM intervals, final WAV and independent ASR.

## How to locate the first wrong boundary

For each failed output, compare in this order:

1. exact Python/SaT piece text;
2. request response/piece sequence;
3. `t3.text` token hash;
4. exact `t3.end speech_seq`;
5. `s3.begin` history/new/window hashes;
6. `s3.end` CFM/cache/emission arithmetic;
7. Python PCM byte/sample ranges and hashes;
8. WAV header/sample count/hash;
9. Parakeet semantic transcript;
10. direct human listening on the Windows host.

Interpretation:

- duplicate source text -> upstream Gemma/SaT;
- wrong response/piece order -> transport/routing;
- repeated semantic material already represented in T3 sequence -> T3 candidate;
- deterministic identical T3 sequence but CFM-only A/B changes the repeated audio -> S3/decoder candidate;
- repetition starts exactly at a piece boundary and matches history/hold offsets -> acoustic history/trim candidate;
- native emission ranges are unique but Python hashes/ranges duplicate -> assembly candidate;
- WAV + ASR are correct but one player repeats -> playback candidate.

Never treat EOS, fifth-consecutive stopping or an absent error as proof of semantic correctness.

## Confidence ledger

Current score: **63/100**.

| Gate | Score | Evidence still required |
|---|---:|---|
| Evidence completeness | 8/10 | Inspect the fresh Windows binaries/GGUF/WAV metadata and complete new runtime logs. |
| Provenance | 6/10 | Hash freshly built EXE/DLL/GGUF/voice and prove exact pushed chatterbox + pinned GGML generated them. |
| Reproduction | 10/10 | Historical canonical and isolated failures reproduced; reconfirm on new runtime. |
| Trace correlation | 6/10 | Execute protocol-v4 trace and correlate every piece to exact new WAV offsets. |
| T3 proof | 5/10 | Compare exact deterministic speech sequences against failing/correct audio. |
| S3 proof | 8/15 | Prove cache/history/emission values at runtime; resolve Nano within-piece branch. |
| PCM/WAV proof | 6/10 | Verify fresh mono PCM16/24k arithmetic and once-only assembly. |
| Playback/semantic | 3/5 | ASR evidence exists; direct listening confirmation is still required. |
| Upstream parity | 5/5 | September 2026 Turbo/MTL/S3/T3 behavior refreshed. |
| Causal intervention | 0/5 | Modified binaries have not been executed before/after on Windows. |
| Performance | 3/5 | Historical Nano warm RTF 0.385; post-pass-2 RTF still required. |
| Lean code | 3/3 | Production source is net -334 raw lines while retaining stronger causal trace. |
| Integration | 0/2 | chatterbox push -> exact Trident pin -> Trident push still must occur. |

## Acceptance criteria for 100/100

- chatterbox native build succeeds on the exact Windows/MSVC/Vulkan host;
- binaries and models are tied to exact source/converter/pins;
- Nano 1–30 and isolated 20–30 contain every item once in order;
- Turbo 1–30 contains every item once in order;
- V3 is correct or any remaining model limitation is separately proven;
- full Gemma/SaT path remains correct;
- every external piece maps unambiguously to T3, S3, PCM and final WAV ranges;
- no unintended history replay or PCM replay exists;
- final WAV is mono PCM16 at 24,000 Hz with exact size/duration arithmetic;
- human listening agrees with PCM/ASR evidence;
- fresh Nano warm RTF remains below 0.7;
- completed installer phases skip and interrupted partial conversion only performs missing work;
- no project environment-variable steering exists;
- diagnostics proven unnecessary after root-cause closure are removed;
- chatterbox is committed/pushed before Trident receives the exact SHA;
- Trident is then verified, committed and pushed;
- both final Git trees are clean.
