================================================================================
CHATTERBOX 3-WAY TTS DIAGNOSIS — FROM-SCRATCH RUNBOOK
For ANY AI agent, on a NEW Windows machine, starting in an EMPTY directory with
no history, no code, no venv. Follow top to bottom. Facts marked [INV] are always
true; [RESULT] are prior findings to re-verify (do not trust blindly).
================================================================================

================================================================================
1. MISSION
================================================================================
Prove, with confidence 100, the ROOT CAUSE of why the "turbo" TTS model produces
~8s of correct speech while "nano" and "v3" produce ~14s, on the SAME fixed
counting text. DIAGNOSIS ONLY. Never fix until confidence is 100, then STOP and
report. (Work is ~80% done; see section 9 for exactly what remains.)

FIXED TARGET TEXT (use verbatim):
  "Count from one to thirty: one, two, three, four, five, six, seven, eight,
   nine, ten, eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen,
   eighteen, nineteen, twenty, twenty-one, twenty-two, twenty-three,
   twenty-four, twenty-five, twenty-six, twenty-seven, twenty-eight,
   twenty-nine, thirty."

================================================================================
2. PREREQUISITES (install once, in this order)
================================================================================
  - git                     (in PATH)
  - Python 3.11+            (in PATH as `python`)
  - Visual Studio 2022      (Desktop C++ workload, MSVC x64)
  - CMake                   (expected at C:/Program Files/CMake/bin/cmake.exe;
                             edit the launcher constant if it lives elsewhere)
  - Vulkan SDK 1.4.357.0    (expected at C:/VulkanSDK/1.4.357.0; edit the
                             launcher `VULKAN` constant if it differs)
The launchers are Windows-only (named pipes, ctypes.windll). No GPU is strictly
required for analysis, but generation uses the Vulkan backend.

================================================================================
3. CLONE + CHECKOUT (empty dir)
================================================================================
  git clone https://github.com/wgabrys88/chatterbox.cpp.git
  git clone https://github.com/wgabrys88/Trident.git
  cd Trident && git checkout v3
The two must be SIBLINGS:  <root>\chatterbox.cpp\  and  <root>\Trident\
(Trident resolves the engine at `Path(__file__).parent.parent / "chatterbox.cpp"`).
IMPORTANT: check out the `v3` branch of Trident — it holds this HANDOVER.md,
the analysis scripts, and the launchers with the updated pins. Do NOT use
Trident's `main` branch (it has stale pins and no handover). The chatterbox.cpp
branch is auto-checked-out by each launcher (ensure_pin), so no manual step is
needed there.

================================================================================
4. PROVIDE A VOICE CLIP
================================================================================
Place a speaker conditioning WAV at <root>\Trident\reference.wav (any clean
speech, ~5-18s, any sample rate; the launcher resamples/normalises). It is
baked into the GGUFs by chatterbox-bake. NOTE: the token-stream defect below is
driven by the TEXT + sampler, so it reproduces QUALITATIVELY with any voice;
byte-for-byte reproduction of the prior numbers requires the original clip,
which is NOT in the repo (private). Do not commit reference.wav.

================================================================================
5. RUN (this builds everything automatically)
================================================================================
  cd Trident
  python tts_nano.py  "TEXT"
  python tts_turbo.py "TEXT"
  python tts_v3.py    "TEXT" en
Each launcher: checks out its C++ branch, verifies HEAD == pinned rev, clones
ggml@7840aaba, cmake-builds into chatterbox.cpp\build\{family}, downloads HF
assets, converts to GGUF, bakes the voice, spawns a named-pipe daemon, and
writes a timestamped WAV (YYYYMMDD-HHMMSS-<family>.wav). The daemon stays
loaded; to kill it use models\{family}.pid. Generated artifacts (models\,
.ckpt-*, .venv-convert-*) are gitignored and recreated on demand.

================================================================================
6. REPO MAP & PINS (this is the current analysis baseline)
================================================================================
chatterbox.cpp (fork wgabrys88):
  turbo  HEAD a11ad2c  token dump + per-step sampler CSV logging (section 8)
  nano   HEAD 0bc62ba  token dump only
  v3     HEAD eec82c24 unchanged
  main   HEAD f42e714  (do not use)
Trident launcher pins (already committed): tts_turbo.py -> a11ad2c,
tts_nano.py -> 0bc62ba, tts_v3.py -> eec82c24.

================================================================================
7. ARCHITECTURE [INV]
================================================================================
- nano  = GPT-2 small  12L / 768  / 12 heads ("110M")
- turbo = GPT-2 medium 24L / 1024 / 16 heads ("350M")
- v3    = Llama 520M    30L / 1024 / 16 heads (separate tokenizer + code path)
- nano & turbo share IDENTICAL inference code (src/t3_nano.cpp + src/gpt2_bpe.cpp
  + src/chatterbox_tts.cpp). Only differences: header include (nano.h == turbo.h,
  byte-identical content) and the GGUF weights/dims. So any nano-vs-turbo
  divergence MUST come from the weights, or from code that only misbehaves at
  24L/1024/16H.
- Sampler (sample_next_token_ex in src/t3_nano.cpp), shared by nano/turbo:
  seed 42, N_PREDICT 1000, top_k 1000, top_p 0.95, temperature 0.8,
  repeat_penalty 1.2 over last 1000 tokens. Since generated seq < 1000, this is
  a FULL-HISTORY UNIQUE-TOKEN-SET penalty (apply_speech_repeat_penalty in
  src/chatterbox_t3_internal.h): every distinct token ever emitted has its logit
  divided (if >0) or multiplied (if <0) by 1.2, forever.
  Order: temperature -> top_k -> top_p -> repeat_penalty -> softmax -> sample.
