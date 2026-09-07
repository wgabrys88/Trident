# Trident TTS Forensic Knowledge Base — 2026-09-07

## Status

This document supersedes no deleted project file. The pre-existing deletion of `TTS_INVESTIGATION_KB.md` is intentionally preserved. This KB records only the evidence and source state produced during the 2026-09-07 continuation.

**Current forensic confidence: 62/100.** Do not call the overall malformed/repeated-speech problem fully solved until the Windows/Vulkan intervention runs below pass. The cross-batch state/finalization defect is proven from source + baseline runtime logs. The V3 learned speech-position mismatch is a proven upstream-parity defect but is not yet proven as the audible V3 root cause. The isolated Nano within-piece repetition remains unresolved.

## Repository baseline

- Trident branch: `v3-optimization`
- Trident supplied HEAD: `7ebbfd20bb579259f55a6fba5fa2b88df4977c31`
- chatterbox.cpp branch: `v3-optimization`
- chatterbox.cpp supplied HEAD / historical Trident pin: `335a94eb9d1211bee1b31e9df8bae24c684494dc`
- pinned GGML revision: `58c3805840b516b2a88ff867ccf7bb41dba79951`
- baseline runtime: Windows 11, VS 2022 x64, Vulkan SDK 1.4.357.0, GTX 1060 6 GB
- runtime device evidence: integer dot available; fp16/bf16/matrix-core capability not available on the actual GTX 1060 runtime despite compiler/SDK feature probes.

`main.py` in this replacement deliberately contains `CHATTERBOX_REV = "PUSH_CHATTERBOX_FIRST_AND_SET_SHA"`. This is intentional. The chatterbox changes must be committed and pushed first; only then may that placeholder be replaced by the exact pushed 40-hex SHA. `install_tts()` fails hard until this is done.

## Preserved baseline observations

### Nano canonical 1–30

Fresh historical run:

- output: `out_07-09-26-11-55-26_tts.wav`
- startup: 16.228 s
- warm synthesis: 10.376 s
- audio: 26.980 s
- RTF: 0.385
- four pieces
- bytes: `236160 / 90240 / 362880 / 605760`
- T3 speech tokens: `124 / 47 / 189 / 315`
- all four T3 pieces stopped at EOS
- Parakeet independently transcribed duplicated 23, 25, and 28 in the final piece
- no independent listening tool was available in the investigation environment

### Nano isolated 20–30

Fresh historical run:

- output: `out_07-09-26-11-57-09_tts.wav`
- SaT split into `20–29` and `30`
- synthesis: 7.034 s
- audio: 12.620 s
- Parakeet transcribed repeated 24/25 **inside the first piece**

This falsifies **cross-piece history as the sole cause** of Nano repetition. It does not identify whether the first wrong boundary is T3 or S3Gen.

## Proven control-flow defect: acoustic state was reset per opportunistic batch

### Baseline source

At supplied chatterbox HEAD, `src/server.cpp` called:

```cpp
tts.begin_synthesis();
```

inside each opportunistic drain of queued requests. `begin_synthesis()` reset:

- cancellation state;
- `pieces_in_session`;
- S3 acoustic state;
- `speech_history`.

The old `pipeline_pieces()` also derived `last_piece` from `i + 1 == work.size()`, where `work` represented only the current opportunistic batch.

### Baseline runtime proof

The preserved `tts.log` shows the same request queued as external pieces 0–3, then:

1. piece 0 is synthesized;
2. `s3.history_cleared` appears immediately afterward;
3. the later drain starts external pieces 1–3;
4. internal T3 numbering restarts at piece 0;
5. the first batch therefore treated external piece 0 as its final acoustic piece even though the request still had pieces 1–3.

This is a proven cross-piece continuity/finalization bug. It can create boundary discontinuity or state semantics inconsistent with the intended complete request. It also made the old `piece=` log field insufficient for reliable external/internal correlation.

### Implemented correction

Protocol version is now 3. Every synthesis request carries:

- response ID;
- external piece ID;
- total piece count.

The server:

- groups an opportunistic batch only within the same epoch **and response**;
- no longer calls `begin_synthesis()` per batch;
- passes external piece ID and `last = piece + 1 == total` into the engine;
- preserves S3 history and PCM hold state across later batches of the same request;
- keeps a persistent session-piece index separate from callback/batch indexing.

The engine's S3 `last_piece` decision therefore derives from the complete request, not the current socket drain.

## Proven V3 upstream-parity defect: learned speech position used KV position

Current official Resemble T3 source uses:

- speech BOS at fixed learned speech position `0`;
- each generated speech token embedded at fixed position `i + 1`.

The local V3 implementation previously reused `n_past`, which includes conditioning/text prompt KV positions, as the learned speech position. That conflated two distinct coordinates:

