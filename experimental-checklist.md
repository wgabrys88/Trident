# experimental checklist

One tree, three families. Not a git-merge of nano/turbo/v3. Not onto main.

## SHAs

| repo | branch | SHA | note |
| --- | --- | --- | --- |
| chatterbox.cpp | experimental | `2bb942bac8bd8653b6751d70be3766ba103c49f3` | parent `d83dbd3`; CMake TTS_FAMILY quotes |
| Trident | experimental | `origin/experimental` | all three launchers pin chatterbox `2bb942b` |

## Done

- [x] Code review (session file, not in repo)
- [x] chatterbox overlay from `origin/main`
- [x] Trident overlay from `origin/main`
- [x] CMake `TTS_FAMILY` tests quoted
- [x] `tts_nano.py` / `tts_turbo.py` / `tts_v3.py` pin `2bb942b`, `branch=experimental`
- [x] `build/nano` CMakeCache `$fam` rewritten to `nano`

## Now

- [x] Push `experimental` in **both** repos (`origin/experimental` exists; no PR to main)
- [x] CMake configure nano (`TTS_FAMILY=nano`, compiled `t3_nano` + `chatterbox_engine_gpt2`)
- [x] CMake configure turbo (`TTS_FAMILY=turbo`, compiling GPT-2 sources into `build/turbo`)
- [x] CMake configure v3 (`TTS_FAMILY=v3`, compiled `t3_v3` + `mtl_bpe` + `chatterbox_engine_v3`)
- [x] Speak nano `20260913-103828-nano.wav` 217004 bytes, wall 2.966s, dur 3.520s, rtf 0.843
- [x] Speak turbo `20260913-103932-turbo.wav` 178604 bytes, wall 4.005s, dur 2.720s, rtf 1.472
- [x] Speak v3 `20260913-104009-v3.wav` 220844 bytes, wall 12.275s, dur 3.600s, rtf 3.410
- [x] First speak of each family rebuilt `build/<family>` as expected

## Do not

- merge `experimental` into `main`
- merge family branches into each other
- force-push `main`
- copy nano knobs onto v3 (no SILENCE_COUNT, RAS, GPT-2 tags)
- copy v3 knobs onto nano/turbo (no MIN_P, CFG, language argv)
- teach C++ the `|||` delimiter
- pop the twelve chatterbox stashes
- commit `knowledgebase.md`, `reference.wav`, `models/`

## Later (not this landing)

- [ ] Nano “thirty” missing on the 21–30 cold-start chunk — reproduce before debug
- [ ] `origin/v3` sibling pins stay stale on purpose
- [x] `models/*.rev` rewritten to `2bb942b` after experimental launch

Leave both repos on `experimental`. To return to production: `git checkout v3` in both.
