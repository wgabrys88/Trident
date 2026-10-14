# Trident knowledge base: golden run of 2026-10-14

The golden-run facts below are backed by the run folder `run_20261014_103307_233581Z` on PE-DMLW, by this repository, or by the public X API. The architecture in that section describes the baseline. Times are Warsaw local time (UTC+2).

## What Trident is
Trident is a generic Windows desktop organism that pursues one goal given as a single sentence.
- **Luna** is the mind (`cursor-agent -p`). She has exactly three tools: `observe`, `decide` and `python`. All screen actions go through `native(...)` inside `python`.
- **Retina** is local vision (an LFM vision model on llama-server). It answers questions about a screenshot, optionally on a zoomed region.
- **Jev** is the decision model, reached through `decide`. He returns a probability for each option the mind offers.
- After an action, the system waits 5 s and returns only the fresh screenshot. It reports no success or failure. Observe returns only the answer and its geometry.
- An outer loop re-invokes Luna after every result. Story carry-over persists between launches; `--fresh` starts with no previous story.

## The run
- **Code.** Branch `runner-h`, commit `33b3b37`, 1339 Python lines. The source was unchanged during the run; the only untracked files afterwards were Luna's outputs, `house_comic.svg` and `house_comic.png`.
- **Start.** `--fresh` at 12:33 with the goal *"Open Microsoft Paint and draw a detailed house, step by step like a comic, then post the finished drawing on X as a comic."*
- **Volume.** 32 mind wakes, 103 `observe`, 91 `python` and 1 `decide`. Wake 1 hit the 360 s time limit (state `incomplete`), and wake 2 continued the same plan.

**Her journey, in her own actions:**
1. **Plan.** She announced she would draw in Paint, verify the canvas, and publish on X. She designed four panels: blueprint, walls, roof and details, finished home.
2. **First drawing.** One blind program with guessed full-HD coordinates, issued after Retina located the canvas as "the entire screen". The result kept horizontal and vertical lines; the diagonals were missing.
3. **Refusing a bad result.** After Retina called the canvas "not a recognizable four-panel comic", she refused to publish what she called "an accidental toolbar sketch".
4. **The SVG pipeline, her own invention (no such code exists in Trident).** In order:
   1. She wrote the comic as an SVG file from Python.
   2. She tried `cairosvg`.
   3. She opened the file with `mspaint`, and the Windows Open With dialog appeared.
   4. She opened it in Chrome instead.
   5. She snipped the screen with Win+Shift+S.
   6. She pasted into a new Paint window and saved `house_comic.png`.
   7. She checked panels 3 and 4 with Retina before going to X.