- transformer KV position;
- learned speech-position embedding index.

Implemented correction:

- `eval_step_mtl()` now accepts both `n_past` and `speech_pos`;
- generation starts speech position at `1` after sampling the first token from the prompt/BOS state;
- `n_past` advances independently for transformer KV placement;
- `speech_pos` advances one per generated speech token.

This is source-level parity with current upstream behavior. The required Windows before/after run has not yet been performed, so classify this as **PARITY BUG FIXED IN SOURCE / AUDIBLE CAUSALITY UNPROVEN**.

## Current official upstream parity facts checked on 2026-09-07

Authoritative source: `resemble-ai/chatterbox` current master as indexed in September 2026.

- Turbo/Nano `tts_turbo.py` calls S3Gen with `n_cfm_timesteps=2`.
- Turbo warns that CFG, min-p, and exaggeration are unsupported/ignored.
- Turbo defaults include temperature 0.8, top-p 0.95, top-k 1000, repetition penalty 1.2.
- Current multilingual `mtl_tts.py` defaults: exaggeration 0.5, CFG 0.5, temperature 0.8, repetition penalty 1.2, min-p 0.05, top-p 1.0.
- Current S3Gen `flow_inference()` selects `2` CFM steps for meanflow and `10` for non-meanflow when no explicit override is passed.
- Current S3Gen documents 16 kHz speaker/tokenizer reference processing and 24 kHz synthesized audio.
- S3Gen streaming semantics ignore the last three speech tokens when not finalized.

References:

- https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/tts_turbo.py
- https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py
- https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/models/t3/t3.py
- https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/models/s3gen/s3gen.py
- https://github.com/ggml-org/ggml/blob/master/docs/gguf.md

Do not change local defaults merely to match upstream. Local Nano CFM remains 1 until the controlled runtime experiment proves whether CFM=2 improves the defect without violating RTF. The CLI now accepts `--cfm-steps 2` for that experiment. V3 historical CFM=5 and sampling differences likewise remain explicit experiment dimensions.

## Logging redesign

The logging goal is **one causal record per boundary**, not per-token spam.

### Common C++ fields

Every `tts_emit` record includes:

- monotonic log sequence `seq`;
- connection ID `conn`;
- event name;
- epoch/response/external piece when context exists;
- monotonic timestamp.

### T3 events

`t3.begin`

- session-piece index;
- text character count;
- text hash;
- generation limit;
- temperature/top-k/top-p/min-p;
- repetition penalty;
- CFG.

`t3.text`

- text-token count;
- text-token hash.

`t3.end`

- generated speech-token count;
- speech-token hash;
- exact generated speech-token sequence;
- dropped final MTL pending token information;
- longest non-overlapping repeated contiguous token span and positions;
- stop reason;
- KV position;
- learned speech position;
- T3 elapsed time and Vulkan counters.

This replaces old top-5/per-token selection logging, which was expensive to inspect and did not identify repeated **subsequences**.

### S3 events

`s3.begin`

- session-piece index;
- real request-final flag;
- acoustic history token count/hash;
- new speech-token count/hash;
- complete S3 window hash.

`s3.end`

- history/new/window token counts;
- actual CFM steps;
- pending PCM entering the call;
- calculated emission begin/end;
- held tail size;
- emitted samples;
- encoder/CFM/F0/STFT/HiFT timing;
- audio duration and S3 RTF;
- Vulkan wait/submit/barrier counters.

The obsolete session `overlap_ms` value was removed because it had no producer and could misleadingly suggest that acoustic history/crossfade was disabled.

### Python assembly events

For every source response/piece:

- source text SHA;
- external piece/total;
- exact piece text;
- emitted byte and sample interval;
- initial silence trim bytes;
- PCM SHA;
- complete WAV SHA.

This makes response routing and PCM once-only assembly independently auditable.

## S3 boundary arithmetic that must be proven by the next run

Current source constants:

- `kSpeechHistoryTokens = 25`
- `kSpeechLookaheadTokens = 3`
- `kSamplesPerToken = 960`
- 24 kHz output
- `n_trim = 24000 / 50 = 480` samples

Current emission code computes:

```text
pending = prior pending_pcm length
begin   = history_tokens * 960 - pending
hold    = non-last piece ? 480 : 0
end     = generated_wav_samples - hold
emit    = [begin, end)
```

The next Windows run must establish the actual logged values for every piece and prove that the prior held 480 samples are crossfaded once, while historical regenerated samples before `begin` are not emitted again.

`overlap_ms=0` from the old log is irrelevant to this acoustic history/crossfade arithmetic.

## Installer corrections

Implemented:

- temporary build paths shortened (`b`, `c`, `k`, `s`) to avoid the observed nested MSVC/Vulkan shader-generator path failure;
- runtime stamp is accepted only when the exact pinned chatterbox+GGML revision is already recorded;
- existing unstamped binaries are no longer silently blessed as the requested native pin;
- conversion checks which GGUF outputs are missing **before** creating the converter venv;
- only missing GGUF conversions run;
- model card/reference assets are handled independently from converter setup;
- temporary conversion/build environments remain inside `TemporaryDirectory` and are removed afterward.

The actual Windows interrupted/completed-phase exercise remains required.

## Trident runtime corrections

Implemented:

- removed `CUDA_VISIBLE_DEVICES` mutation from `chunk.py`; CPU execution remains selected explicitly through ONNX Runtime provider choice;
- removed redundant `start()` call from `synthesize()`;
- fixed V3 timing so startup and warm synthesis are separate durations rather than printing an absolute `perf_counter()` value;
- prints explicit warm RTF as `synthesis_wall / (samples / 24000)`;
- added `--provenance` for pinned revision and runtime/model/voice hashes;
- every model knob may be overridden through CLI for controlled real-app experiments;
- floating-point knobs with zero defaults are declared as `0.0`, so fractional CLI experiments parse correctly.

## Controlled Windows verification procedure

All commands below are PowerShell. Run from the existing repositories after replacing their files while preserving each repository's `.git` directory.

### 1. Commit and push chatterbox FIRST

```powershell
Set-Location C:\Users\px-wjt\Downloads\chatterbox.cpp
git status --short --branch
git diff --check
git diff

git add include/tts-cpp/chatterbox/engine.h include/tts-cpp/chatterbox/log.h `
  src/chatterbox_engine.cpp src/chatterbox_t3_internal.h src/chatterbox_tts.cpp `
  src/main.cpp src/s3gen_pipeline.h src/server.cpp src/t3_mtl.cpp

git commit

git push origin v3-optimization
$ChatterboxSha = (git rev-parse HEAD).Trim()
$ChatterboxSha
```

Do not proceed unless the push succeeds and `$ChatterboxSha` is the pushed commit.

### 2. Pin the exact pushed SHA in Trident

```powershell
Set-Location C:\Users\px-wjt\Downloads\Trident
$Path = (Resolve-Path .\main.py).Path
$Text = [IO.File]::ReadAllText($Path)
$Text = $Text.Replace('PUSH_CHATTERBOX_FIRST_AND_SET_SHA', $ChatterboxSha)
[IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
python -m py_compile .\main.py .\chunk.py .\tts_nano.py .\tts_turbo.py .\tts_v3.py
git diff --check
```

### 3. Fresh short-path native build and provenance

```powershell
python .\tts_nano.py --install
python .\tts_nano.py --provenance
```

Record the complete install output and `.runtime-logs\tts.log`. The native build must identify the pushed chatterbox SHA and pinned GGML SHA.

### 4. Canonical Nano warm run at current local CFM=1

Terminal A:

```powershell
python .\tts_nano.py --load
```

Terminal B:

```powershell
$Numbers = 'One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten. Eleven. Twelve. Thirteen. Fourteen. Fifteen. Sixteen. Seventeen. Eighteen. Nineteen. Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty.'
python .\tts_nano.py --text $Numbers --seed 42 --cfm-steps 1
python .\parakeet.py .\tts_out.wav
Copy-Item .\.runtime-logs\tts.log .\.runtime-logs\nano-cfm1.log
```

### 5. Nano official-parity CFM=2 experiment

Unload and restart because CFM is a server CLI option:

```powershell
python .\tts_nano.py --unload
python .\tts_nano.py --load --cfm-steps 2
```

Then in the synthesis terminal:

```powershell
python .\tts_nano.py --text $Numbers --seed 42 --cfm-steps 2
python .\parakeet.py .\tts_out.wav
Copy-Item .\.runtime-logs\tts.log .\.runtime-logs\nano-cfm2.log
```

Compare `t3.end` sequences between CFM=1 and CFM=2. Because CFM is downstream of T3, identical T3 sequences with differing repetition behavior directly localize the causal dimension to S3/decoder behavior rather than T3 sampling.

### 6. Isolated Nano 20–30

```powershell
$Tail = 'Twenty. Twenty-one. Twenty-two. Twenty-three. Twenty-four. Twenty-five. Twenty-six. Twenty-seven. Twenty-eight. Twenty-nine. Thirty.'
python .\tts_nano.py --text $Tail --seed 42 --cfm-steps 1
python .\parakeet.py .\tts_out.wav
```

If ASR still repeats inside external piece 0, inspect that exact piece's `t3.end speech_seq`, `s3.begin`, and `s3.end` before considering any cross-piece mechanism.

