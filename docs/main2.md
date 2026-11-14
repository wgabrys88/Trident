# Why main2.py is shaped as it is

Written 14 Nov 2026. `main2.py` is a new body written from zero beside `main.py`, answering the findings in `findings-2026-11-14.md` (F1 to F7). It has never been run. Its first launch is its first live test.

Size: 323 lines, 26.1 KB, and a 6.0 KB text for a new mind. `main.py`: 830 lines, 55.7 KB, 9.7 KB. The plan expected about 300 lines, under 20 KB and 3.5 KB; it came out larger.

## The parts and what each answers

**Turns by asking (F2, F5).** `wake(mind, note, see)` appends one line to a per-life file, `evidence\<life>\wakes.jsonl`. The heartbeat gives a turn for every line. The same name continues that mind, a new name is a new mind, different names work at once. The reply at the end of a turn is plain words; nothing parses it. When no mind is working and nothing is asked for, the life ends. Birth is the body writing the first line itself.

**The organism's own eye (F1).** `see` is `'screen'`, a region `(left, top, width, height)`, an image path, or a list of these. Each is captured at the moment of waking and attached as a real image with its path and screen position. `memory.png` is attached to every new mind and again when it changed; the body keeps a copy of each version it showed in the life's folder.

**Jev inside a turn (F1).** `image()` fits a file, a region or the screen to Jev's size limits. `decide()` returns Jev's reply untouched. `locate()` asks a box and a yes/no question together and returns a screen point, the box, `present` and `confidence`. The docstrings carry what the OpenRouter pages say.

**Hands in one frame (F4).** Every helper takes screen pixels: `screen`, `capture`, `windows`, `focus`, `place`, `hands`, `click`, `stroke`, `curve`, `wheel`, `keys`, `write`. `windows(text)` matches any part of a title. Time is local with its offset.

**The wake text (F3, F7).** A new mind is told: this text comes from the body, not the owner; nothing here is an order; how sight, memory and turns work; where things are. The helper list is generated from the body's own signatures and docstrings, so a helper the organism adds appears in the next life. The owner's words, if any, are given once as `owner_said`.

**Journal.** `life_<id>.md`, records `## HH:MM:SS <who> <what>` with `|` before each body line. Thoughts and replies go to the console in full, tools as one line. Cursor's command list is not recorded.

**Protocol.** Same Cursor handshake, session options, auto-allowed permissions and config folder as `main.py`. A question meant for a human is answered "skipped".

## Dropped, and why

- JSON reply contract and `finish`: F2; never used.
- All rules in the prompt (only assignment, research by default, do not repeat, screen outranks memory, deadline handling): F3, F4, F7.
- The built-in goal for a launch without a sentence, and choosing the model from the sentence: the body deciding for the organism.
- `span` and `row_runs`: F6.
- Client-coordinate helpers: F4.
- Deadline and blocking machinery, the workspace lock, the job object (replaced by killing the agent's process tree at the end).
- The repainting console, the web page on port 8765 and `serve`.
- Typed declarations for Windows calls.

Kept: named and concurrent minds, subagents, copies with their own workspace, a look at waking, `memory.png`, mouse, keys, text, wheel, windows, capture, Jev, the shared desktop lock, the journal, rewriting the body under any file name, launching with or without a sentence, `--model`, `--work`.

## Checked without running it

- It parses and compiles in memory; no undefined names; every attribute read is set.
- The wake text formats with the real helper list.
- No task words; dropped names absent.
- Protocol strings match `main.py` in shape.
- In separate scratch scripts: the input structure is 40 bytes; the Jev fit stays within limits for six sizes; the read-only Windows calls work untyped on this machine.

## Unproven until the first launch

- The Cursor exchange through the rewritten client.
- Whether the mind's shell inherits `TRIDENT_WAKES`, which `wake()` needs. The wake text names the file, so a line can be appended by hand.
- Every mouse and keyboard helper. No input was sent.
- `decide()` and `locate()` against Jev.
- At what size attached images reach the model.
- Whether the organism uses `wake()` at all.

## Differences from the approved plan

- Jev pictures are fitted to 280 to 1568 pixels per side, not 256: 256 by 256 is below Jev's minimum pixel count.
- The journal is `life_<id>.md`, not `run_<id>.md`.
- A wake-file line that is not JSON is taken as words for the mind named `trident`.
- A JSON-RPC error from the agent still ends the life, as in `main.py`.

## Owner decisions

- **One `try/except`.** In `Life.waking`, a view that cannot be made is handed to the mind as `{asked, failed}` instead of ending the life. It breaks the letter of "no defensive code". Removing it is a two-line change.
- **The lesson about sight.** Two sentences in the wake text say a tool-opened picture may come back as words. They rest on the organism's own reports. Remove them to let it learn this itself.
- **Console and page.** No live page, no `serve`; the console scrolls. `Get-Content -Wait` on the journal does the same job.
- **Cancel.** A mind can no longer cancel other minds. Never used.
- **Launch options.** `--mind-file` and `--run` are gone.
- **Cleanup.** If the window is closed mid-life, child processes may outlive it briefly.
- **Old memory.** `memory.png` and `story.md` still name `span`, `row_runs` and client coordinates. Recommended: leave them for the organism to correct.
- **The sentence.** The body no longer amplifies it, but an imperative sentence still reads as an order.

## Switching

Launch `main2.py` in place of `main.py` with the same options; `--sentence` is optional:

```powershell
& "$env:USERPROFILE\AppData\Local\Python\pythoncore-3.11-64\python.exe" -B -u -X utf8 "<Trident folder>\main2.py" --model grok-4.7-medium-fast --sentence '<words>'
```

It is ignored by git until `!/main2.py` is added to `.gitignore`. Renaming it to `main.py` needs no code change. Committing is the owner's.
