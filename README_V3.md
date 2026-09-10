# Chatterbox V3 (Multilingual) — AI handover (Trident)

Current handover (supersedes the historical status and next steps below):
C++ v3 is b8bf9ba232c715da7ef160fdd37b9f64dd72b2d9, pushed to origin/v3.
tts_v3.py pins that SHA. Its original chatterbox-v3.cpp path and S3Gen
q4_0 selection are retained; the local checkout is named chatterbox.cpp.
Sentence A's 56 dropped tokens match v3_s3gen_hf.txt; jumping codebook
IDs are not evidence of broken T3. Continue with S3Gen only.
Fixed conv1d_f32: preserve batch through matmul, then permute to [T,C,B].
The previous flattened [T*B,C] reshape scrambled the two CFG streams.
Only the convolution fix is published relative to cbbbb8c. Pre-existing
f16 conversion, finalized encoder length, silence-padding removal,
trim_fade, T3 instrumentation and launcher path/model edits were removed
by corrective commits and discarded locally at explicit request.
The earlier Vulkan sentence A run included those now-removed edits;
it does not validate the final published combination. Intelligibility
remains unconfirmed. Before/after dumps remain in models/v3_s3_dump_before
and models/v3_s3_dump_after. Official source 5de7a54 was inspected
statically; no official CPU inference was run. V3 process stopped.
No new harnesses, tests, fallbacks, or defensive code. Fix demonstrated
errors directly; commit/push C++ v3, then pin/commit/push Trident v3.
Do not reopen Nano/Turbo or merge upstream.

Same document shape as README_NANO.md / README_TURBO.md / README_V3.md.
Read all three as columns of one table. Do not copy numbers, graphs, bake
windows, tokenizers, or CFM schedules across families.

This file is for AI continuation and final handover. Human ear-check is the
quality gate. File size, duration, and RMS are not a pass.

NANO-MATCHING IS A FAULT. V3 is Llama T3 + mtl BPE 2454 + perceiver + CFG
+ classic CFM. Copy Nano/Turbo STRUCTURE (daemon, bake, Vulkan, throw).
Do not copy Nano/Turbo GRAPH conventions.

----------------------------------------------------------------------
## 0. How to use this file
----------------------------------------------------------------------

Stay on chatterbox-v3.cpp branch v3 and Trident branch v3.
Launcher is tts_v3.py only. Do not edit nano or turbo trees from here.
PowerShell only. Fail hard. No fallbacks. No force-push. No git checkout SHA.
CONFIDENCE 100: if a number, tensor name, path, SHA, bake window, CFM step
count, ENC_COND, language_id, graph shape, rope theta, flash_attn layout,
V permute, or "is this pid still alive" is not read from THIS pin / THIS
file / THIS GET / THIS command in this session, STOP.

Shell is powershell.exe. NEVER && || bash HEREDOC git commit HEREDOC
python -c with a URL inside PowerShell. ALWAYS: cmd1; cmd2; cmd3 and
if ($LASTEXITCODE -ne 0) { throw "name $LASTEXITCODE" }

----------------------------------------------------------------------
## 1. Identity
----------------------------------------------------------------------

Family              V3  (Chatterbox Multilingual)
Official name       ChatterboxMultilingualTTS  t3_model="v3"
C++ clone           C:\Users\eb-wjt\Downloads\synthesis\chatterbox-v3.cpp
C++ branch          v3
C++ HEAD            cbbbb8c5941f54c3449f677a03d5adf04ad766a4
C++ remote          https://github.com/wgabrys88/chatterbox.cpp.git
Trident clone       C:\Users\eb-wjt\Downloads\synthesis\Trident
Trident branch      v3
Trident remote      https://github.com/wgabrys88/Trident.git
Launcher            tts_v3.py
CHATTERBOX_REV      cbbbb8c5941f54c3449f677a03d5adf04ad766a4
Status              NOT SHIPPED. English A FAILED (human: drums / fast
                    garble) after the Perceiver head-split commit.

Analogical rule:
  chatterbox v3  <->  Trident v3  <->  tts_v3.py

Prior C++ tip 65af9ac1f979e03ca8ecce2be820ab13e7b23566 was the first
isolated V3 implementation. cbbbb8c is 65af9ac plus Perceiver QKV
reshape + T3 token dump.

----------------------------------------------------------------------
## 2. Product shape
----------------------------------------------------------------------