- kSamplesPerToken = 960 @ 24000 Hz => token i = 40ms of audio. S3Gen = meanflow,
  CFM_STEPS = 2. Speech vocab 6563 (0..6560 speech, 6561 start, 6562 stop);
  SILENCE token = 4299; generate_t3 appends 3x4299 at the end. V3 resizes wav to
  (n-1)*960; nano/turbo do not resize. reference.wav is NOT the target text.
- Key source files: src/chatterbox_engine.cpp (generate_t3), src/t3_nano.cpp
  (GPT-2 eval_prompt/eval_step + sampler), src/chatterbox_tts.cpp (S3Gen),
  src/chatterbox_t3_internal.h (sampler + hparams + KV layout),
  include/tts-cpp/chatterbox/{nano,turbo,v3}.h (constants),
  scripts/convert-t3-{nano,turbo}-to-gguf.py.

================================================================================
8. KEY FINDINGS SO FAR [RESULT — re-verify, not gospel]
================================================================================
The defect is NOT a general turbo bug. It is triggered by TEXT CONTENT/LENGTH:
  - Counting text (repetitive list): nano emits 53x silence 4299 (natural pauses
    between numbers); turbo emits only 2 natural silences -> turbo runs the
    numbers together with no pauses and its speech degrades around ~8s.
  - Natural 4-sentence paragraph: ALL THREE are perfect (nano ~3 pauses at
    sentence boundaries).
  - Stress text (392 text tokens: count 1..40 + one sentence repeated x10 +
    count 41..50): nano emits EOS after ~14 speech tokens (0.68s), turbo after
    ~142 (5.9s), v3 errors "T3 stopped without EOS" (hits N_PREDICT=1000).
=> Repetition AND length both trigger failure; turbo is just the most sensitive.
PRIME SUSPECT: the full-history unique-token repeat penalty suppressing the
silence token (and other legitimate repeats) on repetitive input.
The turbo counting run is DETERMINISTIC (seed 42): 541 dropped tokens, silence
at positions [1,537,538,539,540] (538-540 are the 3 appended; only ~2 natural).

================================================================================
9. METHODOLOGY (the only way to work here: log in C++, then pandas)
================================================================================
There is no such thing as too much logging, as long as the log is CSV.
Workflow for any hypothesis:
  1. Add exhaustive CSV logging to the REAL C++ (not Python shims).
  2. git commit, bump the launcher CHATTERBOX_REV pin, run (launcher rebuilds).
  3. Load the CSV with pandas and compute the answer.

Already implemented (turbo branch, commit a11ad2c): sample_next_token_ex writes
one CSV row per generated token to models\turbo_sample_dump.csv:
  step,chosen,chosen_prob,sil4299_prob,sil4299_rank,sil4299_seen,gen_len,
  top0_id,top0_prob,...,top9_id,top9_prob
(sil4299_seen flips 0->1 once silence 4299 is in the history, i.e. when the
repeat penalty starts biting it.) The CSV filename is HARDCODED to
"turbo_sample_dump.csv" and the dump to "turbo_t3_dump.txt" in
chatterbox_engine.cpp — port the 3-file edit (engine.cpp + internal.h + t3_nano.cpp)
onto the nano branch to get "nano_sample_dump.csv".

Analysis venv (create with `python -m venv .venv`) and install:
  pandas numpy scipy librosa soundfile matplotlib gguf==0.19.0
  praat-parselmouth pyworld
(gguf: read KV via field.contents() not .data. pyworld aperiodicity is 2D -> use
.mean(axis=1).) Reference analysis scripts are in Trident\analysis\*.py but their
WAV filenames are hardcoded to prior-run timestamps — update paths before use.

================================================================================
10. NEXT STEPS (ordered; each ends in a file under Trident\analysis\)
================================================================================
 1. pandas-analyze turbo_sample_dump.csv: for every step where silence 4299 was
    plausible, show sil4299_prob / sil4299_rank BEFORE vs AFTER sil4299_seen
    flips to 1. Confirm the repeat penalty collapses the silence token and
    pinpoint the first step where a pause is lost. -> sampler_analysis.md
 2. Port the CSV logging to the nano branch, run the SAME counting text, produce
    nano_sample_dump.csv. Diff turbo vs nano step-by-step at the step where nano
    emits silence but turbo does not (this is the 8s-vs-14s mechanism).
 3. DECISIVE EXPERIMENT: set REPEAT_PENALTY=1.0 in turbo.h, rebuild, run counting
    text. If turbo now emits pauses like nano -> root cause CONFIRMED.
    (Also try REPEAT_LAST_N=small to see the window effect.)
 4. Separately explain the STRESS-text early-EOS: prompt_len = 1+375+n_text+1;
    compare against the model's max positions; the reference PyTorch tts_turbo.py
    tokenizes with truncation=True (max 1024) but gpt2_bpe.cpp does NOT truncate.
 5. When ONE mechanism explains all three texts and is consistent across
    nano/turbo/v3 -> write analysis/root_cause.txt, then STOP and await
    instruction to fix.

================================================================================
11. GOTCHAS
================================================================================
- Windows PowerShell 5.1 only: no '&&', no 'head', no process substitution.
  Use Select-String (not grep). Run python as `python` from the Trident dir; the
  analysis venv python is <root>\.venv\Scripts\python.exe.
- Every run OVERWRITES the dumps/CSV in models\; copy them aside first.
- v3_t3_dump.txt has NO hparams block (older code) — guard dict keys.
- After committing a C++ change you MUST bump the matching launcher CHATTERBOX_REV
  or the launcher fails its pin check.
- "8s vs 14s" is approximate; token indices are authoritative.
- commit with extended messages; do NOT push secrets; reference.wav is private.
================================================================================
