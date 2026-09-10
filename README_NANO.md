# Chatterbox NANO — AI handover (Trident)

Same document shape as README_NANO.md / README_TURBO.md / README_V3.md.
Read all three as columns of one table. Do not copy numbers, graphs, bake
windows, tokenizers, or CFM schedules across families.

This file is for AI continuation and final handover. Human ear-check is the
quality gate. File size, duration, and RMS are not a pass.

----------------------------------------------------------------------
## 0. How to use this file
----------------------------------------------------------------------

Stay on chatterbox.cpp branch nano and Trident branch nano.
Launcher is tts_nano.py only. Do not edit turbo or v3 trees from here.
PowerShell only. Fail hard. No fallbacks. No force-push. No git checkout SHA.
CONFIDENCE 100: if a number is not in THIS file / THIS tree / a GET this
session, STOP.

----------------------------------------------------------------------
## 1. Identity
----------------------------------------------------------------------

Family              NANO
Official name       Chatterbox Nano
C++ clone           C:\Users\eb-wjt\Downloads\synthesis\chatterbox.cpp
C++ branch          nano
C++ HEAD            56b80874a8fcd61e312d25ddc49da38685a4771c
C++ remote          https://github.com/wgabrys88/chatterbox.cpp.git
Trident clone       C:\Users\eb-wjt\Downloads\synthesis\Trident
Trident branch      nano
Trident HEAD        2d3e7d4b458bbdc9333c9ab3ded760346608b966
Trident remote      https://github.com/wgabrys88/Trident.git
Launcher            tts_nano.py
CHATTERBOX_REV      56b80874a8fcd61e312d25ddc49da38685a4771c
Status              SHIPPED. Human ear-check A/B intelligible (prior campaign).

Analogical rule:
  chatterbox nano  <->  Trident nano  <->  tts_nano.py

----------------------------------------------------------------------
## 2. Product shape
----------------------------------------------------------------------

one Engine({t3,s3}).synthesize(text)
one named-pipe server in server.cpp
argv: exe  t3.gguf  s3.gguf  out.wav  pipe
NO language argv
one bake.exe overwrites ONLY nano GGUFs
compile-time sampler in nano.h
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

HF weights     ResembleAI/chatterbox-nano
               71ccd1d0081b430592cea481f4307e764e07bc64
Official py    github.com/resemble-ai/chatterbox  (Nano/Turbo family)
ggml           7840aaba1989c6deeefede1d77d5aaf8f52b947e
               (detached in chatterbox.cpp/ggml is expected)

HF assets (tts_nano.py ASSETS):
  t3_nano_v1.safetensors
  s3gen_meanflow.safetensors
  conds.pt
  ve.safetensors
  vocab.json
  merges.txt
  added_tokens.json

----------------------------------------------------------------------
## 4. T3
----------------------------------------------------------------------