one Engine({t3,s3,language_id}).synthesize(text)
language_id is daemon argv[5]
one named-pipe server
argv: exe  t3.gguf  s3.gguf  out.wav  pipe  language
one bake.exe overwrites ONLY v3 GGUFs
compile-time sampler in v3.h
no runtime CFG / min_p / exaggeration knobs
n_past = 0 at the start of every generate_t3
throw std::runtime_error on failure
CMake FATAL_ERROR unless Vulkan-only
init_backend = ggml_backend_vk_init(0); throw if null
weights, KV, graphs on THAT Vulkan backend
CMake links t3_v3.cpp + mtl_bpe.cpp.
Do not link leftover t3_nano.cpp / gpt2_bpe.cpp into the V3 server
(they may still sit on disk).
If a V3 op has no ggml Vulkan path, STOP. Do not implement it on CPU.

----------------------------------------------------------------------
## 3. Pins
----------------------------------------------------------------------

HF weights     ResembleAI/chatterbox
               5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18
Official py    github.com/resemble-ai/chatterbox master
               5de7a54aa4e5e2baadb0182dde554908b48b85c2
               local copies under synthesis/_v3_pin_src/
ggml           7840aaba1989c6deeefede1d77d5aaf8f52b947e
               (detached in chatterbox-v3.cpp/ggml is expected)

Official from_pretrained(t3_model="v3") allow_patterns:
  ve.pt, t3_mtl23ls_v3.safetensors, s3gen.pt,
  grapheme_mtl_merged_expanded_v1.json, conds.pt, Cangjie5_TC.json
Official does NOT load s3gen_v3.safetensors.
We converted s3gen.safetensors (safetensors sibling of s3gen.pt).

.ckpt-v3 files:
  t3_mtl23ls_v3.safetensors
  s3gen.safetensors
  ve.safetensors
  grapheme_mtl_merged_expanded_v1.json
  conds.pt
  Cangjie5_TC.json

----------------------------------------------------------------------
## 4. T3
----------------------------------------------------------------------

Backbone            Llama_520M  (~520M)
Layers              30
Hidden              1024
Heads               16
Head dim            64
KV heads            16  (no GQA)
FF                  SwiGLU  intermediate_size=4096
                    ggml_swiglu_split(gate, up) = silu(src0)*src1
Norm                RMSNorm  eps=1e-05  (no bias)
Position            learned input_pos_emb PLUS Llama3 RoPE
                    rope_theta=500000.0
                    rope_type=llama3
                    rope_scaling factor=8.0 high_freq_factor=4.0
                    low_freq_factor=1.0
                    original_max_position_embeddings=8192
                    max_position_embeddings=131072 UNUSED HF loader padding
                    Do NOT allocate KV at 131072
RoPE ggml           GGML_ROPE_TYPE_NEOX  (HF rotate_half)
                    freq_factors stored as inv/new_inv so ggml DIVIDE
                    matches llama3 scaled inv_freq
Perceiver           YES  query (1,32,1024)  AttentionBlock2 heads=4
                    head_dim=256  relative_pos_embeddings=False
                    cross-attn(query, h) then self-attn
                    LayerNorm + to_q/k/v + proj_out all have bias
CFG on T3           YES  CFG_WEIGHT=0.5
                    ONE KV tensor last dim CFG_BATCH=2  (not two caches)
                    logits = cond + cfg*(cond-uncond)
Emotion adv         YES  Linear 1->1024 bias=False  EXAGGERATION=0.5
Speech codebook     8194  start 6561  stop 6562
Cond speech tokens  150
Text vocab          2454  start_text=255  stop_text=0
                    english_only() is 704. UNUSED. Do not ship 704.
n_ctx in GGUF       1 (spkr) + 32 (perceiver) + 1 (emotion)
                    + 2050 (text_pos table) + 2 (double BOS)
                    + 1000 (N_PREDICT) = 3086
T3 sources          src/t3_v3.cpp
                    src/mtl_bpe.cpp
                    include/tts-cpp/chatterbox/v3.h

Official inference() -- COPY, do not "fix":
  prepare_input_embeds includes initial BOS + speech_pos.forward
    (arange 0..sl, NOT token ids)
  THEN concat a SECOND BOS with get_fixed_embedding(0)
  text_emb[1].zero_() THEN add learned text_pos  (uncond = pos only)
  next speech embed uses get_fixed_embedding(i+1)
  predicted does NOT include the initial BOS
  drop_invalid_tokens strips SOS=6561 and EOS=6562
  wav trimmed to (n_tokens-1)*960

