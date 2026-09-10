# Chatterbox TURBO — AI handover (Trident)

Same document shape as README_NANO.md / README_TURBO.md / README_V3.md.
Read all three as columns of one table. Do not copy numbers, graphs, bake
windows, tokenizers, or CFM schedules across families.

This file is for AI continuation and final handover. Human ear-check is the
quality gate. File size, duration, and RMS are not a pass.

----------------------------------------------------------------------
## 0. How to use this file
----------------------------------------------------------------------

Stay on chatterbox-turbo.cpp branch turbo and Trident branch turbo.
Launcher is tts_turbo.py only. Do not edit nano or v3 trees from here.
PowerShell only. Fail hard. No fallbacks. No force-push. No git checkout SHA.
CONFIDENCE 100: if a number is not in THIS file / THIS tree / a GET this
session, STOP.

----------------------------------------------------------------------
## 1. Identity
----------------------------------------------------------------------

Family              TURBO
Official name       Chatterbox Turbo
C++ clone           C:\Users\eb-wjt\Downloads\synthesis\chatterbox-turbo.cpp
C++ branch          turbo
C++ HEAD            aec122a248b1fcc6670c0ef7b130d42671beb459
C++ remote          https://github.com/wgabrys88/chatterbox.cpp.git
Trident clone       C:\Users\eb-wjt\Downloads\synthesis\Trident
Trident branch      turbo
Trident HEAD        96def65688b3c7483aa25b32342252148e29b094
Trident remote      https://github.com/wgabrys88/Trident.git
Launcher            tts_turbo.py
CHATTERBOX_REV      aec122a248b1fcc6670c0ef7b130d42671beb459
Status              SHIPPED. Human ear-check A/B intelligible (prior campaign).

Analogical rule:
  chatterbox turbo  <->  Trident turbo  <->  tts_turbo.py

Turbo is the SAME GRAPH FAMILY as Nano (GPT-2 T3 + meanflow S3Gen),
bigger T3 weights. It is NOT Llama. Do not treat Turbo as a V3 reference
for attention, RoPE, perceiver, CFG, or classic CFM.

----------------------------------------------------------------------
## 2. Product shape
----------------------------------------------------------------------

one Engine({t3,s3}).synthesize(text)
one named-pipe server in server.cpp
argv: exe  t3.gguf  s3.gguf  out.wav  pipe
NO language argv
one bake.exe overwrites ONLY turbo GGUFs
compile-time sampler in turbo.h
no runtime CFG / min_p / exaggeration knobs
n_past = 0 at the start of every generate_t3
throw std::runtime_error on failure
CMake FATAL_ERROR unless Vulkan-only
init_backend = ggml_backend_vk_init(0); throw if null
weights, KV, graphs on THAT Vulkan backend
Host RAM may hold wav bytes and the text string. Compute graphs do not.
No TCP. No logs. No streaming PCM. No in-engine chunker. No CPU backend.

----------------------------------------------------------------------
## 3. Pins
----------------------------------------------------------------------

HF weights     ResembleAI/chatterbox
               749d1c1a46eb10492095d68fbcf55691ccf137cd
               (tts_turbo.py URL host path uses chatterbox-turbo resolve
                on that sha; record the SHA actually downloaded)
Official py    github.com/resemble-ai/chatterbox  tts_turbo.py / T3Config
ggml           7840aaba1989c6deeefede1d77d5aaf8f52b947e
               (detached in chatterbox-turbo.cpp/ggml is expected)

HF assets:
  t3_turbo_v1.safetensors
  s3gen_meanflow.safetensors
  conds.pt
  ve.safetensors
  vocab.json
  merges.txt
  added_tokens.json

YAML t3_turbo_v1.yaml is NOT geometry. Python sets cond 375.
n_transformer_layers: 30 in YAML is a LIE vs file (24 layers).
Always take n_layer/n_embd/n_ctx from the safetensors.

----------------------------------------------------------------------
## 4. T3
----------------------------------------------------------------------

