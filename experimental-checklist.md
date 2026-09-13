# experimental checklist

One tree, three families. Not a git-merge of nano/turbo/v3. Not onto main.
Do not tag, delete, or fast-forward `main` until a fresh-clone session says so.

## SHAs (origin/experimental)

| repo | SHA | note |
| --- | --- | --- |
| chatterbox.cpp | `2bb942bac8bd8653b6751d70be3766ba103c49f3` | parent `d83dbd3`; CMake TTS_FAMILY quotes |
| Trident | this branch | all three launchers pin chatterbox `2bb942b`, `branch=experimental` |

## Landing (done)

- [x] Code review (session file, not in repo)
- [x] Overlay from `origin/main` in both repos (not a git merge of family tips)
- [x] CMake `TTS_FAMILY` tests quoted
- [x] Poisoned `build/nano` cache `$fam` rewritten to `nano`
- [x] Push `origin/experimental` in both repos (no PR to main)
- [x] CMake nano: GPT-2 `t3_nano` + `chatterbox_engine_gpt2`
- [x] CMake turbo: GPT-2 sources into `build/turbo`, `TTS_FAMILY=turbo`
- [x] CMake v3: Llama `t3_v3` + `mtl_bpe` + `chatterbox_engine_v3`
- [x] Speak nano `20260913-103828-nano.wav` 217004 bytes, wall 2.966s, dur 3.520s, rtf 0.843 (operator heard)
- [x] Speak turbo `20260913-103932-turbo.wav` 178604 bytes, wall 4.005s, dur 2.720s, rtf 1.472 (operator heard)
- [x] Speak v3 `20260913-104009-v3.wav` 220844 bytes, wall 12.275s, dur 3.600s, rtf 3.410 (operator heard)
- [x] `models/*.rev` rewritten to `2bb942b`

## Constraints (still valid, not work)

- [x] `origin/v3` sibling pins stay stale on purpose: nano `77d3c85`, turbo `6af3e8b`. Do not “fix” them on family branches.
- [x] chatterbox has twelve local stashes (`stash@{0}` .. `stash@{11}`). Do not pop.
- [x] `knowledgebase.md` gitignored. Do not commit it, `reference.wav`, `models/`, venvs, dated WAVs.
- [x] Do not merge `experimental` into `main` in this repo state.
- [x] Do not copy nano knobs onto v3 (no SILENCE_COUNT, RAS, GPT-2 tags).
- [x] Do not copy v3 knobs onto nano/turbo (no MIN_P, CFG, language argv).
- [x] C++ never sees `|||`.

## Known quality gap (reproduced, not fixed)

- [x] Reproduced on experimental nano, cold start, explicit list
  `Twenty-one, twenty-two, ... twenty-nine, thirty.`
  WAV `20260913-104816-nano.wav` 508844 bytes, wall 5.446s, dur 9.600s, rtf 0.567
  New server pid 9976 after 8s gap.
  Parakeet: `Twenty-one, twenty-two, twenty-three, twenty-four, twenty-five, twenty-six, twenty-seven, twenty-eight, twenty-nine`
  **thirty missing.** Do not debug until a second ear/ASR on a fresh clone agrees.
- [x] Shorthand prompt `Twenty-one through thirty.` is the wrong test: it speaks the range
  (`20260913-104618-nano.wav`, Parakeet `21 through 30.`, 2.16s). Use the explicit list.

## Not done (needs a fresh clone)

- [ ] Clone both `experimental` branches into a new empty Windows folder and speak all three families there
- [ ] Repeat the nano 21–30 explicit-list cold start on that clone (ear + Parakeet)
- [ ] Only then: archive-tag family branches, delete them, fast-forward `main`, delete `experimental`

Leave both repos on `experimental`. Production return: `git checkout v3` in both.