Backbone            GPT-2 small
Layers              12
Hidden              768
Heads               12  (n_embd // 64)
Head dim            64
FF                  GPT-2 GELU MLP (c_fc / c_proj), fused c_attn
Norm                LayerNorm + bias
Position            GPT-2 wpe learned table. No RoPE. No Llama.
Perceiver           NO
CFG on T3           NO (ignored)
Emotion adv         NO
Speech codebook     6561 + start 6561 + stop 6562 = 6563
Cond speech tokens  375
n_ctx from GGUF     wpe table length 8196
T3 sources          t3_nano.cpp  (linked into the Nano server)
                    gpt2_bpe.cpp

Do not allocate KV at 131072. Nano n_ctx is the wpe table.

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

ENC_COND_LEN        15 * S3_SR   (15s @ 16 kHz)
DEC_COND_LEN        10 * S3GEN_SR
Bake VE cap         30s @ 16 kHz  (bake.cpp resize)
Cond tokens         15s @ 16 kHz  (main.cpp cond_wav)
Prompt tokens       10s @ 16 kHz
Prompt feat         10s @ 24 kHz
Campplus emb        10s @ 16 kHz
Empty wav           throw
Voice tensors overwrite ONLY nano GGUFs.

375 is the VOICE PROMPT PREFIX, not "15s of good audio".

----------------------------------------------------------------------
## 7. S3Gen / CFM
----------------------------------------------------------------------

Family              meanflow
n_cfm_timesteps     2
t_span              2-step linear (not cosine)
CFG on CFM          NO
time_embed_mixer    PRESENT (meanflow). Use the meanflow path.
S3_SR               16000   hop 160   token rate 25 Hz
S3GEN_SR            24000
kSamplesPerToken    960
SILENCE_TOKEN       4299
SPEECH_VOCAB_SIZE   6561  (flow packing drops token >= 6561)

----------------------------------------------------------------------
## 8. Sampler (compile-time nano.h)
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

Do not raise N_PREDICT. Split paragraphs in Python.

----------------------------------------------------------------------
## 9. Convert / GGUF
----------------------------------------------------------------------

scripts/convert-t3-nano-to-gguf.py
scripts/convert-s3gen-to-gguf.py
T3 quant             Q8_0
S3Gen quant          Q4_0
Outputs              Trident/models/chatterbox-t3-nano-q8_0.gguf
                     Trident/models/chatterbox-s3gen-nano-q4_0.gguf
Skip                 tfmr.wte.weight (official deletes it)
STOP                 any other unknown tfmr.* / cond_enc.*
text_head.weight     not listed as a Nano convert fact in the V3 campaign.
                     Do not invent a skip from Turbo.

----------------------------------------------------------------------
## 10. Graph / ggml
----------------------------------------------------------------------

Q/K/V from fused GPT-2 c_attn split.
Views [HD, N, n_head].
ggml_flash_attn_ext(Q,K,V,mask)
reshape_2d(attn, n_embd, N)  -- no permute after flash_attn
V is NOT permuted. This is GPT-2 fused c_attn, not Llama.

s3tokenizer.cpp in the same tree uses a DIFFERENT V permute
(1,2,0,3) because its starting layout differs. Do not treat Nano T3
as the flash_attn oracle for Llama.

ggml.h flash_attn_ext:
  q:   [n_embd_k, n_batch, n_head,    ne3]
  k:   [n_embd_k, n_kv,    n_head_kv, ne3]
  v:   [n_embd_v, n_kv,    n_head_kv, ne3]  !! not transposed !!
  res: [n_embd_v, n_head,  n_batch,   ne3]  !! permuted !!

KV: n_embd * n_layer * n_ctx. No CFG batch dim.
CHBX_MAX_NODES = 8192. Do not raise until a throw.

----------------------------------------------------------------------
## 11. Isolation names
----------------------------------------------------------------------

PIPE     \\.\pipe\chatterbox- + sha256(str(ROOT).encode())[:12]
         NOTE: tts_nano.py on 95a85aa / 2d3e7d4 still uses this older
         colliding form (no b"nano" suffix). Do not "fix" it while
         working V3. If touching tts_nano.py: checkout Trident nano first.
PID      models/server.pid
STAMP    models/voice.sha256
REV      models/rev
OUT      tts_out.wav   (daemon writes argv[3] in place)
CKPT     .ckpt         (or .ckpt-nano if later isolated)
venv     .venv-convert  /  prefer .venv-convert-nano if pins diverge
Do not point this launcher at turbo or v3 GGUFs.
Do not cross-kill turbo.pid or v3.pid.

----------------------------------------------------------------------
## 12. Proven facts
----------------------------------------------------------------------

Vulkan-only cmake + vk_init(0) builds and runs.
Named-pipe daemon stays up across utterances.
Bake then speak. Second speak is hot.
English A and B intelligible (prior campaign, human ear).
GPT-2 graphs read n_layer/n_embd/n_ctx from GGUF.
n_ctx from wpe table, not a YAML.

----------------------------------------------------------------------
## 13. Problems found
----------------------------------------------------------------------

tts_nano.py isolation names still collide with a naive copy
(PIPE/PID/OUT/CKPT without a family suffix). Turbo and V3 were
isolated instead of rewriting Nano in the V3 campaign.
Launcher historically used git checkout SHA (detached HEAD). Fixed
on Trident nano 2d3e7d4: require_pin, no SHA checkout.

----------------------------------------------------------------------
## 14. Unread / investigate
----------------------------------------------------------------------

Whether nano safetensors contain text_head.weight was NOT listed in
the V3 campaign. Do not claim it does.
Whether a later Nano launcher rename of PID/PIPE landed: read
tts_nano.py on Trident nano this session before editing.

----------------------------------------------------------------------
## 15. Checklist
----------------------------------------------------------------------

[x] clone + branch nano
[x] convert T3/S3Gen
[x] Vulkan build server+bake
[x] bake + speak A/B
[x] human ear A/B pass
[x] pin 56b8087 in tts_nano.py
[x] require_pin (no SHA checkout)
[ ] optional: family-suffix PIPE/PID/OUT without breaking a live Nano user

----------------------------------------------------------------------
## 16. What not to do
----------------------------------------------------------------------

New GitHub remotes. Force-push. reset --hard. rebase. amend unless asked.
git checkout <40-char-sha> in chatterbox.cpp or Trident.
Work on main. Checkout turbo or v3 inside chatterbox.cpp.
Copy Nano graphs onto Llama V3.
Copy Nano 15s ENC_COND or 375 prefix onto V3.
Copy meanflow / time_embed_mixer onto V3.
GGML_CPU=ON. CUDA. Hybrid CPU+Vulkan.
Raise N_PREDICT. Raise CHBX_MAX_NODES before a throw.
Leave chatterbox-server.exe in VRAM after a work block.
Claim a WAV is intelligible from RMS.
PowerShell && or bash HEREDOC.
git-add helper _*.py

----------------------------------------------------------------------
## 17. Next session
----------------------------------------------------------------------

Nano is shipped. Do not start a Nano rewrite unless a regression is
proven on THIS tree. If V3 work needs a Nano graph fact, READ
chatterbox.cpp src/t3_nano.cpp and ggml.h. Do not assume Nano
flash_attn conventions are correct for Llama.