mtl_tts.py generate() cats text batch-2 THEN pads SOT/EOT then
inference max_new_tokens=1000. hp.max_speech_tokens=4096 is a TENSOR
CEILING, not quality. Do not raise N_PREDICT.

cond_enc concat: spkr (1) + clap empty (0) + perceiver (32) + emotion (1)
= 34. Expand same cond to batch 2.

----------------------------------------------------------------------
## 5. Tokenizer
----------------------------------------------------------------------

Family              multilingual BPE (HuggingFace tokenizers JSON)
File                grapheme_mtl_merged_expanded_v1.json
Vocab               2454
Merges              265
added_tokens        118
pre_tokenizer       Whitespace
[STOP]=0 [UNK]=1 [SPACE]=2 [START]=255 [en]=708 [PAD]=705
[en] and [SPACE] are added_tokens special=true; GGUF type 4
encode: punc_norm, lowercase, NFKD, prepend [lang],
        space -> [SPACE], match added longest-first, Whitespace split, BPE
English extras: none
zh/ja/he/ko/ru extras NOT implemented; mtl_bpe.encode throws
  "language extras unread" for those five.
English A uses language_id=en.

PROVEN 2026-09-10 sentence A:
  "The billing issue is resolved." language=en
  HF Tokenizer.from_file ids:
    708 42 2 15 216 52 2 54 133 18 2 54 2 46 123 25 35 49 9
  C++ mtl_bpe.encode (v3_t3_dump.txt bpe line): IDENTICAL
  text wrapped SOT/EOT: 255 ... 0
Tokenizer is NOT the drums cause.

----------------------------------------------------------------------
## 6. Conditioning and bake
----------------------------------------------------------------------

ENC_COND_LEN        6 * S3_SR     (read from mtl_tts.py, NOT Nano 15s)
DEC_COND_LEN        10 * S3GEN_SR
Bake VE cap         NONE  (official embeds_from_wavs uses full 16k wav,
                    trim_top_db only). bake.cpp has no 30s cap.
Cond tokens         6s @ 16 kHz  max_len=150
Prompt tokens       10s @ 16 kHz
Prompt feat         10s @ 24 kHz
Campplus emb        10s @ 16 kHz
Empty wav           throw
Voice tensors overwrite ONLY v3 GGUFs.

conds.pt:
  t3.speaker_emb (1,256)
  t3.cond_prompt_speech_tokens (1,150)
  t3.emotion_adv (1,1,1)
  gen.prompt_token (1,157) gen.prompt_feat (1,314,80) gen.embedding (1,192)

----------------------------------------------------------------------
## 7. S3Gen / CFM
----------------------------------------------------------------------

Family              classic CFM  (NOT meanflow)
n_cfm_timesteps     10
t_span              1 - cos(linspace(0,1,11) * 0.5 * pi)
inference_cfg_rate  0.7
sigma_min           1e-06
solver              euler
time_embed_mixer    ABSENT. Do not call compute_time_mixed.
one estimator forward with batch 2:
  x both halves = x
  mu/spks/cond: cond half = values, uncond half = 0
  t same both halves
  r=None so compute_time_mlp(t) only
  dxdt = (1+0.7)*cond - 0.7*uncond
S3_SR               16000   hop 160   token rate 25 Hz
S3GEN_SR            24000
kSamplesPerToken    960
SILENCE_TOKEN       4299
SPEECH_VOCAB_SIZE   6561  (s3gen_synthesize drops token >= 6561)
Engine drop_invalid uses SOS/EOS strip.

Vulkan compute of the full CFM graph at batch 2 is UNPROVEN.
ggml_im2col 1D: b->ne[3]==1, output ne[2]=b->ne[2] so [T,C,2] is the
1D batch layout in C. If unsure, dump ne[] after one forward.

----------------------------------------------------------------------
## 8. Sampler (compile-time v3.h)
----------------------------------------------------------------------

SEED=42
N_PREDICT=1000
TOP_K=0
TOP_P=1.0          (mtl_tts.py generate() default top_p=1.0)
MIN_P=0.05
TEMPERATURE=0.8
REPEAT_PENALTY=1.2
REPEAT_LAST_N=1000
CFG_WEIGHT=0.5
CFM_STEPS=10
CFM_CFG=0.7
SILENCE_TOKEN=4299
EXAGGERATION=0.5
NO SILENCE_COUNT

