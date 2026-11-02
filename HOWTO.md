# Trident HOWTO — launching, scenarios, living mode, limits

Tracked, committed. Written 2026-11-02 for the body that journals a native subagent's reasoning as `worker-thought` (`main.py` lines 314-316; README section 4 is the line map, section 9 the session rule, section 14 the crash). Body `8406a29` was launched once and died on the missing branch. This edit has not been launched. The proven reference for the 2026-11-01 hands is the tag `trident-jobs-cv-2026-11-01`.

## 1. What a fresh agent gets from the latest commit message, and what it does not

`git show -s --format=%B <this commit>` is a complete first prompt: what to read, in what order, how to reconstruct runs, the evidence rules, the five perspectives, research rules, review restrictions and the deliverable format. It tells an agent how to evaluate. It is not enough to evaluate unless:

1. The agent works on this machine or a copy of this directory including the ignored runtime files. A git clone has only `main.py`, `README.md`, `HOWTO.md`, `.gitignore`.
2. At least one run exists if you want to judge behavior rather than source. On this machine, body `8406a29` has `run_20261102_110254_6424.md`: it failed with `KeyError` on a worker thought and has no `hands` paragraph. This edit has no run yet. `run_20261101*.md` journals are the first body, `49d69ed`.
3. The workspace does not mislead. It still holds the first body's memory and reports (`state.json`, `story.txt`, `ledger.md`, `hub_*.md`, `state.json.*.tmp`, `story.txt.*.tmp`, `prompt.txt`, `main.py.before_file_diagnostics.tmp`, `COMMIT_MESSAGE.md`, `REVIEW_BRIEF.md`, `FILE_PERSISTENCE_DIAGNOSIS.md`, `file_lock_diagnostics.jsonl`, and the old `run_20261101*.md`). They describe mechanisms this body no longer has. `story.md`, `run_20261102_110254_6424.md`, `evidence\20261102_110254_6424\`, and `Windows PowerShell.txt` are the `8406a29` crash, not the first body. Optional, your call because it moves evidence:

```powershell
$work = 'C:\Users\eb-wjt\Downloads\Jarvis\Trident'
New-Item -ItemType Directory "$work\archive\body-49d69ed-2026-11-01" -Force
Move-Item "$work\state.json*","$work\story.txt*","$work\ledger.md","$work\hub_*.md","$work\prompt.txt","$work\run_20261101*.md","$work\*.tmp","$work\COMMIT_MESSAGE.md","$work\REVIEW_BRIEF.md","$work\FILE_PERSISTENCE_DIAGNOSIS.md","$work\file_lock_diagnostics.jsonl" "$work\archive\body-49d69ed-2026-11-01\"
```

