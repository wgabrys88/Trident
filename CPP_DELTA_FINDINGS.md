# CPP delta findings

Status: CLOSED. Shipped.

`chatterbox.play` is the playback gate. It shipped in `647a16f` on `src/chatterbox.cpp`.

## Contract

`chatterbox.exe` reads `chatterbox.play` with `trident::cfg_on`. The value is `on` or `off`.

- `on`: the wav is written and named, then `PlaySoundW` plays it on the default speakers. A failed `PlaySoundW` returns 1.
- `off`: the wav is still written and named. PlaySound is skipped.

`mouth.py` owns Speakers. It writes `chatterbox.play off` and plays each wav with `PlaySoundW` (`SND_FILENAME | SND_NODEFAULT`) while the next chunk synthesizes. Direct `chatterbox.exe` with `chatterbox.play on` plays the wav itself.

## What shipped

`647a16f` gates `PlaySoundW` on `chatterbox.play`. When play is false, synthesize still writes the wav and the output text names it. `mouth.py` uses that off path so playback and the next synthesize overlap.

Further `.cpp` edits still need Wojciech's explicit go.

See GOAL.md, AGENTS.md, RULES.md, BOTS.md.