Do not raise N_PREDICT. GitHub issue #181 is the 40s cap; answer is
sentence chunking OUTSIDE the engine. Do not switch to 768.

----------------------------------------------------------------------
## 9. Convert / GGUF
----------------------------------------------------------------------

scripts/convert-t3-v3-to-gguf.py
scripts/convert-s3gen-v3-to-gguf.py
T3 quant             Q8_0
S3Gen quant          Q4_0
Outputs              Trident/models/chatterbox-t3-v3-q8_0.gguf     614589728
                     Trident/models/chatterbox-s3gen-v3-q4_0.gguf  725858752
Skip                 tfmr.embed_tokens.weight  dummy vocab 8
Skip                 text_head.weight          training forward()/loss only
STOP                 any other unknown T3 name
S3Gen STOP           if time_embed_mixer PRESENT
                     if tokenizer length != 2454
Llama Linear (out,in). GGUF reverses numpy dims. Convert did NOT
extra-transpose. Do not add a Q/K weight permute unless rope mode is
switched at the same time:
  HF layout + GGML_ROPE_TYPE_NEOX
  or permuted layout + GGML_ROPE_TYPE_NORMAL
Mixing those two is a graph bug.

Do not put s3gen_v3.safetensors through the V3 convert.

----------------------------------------------------------------------
## 10. Graph / ggml
----------------------------------------------------------------------

Llama T3 (t3_v3.cpp build_transformer_core):
  Q/K/V linear then reshape_4d(HD, n_head, N, CFG_BATCH)
    = [64, 16, N, 2]  -- CORRECT grouping of 1024 = 16*64
  RoPE on Q and K (not V), GGML_ROPE_TYPE_NEOX, pos length N
  permute 0,2,1,3 -> [HD, N, n_head, CFG_BATCH]
    matches ggml_flash_attn_ext q/k/v
  V uses the SAME permute as K (ggml.h: V not transposed)
  flash_attn then reshape_3d(n_embd, N, CFG_BATCH)
    result is [n_embd_v, n_head, n_batch, ne3]; merging HD*n_head is valid
  KV view strides use n_ctx padding; CFG_BATCH is last dim of ONE cache

Vulkan rope_neox (rope_funcs.glsl):
  theta_base = rope_data_pos[i2] * ...
  i2 is dim2 (sequence). i3 is CFG batch.
  Both CFG streams share pos[i2]. Passing length N is CORRECT.
  Uncond RoPE is NOT garbage from ne[3]=2 indexing.

Perceiver (t3_v3.cpp perceiver_attn) BEFORE cbbbb8c:
  reshape_3d(HD, n_q, n_head) from [n_embd, seq]
  WRONG: grouped 1024 as (seq, heads) not (heads, head_dim)
AFTER cbbbb8c (this commit):
  reshape_3d(HD, n_head, seq) then permute 0,2,1,3
  PROVEN layout bug vs HF view(B, T, n_head, head_dim)
  NOT sufficient: English A still garble; speech ids still jump

s3tokenizer.cpp V permute 1,2,0,3 disagrees with t3_v3. Spec is
ggml.h + Llama q/k/v layout, not s3tokenizer.

CHBX_MAX_NODES = 8192. Prompt graph ran. Do not raise first.

----------------------------------------------------------------------
## 11. Isolation names
----------------------------------------------------------------------

PIPE     \\.\pipe\chatterbox-v3- + sha256(str(ROOT).encode() + b"v3")[:12]
PID      models/v3.pid
STAMP    models/v3.voice.sha256
REV      models/v3.rev
OUT      tts_out_v3.wav
CKPT     .ckpt-v3
DUMP     models/v3_t3_dump.txt   (written by generate_t3 next to T3 GGUF)
venv     .venv-convert-v3
require_pin: branch == "v3" and HEAD == CHATTERBOX_REV
never git checkout a SHA
ggml: clone only if missing, then checkout GGML_REV once.
      If ggml exists, verify HEAD == GGML_REV and throw.
speak() wait-while-alive; missing argv[2] SystemExit("language")
Do not cross-kill nano/turbo pids.
Do not git-add helper _v3_tok_cmp.py _v3_mtl_bpe_cmp.py

