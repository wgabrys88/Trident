KNOWLEDGEBASE
You are a fresh agent. You may be a different model or provider than the one
that wrote this. There is no chat history. This file is the runbook. Opened
files beat this file. tts_nano.py tts_turbo.py tts_v3.py tts_common.py beat
this file. git ls-remote and git show beat any SHA written anywhere, including
here. Do not cite SHAs or dump counts from memory. Discover them.
ASCII only. No markdown tables. Numbers live in sentences.
PowerShell: use semicolon, not &&. Unquoted PowerShell strings split argv.
Pass long text as one Python string via subprocess.run([exe, script, text]).
Internet is allowed for eval library APIs and for interpreting MOS/WER papers.
Do not use the web to invent C++ knobs that are not on the checked-out branch.

This file, bootstrap_state.py, eval_tts.py, eval_out/, WAVs, models/, dumps,
reference.wav, venvs, and experiment scripts are untracked. Do not commit them.
Trident .gitignore is an allowlist of tracked launcher files, then excludes
knowledgebase.md bootstrap_state.py eval_tts.py eval_out/ so they stay local.
If knowledgebase.md is missing from .gitignore on a fresh clone, add that line
only when the user asks; do not commit .gitignore unless asked.

================================================================================
0. YOU MAY KNOW NOTHING
================================================================================
Assume clones, pins, Vulkan, GGUF, eval venv, and this file may be absent.
Do not stall. Do not walk trees into context. Recreate bootstrap_state.py if
missing (fail-hard, stderr discarded, stdout OK/MIS/BAD, exit 1 on BAD).
Run: python bootstrap_state.py
Act only on MIS/BAD. Add a probe to bootstrap_state.py only when you would
otherwise retype the same git or file check by hand. Never commit it.

If eval_out/short_baseline_analysis.txt exists, read it as a frozen two-
sentence calibration snapshot only. Re-score those WAVs only if they still
exist and you need a number. If they are gone, ignore the snapshot. It is not
the mission. eval_out/baseline-tts-analysis.canvas.tsx is the same snapshot.

================================================================================
1. HARD RULES
================================================================================
Do not touch main. Do not merge family branches into each other or onto main
until the user says merge.
Do not checkout Trident nano or turbo unless git ls-tree -r --name-only v3
lists tts_common.py. Trident work branch is v3. C++ families are nano, turbo,
v3. After a family launcher runs, C++ checkout is THAT family. Read only that
branch header. Wrong header is a bug. Use git show <branch>:path for other
families. Do not switch C++ to main.
One live chatterbox-server.exe. Kill siblings via pid files in models/.
Wait until pid is gone and named pipe is absent before rebuild. Do not sleep
to hide GGUF load. CreateNamedPipe and ConnectNamedPipe before Engine. Else
that is the bug.
Do not invent C++ argv knobs. Do not change header defaults in nano.h turbo.h
v3.h unless the user explicitly orders a header edit as part of a fix.
Do not add MIN_P to nano or turbo. Do not add SILENCE_COUNT to v3.
Same env helper names across families in chatterbox_t3_internal.h: envf envi
effective_*. Family-only symbols only where that header already has them.
Bare metal. No harnesses, no tests-as-product, no defensive layers, no
fallbacks. Fail hard. No small.en. No archived-WAV substitute. No
resemblyzer-then-MFCC branch. Speaker score is MFCC-mean cosine vs
reference.wav. No try/except around required eval libs.
Do not archive WAVs. Do not copy dated synth WAVs into models/archive.
Dumps stay next to the T3 GGUF of the family that wrote them. Copy dumps to
eval_out/dumps/<wav_stem>_<family>_*.txt when running experiments so later
synths do not destroy bind evidence.
Vulkan is the synth backend for the whole C++ project. Do not rebuild with
CUDA. Do not treat nvidia-smi as the synth device. Missing Vulkan SDK paths
in tts_common.py is a hard stop. Eval scorers in .venv-eval run on CPU.
Never git config. Never force-push main. Never skip hooks unless asked.
Never push main. Never commit this file or the untracked list above.
Do not grow Trident past the allowlist. No extra tracked scripts.