5. **On X: varied attachment attempts, no two consecutive programs identical.** She used the media button, the file picker, Ctrl+L with the file path, Tab navigation, a reload, the profile page, a new composer with `n`, and the leave-site dialog.
6. **The post.** At **12:59:31** exactly one post was published on @wgab88: [post 2110324618863215021](https://x.com/wgab88/status/2110324618863215021), *"A detailed house comic, drawn step by step in Microsoft Paint. #comic #digitalart"*. The X API returns **no media attachment** on it, and it is the only post in the run window. The image never reached X. Her later Post click (13:00:23) and Ctrl+Enter (13:02:27) created no further posts.
7. **Retina was wrong about X.** At various times it reported "newly posted item showing the house comic", "confirmed published" and an image post. At other times it read the profile bio and a skydiving photo as the newest post, and once invented an author named "house_comic.png". Luna's "Done" claims at 12:52, 13:03 and 13:06 all rest on such answers. At 12:52 no post existed yet. The image claims were never true.
8. **Re-verification.** Each wake re-checked the live screen instead of trusting her previous claim: "the earlier completion record is not enough to verify the live result."
9. **The only Jev call (13:07:56).**
   - What led to it: Retina said "not visibly published" (13:07:42), then "a house image comic-style... details are not clear" (13:07:51).
   - What Luna asked: whether the goal was done, with options confirmed, uncertain or not_done.
   - What Jev answered: **uncertain 0.61**, confirmed 0.39, in 0.5 s, for $0.00002.
   - Since the post had no image, Jev was closer to the truth than Retina.
   - What Luna did next: she inspected further, reopened the composer and re-attached the image.
10. **Stop.** The owner's Telegram "Stop working" arrived. In the next wake she set her goal to "Stop working", recorded that the image sat "in an unsent X composer; it was not published by this attempt", and took no further screen action.
11. **Shutdown.** The outer loop woke her 22 more times, each answered "Stopped as requested". `run.py live()` calls the mind again straight after every completed answer, and Luna has no tool that ends the organism. The stop marker shut everything down in 4 s.

## Proven lessons
- The commits that removed code (gates, fences, schema decoding, tests, uncertainty logic, device status replies, observe metadata) were followed by the best behavior seen so far: planning, invention, varied search, self-doubt, a well-timed Jev call and instant obedience. **Removing code made the system behave better.**
- Retina's yes/no verdicts on whether the goal is done are unreliable. Jev, asked once, did not accept the false completion.
- `decide` is optional in `instinct.txt`. Luna used it only when two vision answers contradicted each other.
- Without `await`, `native` does nothing. She once called it without await and had to retry.
- There is no rest after a done or stopped goal, and there is no self-shutdown.
- A fixed 5 s settle follows every action.

## Not determined by the evidence
- Which exact keystroke submitted the post at 12:59:31.
- Why the first drawing lost its diagonals.
- Why `cairosvg` did not produce the PNG.

## Prompt for the next agent
You maintain Trident (github.com/wgabrys88/Trident, branch `runner-h`). Read this file and the extended message of the commit that added it, then work this way:
1. Reduce the code toward bare metal one piece at a time.
2. After each cut, run the same goal live with `python run.py --fresh "<goal>"` and compare against this golden run (tag `MILESTONE-BASELINE-HOUSE-X`).
3. Keep only the cuts that hold or improve behavior.
4. Analyze each run like a detective. Every outcome is a symptom: trace it back through Luna's stream, the Retina and Jev exchanges, and the code. Record only proven facts and mark the rest as unknown.

**Fix generically, never task-specific:**
- Rest after a done or stopped goal.
- Let Luna end the organism.
- Make Jev the judge of done and of choices, and Retina only the describer.
- Make `await` explicit for `native`, `observe` and `decide`.
- Make the settle time adaptive.

**Cut candidates:**
- the bus state renames and the operation ledger
- the raw dump file for every exchange
- surplus context fields

**Keep:**
- process control and clean shutdown
- story carry-over and `--fresh`
- zoom
- releasing stuck input
- surviving a failed mind invocation

**Rules:**
- Small code, few constraints.
- No defensive code, no artificial branching, no tests, no task-specific logic.
- Never read, print or commit `.env` or credentials.
- Chess only against chess.com bots.
- Never force-push.
- Answer the owner briefly and plainly.

## Subsequent code changes: 2026-10-14 (`09e2c73`)

These are implementation changes, reviewed from source only. The owner explicitly requested no installer, application, tests or harness runs; the earlier live-comparison workflow was not performed. No improved desktop outcome is claimed.

- `await rest()` now schedules an indefinite pause after the current invocation; `await rest(seconds=N)` schedules a future wake. Owner text, transcribed voice and incoming calls wake the organism; device diagnostics do not. `request_stop()` uses the existing shutdown marker.
- Instinct explicitly requires awaiting async interfaces and assigns completion and meaning judgments to Jev, with Retina describing pixels. These are instructions to Luna, not code-level completion gates.
- Native input releases before settling. Sampling starts after at least one second, uses 0.25-second intervals and three unchanged comparisons, and has a five-second total waiting cap. Exact RGB differences are compared; an animated desktop can reach the cap. Settling does not establish success.
- Python has one live namespace per mind invocation. `remember` persists story and memory; helpers and variables no longer replay across invocations. Cooperative Python timeouts cannot interrupt synchronous blocking code; the 360-second mind deadline still owns and terminates the Cursor/MCP process tree.
- File-based Bus requests are consumed before dispatch and never replayed automatically. Temporary requests and reply slots replace the operation ledger and prepared/active/cancelled filenames. Exchanges and inputs are recorded in one UTF-8 `exchange.log`; `recall(id)` reads matching records.
- Monitor-facing paths remain `run_<timestamp>/`, `*_state.json`, `*_cursor_to_luna_<id>_response.jsonl`, and `*_screen_<id>.png`. Cursor retains stream-json and partial output; lines also enter `exchange.log` as they arrive. Timeout and shutdown leave the emitted stream intact.
- State retains paths, active/process_state, memory, owner, screen and inputs, and adds wake_id/wake_at. Inputs contain their messages directly instead of paths to input files. The operations, executions, artifacts and observations inventories are removed. There are no orientation.json or tools.py files; the wake's desktop description is supplied in the prompt. Cursor prompt files exist only during the invocation; standalone exchange dumps and subprocess stderr files are replaced by the log.
- Windows Job/Mutex control, input release, story carry-over, --fresh, zoom, Telegram voice and calls remain. Exited devices are respawned. The unproven drawing, SVG and submission causes remain unresolved, and mind_seconds remains 360.

## First live run of `09e2c73`: owner-reported findings

The owner supplied these proven findings from `run_20261014_143550_363708Z`, citing `exchange.log`, two Luna streams, screenshots and `trident_errors.txt`. This change did not inspect that run data. The goal was a detailed house comic in Paint, posted on X; times below are Warsaw local time.

- The run reached drawing, saving, attachment and caption entry in about ten minutes. Session one produced scattered stamps and lines. Session two produced four panels with a foundation, walls/windows/door, a house/roof/tree/path, and a fence/chimney/smoke. Adaptive settling and in-process Python worked: all 18 Python and 14 observe calls returned.
- At 16:46, maintenance's unconditional state rewrite hit `PermissionError [WinError 5]` during atomic replacement. The exception escaped the TaskGroup, cancelled Luna mid-step and killed the organism, leaving a pending file. The exact process holding the target was not identified.
- At 16:42, the 360-second mind limit interrupted session one mid-post without last_words. Two long drawing programs also outlasted native's 30-second Bus deadline while strokes continued. Python retained an additional 240-second limit.
- Narrative stayed empty: Luna never called remember to save it. Session two restarted the drawing from zero. There were zero decide calls. Retina supplied malformed or inconsistent coordinates and false scene/attachment claims.
- A file named `house_comic.png` was HEIF (`ftyp mif1`); an earlier save was `house_comic.gif`. Both were in the repository folder. Upload used session one's GIF instead of the finished comic.
- Native's code/prediction arguments were reversed once, and a Python tool call omitted its arguments. Crops were full-screen or nearly full-screen rather than useful zoom.

## Follow-up implementation: source review only

- Maintenance edits state only for a nonempty restart request. Atomic replacement retries PermissionError up to 20 attempts, 50 ms apart, and removes its pending file in finally. Exhausted retries and other failures still propagate.
- Remove mind/Python/Bus/synthesis duration limits, Bus expiry fields, startup/readiness deadlines and the 30-second failure delay. Preserve actual HTTP/Telegram service limits, installer download limits, shutdown bounds and settle timing.
- The wake state ends a mind invocation on owner input or requested rest. Supersession drains Cursor/MCP, stops Manipulator and its owned processes, releases input, removes that role's transport files and respawns it. Rest and supersession are expected interruptions. Existing stream files and the exchange.log JSON envelope stay intact.
- memory.trail retains the last 20 native attempts: goal, prediction, result image and error. It is saved before dispatch and after receipt, and carries into previous_life. A null image means no result was received; the trail is not a milestone verdict or action replay.
- Native requires named code and prediction arguments. The Python description shows the source-string request and native call; device no longer accepts seconds. Temporary replies are now `<id>_to_<role>_reply.json`, so the same short cleanup loop removes queued requests, reply slots and their pending files.
- Instinct saves milestone narratives immediately. Decide is mandatory only for real choices, mismatched predictions, irreversible steps and final completion. It supplies goal, narrative, latest Retina answer and candidate actions, then follows the top choice. Milestone notes do not wait for Jev.
- Instinct calls for focused and nested crops, re-observation of inconsistent boxes, artifacts under RUN, actual-format/header checks and attachment of the exact verified file. Format metadata may be inspected without interpreting pixels. No file-format validator or coordinate-repair code was added.
- Cut forwarding wrappers, redundant vision model-list readiness verification, screenshot-size/crop validation branches and Cursor read-denial rules. Windows DPI setup, zoom mapping, all-screen capture, streaming, Telegram call authorization and installation remain.

No installer, application, tests, harnesses or compilation checks were run for this follow-up. No run data, .env or credentials were inspected. The untracked house_comic files were left untouched. These changes are not runtime-verified improvements.