After any V3 speak, before ending:
  read models/v3.pid only
  if live, TerminateProcess that pid
  unlink v3.pid
  confirm OpenProcess fails

----------------------------------------------------------------------
## 12. Proven facts
----------------------------------------------------------------------

Git (do not assume without rev-parse):
  chatterbox-v3.cpp v3  cbbbb8c5941f54c3449f677a03d5adf04ad766a4  origin/v3
  Trident pin in tts_v3.py must equal that FULL SHA after this handover.

Compile, link, bake, pipe ack, second-call hot, EOS:
  PROVEN on 65af9ac and again on cbbbb8c.
Intelligible English: FAILED (human Wojciech, twice).

Tokenizer A: HF == C++ mtl_bpe. PROVEN.
Vulkan rope pos[i2] for ne[3]=2: both CFG streams share sequence
  positions. PROVEN from rope_neox.comp / rope_funcs.glsl.
Llama T3 QKV reshape_4d(HD, n_head, N, 2) grouping: matches HF
  view(B,T,n_head,head_dim). Layout argument PROVEN. Numerics not proven.
Perceiver old reshape(HD, n_q, n_head): layout BUG vs HF. PROVEN.
  Fix committed in cbbbb8c. Tokens CHANGED after the fix but still jump.
  Ear still garble.

Dump after cbbbb8c English A (models/v3_t3_dump.txt):
  bpe     708 42 2 15 216 52 2 54 133 18 2 54 2 46 123 25 35 49 9
  text    255 ... 0
  predicted_count 57  (includes EOS 6562)
  dropped_count   56
  dropped ids jump across 0..6560 with local repeats
    (6486 x3, 6405 x3, mid-run SILENCE 4299) then EOS
  Plan rule: jumping ids -> suspect T3, not S3Gen-only.
  Do not pick T3 vs S3Gen by ear.

Prior dump on 65af9ac: predicted_count 55, dropped 54, also jumping,
also ended 6486/6405 runs. Length ~53 audio tokens after drop-last
matched tts_out_v3.wav 50880 samples / 960.

ggml ops present in this Vulkan build:
  ggml_rms_norm ggml_silu ggml_swiglu_split ggml_rope_ext
  GGML_ROPE_TYPE_NEOX GGML_ROPE_TYPE_NORMAL ggml_flash_attn_ext

----------------------------------------------------------------------
## 13. Problems found
----------------------------------------------------------------------

1. English A unintelligible (drums / fast weird sounds). Still open.
2. Copied Nano flash_attn conventions onto Llama in the first V3 pass.
   That plan was a fault. Derive from V3 pin + ggml.h + dumps.
3. Perceiver QKV reshape (HD, n_q, n_head) scrambled heads.
   Fixed in cbbbb8c. Did not clear the ear-check.
4. First V3 implementation treated "Nano works" as proof of ggml call
   layouts (V permute, no post-permute, 2D CFM tensors). Forbidden.
5. Launcher SHA checkout (other families) caused detached HEAD.
   V3 launcher uses require_pin only.

----------------------------------------------------------------------
## 14. Unread / investigate
----------------------------------------------------------------------

STOP rather than assume:
  llama3 freq_factors vs HF inv_freq on a few i (formula looks right;
    numerical dump not run)
  bake cond length actually written into the GGUF after rewrite
  zh/ja/he/ko/ru tokenizer extras
  pid liveness without a fresh OpenProcess
  Vulkan flash_attn shader path for ne3=2 vs ggml.h C comment
    (C API layout matches; shader occupancy/GQA unproven as the drums cause)
  CFM estimator batch-2 Vulkan im2col / conv1d ranks
  speech_head Q8_0 sensitivity (unlikely drums; still unread)
  T3HuggingfaceBackend position_ids / patched generate vs our
    explicit double-BOS + n_past RoPE
  Whether CFG logits nb[1]/nb[2] match mul_mat [vocab, N, 2] on Vulkan
  Learned pos + RoPE both on (official does both; still a suspect
    if position_ids differ from 0..seq-1)

Next dump isolation (do not guess A vs B by ear):
  A. T3 codes garbage (Llama attn / RoPE / CFG / remaining perceiver /
     learned pos)
  B. S3Gen CFM garbage (batch-2 estimator / cosine / vocoder)
  C. both
  Tight codebook trajectory -> suspect S3Gen.
  Ids jumping 0..6560 with no structure -> suspect T3.  (CURRENT)