================================================================================
2. LAYOUT AND CLONE
================================================================================
Parent folder. Sibling directory names MUST be chatterbox.cpp and Trident.
  https://github.com/wgabrys88/chatterbox.cpp
  https://github.com/wgabrys88/Trident
chatterbox.cpp also lists upstream https://github.com/gianni-cor/chatterbox.cpp
cd Trident ; git checkout v3
ROOT is the directory of tts_common.py. CHATTERBOX is ROOT.parent /
chatterbox.cpp. MODELS is ROOT/models. REF is ROOT/reference.wav.
reference.wav must be 16-bit PCM mono 24000 Hz. Untracked. Bake and synth
refuse to run without it.
Tracked Trident files: discover with git ls-tree -r --name-only v3. Expect
.gitignore .gitattributes tts_common.py tts_nano.py tts_turbo.py tts_v3.py
and nothing else.
CMAKE, VULKAN, GGML_REV, generator Visual Studio 17 2022 x64, and CMake
flags are literals in tts_common.py. Need Vulkan Include, Lib/vulkan-1.lib,
Bin/glslc.exe. CMake sets GGML_VULKAN=ON GGML_CUDA=OFF GGML_CPU=OFF
GGML_OPENMP=OFF. chatterbox.cpp CMakeLists.txt fatals if those are wrong.
ggml inside chatterbox.cpp/ggml must equal GGML_REV from tts_common.py.
Discover: git -C chatterbox.cpp/ggml rev-parse HEAD.

================================================================================
3. DERIVE STATE (NEVER REMEMBER)
================================================================================
Read Variant fields from each tts_*.py. chatterbox_rev in that file is the
pin. Compare to git ls-remote origin refs/heads/<family> in chatterbox.cpp
and to git rev-parse of the local family branch. Compare also to
models/<rev_name> written by the last successful build.
If launcher pin != origin family tip, that is BAD until you pin after push.
If exe/GGUF/Vulkan are unproven, prove them with Hello. once per family
(v3 needs language en), then stop calling that bootstrap. Hello. is a
Length>44 WAV, not a feeling.

Family identity from launchers (names not SHAs; discover SHAs from files):
nano branch nano, GPT-2 T3, t3_nano.cpp, gpt2_bpe.cpp, convert-t3-nano-to-
gguf.py and convert-s3gen-to-gguf.py, t3 chatterbox-t3-nano-q8_0.gguf, s3
chatterbox-s3gen-nano-q4_0.gguf, pid server.pid, stamp voice.sha256, rev
file rev, build dir nano, pipe_tag empty bytes, knobs GPT2_KNOBS, no
language argv. HuggingFace asset list is the assets tuple in tts_nano.py.
turbo same GPT-2 stack as nano but turbo.h, convert-t3-turbo-to-gguf.py,
different GGUF names, pid turbo.pid, stamp turbo.voice.sha256, rev turbo.rev,
pipe_tag b"turbo". CMake on turbo branch still compiles t3_nano.cpp.
v3 Llama T3, t3_v3.cpp, mtl_bpe.cpp, convert-t3-v3-to-gguf.py and
convert-s3gen-v3-to-gguf.py, needs_language True, pid v3.pid, pipe_tag
b"v3", knobs V3_KNOBS, EngineOptions adds language_id.

CMake link list is source of truth per branch. nano and turbo: t3_nano.cpp
gpt2_bpe.cpp chatterbox_tts.cpp chatterbox_engine.cpp plus bake tree.
v3: t3_v3.cpp mtl_bpe.cpp chatterbox_tts.cpp chatterbox_engine.cpp plus bake
tree. Dead-code deletion: grep unused symbols on that checkout, delete only
proven-dead, keep bake/server/sampler/dump/getenv that still run, ask if
unsure, keep diffs uniform across families.

Header defaults: read from include/tts-cpp/chatterbox/nano.h turbo.h v3.h
via git show on each branch. Do not copy defaults into this file as gospel.
nano and turbo share: SEED 42, N_PREDICT 1000, TOP_K 1000, TOP_P 0.95,
TEMPERATURE 0.8, REPEAT_PENALTY 1.2, REPEAT_LAST_N 1000, CFM_STEPS 2,
SILENCE_TOKEN 4299, SILENCE_COUNT 3. v3 differs: TOP_K 0, TOP_P 1.0,
MIN_P 0.05, CFG_WEIGHT 0.5, CFM_STEPS 10, CFM_CFG 0.7, SILENCE_TOKEN 4299,
no SILENCE_COUNT.

