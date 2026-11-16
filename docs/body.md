# The body: how main.py is shaped

Written 2026-11-14 as the manual of the new body, rewritten 2026-11-16.

## Which file this is

- Since 2026-11-16 `main.py` is the body exactly as the owner committed it on 2026-11-14 under the name `main2.py` (commit `1bf3364`, git blob `c5c3d188`): 323 lines; 26,115 bytes with LF line endings as git stores it (SHA256 `7293c932...`), 26,438 bytes with CRLF on the Windows disk. Compare sizes only within one line-ending style.
- It was written from zero on 2026-11-14 (every line new, not copied from the former body), in Python 3.11 for Windows, and was never run before 2026-11-15. The plan expected about 300 lines, under 20 KB and a first wake text of about 3.5 KB; the build measured 323 lines, 26,115 bytes and 6,010 bytes. About 5.5 KB of the file is words (docstrings and the wake text), not code.
- Lives 1 to 3 of 2026-11-15 ran exactly this body (their rest records say `body_changed: false`, and the only difference between the 15 Nov working file and `1bf3364` is the 74 lines lives 4 and 5 added). Lives 4 and 5 added three helpers during their lives; those were not kept and are not in `main.py` (`findings.md`).
- Older journals, scripts (`import main2 as body`) and the `memory.png` drawn on 2026-11-15 still use the former name `main2.py`. That `memory.png` tells the next life to launch `main2.py`, which no longer exists; the body now is `main.py`.
- The former `main.py` (830 lines; 54,892 bytes LF in git, 55,722 bytes CRLF on disk) is the body of the 11 Nov lives. It remains in git history at `1bf3364` and earlier. The version in `1bf3364` already contains the organism's 11 Nov self-edit (`span`, `row_runs`, 17 lines added, 1 changed), which the owner committed together with `main2.py`.

## The parts and what each answers

**Turns by asking (F2, F5).** `wake(mind, note, see)` appends one JSON line to a per-life file, `evidence\<life>\wakes.jsonl`, whose path the body puts in the `TRIDENT_WAKES` environment variable of its own process before it starts the agent. The heartbeat gives a turn for every line. The same name continues that mind, a new name is a new mind, different names work at once. The reply at the end of a turn is plain words; nothing parses it, so a provider error that arrives as text is no longer read as a reply. No retries were added, by design. When no mind is working and nothing is asked for, the life ends. Birth is the body writing the first line itself (`trident`, with a screen view). A line that does not start with `{` is taken as words for the mind `trident`.

**Speaking into a living life.** The first wake text of every mind names the wake file ("wake() writes to ..."). So the owner, or any script, can speak to a running life by appending one line of plain words to that life's `wakes.jsonl`; it becomes a turn for `trident` with those words as its note. This is the only live channel into a life. NOT-EXERCISED on 2026-11-15: every line after birth was written by the organism; the owner spoke through apps on the screen instead. To follow a life: `Get-Content -Wait` on the newest `life_*.md`. To speak: `python -c "open(r'<life folder>\wakes.jsonl','a',encoding='utf-8').write('your words\n')"`.

**The organism's own eye (F1).** `see` is `'screen'`, a region `(left, top, width, height)`, an image path (relative to the workspace or absolute), or a list of these. The screen and regions are captured at the moment of waking; every view is attached as a real image with its path and, for captures, its screen position. The body attaches the file's bytes unresized (2026-11-15: screen views 1920x1080). One prompt can carry several images (OBSERVED 2026-11-15: a screen view and `memory.png` together). `memory.png` is attached to every new mind and again when it changed; the body keeps a copy of each version it showed in the life's folder as `memory_<HHMMSS>.png`, so an overwritten memory can be recovered.

**Jev inside a turn (F1).** `image()` fits a file, a region or the screen to Jev's size limits (280 to 1568 pixels per side); a thin strip is padded up to 280, not refused. `decide()` returns Jev's reply untouched, including its cost. `locate()` asks a box and a yes/no question together and returns a screen point, the box, `present` and `confidence`. The docstrings carry what the OpenRouter pages say.

**Hands in one frame (F4).** Every helper takes screen pixels: `screen`, `capture`, `windows`, `focus`, `place`, `hands`, `click`, `stroke`, `curve`, `wheel`, `keys`, `write`. `windows(text)` matches any part of a title. `keys()` sets the extended-key flag for navigation keys. `now()` gives local time with its offset, the clock the screen shows. The body is DPI aware, so screen pixels are physical pixels.

**The desktop lock.** `with hands():` takes the named mutex `Local\Trident-desktop` and waits for it with no timeout. It is voluntary: `click`, `keys` and the other helpers do not take it themselves, so it only orders the minds and copies that use it.