Backbone            GPT-2 medium
Layers              24  (tfmr.h.0 .. tfmr.h.23)
Hidden              1024
Heads               16  (1024 // 64)
Head dim            64
FF                  GPT-2 GELU MLP, fused c_attn
Norm                LayerNorm + bias
Position            GPT-2 wpe (8196, 1024). No RoPE. No Llama.
Perceiver           NO  (use_perceiver_resampler=False)
CFG on T3           NO  (official ignores CFG / min_p / exaggeration)
Emotion adv         NO
Speech codebook     6561 + start 6561 + stop 6562 = 6563
Cond speech tokens  375
n_ctx from GGUF     wpe table length 8196
T3 sources          t3_nano.cpp graphs reused (read hparams from GGUF)
                    gpt2_bpe.cpp
                    include/tts-cpp/chatterbox/turbo.h

Bigger planner, same graph family as Nano. Practical text: one English
sentence up to ~40 words. Not a paragraph.

----------------------------------------------------------------------
## 5. Tokenizer
----------------------------------------------------------------------

Family              GPT-2 BPE
Vocab               50276  (convert SystemExit if length != 50276)
Files               vocab.json  merges.txt  added_tokens.json
Language id         unused
English extras      none

----------------------------------------------------------------------
## 6. Conditioning and bake
----------------------------------------------------------------------

ENC_COND_LEN        15 * S3_SR
DEC_COND_LEN        10 * S3GEN_SR
Bake VE cap         30s @ 16 kHz  (same bake.cpp family as Nano)
Cond tokens         15s @ 16 kHz
Prompt tokens       10s @ 16 kHz
Prompt feat         10s @ 24 kHz
Campplus emb        10s @ 16 kHz
Empty wav           throw
Voice tensors overwrite ONLY turbo GGUFs.

375 is the VOICE PROMPT PREFIX, same as Nano. It is not V3's 150.

----------------------------------------------------------------------
## 7. S3Gen / CFM
----------------------------------------------------------------------

Family              meanflow  (same file family as Nano)
n_cfm_timesteps     2
t_span              2-step linear (not cosine)
CFG on CFM          NO
time_embed_mixer    PRESENT (meanflow)
S3_SR               16000   hop 160   token rate 25 Hz
S3GEN_SR            24000
kSamplesPerToken    960
SILENCE_TOKEN       4299
SPEECH_VOCAB_SIZE   6561

Convert STOP if listed keys/shapes are not the meanflow set the
turbo-tree convert-s3gen-to-gguf.py requires.

----------------------------------------------------------------------
## 8. Sampler (compile-time turbo.h)
----------------------------------------------------------------------

SEED=42
N_PREDICT=1000
TOP_K=1000
TOP_P=0.95
TEMPERATURE=0.8
REPEAT_PENALTY=1.2
REPEAT_LAST_N=1000
CFM_STEPS=2
SILENCE_TOKEN=4299
SILENCE_COUNT=3
No CFG_WEIGHT. No MIN_P. No EXAGGERATION.

turbo.h copies nano.h values. Do not add CFG. Do not switch N_PREDICT
to 768 because of a third-party note. Ear-check first.

----------------------------------------------------------------------
## 9. Convert / GGUF
----------------------------------------------------------------------

scripts/convert-t3-turbo-to-gguf.py
scripts/convert-s3gen-to-gguf.py
T3 quant             Q8_0
S3Gen quant          Q4_0
Outputs              Trident/models/chatterbox-t3-turbo-q8_0.gguf
                     Trident/models/chatterbox-s3gen-turbo-q4_0.gguf
Skip                 tfmr.wte.weight (official deletes it)
Skip                 text_head.weight (50276, 1024)
                     PROVEN training-only: official inference_turbo never
                     reads it. Safe skip for inference GGUF.
STOP                 any other unknown name
Do not put turbo weights through convert-t3-nano-to-gguf.py.

T3 keys listed from t3_turbo_v1.safetensors:
  tfmr.h.0 .. tfmr.h.23
  tfmr.ln_f.weight/bias   n_embd=1024
  tfmr.wpe.weight         (8196, 1024)
  text_emb.weight         (50276, 1024)
  speech_emb.weight       (6563, 1024)
  speech_head.weight/bias (6563, ...)
  cond_enc.spkr_enc.weight/bias  256 -> 1024

----------------------------------------------------------------------
## 10. Graph / ggml
----------------------------------------------------------------------

Same GPT-2 fused c_attn path as Nano t3_nano.cpp.
Q/K/V views [HD, N, n_head].
ggml_flash_attn_ext then reshape_2d(n_embd, N). No post-permute.
V is NOT permuted.
No RoPE. No CFG_BATCH. No Perceiver.

KV: n_embd * n_layer * n_ctx = 1024 * 24 * 8196 (larger than Nano).
If Vulkan alloc fails, throw. Do not move KV to CPU.
CHBX_MAX_NODES = 8192. Prompt graph ran. Do not raise until a throw.

This layout is NOT a license to copy it onto V3 Llama.

----------------------------------------------------------------------
## 11. Isolation names
----------------------------------------------------------------------

PIPE     \\.\pipe\chatterbox-turbo- + sha256(str(ROOT).encode() + b"turbo")[:12]
PID      models/turbo.pid
STAMP    models/turbo.voice.sha256
REV      models/turbo.rev
OUT      tts_out_turbo.wav
CKPT     .ckpt-turbo
venv     .venv-convert-turbo
speak()  wait while running() + WaitNamedPipeW 1s loops;
         RuntimeError("daemon") if PID dies; no 120s single shot
require_pin: branch == "turbo" and HEAD == CHATTERBOX_REV
never git checkout a SHA

Do not cross-kill nano server.pid or v3.pid.

----------------------------------------------------------------------
## 12. Proven facts
----------------------------------------------------------------------

text_head.weight is training-only. Skip is correct.
T3+S3Gen convert succeeded.
Vulkan-only cmake --build Release succeeded.
Bake + speak A/B intelligible (human ear, prior campaign).
Second speak hot pipe.
require_pin coded; no SHA checkout.
Pushed origin/turbo aec122a NEVER --force.

----------------------------------------------------------------------
## 13. Problems found
----------------------------------------------------------------------

Launcher SHA checkout caused detached HEAD. Fixed: require_pin.
t3_turbo_v1.yaml lies about n_layer (30 vs 24) and cond length
(250 vs Python 375). Ignore YAML for geometry.
text_head.weight stopped the first convert. Resolved: skip.

----------------------------------------------------------------------
## 14. Unread / investigate
----------------------------------------------------------------------

None blocking Turbo A/B. If a Turbo regression appears, re-GET the
HF sha and re-read inference_turbo before changing graphs.

----------------------------------------------------------------------
## 15. Checklist
----------------------------------------------------------------------

[x] extra clone chatterbox-turbo.cpp, branch turbo
[x] HF GET 749d1c1a
[x] text_head resolved (skip)
[x] T3 + S3Gen convert
[x] tts_turbo.py isolated names + require_pin
[x] Vulkan build, bake, speak A/B
[x] human ear A/B pass
[x] commit+push chatterbox turbo aec122a NEVER --force
[x] pin FULL SHA; commit+push Trident turbo 96def65 NEVER --force

----------------------------------------------------------------------
## 16. What not to do
----------------------------------------------------------------------

New GitHub remotes. Force-push. reset --hard. rebase. amend unless asked.
git checkout <40-char-sha> in chatterbox-turbo.cpp or Trident.
Work on main. Checkout turbo inside chatterbox.cpp (Nano tree).
Trust t3_turbo_v1.yaml for n_layer or cond length.
Copy Turbo 15s ENC_COND or 375 prefix onto V3.
Copy meanflow onto V3.
Reintroduce runtime CFG knobs.
GGML_CPU=ON. CUDA. Hybrid CPU+Vulkan.
Raise N_PREDICT. Raise CHBX_MAX_NODES before a throw.
Leave chatterbox-server.exe in VRAM after a work block.
Claim a WAV is intelligible from RMS.
PowerShell && or bash HEREDOC.
git-add helper _*.py

----------------------------------------------------------------------
## 17. Next session
----------------------------------------------------------------------

Turbo is shipped. Do not start a Turbo rewrite unless a regression is
proven on THIS tree. V3 is a different architecture. Turbo success does
not prove V3 flash_attn / RoPE / CFM graphs.