Input text size is counted as GPT-2 or MTL BPE tokens on the prompt, written
in the dump "text" line as space-separated ids. prompt_slots equals one plus
cond_prompt_len plus bpe_tokens plus one (start token, baked cond, text,
start_speech). t3_nano.cpp throws "T3 prompt exceeds context" when
prompt_slots exceeds n_ctx from GGUF (typically 8196). That is a hard input
ceiling, separate from generation length. Output speech is capped by
N_PREDICT (1000 tokens, about 40 seconds at kSamplesPerToken 960 and 24 kHz).
Reference PyTorch Resemble chatterbox uses max_new_tokens=1000; community
chunk guidance is about 300 characters per chunk (resemble-ai/chatterbox#181).

================================================================================
4. PROTOCOL
================================================================================
Pipes: sha256(str(ROOT)+pipe_tag) hex[:12]
nano  \\.\pipe\chatterbox-<tag>
turbo \\.\pipe\chatterbox-turbo-<tag>
v3    \\.\pipe\chatterbox-v3-<tag>
Server argv nano/turbo: chatterbox-server.exe t3 s3 pipe. argc<4 is fatal.
Server argv v3: exe t3 s3 pipe language. argc<5 is fatal. v3 EngineOptions
has language_id from argv[4].
Bake argv: chatterbox-bake.exe t3 s3 reference.wav. Bake inits Vulkan,
LUFS-normalises ref to -27, resamples to 16 kHz for voice encoder, writes
speaker_emb and cond speech tokens into T3 GGUF, writes prompt_token,
prompt_feat, embedding into S3 GGUF, stamps cond_prompt_length. Changing
reference.wav changes sha256(REF) vs models/<stamp_name> and rebakes.
Launchers kill other family pids, maybe rebuild if exe/bake/rev stamp
mismatch the pin, maybe convert GGUF from HuggingFace assets, maybe bake,
maybe respawn if knobs or language stamp changed, then speak.
speak writes wav_path newline then text newline, blocks on ok\n. CR/LF in
text become spaces. Load delay before first ok is normal.
WAV write in server.cpp: PCM 1ch 24000 16-bit, 44-byte header, atomic
replace via .tmp. Length<=44 means no PCM. kSamplesPerToken is 960 in
s3gen_pipeline.h. Duration is not success.

Dumps when CHATTERBOX_SAMPLER_LOG is 1 (launchers setdefault on spawn):
<family>_sample_dump.csv and <family>_t3_dump.txt next to the T3 GGUF.
Family prefix is nano, turbo, or v3 per branch in chatterbox_engine.cpp.
CSV columns start with step,chosen,... Bind steps, chosen==4299 count,
predicted_count, dropped_count, eos. eos 1 means last predicted token was
stop_speech. eos 0 means N_PREDICT or n_ctx. GPT-2 dumps also print n_embd
n_head n_layer n_ctx vocabs start_speech stop_speech cond_prompt_len.

Knobs: KNOB_ENV in tts_common.py maps CLI flag to env var. Stamp
models/<name>.knobs as name=value per family knobs tuple. Empty value means
header default (env var unset). Stamp change kills and respawns. Unknown CLI
flag prints usage() from disk and exits. A knob exists only if that family's
generate, sample, or s3gen reads it on the checked-out branch.
GPT2_KNOBS = shared + silence-count
V3_KNOBS = shared + min-p cfg-weight cfm-cfg
Shared: repeat-penalty temperature top-k top-p repeat-last-n seed n-predict
cfm-steps silence-token
Do not pass min-p cfg-weight cfm-cfg to nano or turbo launchers.

SILENCE_COUNT on GPT-2: effective_silence_count() in chatterbox_t3_internal.h
appends that many silence tokens to the S3Gen list after T3 sampling in
generate_t3. It is not a consecutive-4299 abort. v3 has SILENCE_TOKEN and
no SILENCE_COUNT.

Launch:
python tts_nano.py  [flags] "<text>"
python tts_turbo.py [flags] "<text>"
python tts_v3.py    [flags] "<text>" en
usage() on disk wins. Long text: one argv string.

================================================================================
5. EVAL
================================================================================
.venv-eval is the scorer venv, not .venv-convert*. pip logs go to
eval_out/pip.log if you create one. Install from eval_out/requirements.lock
if missing: numpy scipy soundfile librosa matplotlib praat-parselmouth
pystoi jiwer faster-whisper silero-vad audiobox-aesthetics torch torchaudio
funasr modelscope utmos-pytorch. Skip pesq and resemblyzer. No versa,
audioevals, whisperx, openai-whisper, pandas.
faster-whisper medium.en on CPU int8, word_timestamps, vad_filter=False.
Number-words to digits then jiwer WER/CER plus in-order coverage.
audiobox-aesthetics: pass torch tensor and sample_rate, not file path.
Tone from VAD pauses plus F0 75-400 Hz: flowing listed mixed broken flat.
funasr emotion2vec_plus_base at 16 kHz, cosine vs reference.wav.
UTMOS, audiobox PQ and CE, HNR, jitter, shimmer, voiced fraction, 4299
timeline answer how long speech stays human. Order and coverage still matter.
pystoi needs aligned same-text reference; reference.wav is a clone prompt,
so do not score STOI unless you have a parallel clean take.
eval_tts.py is untracked. It reads eval_out/benchmark_text.txt and writes
eval_out/report.json. Coverage needles must match that text. PASS only if
no FAIL and WER<=0.12. FAIL if missing tail, dropped integers, dropped
garden repeats, dropped street/postal/time, trailing garbage, or eos 0 with
predicted_count short of N_PREDICT.
Failure classes for analysis: length (eos 1, tiny predicted_count vs prompt
BPE still in dump), budget (eos 0 at N_PREDICT cap), content (PCM long but
ASR drops structured needles), collapse (chosen4299 dominates or F0/HNR
cliff), mix (multiple). Do not describe WAVs by ear. Numbers from dumps and
report.json only.

Eval suite scripts (all untracked under eval_out/):
threshold_probe.py bisects hello, count_10_27, short baseline, and long
benchmark char slices at 25/50/75/100 percent for nano and turbo. Writes
threshold_report.json.
structure_experiment.py runs numbered vs flowing prose pairs at similar char
tiers. Appends experiment_report.jsonl and writes experiment_summary.json.
Copies dumps to eval_out/dumps/.
analyze_wav_135239.py is a template for timed ASR plus VAD on one WAV.
count_10_27.txt holds the ten-to-twenty-seven counting prompt.
benchmark_text.txt holds the long benchmark one-liner (section 10 below).

Before a fix: save eval_out/report.json experiment_summary.json
threshold_report.json as eval_out/baseline_pre_fix/ copies if comparing.
After pin push rebuild: rerun bootstrap_state.py, threshold_probe.py,
structure_experiment.py, and .venv-eval python eval_tts.py. Diff predicted_count,
duration_s, failure_class, and ASR fields. That diff is the proof the fix landed.

================================================================================
6. MISSION HIERARCHY
================================================================================
Always: hard rules, derive, Vulkan synth, CPU eval, discover pins.
Default subject when unspecified: nano at header defaults on measurable text.
Turbo and v3 are contrast families when nano measurement is ambiguous. Do not
start turbo or v3 perfection campaigns unless the user says so. Do not merge.

NEXT SESSION TOPIC (when user assigns it):
Implement C++ or launcher corrections discovered by eval, then git workflow:
(1) fix on the correct chatterbox.cpp family branch (nano first unless the
bug is family-specific), (2) commit on that branch with why in message,
(3) set chatterbox_rev in tts_<family>.py to git rev-parse HEAD of that
branch, (4) commit Trident v3 allowlist only (tts_*.py tts_common.py if
changed), (5) push chatterbox family branch and Trident v3 to origin, (6)
rebuild locally (launcher will rebuild when rev stamp mismatches pin), (7)
rerun eval suite and compare to pre-fix snapshots. Do not push main. Do not
commit knowledgebase eval scripts WAVs models reference.wav venvs eval_out.
If pin in launcher != origin family tip after your push, that is BAD until
they match.

Later sessions (not unless assigned): turbo then v3 perfection same pattern.

================================================================================
7. METHODOLOGY
================================================================================
Validate from scratch even if dumps exist. Confirm live C++ branch matches
the family you are measuring before reading headers.
Inspect GGUF before trusting a WAV. T3 q8_0, S3 q4_0. List keys via convert
venv gguf or one-shot Python: chatterbox.n_embd n_ctx cond_prompt_length,
builtin speaker_emb, cond_prompt_speech_tokens; S3 s3gen.builtin.prompt_token_len
prompt_feat_frames embedding. Compare voice stamp sha256(REF) to
models/<stamp_name>. Rebake when stamp differs.

Text size experiments (derive thresholds, do not memorize numbers):
Run threshold_probe.py and structure_experiment.py after bootstrap OK.
Kill stale chatterbox-server.exe if pipe busy before synth batch.
Hypothesis to test with instruments: numbered short sentences vs one flowing
sentence at similar char count. Measure bpe_tokens from dump, not chars.
Structure affects ASR fidelity and chosen4299 rate more often than the
15-token instant cliff. Dense benchmark trap text (homophones postals garden
repeats) fails earlier than normal prose at higher BPE. Turbo tolerates
longer benchmark slices than nano before the cliff; verify on each pin.

Knob experiments after default run on the same text, one knob at a time, CLI
only, do not edit headers unless user orders it. If chosen==4299 dominates,
first knob is --repeat-penalty 1.0. Other legal GPT2_KNOBS: temperature
top-k top-p repeat-last-n seed n-predict cfm-steps silence-token silence-count.
Never min-p or cfg on nano/turbo. Use literature (llama.cpp defaults:
repeat-penalty 1.0 disables penalty; header uses 1.2) to pick ranges, then
measure. n-predict does not override eos 1 stop_speech early exit.

Bisect input BPE when generation is unusable: Hello., count list, short
baseline, long slices, full benchmark. Preserve dump per trial under
eval_out/dumps/. No chunker in C++ unless user adds one.

================================================================================
8. GROUND TRUTH
================================================================================
Opened file beats this file.
Launcher Variant beats memory.
git ls-remote beats a remembered tip.
git show <family>:<path> beats a remembered header.
models/<family>.knobs empty values mean header defaults.
The dump next to the T3 GGUF from the synth that wrote the WAV is the only
legal source for eos, predicted_count, and chosen==4299 for that WAV unless
you copied it to eval_out/dumps/.
eval_out/report.json is the legal source for WER and MOS-like scores when it
names that WAV and the matching dump bind.
reference.wav bytes are bake identity. sha256 is the stamp.
CMakeLists.txt on the checked-out family is the link list.

================================================================================
9. CONFIDENCE GATE
================================================================================
No "works" without a launcher WAV path and Length>44.
No knob claim without file:line on the checked-out family reading that env.
No eos or silence claim without that synth's dump (or preserved copy).
No WER without faster-whisper medium.en plus jiwer on the exact source text
in eval_out/benchmark_text.txt or the experiment prompt used.
No naturalness claim without UTMOS, audiobox PQ and CE, and F0/VAD from
.venv-eval.
No "fix landed" without pushed pin matching origin, rebuilt exe rev stamp,
and post-fix eval suite diff vs pre-fix snapshot.
Vulkan is synth. Eval is CPU. Do not merge. Do not touch main.

================================================================================
10. LONG BENCHMARK TEXT (one argv)
================================================================================
Write this exact string to eval_out/benchmark_text.txt. Speak as one prompt.
eval_tts.py coverage needles must match it after the same norm() pipeline.

Please confirm this delivery route as a single spoken record. Count one to forty: one, two, three, four, five, six, seven, eight, nine, ten, eleven, twelve, thirteen, fourteen, fifteen, sixteen, seventeen, eighteen, nineteen, twenty, twenty-one, twenty-two, twenty-three, twenty-four, twenty-five, twenty-six, twenty-seven, twenty-eight, twenty-nine, thirty, thirty-one, thirty-two, thirty-three, thirty-four, thirty-five, thirty-six, thirty-seven, thirty-eight, thirty-nine, forty. Leave 221B Baker Street, London NW1 6XE at 07:05, not 7:05 PM, then 1600 Pennsylvania Avenue Northwest, Washington, DC 20500 by 13:00, then 350 Fifth Avenue, New York, NY 10001 at 4:30 p.m., then 10 Downing Street, London SW1A 2AA at 23:59, then ul. Marszalkowska 1, 00-624 Warszawa at 08:00, then 1 Infinite Loop, Cupertino, CA 95014, then 11 Wall Street, New York, NY 10005, then Princes Street, Edinburgh EH2 2AN. Read these codes exactly: SW1A 1AA, EC1A 1BB, 90210, 10001, 00-001, G1 1AA, W1A 0AX. Times that must not collapse: 00:00, 00:01, 12:00, 12:01, 07:05, 7:05, 13:00, 16:30, 23:59. Homophones to keep distinct: Wright Road is not Right Road; St. John's Street is not Saint Johns Place; there to their house on They're Lane. Repeat ten times: The sun is shining over the garden. The sun is shining over the garden. The sun is shining over the garden. The sun is shining over the garden. The sun is shining over the garden. The sun is shining over the garden. The sun is shining over the garden. The sun is shining over the garden. The sun is shining over the garden. The sun is shining over the garden. Count forty-one to fifty: forty-one, forty-two, forty-three, forty-four, forty-five, forty-six, forty-seven, forty-eight, forty-nine, fifty. End with this science sentence: because the universe is vast and full of mysteries that curious minds continue to explore every single day, the courier still has to say serial AB-0042, order 3.14159, and room 101.

Coverage after digit-word norm: in-order integers 1 through 50, garden phrase
count 10, streets postals times homophones science tail. Missing any class
is FAIL. Derive exact needle strings from this source using eval_tts.py norm().

Counting probe text for structure experiments lives in eval_out/count_10_27.txt.

================================================================================
11. GIT
================================================================================
Discover remotes: git -C Trident remote -v ; git -C chatterbox.cpp remote -v
Discover tips: git ls-remote origin refs/heads/v3 refs/heads/main (Trident)
and refs/heads/nano refs/heads/turbo refs/heads/v3 refs/heads/main (chatterbox)
If you change C++ on nano: commit on nano branch, pin tts_nano.py
chatterbox_rev to that commit, commit Trident v3 allowlist only, push both
remotes. Same pattern for turbo and v3 on their branches.
Never commit knowledgebase.md bootstrap_state.py eval_tts.py eval_out/ wavs
dumps models reference.wav venvs experiment scripts.
Optional: commit Trident .gitignore only if user wants extra exclusion names
on origin. Ask first. knowledgebase.md should stay in .gitignore exclude list.
Never git config. Never force-push. Never skip hooks unless asked.
Never push main.

================================================================================
12. CHECKLIST
================================================================================
DERIVE
[ ] Recreate bootstrap_state.py if missing. python bootstrap_state.py
[ ] Clone siblings if dirs missing. checkout Trident v3
[ ] reference.wav present, 16-bit PCM mono 24000
[ ] Pins == origin family tips (read tts_*.py, verify ls-remote)
[ ] Trident HEAD == origin/v3. Tracked tree clean (-uno)
[ ] Vulkan Include Lib/vulkan-1.lib glslc exist. ggml HEAD == GGML_REV
BOOTSTRAP (only if exe/GGUF/Vulkan unproven)
[ ] Hello. per family Length>44. v3 with language en
FIX AND PROVE (when assigned)
[ ] C++ commit on correct family branch
[ ] Pin tts_<family>.py chatterbox_rev to that commit
[ ] Trident v3 allowlist commit and push
[ ] Rebuild via launcher (rev stamp matches pin)
[ ] Save pre-fix eval snapshots if not already saved
[ ] Rerun threshold_probe.py structure_experiment.py eval_tts.py
[ ] Diff pre vs post predicted_count duration_s failure_class WER
EVAL ROUTINE
[ ] Kill stale chatterbox-server.exe if pipe busy
[ ] Header defaults unless knob experiment
[ ] Copy dumps to eval_out/dumps/ per trial
[ ] Classify length budget content collapse mix from dump plus ASR
[ ] Do not archive WAVs. Do not describe by ear

When rewriting this file from zero: keep derive-don't-remember, bootstrap
script rule, Vulkan-only synth, no WAV archive, no fallbacks, measurable
text missions, eval suite paths, dump preservation, pin-push-rerun proof
loop for fixes, and git allowlist discipline.
