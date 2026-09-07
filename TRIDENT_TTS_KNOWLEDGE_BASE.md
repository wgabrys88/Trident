# TRIDENT TTS — SINGLE KNOWLEDGE BASE

## 1. Authority / repository state

This file replaces all older engineering Markdown.

Last pushed baseline:
- Trident: `c1c29a1a71ec8237b4c069af8d9def77046ff053`
- chatterbox.cpp: `a20e18d09a4066de97c502b2ffaf9026eb5a66c2`
- GGML: `58c3805840b516b2a88ff867ccf7bb41dba79951`
- branch: `v3-optimization`

Current audit candidate is based on that baseline and is not yet a pushed/pinned pair. Push native first; Trident then pins that exact 40-hex SHA.

Host: Windows 11, PowerShell, Python 3.11, VS2022 x64, CMake, Vulkan SDK, GTX 1060 6 GB. Audio: mono PCM16, 24 kHz.

## 2. North Star

A lean local speech system where:
`prompt → Gemma brain → breath lines → CPU SaT → TTS request → T3 → S3Gen → PCM → WAV → optional Parakeet`

Requirements: content once/in order; natural continuity; one owner per state lifetime; deterministic provenance/traceability; warm Nano complete-WAV RTF <0.7; fail hard; no retries/fallback mazes/post-hoc dedup.

## 3. Current contracts

Brain writes for speech: short natural sentences, one breath per line, no Markdown/code/URLs/meta-reasoning.

`chunk.py` uses CPU SaT. Newlines are hard speech boundaries; SaT may cut inside a line.

Protocol v4 request = 7 little-endian uint32 / 28 bytes:
`magic | version | kind | response | piece | total | payload_bytes`.
Response uses same shape; field 6 is chunk index.

One Python connection owns one request; pieces are `0..total-1`. One Engine call owns one acoustic session and resets acoustics once. T3 then S3 run sequentially. Caller does not own final acoustic lifetime.

V3 transformer/KV position and generated-speech learned position are separate.

S3 constants:
- history: 25 speech tokens;
- lookahead: 3 tokens;
- 960 samples/token at 24 kHz;
- non-final PCM hold: 480 samples (20 ms).

## 4. Proven facts / excluded causes

Former proven defect: request lifetime was tied to temporary server batches, so S3/history could reset and a partial batch could be finalized. Removed.

The current Nano duplication is NOT explained by:
- Python WAV concatenation;
- socket replay of whole pieces;
- request/session reset;
- whole-piece S3 history replay;
- incorrect total sample accounting;
- the 480-sample hold being emitted twice;
- CFM step count alone.

Evidence: CFM=1 and CFM=2 produced identical T3 sequences/hashes, while CFM=2 still duplicated 19, 23 and 25. CFM count therefore does not change T3 output or solve this defect.

CFM=2 is now the intended Nano default only for upstream parity, not as a claimed fix.

Pattern: 19 is at a piece end; 23 and 25 are inside the next piece. Not a boundary-only replay defect.

## 5. Remaining causal boundary

Do not state “S3 is proven guilty”: distinct T3 token IDs may still encode duplicated speech.

Remaining boundary:
`T3 latent semantics → S3 embedding/encoder → CFM → mel/history conditioning → F0/excitation → HiFT waveform → boundary emission`.

Measure, do not speculate: S3 gets `token_start=0` for every piece; each piece also enters the internal S3 path with local `final=true` and 3-token lookahead treatment.

## 6. Audit candidate / deterministic evidence

`--audit` is diagnostic only; never use it for RTF.

Audit records:
- exact UTF-8 request bytes before send and after native receive;
- exact PCM16 frame bytes before native send and after Python receive;
- exact T3 text-token IDs, speech-token sequence/hash and generation-logit fingerprints;
- S3 speech-token window;
- token embeddings;
- encoder output;
- deterministic CFM initial state and every CFM step;
- generated mel and history-conditioned mel;
- F0;
- excitation source and phase state;
- STFT;
- raw HiFT waveform;
- incoming/outgoing mel and source caches;
- pending/held/emitted PCM;
- S3Tokenizer round-trip tokens from generated waveform.

Audit must prove mel/source/phase continuity, held PCM = next pending PCM, and byte-for-byte equality of Python/native request and PCM16 wire data.

Parakeet JSON timestamps map `wrong word → WAV sample → piece → raw S3 sample → approximate speech-token position → local stage fingerprints + round-trip tokens`.

Boundary mapping must include `emit_begin`; plain `piece_sample // 960` is wrong near crossfades.

## 7. Required next run

1. Finish native static/interface review.
2. Commit/push chatterbox; pin exact SHA in Trident.
3. Clean Windows build.
4. Run audited `validate.py` on canonical counting and isolated 20–30. Preserve full logs/artifacts/WAV/Parakeet JSON.
5. For 19/23/25, find the first stage whose local evidence diverges.
6. Make one causal change; repeat identical seeded input.
7. After correctness, run non-audit Nano and measure warm RTF.

## 8. Engineering rules

- Runtime evidence > docs/comments/issues; read complete fresh logs.
- Preserve user edits/deletions; PowerShell on host.
- No mocks/harnesses as synthesis proof; no project env steering.
- No retry/fallback/catch-ignore wrapper mazes.
- No number hacks, post-hoc dedup, or tuning to hide faults.
- Prefer deletion/single ownership; change one causal dimension at a time.
- Never claim 100% before every gate passes.

## 9. Git / acceptance

Native order:
`edit chatterbox → build/real-run verify → commit → push → copy SHA → pin Trident → rebuild/verify → commit/push Trident`.

Completion requires exact pinned build/provenance; correct Nano/Turbo/V3/full pipeline; duplicated-word first-wrong stage identified/fixed; exact PCM16 mono 24 kHz; ASR+listening agreement; audit continuity/wire checks; Nano warm non-audit RTF <0.7; installer resume; pushed clean repos.

If any item is missing, report the exact blocker and confidence; do not declare completion.