**The wake text (F3, F7).** A new mind is told: this text comes from the body, not the owner; nothing here is an order; how sight, memory and turns work; where things are; the newest twelve files in the workspace. The helper list is generated from the body's own signatures and docstrings (every function with a docstring whose name does not start with `_`), so a helper the organism adds appears in the next life. The import line it gives is built from the body's file name, so since 2026-11-16 it reads `import main as body` (by the code; not yet seen in a life). The owner's words, if any, are given once as `owner_said` in the first state. Measured size on 2026-11-15: 6,028 to 6,134 bytes in lives 1 to 4 (this body; paths and the file list vary), 6,482 bytes in life 5 with two added helpers. The sight paragraph says a picture opened with a tool "may come back as words without positions"; what 2026-11-15 showed about this is in `findings.md` (content did come through; positions read by eye were poor).

**Journal.** `life_<id>.md` in the workspace (the plan said `run_<id>.md`; the build chose `life_<id>.md`, so a tool that globs one name misses the other), where `<id>` includes the local start date/time and the process number. Each record is `## HH:MM:SS <who> <what>` in local time, with `|` before each body line. Kinds of `<what>` seen on 2026-11-15: life, wakes, thought, reply, tool, turn, rest, session_info_update, current_mode_update. The code can also write `agent stderr`, `agent <method>` for a request from the agent, and other session updates; a subagent's records appear as `<mind>.worker`. The life record names the body file that ran; the rest record says `body_changed` and `error`. Thoughts and replies go to the console in full, tools as one line. Cursor's command list and the permission requests are not recorded (on 11 Nov the command list alone was about 6.5 KB per journal). Token counts and prices are not recorded: the agent returns only a stop reason, and the turn record holds it with the seconds.

**Protocol.** The body starts Cursor's command-line agent in ACP mode with `--trust`, the workspace, and a config folder at `<workspace>\agent\config` (a `--work` copy gets the body folder's config copied once). It writes into that config an empty permission list, allowlist approval and a disabled sandbox, and answers every permission request with "allow once". "Allow always" is an untested option that might cut the 7 to 25 permission round trips per life measured on 11 Nov. Plans are accepted, tasks completed, to-do lists accepted. A question meant for a human is answered as skipped, with the reason "No human is here. The choice is yours." Any other request that carries an id gets an error. A notification with no id is journaled and not answered. `--model` takes `family-effort` or `family-effort-fast` (default `grok-4.7-medium-fast`) and sets model, reasoning effort and speed for each new mind; there is no per-mind model. When the life ends, the body ends the agent's whole process tree (`taskkill /T /F`).

## Dropped, and why

