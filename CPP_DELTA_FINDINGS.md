# CPP delta findings (parked)

Review only. No `.cpp` edits in this cook. Window: last ~2 hours on `runner-h` relative to tip `ebfcbeb`.

## Commits touching C++

One: `647a16f` (2026-09-26 22:31 +0200) — Overlap mouth playback with next-chunk synthesize.

File: `src/chatterbox.cpp` (+4 / -1).

### What changed

- Reads `chatterbox.play` via `trident::cfg_on` into `bool play`.
- `PlaySoundW` runs only when `play` is true.
- When `play` is false, the wav is still written and named in the output text; PlaySound is skipped.

### Why

Lets `mouth.py` cold-run `chatterbox.exe` with `chatterbox.play off` so synthesize and Speakers playback can overlap in Python. Direct `chatterbox.exe` with `play on` still plays itself.

### Findings

1. Contract matches the Mouth one-shot: synthesize-only versus play-on-default-speakers is one settings key.
2. When play is on, PlaySoundW failure still returns 1.
3. No other `.cpp` files moved in the window. Nearby commits were docs and Python (`mouth.py`, `hear.py`, living docs).
4. No fix in this cook. RULES.md item 10: do not change C++ / `.cpp` without Wojciech's explicit go. Prior C++ delta stays parked for the next war room.

See GOAL.md, AGENTS.md, RULES.md, BOTS.md.