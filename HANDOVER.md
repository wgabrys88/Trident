# TRIDENT HANDOVER — 2026-08-22 (Pascal GTX1060 6GB, session: voice-mixing forensics)

GOAL: fix mixed-voice drift mid-sentence + GPU heartbeat; Vulkan-only; reduce LOC.

## DONE (committed, built, R2 validated end-to-end)

1. Patch system REPLACED. All `patches/chatterbox-*.patch` deleted.
   - `patches/native_patch.py`: OPS dict {file: [(old,new),...]} exact-string transforms vs pinned ddca05f (23 generated) + fp2 additions (se_cap, ct_stats, dump_out+5 probe blocks, <set>, dxdt_step0, CFM<5 guard restore). 30 total.
   - `patches/apply_native_patch.py`: applier; EOL-safe (preserves CRLF/LF per file), fails LOUD if anchor count != 1.
   - `patches/gen_native_patch.py`: dev tool that regenerates base OPS from `git diff -U6` of an installed tree (anchors auto-verified unique vs upstream).
   - `installer.py`: apply_chatterbox_patches runs applier via sys.executable; chatterbox_native_revision() hashes native_patch.py + apply_native_patch.py → any edit triggers full re-clone/reset/rebuild (~4 min).
2. FIX log.py `end_run`: missing `global _run_mark` caused UnboundLocalError after EVERY run (exit 1, no server.log slice). Now exit 0, slices written.
3. FIX resident.py: removed GGML_VK_{MEMORY,PERF,SYNC}_LOGGER=1 defaults → per-node cerr flood gone (39 MB / 2.4M lines per request → 543 KB whole session). Chunk-0 T3: 3652 ms → 1016 ms. Loggers remain opt-in env (pass-through kept in _spawn_detached).
4. FIX VoiceEncoder OOM (v3/kamala 39 s ref wanted 2.28 GB graph): bake caps VE input at 30 s (`event=voice_bake stage=se_cap`). Trump 21.9 s unaffected → old fingerprints still comparable.
5. FIX restored non-meanflow CFM<5 throw (chatterbox_tts.cpp ~L2096); v3 cfm_steps=5 enforced by config.
6. NEW forensics: `ct_stats` distinct/min/max of ct_data; `dxdt_step0` first-CFM-step fingerprint; PCM rms/mean/min/max/zeros/fnv on chunk+pcm lines (tts/src/session.cpp); parity probe extended with gelu / conv1d_im2col(s2p1) / conv1d_dw_im2col(k31p15) / rope_neox(64,20,T NEOX θ10000) / soft_max_attn(512x64).

## RUN R2 EVIDENCE (turbo trump cold, short EN text)

- exit 0; se_ref == se_readback fnv ee21cbfc1f556114; != se_builtin be997e5fbad7aab0.
- `ct_stats n=375 min=4252 max=4252 distinct=1` → S3TokenizerV2 COLLAPSE confirmed (was only first/mid/last before).
- Parity probe: mul_mat 1.02e-4, conv_transpose_1d 1.06e-6, soft_max(_attn) ≤6.8e-7, group_norm 0 — OK.
- **rope_neox max_rel = 1.699e-01 ← ROOT CAUSE**; gelu 1.30e-3, conv1d_im2col 2.15e-3, conv1d_dw_im2col 5.49e-4 (secondary).

## DIAGNOSIS

Vulkan RoPE NEOX miscomputes vs CPU reference. s3tokv2 q/k use NEOX (s3tokenizer.cpp:484-491) → positional signal destroyed → time-invariant encoder hidden → constant FSQ token 4252 → flow prompt conditioning dead → identity carried only by weak SE paths → the heard voice drift. t3_mtl.cpp:362 ALSO uses NEOX rope → v3 T3 likely corrupted too; turbo/nano T3 use learned positions (GPT-2 style) → unaffected.

## NEXT STEPS

A) Fix rope: inspect `third_party/chatterbox.cpp/ggml/src/ggml-vulkan/vulkan-shaders/rope.neox.f32` (+rte variants; pipeline selection ggml-vulkan.cpp:4515-4535, dispatch ~L9461-9508) vs CPU semantics in ggml.c (half-split pairing, inv_freq). Rebuild → rerun R2 → expect ct_stats distinct >> 1 and rope max_rel ≲1e-5. Then re-check conv1d_im2col 2e-3.
B) Runs: R3 repeat turbo cmd → expect `reuse pid=` (warm reuse finally exercised); R4 `nano -r obama` same text → replacement logged (old pid stopped), se_ref(obama) != ee21cbfc…, record first divergent stage; R5 `v3 data/short-pl.txt --language pl -r kamala` → VE cap holds, SOT 255/EOT 0 in text_tokens line.
C) Final report tables: per-run first-bad-stage; Trump-vs-Obama fingerprint diff (se_ref/ct_ref/s3_prompt_feat/dxdt_step0/pcm fnv); same-voice repeatability (R2 vs R3 hashes must be identical); warm/cold RTF; CPU-fallback violations (none expected — t3+s3gen both log backend=Vulkan); crashes; invariants (peak=0.990000 every chunk is the deliberate HiFT clamp y_trim ±0.99 — NOT corruption).

## GOTCHAS

- install() does reset --hard on third_party/chatterbox.cpp each rebuild (worktree edits there are transient by design).
- Python strings holding C code with \n escapes must be r""" … """ (bit us once: literal newline inside a C string → MSVC syntax error).
- Ports 17931/17932 unused in tts-only runs (Parakeet/Gemma untested this session).
- Prepared refs stable cross-machine: trump sha256 b97383ab…, kamala 153d044c….
- Legacy evidence kept under data/runs/* (PASCAL-RUN-LOG.md, IRISXE zip: IrisXe rtf turbo 1.669 / nano 1.672 / v3 2.402, client-side logs only). Old trident{,2,3}.log root logs were cleaned after full analysis.
