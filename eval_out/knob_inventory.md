Knob inventory from the checked-out family files and Trident v3 launchers.
If a file on disk disagrees, the file wins. Discover SHAs with git.

CHATTERBOX_SAMPLER_LOG is spawn setdefault 1. Not a CLI flag.

Empty models/<family>.knobs values mean header defaults (env popped at spawn).
Unsupported flags print usage and exit.

nano (header include/tts-cpp/chatterbox/nano.h)
SEED 42 header:3 getenv CHATTERBOX_SEED effective_seed engine.cpp:43 read yes launcher --seed stamp models/nano.knobs
N_PREDICT 1000 header:4 CHATTERBOX_N_PREDICT effective_n_predict engine.cpp:40 read yes --n-predict
TOP_K 1000 header:5 CHATTERBOX_TOP_K t3_nano.cpp:304 read yes --top-k
TOP_P 0.95 header:6 CHATTERBOX_TOP_P t3_nano.cpp:305 read yes --top-p
TEMPERATURE 0.8 header:7 CHATTERBOX_TEMPERATURE t3_nano.cpp:303 read yes --temperature
REPEAT_PENALTY 1.2 header:8 CHATTERBOX_REPEAT_PENALTY chatterbox_t3_internal.h:35 read yes --repeat-penalty
REPEAT_LAST_N 1000 header:9 CHATTERBOX_REPEAT_LAST_N internal.h:42 read yes --repeat-last-n
CFM_STEPS 2 header:10 CHATTERBOX_CFM_STEPS chatterbox_tts.cpp:1056 read yes --cfm-steps
SILENCE_TOKEN 4299 header:11 CHATTERBOX_SILENCE_TOKEN t3_nano.cpp:306 engine.cpp:42 tts.cpp:1009 read yes --silence-token
SILENCE_COUNT 3 header:12 CHATTERBOX_SILENCE_COUNT engine.cpp:41 read yes --silence-count
MIN_P CFG_WEIGHT CFM_CFG EXAGGERATION: not in nano.h, not read, not wired.

turbo (header include/tts-cpp/chatterbox/turbo.h)
Same names, defaults, env keys, CLI flags as nano.
Reads: turbo.h:3-12, engine.cpp:40-43, t3_nano.cpp:303-306, tts.cpp:1006-1056, internal.h getenv.
Stamp models/turbo.knobs
MIN_P CFG_WEIGHT CFM_CFG EXAGGERATION: not in turbo.h, not wired.

v3 (header include/tts-cpp/chatterbox/v3.h)
SEED 42 header:3 CHATTERBOX_SEED engine.cpp:49 --seed
N_PREDICT 1000 header:4 CHATTERBOX_N_PREDICT engine.cpp:78 --n-predict
TOP_K 0 header:5 CHATTERBOX_TOP_K t3_v3.cpp:399 --top-k
TOP_P 1.0 header:6 CHATTERBOX_TOP_P t3_v3.cpp:400 --top-p
MIN_P 0.05 header:7 CHATTERBOX_MIN_P t3_v3.cpp:398 --min-p
TEMPERATURE 0.8 header:8 CHATTERBOX_TEMPERATURE t3_v3.cpp:397 --temperature
REPEAT_PENALTY 1.2 header:9 CHATTERBOX_REPEAT_PENALTY internal.h:36 --repeat-penalty
REPEAT_LAST_N 1000 header:10 CHATTERBOX_REPEAT_LAST_N internal.h:46 --repeat-last-n
CFG_WEIGHT 0.5 header:11 CHATTERBOX_CFG_WEIGHT t3_v3.cpp:336 --cfg-weight
CFM_STEPS 10 header:12 CHATTERBOX_CFM_STEPS tts.cpp:1060 --cfm-steps
CFM_CFG 0.7 header:13 CHATTERBOX_CFM_CFG tts.cpp:1061 --cfm-cfg
SILENCE_TOKEN 4299 header:14 CHATTERBOX_SILENCE_TOKEN t3_v3.cpp:401 tts.cpp:1013 --silence-token
SILENCE_COUNT: not in v3.h, not wired.
EXAGGERATION: removed from v3.h (generate uses builtin_emotion_adv). Not wired.

server.cpp argv is not a sampler knob: exe t3 s3 pipe [language for v3].