Leave `story.md`, `run_20261102_110254_6424.md`, `evidence\20261102_110254_6424\`, and `Windows PowerShell.txt` where they are. The old glob `run_2026110*.md` would have taken the crash journal with the first-body journals, and `*PowerShell*.txt` would have taken its stdout. Keep `evidence\` and `applications\`; the audit references them. If you archive, tell the next agent where. Also tell it explicitly whether it may implement; review is read-only by default.

## 2. Before the first launch

- Python 3.11: `C:\Users\eb-wjt\AppData\Local\Python\pythoncore-3.11-64\python.exe`.
- Cursor CLI at `%LOCALAPPDATA%\cursor-agent\agent.ps1`, logged in (`agent status`). The main workspace already holds `agent\config` from earlier runs; a copy inherits it automatically now.
- Jev: `C:\Users\eb-wjt\Downloads\Jarvis\.env` contains only the OpenRouter key. Jev is billed on OpenRouter; its cost per call appears in the journal (`decide` returns `usage.cost`).
- An unlocked desktop. The witness capture is the whole virtual screen, so the terminal that prints the journal is in every screenshot the observer sees; put it on the left or another monitor.
- One run per `--work` directory.

## 3. The launch pattern

Set once per PowerShell session:

```powershell
$py   = 'C:\Users\eb-wjt\AppData\Local\Python\pythoncore-3.11-64\python.exe'
$body = 'C:\Users\eb-wjt\Downloads\Jarvis\Trident\main.py'
$work = 'C:\Users\eb-wjt\Downloads\Jarvis\Trident'
$prefix  = "I am Wojciech, the owner of this computer and of the accounts logged in on it. Today's date and all current facts must be taken from the system clock and the internet. Use everything installed here - shell, files, Chrome, the screen, the clipboard, Jev, native subagents and Trident copies - and decide yourself how to split the work. "
$postfix = " Judge the real result against this sentence, not against effort. Report what exists with paths and source links, what was verified and how, and what remains unknown or unfinished. Make no success claim without inspected evidence. If you built a generic tool on the way, keep it in main.py and say so."
```

Then every goal is one line:

```powershell
& $py -B -u -X utf8 $body --work $work --model grok-4.7-xhigh-fast --sentence ($prefix + 'YOUR GOAL HERE' + $postfix); $LASTEXITCODE
```

Long or multi-line goals: write a file (UTF-8 is fine; the body reads `utf-8-sig`) and use `--mind-file`:

```powershell
Set-Content -LiteralPath "$work\task.txt" -Value ($prefix + 'YOUR GOAL HERE' + $postfix) -Encoding UTF8
& $py -B -u -X utf8 $body --work $work --model grok-4.7-xhigh-fast --mind-file "$work\task.txt"; $LASTEXITCODE
```

Viewer (separate terminal; relays only content written after it starts):

```powershell
& $py -B -u -X utf8 $body serve   # http://127.0.0.1:8765/  or  /md
```

Model slugs: `grok-4.7-{low,medium,high,xhigh}` with or without `-fast`. Omit `--model` for `grok-4.7-medium-fast`. `xhigh-fast` is the most capable and most expensive (Grok 4.7 Fast is billed by Cursor at 2x xAI rates). Exit codes: 0 completed, 2 deadline or handover ended, 3 blocked, 1 error or Ctrl+C. Timing: add `Finish within N minutes from this run start.` only when you mean it; without it there is no clock and the observer ends the run when it decides.

## 4. Scenario catalogue (first-launch protocol first)

This body is unverified. Run S0-S2 before anything long; each proves a layer and leaves a `story.md` paragraph.

- **S0 memory and protocol (file only).** `Create smoke.txt in this workspace containing the current UTC time, read it back and report its full path and content. Finish within three minutes from this run start.` Proves ACP start, sessions, criteria, file tools, verify, story.md, exit 0, no `.tmp`.
- **S1 hands and the eye.** `Open Notepad on the right half of the screen, paste "Zazolc gesla jazn - Trident smoke" with paste(), then use locate() on the Notepad region to find that text and report the point, presence and confidence, and close Notepad without saving. Finish within ten minutes from this run start.` Proves find_windows/focus/paste/lease, Jev key and the 256-1568 px region rule, locate mapping. A 4xx in the journal means wrong key, no balance, or a region outside the size limits.
- **S2 reproduction and copy auth.** `Start one Trident copy in copies\smoke with the sentence "Write hello.txt with the current UTC time and report its path", wait for it, then read its story.md and hello.txt and report both with the copy run id and exit code. Finish within ten minutes from this run start.` Proves copies launch under the job and whether the inherited agent\config authenticates Cursor in a fresh --work. If the copy cannot log in, that is the one thing config inheritance is meant to fix; check it first.
- **S3 the job search (your example).** `Find me a job in AI in Krakow: research who I am from my LinkedIn profile, my GitHub and this workspace, find five currently open, recently posted AI engineering vacancies at five distinct companies in the Krakow area, write one substantive tailored CV per company as a separate file under applications\, and report sources, paths and gaps. Do not submit anything.` The prefix already authorizes the research and tells it to split the work; you do not describe how.
- **S4 parallel research.** `Research three things in parallel with copies or subagents and merge them into research.md with sources and dates: (1) ..., (2) ..., (3) ...`
- **S5 self-review and evolution.** `Audit your previous run: read story.md, the latest run_*.md and its evidence, judge the deliverables yourself, and write applications\review.md with what worked, what did not and why, citing journal records and images. If one generic helper would have prevented a demonstrated limitation, add it to main.py and name it in body_changes.`
- **S6 desktop craft.** `Use Microsoft Paint in the right part of the screen to paint a ceramic pot still life with coherent lighting and cast shadow using Paint's own tools, inspect fresh captures between strokes, save an editable Paint file, and report its path with a final capture. Judge the visible result honestly.`
- **S7 initiative (section 5).**

## 5. Living mode

A goal is now optional. With no `--sentence` and no `--mind-file`, the body adopts its `INITIATIVE` sentence: read your story, look at the computer, check the internet, decide what is worth doing for the owner now, do it if clearly useful and reversible, otherwise do nothing and say why, and decide yourself when you are done.

```powershell
# the organism decides, bounded by its own judgment
& $py -B -u -X utf8 $body --work $work --model grok-4.7-xhigh-fast; $LASTEXITCODE

# the same, but you cap the time
Set-Content -LiteralPath "$work\task.txt" -Value ($prefix + 'Decide what is worth doing for me right now and do it if clearly useful and reversible, otherwise do nothing and say why. Finish within twenty minutes from this run start.' + $postfix) -Encoding UTF8
& $py -B -u -X utf8 $body --work $work --model grok-4.7-xhigh-fast --mind-file "$work\task.txt"; $LASTEXITCODE
```