----------------------------------------------------------------------
## 15. Checklist
----------------------------------------------------------------------

[x] clone chatterbox-v3.cpp, checkout v3; Trident checkout v3
[x] HF GET 5bb1f6ee ; official py GET 5de7a54
[x] T3 convert; skip only embed_tokens + text_head
[x] S3Gen convert; time_embed_mixer ABSENT; tokenizer 2454 gate
[x] v3.h, t3_v3.cpp, mtl_bpe.cpp, classic CFM, server argv[5]
[x] cmake --build Release server+bake
[x] bake; spawn; speak A; second speak hot pipe
[x] human ear-check A FAILED
[x] tokenizer HF vs mtl_bpe MATCH
[x] Vulkan rope pos[i2] for CFG batch PROVEN OK
[x] T3 token dump (jumping ids)
[x] Perceiver head split fix committed+pushed cbbbb8c NEVER --force
[x] pin FULL SHA in tts_v3.py
[ ] English A intelligible. Do not raise N_PREDICT.
[ ] Prove remaining broken graph from V3 pin + ggml.h + dumps
[ ] Fix only what those reads prove; rebuild Vulkan-only
[ ] Re-speak A; human ear
[ ] kill v3 pid; commit+push chatterbox then pin then Trident

----------------------------------------------------------------------
## 16. What not to do
----------------------------------------------------------------------

New GitHub remotes. Force-push. reset --hard. rebase. amend unless asked.
git checkout <40-char-sha> in chatterbox-v3.cpp or Trident.
Commit on detached HEAD.
Sequential two-KV CFG on T3.
Sequential two-estimator CFG on S3Gen.
Calling compute_time_mixed / requiring time_embed_mixer.
"Fixing" official double-BOS.
Allocating Llama KV at 131072.
Adding SILENCE_COUNT onto generate_t3.
Linking leftover unused sources into the V3 server.
Putting s3gen_v3.safetensors through the V3 convert.
Raising N_PREDICT above 1000.
Raising CHBX_MAX_NODES before a throw.
Using T3Config.english_only vocab 704.
Copying Nano/Turbo 375, 15s ENC_COND, meanflow, GPT-2 flash_attn,
  or "V not permuted because Nano".
Claiming A pass from file size, duration, or RMS.
Leaving chatterbox-server.exe in VRAM.
Working on main.
PowerShell && or bash HEREDOC.
git-add helper _*.py
GGML_CPU=ON, ggml_backend_cpu, CUDA, hybrid CPU+Vulkan.

----------------------------------------------------------------------
## 17. Next session
----------------------------------------------------------------------

Resume from English A FAILED after cbbbb8c. Trees must be:
  chatterbox-v3.cpp  abbrev-ref v3  HEAD == CHATTERBOX_REV in tts_v3.py
  Trident            abbrev-ref v3

Order:
  0. Read THIS file and README_NANO.md + README_TURBO.md as columns.
     Differences that matter for V3: Llama vs GPT-2, 150 vs 375,
     2454 vs 50276, CFG vs ignored, classic CFM 10 vs meanflow 2,
     ENC_COND 6s vs 15s, perceiver vs none, RoPE vs wpe.
  1. Re-read pid/process. Kill leftover v3 daemon if any.
  2. Do not revert the Perceiver permute. It is a proven layout fix.
  3. Derive the NEXT T3 suspect from pin + ggml.h + dump, not from Nano.
     Remaining high-value unread: llama3 freq_factors numeric, CFG
     logits nb[], Vulkan flash_attn ne3=2, learned-pos vs RoPE
     position_ids, CFM batch-2 only after T3 trajectory looks tight.
  4. Fix only what those reads prove. Rebuild
     cmake --build Release --target chatterbox-server --target chatterbox-bake
     if ($LASTEXITCODE -ne 0) { throw "build $LASTEXITCODE" }
  5. python tts_v3.py "The billing issue is resolved." en
     Compare models/v3_t3_dump.txt to the dump in section 12.
     Human ear. If drums again, STOP and dump more. Do not raise N_PREDICT.
  6. Kill v3 pid; unlink; confirm OpenProcess fails.
  7. Commit+push chatterbox-v3.cpp v3. Pin FULL SHA. Commit+push Trident v3.
     NEVER --force. Extended commit message. If work is done, commit it.

Ear-check sentence A:
  The billing issue is resolved.
Language: en
