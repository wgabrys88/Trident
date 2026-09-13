# experimental checklist

One tree, three families. Not a git-merge of nano/turbo/v3. Not onto main.

## SHAs

| repo | branch | SHA | note |
| --- | --- | --- | --- |
| chatterbox.cpp | experimental | `2bb942bac8bd8653b6751d70be3766ba103c49f3` | parent `d83dbd3`; CMake TTS_FAMILY quotes |
| Trident | experimental | (this commit) | all three launchers pin chatterbox `2bb942b` |

## Done

- [x] Code review (session file, not in repo)
- [x] chatterbox overlay from `origin/main`
- [x] Trident overlay from `origin/main`
- [x] CMake `TTS_FAMILY` tests quoted
- [x] `tts_nano.py` / `tts_turbo.py` / `tts_v3.py` pin `2bb942b`, `branch=experimental`
- [x] `build/nano` CMakeCache `$fam` rewritten to `nano`

## Now

- [ ] Push `experimental` in **both** repos: `git push -u origin experimental`
- [ ] CMake configure actually succeeds for nano, turbo, v3
- [ ] Speak nano
- [ ] Speak turbo
- [ ] Speak v3 (`python tts_v3.py "<text>" <language>`)
- [ ] First speak of a family rebuilds `build/<family>` — that is expected

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
- [ ] `models/*.rev` rewrite on first successful experimental launch

Leave both repos on `experimental`. To return to production: `git checkout v3` in both.