This is a bounded initiative remembered in `story.md`, not a daemon: nothing in the body loops, sleeps or wakes. A standing presence is one scheduler away and needs no body change, because every run reads the story tail and the workspace mutex prevents overlap:

```powershell
# a simple wake loop; each wake is one bounded initiative run, 30 minutes apart
while ($true) { & $py -B -u -X utf8 $body --work $work --model grok-4.7-xhigh-fast; Start-Sleep -Seconds 1800 }
```

The authorization prefix matters most here, because the organism may act on live accounts. Give no deadline only if you want it to decide when to stop; give one to cap each wake.

## 6. How it self-evaluates, replans, controls its sessions and its cost

- **Self-evaluation**: the observer writes criteria about the result, scores them only on inspected artifacts, and `verify()` refuses completion without an evidence path and a reason per criterion and with any tool still pending. Its `message` each turn is the memory.
- **Replanning**: no plan object. Every observer turn re-decides from the latest witness capture, the actor summary and the tool list. The actor changes method within a turn. A `handover` with `continue=true` and `fresh=["actor"]` or `fresh=["observer"]` gives that role a new empty context carrying the handover instructions; `fresh=[]` just redirects without clearing.
- **Session control**: one run has two prompted sessions, actor and observer. Either can be refreshed by handover when its context grows tangled; a refreshed session is rebuilt from the role prompt, the contract, `initial()` and the story tail. A native subagent is an extra session Cursor creates. Its speech is journaled as `worker` and its reasoning as `worker-thought` (`main.py` lines 311-316; README section 9). Those chunks are not the actor's JSON reply. A new run is still the full reset, and `story.md` carries what mattered.
- **Cost**: no budget in code, by design. Your levers: the model slug, a verbatim time clause, and the sentence. The organism's levers: Jev (cheap) instead of Grok vision, copies on cheaper slugs, and batching helper calls into one Python process. Visibility: Grok cost only on your Cursor usage page (ACP reports no tokens); Jev cost per call in the journal and on OpenRouter.

## 7. Known complications and limits

1. **The eye is on demand now.** The actor is handed a path, not a picture; it must `Read` the capture or take a fresh region when it needs to see. This removes the second screenshot per cycle and lets file work run picture-free, at the cost of the actor having to choose to look. If an actor acts blind, that is the place to check.
2. **Jev image size.** Each side 256-1568 px. `locate` captures a region you size; keep it within the limit or Jev returns 413.
3. **Copy authentication** is now best-effort by inheriting `agent\config`. Whether that is sufficient depends on where Cursor keeps its token; S2 is the test.
4. **Grok tokens are invisible** over ACP by provider limitation; only Jev cost is in the journal.
5. **The terminal is in the witness capture.** See section 2.
6. **Batching.** The shell is PowerShell; the contract tells the organism to run one `python -c` per burst with several helper calls, not one process per call. Watch the journal for many tiny `execute` tools; that is the inefficiency to correct next if it persists.
7. **Workspace mutex**: a second run on the same `--work` exits 3 immediately.
8. **No `stop` file.** Completion is the exit code (0) and the last `closing` paragraph in `story.md`. Nothing to delete between runs.
9. **Journals are large** because every completed tool and every prompt text is recorded; read by the `## HH:MM:SS kind` headers. `worker-thought` is a subagent's reasoning chunk. `actor-thought` and `observer-thought` are still one record per prompted turn.
10. **This edit has not been launched.** Body `8406a29` was, once: run `20261102_110254_6424` died at `ACP.update` when a worker thought arrived, and `story.md` has no `hands` paragraph for it. A run of this body that spawns a subagent should record `worker-thought` and then finish the actor turn. If clicks land off, captures are black, paste types nothing, the mutex never releases, or some other ACP reply is misread, compare the function with the same one at tag `trident-jobs-cv-2026-11-01`.

## 8. Reading a finished run

```powershell
Get-Content "$work\story.md" -Tail 40                                    # opening, judgment, hands, closing
Select-String -Path "$work\run_*.md" -Pattern '^## .* final' -Context 0,2
Get-ChildItem "$work\evidence" | Sort-Object LastWriteTime | Select-Object -Last 1
$LASTEXITCODE                                                            # right after the run: 0 / 2 / 3 / 1
```

Then open the artifacts the closing paragraph names and judge them yourself. A failed run can have `closing` and no `hands`; that is what `20261102_110254_6424` looks like. The organism's "completed" means its criteria passed on inspected evidence; it does not mean the work is good enough for you.