### 7. Turbo

```powershell
python .\tts_turbo.py --load
python .\tts_turbo.py --text $Numbers --seed 42 --cfm-steps 2
python .\parakeet.py .\tts_out.wav
python .\tts_turbo.py --unload
```

### 8. V3

First run the corrected learned-position implementation with local historical knobs:

```powershell
python .\tts_v3.py --load --language en
python .\tts_v3.py --text $Numbers --language en --seed 42
python .\parakeet.py .\tts_out.wav
python .\tts_v3.py --unload
```

Only after that, run official-parity dimensions separately rather than all at once:

```powershell
python .\tts_v3.py --load --language en --repeat-penalty 1.2
# run/record
python .\tts_v3.py --unload

python .\tts_v3.py --load --language en --min-p 0.05 --top-p 1.0
# run/record
python .\tts_v3.py --unload

python .\tts_v3.py --load --language en --cfg-weight 0.5
# run/record
python .\tts_v3.py --unload

python .\tts_v3.py --load --language en --cfm-steps 10
# run/record
python .\tts_v3.py --unload
```

Do not combine these dimensions until individual causal signatures are known.

### 9. Full Gemma → SaT path

After direct TTS is correct:

```powershell
python .\main.py $Numbers
```

Verify Gemma line structure, SaT piece concatenation, request identities, PCM ranges, final WAV, and Parakeet output.

## How to identify the first wrong boundary from the new log

1. **Source/SaT wrong**: piece texts themselves contain omission/duplication/reordering.
2. **Routing wrong**: response/piece IDs or Python PCM byte intervals are not contiguous/unique.
3. **T3 candidate**: repeated semantic output is accompanied by a material repeated speech-token subsequence or changes when only T3 sampling knobs change.
4. **S3 candidate**: identical deterministic T3 sequences produce different repeated audio when only CFM/S3 behavior changes.
5. **History/trim candidate**: repeat begins at an external piece boundary and duration/offset aligns with logged history/pending/emission arithmetic.
6. **Assembly candidate**: S3 emits unique ranges but Python hashes/ranges show the same PCM response appended twice.
7. **Playback candidate**: final WAV PCM and independent ASR are correct but one playback path repeats.

Never infer causality from `stop=eos`, `stop=repeat`, a zero overlap field, or one good listening run alone.

## 100/100 gate — exact remaining work

Current score: **62/100**.

| Gate | Current | Required for full credit |
|---|---:|---|
| Evidence completeness | 8/10 | Inspect fresh native binaries/GGUF/WAV metadata and all new logs generated after rebuild. |
| Provenance | 6/10 | Hash fresh EXE/DLLs/GGUFs/voice and prove pushed chatterbox + pinned GGML produced them. |
| Reproduction | 10/10 | Baseline canonical and isolated failures already reproduced. Reconfirm on newly rebuilt runtime. |
| Trace correlation | 6/10 | Run protocol-v3 logging and trace every external piece through exact WAV offsets. |
| T3 proof | 5/10 | Use exact new speech sequences and controlled T3-vs-S3 experiments to implicate/eliminate T3. |
| S3 proof | 8/15 | Verify history/pending/emit arithmetic at runtime and resolve within-piece Nano branch. |
| PCM/WAV proof | 6/10 | Verify new-run PCM16 mono 24 kHz ranges, hashes, data size and once-only assembly. |
| Playback/semantic | 3/5 | Independent ASR exists; add direct listening confirmation on user machine. |
| Upstream parity | 5/5 | September 2026 official T3/Turbo/MTL/S3/GGUF facts refreshed. |
| Intervention | 0/5 | Required before/after runs have not executed against modified binaries. |
| Performance | 3/5 | Baseline Nano RTF 0.385; post-change warm RTF still required. |
| Lean code | 2/3 | Source remains small but diagnostic capability added net LOC; remove any logging proven redundant after root cause. |
| Integration | 0/2 | Must push chatterbox first, insert pushed SHA, then commit/push Trident. |

Do not report 100/100 until every row above is satisfied.

## Acceptance targets

- Nano canonical 1–30: every item once, correct order.
- Nano isolated 20–30: no within-piece repetition.
- Turbo canonical: every item once, correct order.
- V3 canonical: correct or a separately proven upstream/model limitation.
- full Trident path correct.
- no unintended historical PCM replay.
- WAV is mono PCM16 at 24,000 Hz with exact byte/sample arithmetic.
- post-change Nano warm RTF < 0.7.
- completed installer phases skip; interrupted phase resumes without redoing completed expensive work.
- no project environment-variable steering.
- chatterbox pushed before Trident pin/commit.
- clean final Git trees.