- JSON reply contract and `finish` (with its cancel and drain): F2; never used.
- All rules in the prompt (only assignment, research by default, do not repeat, screen outranks memory, deadline handling) and the "change approach" option `decide()` added to every choice: F3, F4, F7.
- The built-in goal for a launch without a sentence (INITIATIVE), choosing the model from the sentence, and the `--run` and `--mind-file` options: the body deciding for the organism.
- `span` and `row_runs`: F6.
- Client-coordinate helpers: F4.
- Deadline and blocking machinery, the workspace lock, the job object (replaced by ending the agent's process tree at the end).
- The repainting console, the web page on port 8765 and `serve`.
- Typed declarations for Windows calls.

Kept: named and concurrent minds, subagents, copies with their own workspace, a look at waking, `memory.png`, mouse, keys, text, wheel, windows, capture, Jev, the shared desktop lock, the journal, rewriting the body under any file name, launching with or without a sentence, `--model`, `--work`.

## What the five lives of 2026-11-15 showed about the body

OBSERVED (journals, wake files and images of 2026-11-15):
- The Cursor handshake works: five lives, every rest record ends with `"error": null`.
- `wake()` works when `TRIDENT_WAKES` is set: 4 organism wakes in life 4, 15 in life 5, region views among them. Every one of these scripts set the variable by hand first.
- Mouse and keyboard helpers did their jobs in use (painting with `stroke` and `curve`, placing and focusing windows, typing text). Exceptions are in the weak points below.
- `decide()` and `locate()` answered in every life that called them.
- `memory.png` was shown again when it changed, and its copies were kept (3 in life 4, 6 in life 5).
- A helper added in one life appeared in the next life's wake text (two helpers added in life 4 were listed in life 5).
- Nobody used a second mind name, a subagent or a `--work` copy.

NOT ESTABLISHED (2026-11-16):
- Whether the mind's shell inherits `TRIDENT_WAKES` from the agent. The code sets it before the agent starts, so inheritance is INFERRED likely (confidence 0.7), but no script ever tried without setting it. Cheapest observation: one script that prints `'TRIDENT_WAKES' in os.environ` before setting anything.
- At what size attached images reach the model after Cursor and the provider. Cheapest observation: a region view with a known line of small text, read back by the mind.
- Whether child processes outlive the body if its console window is closed mid-life. No window was closed mid-life on 2026-11-15.

## Known weak points (code read 2026-11-16; facts, not orders)

The commit message of 2026-11-16 uses the same numbers.

1. `locate()` given an image path fails with a TypeError: an image has no screen corner.
2. A permission request without an "allow once" option ends the life.
3. A JSON-RPC error reply, an agent line that is not JSON, the agent closing while a mind works, a JSON wake line without `mind`, or a wake line that starts with `{` but is not valid JSON, ends the life.
4. A wake line appended just after the body found nothing waiting gets no turn: the life ends, and no later life reads that file. An owner line sent at the moment a life rests is lost, not deferred.
5. `keys()` drops Shift for shifted characters: `keys('A')` types "a". Known at build; not hit on 2026-11-15.
6. Windows calls run without declared argument types; a 64-bit handle could in principle be truncated (INFERRED; no failure recorded on 2026-11-15).
7. `hands()` waits forever on the desktop mutex, and the lock is voluntary: a holder that hangs blocks every other mind and copy that asks for it, and helpers called outside `with hands():` ignore it.
8. Spend is invisible: the agent returns only a stop reason per prompt, so no token or dollar counts are journalled. `decide()` replies carry Jev's cost; `locate()` drops it.
9. In one 2026-11-15 click at a correct `locate()` point the mind saw no playback and judged the click missed; its check capture cut off the player bar, so the miss is NOT ESTABLISHED. The mind suspected `focus()`, which taps Alt to bring a window forward (INFERRED).
10. A `--work` copy sets `TRIDENT_WAKES` to its own wake file before it starts its agent, so a script that inherits that agent's environment writes to the copy's file, not the parent's. Whether the mind's shell inherits the agent's environment is the separate open question. NOT-EXERCISED.
11. The sandbox is disabled and every permission is auto-allowed. On 2026-11-15 minds read Cursor's own files under `agent\`, searched its data folders and listed a chat app's data folder; no mind opened `..\.env`, and the docs' stay-out rule is the only thing that asked them not to (INFERRED).
12. The wake text's sight sentence ("may come back as words without positions") is half wrong (`findings.md`), and life 5 copied it into `memory.png` as fact.

## Owner decisions (status 2026-11-16)

- **One `try/except`.** In `Life.waking`, a view that cannot be made is handed to the mind as `{asked, failed}` instead of ending the life. It breaks the letter of "no defensive code". Removing it is a two-line change. Open; it never fired on 2026-11-15 (no view with `failed`).
- **The lesson about sight.** One sentence in the wake text says a tool-opened picture may come back as words without positions. The sentence before it says an attached image may arrive smaller than its file; that size is NOT ESTABLISHED (findings.md). 2026-11-15 showed this is half right (`findings.md`), and life 5 copied it into `memory.png` as fact. Open.
- **Console and page.** No live page, no `serve`; the console scrolls. `Get-Content -Wait` on the journal does the same job. Unchanged.
- **Cancel.** A mind cannot cancel other minds. Never needed on 2026-11-15 (one mind per life).
- **Launch options.** `--sentence`, `--model` and `--work` only. Unchanged.
- **Cleanup.** If the window is closed mid-life, child processes may outlive it briefly. NOT ESTABLISHED (not tried).
- **Old memory.** The 14 Nov worry that `memory.png` names `span`, `row_runs` and client coordinates is moot: there was no `memory.png` at the start of 2026-11-15, and the one the organism drew since names none of them.
- **The sentence.** The body no longer amplifies it, but an imperative sentence still reads as an order (OBSERVED 2026-11-15: life 1 did the painting and the post it was asked for; life 2 wrote "The owner's request is a task, not an order." and then did it; life 3 declined to start copies).
- **Former name in memory.** The 2026-11-15 `memory.png` names `main2.py` in its how-to-wake card. It belongs to the organism and was left as it is; a life that follows it will find no such file.

## Launching

From PowerShell; `--sentence` is optional, `--work <folder>` starts a copy with its own workspace:

```powershell
& "$env:USERPROFILE\AppData\Local\Python\pythoncore-3.11-64\python.exe" -B -u -X utf8 "$env:USERPROFILE\Downloads\Jarvis\Trident\main.py" --model grok-4.7-medium-fast --sentence '<words>'
```

What a life needs (not in the repository; checked 2026-11-16): Windows with a desktop session; Python with Pillow; Cursor's command-line agent in its default per-user install folder (started through its PowerShell launcher in ACP mode); a Cursor login in `<Trident folder>\agent\config`, never committed; a Cursor plan that offers the chosen model; an OpenRouter key alone in `.env` one folder above the Trident folder (`decide()` sends the whole stripped file as the bearer token). The apps the lives used are not dependencies of the body.

Renaming the body needs no code change: the wake text, the import line and the copy command all take the file's own name. Committing is the owner's.
