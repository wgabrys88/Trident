# 05. Evolution

Date of review: Tuesday 27 October 2026. Branch `runner-h`, HEAD `190058900aaafd8e2a8bdb6bbdc9b3dc45671a11`. Working tree of code: `organism.py` (1044 lines, 65412 bytes) and `main.py` (3 lines, 123 bytes). No public-web search was made. No model-memory paper is cited.

`edit_log.txt` is absent. A recursive search of the workspace returned no file of that name.

## 1. Scope and method

The question is whether this organism grows: what one wake writes into `story.txt`, `ledger.md`, `fara_traj.json`, and `organism.py`, and whether the next chat or the next wake still has that fact.

Two inheritance lines are kept apart.

- The organism's line is the files it rewrites during a wake. `story_run` replaces `story.txt` (`organism.py` lines 763–768). `_enter` replaces only this wake's lesson and writes the whole ledger back (`organism.py` lines 738–752). `eye_steps` starts or wipes `fara_traj.json` (`organism.py` lines 615–620). `render_prompt` is what the next chat actually receives (`organism.py` lines 796–806).
- The operator's line is git. The last 15 commits on `runner-h` are by Wojciech, each with `Co-authored-by: Cursor <cursoragent@cursor.com>`. A commit counts as the organism's only when a run log shows the wake editing the file. That happens once in this set, during `20261027_151228` (commit `9823582`, 15:42:51). The memory commits from 18:33 through 20:25 sit between wakes. The following run reads the new lines. It does not show the organism applying the patch.

Labels used below:

- OBSERVED: a quote with a file and a line, or a timestamped heading, in at least two independent places.
- INFERRED: a conclusion that needs the two sources plus a step. Confidence is stated.
- SINGLE-SOURCE: only one file supports it.
- SYMPTOM: what the next wake no longer has. ROOT CAUSE: the line that dropped it. If the chain breaks, the break is named.

Every required file was opened and every line was scanned (line counts are in the appendix and match the files on disk). Continuous text was read for `organism.py`, `main.py`, `ledger.md` (19 lessons, 39 lines), `prompt.txt`, every scratch story and goal file named in the brief, `fara_traj.json`, and the four short runs `142947` (170 lines), `155202` (500), `160354` (2044; lines 401–1599 are the repeated `chars=N` / `tool` / `shell` pattern between the narrative headings), and `182210` (236). For the long runs, every heading was extracted, every `say` / `story` / `ledger` / `report` line was taken with its line number, and every inheritance needle was counted across every line. Cited ranges were then read as text. `ledger.history.jsonl` (35 lines, 156925 bytes) was scanned in full because it is the only archive of lessons that `_enter` later replaced. It is not in the required list; it is used only where a lesson was overwritten.

`git status` on 27 October 2026, after HEAD `1900589`: branch `runner-h` is up to date with `origin/runner-h`. The only untracked file is `wake_traj_summary.py`. That file was not read. `skills\.gitkeep` is 0 bytes.

### Counts, not impressions

| Item | Count | Where |
| --- | ---: | --- |
| Run markdown files read | 16 | appendix |
| `## wake` lessons now in `ledger.md` | 19 | `ledger.md` lines 1–39, `## wake` counted 19 |
| Of those 19, the empty fallback | 1 | `ledger.md` line 27: `No lesson was written.` |
| Wakes in this 16 with no ledger section at all | 2 | `20261027_142947`, `20261027_182210` |
| Lesson present in the history archive and absent from `ledger.md` | 1 | `20261026_120416` |
| `story` headings (story rewrites) across the 16 runs | 41 | heading counts below |
| `ledger` headings (lesson replacements during a wake) | 27 | heading counts below |
| Lessons that survive per wake | 1 | `_enter` keeps one body per run id |

Story and ledger headings, from the `MEASURED` block that `render_prompt` computed by reading each run file (`prompt.txt` lines 171–173) plus the last run's own headings:

| Run | story headings | ledger headings | chats seen | end state |
| --- | ---: | ---: | ---: | --- |
| 133306 | 10 | 8 | 4 (`start` at 13:33:12, 13:43:15, 13:54:59, 14:00:36) | stopped, 33.0 of 40 min |
| 141013 | 1 | 1 | 2 processes, 1 `start` heading | failed_agent, 14.3 of 40 |
| 142947 | 0 | 0 | 1, file ends at 14:31:19 mid-thought | no `mind` heading, no lesson |
| 143208 | 5 | 4 | 4 | stopped, 33.0 of 40 |
| 151228 | 1 | 1 | 2 `start` headings | failed_agent, 31.2 of 40 |
| 155202 | 1 | 1 | 2, second chat exits at 16:02:17 | failed_agent, 10.2 of 40 |
| 160354 | 4 | 4 | 6 | stopped, 34.0 of 40 |
| 182210 | 1 | 0 | 1, file ends at 18:26:42 mid-eye | no `mind` heading, no lesson |
| 183727 | 2 | 1 | 1 | stopped, 4.7 of 5 |
| 190548 | 2 | 0 | 2 (rotate 19:11:22, then deadline 19:15:48) | deadline; fallback lesson |
| 191902 | 2 | 1 | 1 | stopped, 8.8 of 10 |
| 194156 | 3 | 1 | 2 (rotate 19:51:42, deadline 19:51:56) | deadline, 10.0 of 10 |
| 195316 | 4 | 1 | 1 | deadline, 10.0 of 10 |
| 200412 | 2 | 1 | 1 | stopped, 9.9 of 10 |
| 201442 | 2 | 2 | 1 | stopped, 9.9 of 10 |
| 202606 | 1 | 1 | 1 | stopped, 9.9 of 10 |
| Total | 41 | 27 |  |  |

A story heading replaces `story.txt`. It does not append. Eight of the ten story writes in `133306` are gone except as `story_len` integers in that run. The same is true of the seven earlier lesson bodies of that wake: `ledger.history.jsonl` kept the previous full ledger before each write through 15:43:39, and then the archive stopped.

### The code that keeps or drops memory

Current `organism.py`, read in full.

Prompt shows one lesson, the last one, not the file:

```735:737:organism.py
def newest(ledger):
    parts = re.split(r'(?m)^## wake ', ledger or '')
    return 'There is no ledger lesson yet.' if len(parts) < 2 else '## wake ' + parts[-1].strip()
```

`render_prompt` puts that single lesson under `LEDGER`, the whole `story.txt` under `STORY`, and a count of every earlier run under `LEARNED` (`organism.py` lines 796–806). The story is marked inherited when `story.meta.json` names a different run:

```801:801:organism.py
    status = 'current wake' if meta.get('run') == SET['run'] else 'inherited; not current progress'
```

On disk, `_enter` now keeps every other wake's lesson and replaces only this run's body (`organism.py` lines 745–752). There is no length cap in HEAD. That cap existed earlier. At commit `534ce27` (13:45:46, during wake `133306`), `memory.py` lines 107–108 were:

```
while len(kept) > 12 or sum(map(len, kept)) > 8000:
    del kept[0]
```

The same two lines are `organism.py` lines 715–716 at commit `9823582` (15:42:51) and still at `b142051` (18:33:08). Commit `35863c0` (18:36:20), message "Keep every ledger lesson and every earlier run.", deletes the loop. From `183727` onward a new lesson is appended. A lesson already deleted is not restored.

`journals` at HEAD returns every earlier `run_*.md` (`organism.py` lines 712–716). At `9823582` line 680 the same function ended in `[-5:]`, so `--learn` and the prompt's `LEARNED` line named at most five wakes. Commit `4c12416` (19:05:26) only changes the tool sentence. The prompt embedded in `183727` still says `--learn. Count headings in up to five earlier run markdown files.` while the `LEARNED` line in that same prompt lists 8 wakes. OBSERVED: the sentence lagged the code by one commit.

The eye's trajectory does not cross a terminated task:

```615:620:organism.py
def eye_steps(app, text, end, limit, fresh):
    file = app.work / 'fara_traj.json'
    if fresh: data = {'open': True, 'turns': []}
    else:
        data = json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'open': False, 'turns': []}
        data = data if data.get('open') else {'open': True, 'turns': []}
```

`eye_watch` calls `eye_steps` with `fresh=True` (`organism.py` line 671), so each new sentence starts an empty trajectory. At wake start, an open trajectory is closed but its turns are kept long enough for the first prompt (`organism.py` lines 934–938). Inside one trajectory, images older than the last three shot-bearing turns are dropped:

```502:502:organism.py
    kept = [turn for turn in turns if turn.get('shot')][-3:]
```

A turn that is not in `kept` is sent to the eye as text with no bitmap (`organism.py` lines 507–510).

`drive` finally calls `enter(app.work, current_lesson(app.work), outcome)` (`organism.py` line 979). If the wake never called `--ledger-file`, `current_lesson` returns `''` and `_enter` stores `No lesson was written.`

`story_run` does not keep the previous story. Scratch files (`story_wake.txt`, `wake_story.txt`, `ledger_this.txt`, and the rest in the appendix) are leftovers. The next prompt does not read them unless a later chat opens them with its own tools.

### Operator commits that changed what could be inherited

Read from `git log --format=fuller -15`. Times are 27 October 2026, +0100.

| Commit | Time | What it changed for memory |
| --- | --- | --- |
| `9823582` | 15:42:51 | One `organism.py`. The message records the wake command and `organism_lines=992`. This commit is inside run `151228`. |
| `6739ed1` | 15:43:37 | Hyphenated flags such as `--story-file` and `--mind-file`. |
| `b142051` | 18:33:08 | Grok plans, Fara acts. Journal still cuts text (`heading` limit 360 at line 170 of that file) and `eye_log` keeps `rows[-12:]`. |
| `ec3a1d5` | 18:35:01 | Headings and tool results are no longer cut. |
| `0bad528` | 18:35:36 | Full thought and full unparsed reply. `journals` still `[-5:]`. |
| `35863c0` | 18:36:20 | "Keep every ledger lesson and every earlier run." The 8000-character drop is gone. `newest` still returns one lesson. |
| `26f2fa0` | 18:37:15 | Full thought and full tool call in the journal. |
| `c3887eb` | 19:02:57 | Stop new work only in the last five percent. |
| `866dc42` | 19:03:56 | Console mirror runs through the mind heading. |
| `02cf66d` | 19:05:12 | One copy of each shell command in the tool heading. |
| `4c12416` | 19:05:26 | `--learn` text says every earlier run, not five. |
| `0b353b9` | 19:18:51 | A word is typed; a key is named. Between `190548` and `191902`. |
| `657392c` | 19:41:33 | Eye thread lives for the whole wake. The message records the actual break in `190548` and `191902`. At the start of `194156`. |
| `233661f` | 19:53:05 | "Tell the eye when the same click already happened." Cites 19:47:15–19:51:57, `left_click` at `[500, 498]`. At the start of `195316`. |
| `1900589` | 20:25:48 | "Do not repeat a click that already happened." Cites 20:18–20:24, `left_click` at `[283, 230]`. Between `201442` and `202606`. |

The repeat-click guard now in `eye_steps` (`organism.py` lines 648–650) is the 20:25 operator commit. Run `202606` reads those lines and does not edit them (`run_20261027_202606.md` lines 79 and 437).

## 2. Timeline

For each wake: what it was handed, what it wrote, what the next wake still had. "Quoted" means the next run's own text contains the previous lesson's distinctive words, not merely that `ledger.md` still holds them.

### 2.1 `20261027_133306` (13:33:06–14:06:04)

Received. The history snapshot just before its first ledger write (13:35:15, history line 19) still has the 26 October lessons, newest `20261027_124510`. OBSERVED in `ledger.history.jsonl`. The run's first say does not quote that lesson. It quotes the sentence: "The wake is to make Trident smaller and task-agnostic" (`run_20261027_133306.md` line 57).

Wrote. Ten story replacements and eight lesson replacements. The last lesson is the one that survived (`ledger.md` lines 11–12):

> At 14:05:40.843 no further code edit was started. Restore exit was skipped and git status was M mind.py M tests/test_body.py. mind.shed, if still uncommitted, adds lines and would delete agent/config/chats, agent/data/projects, streams, shots, and actions on the next drive().

The run report matches it (`run_20261027_133306.md` line 3189): "At 14:05:40 the 20 percent line had already passed, so this wake stopped with no new edit, no test run, and no stroke."

Next wake. `141013` quotes `mind.shed` five times (first at line 25) and says (`run_20261027_141013.md` line 95): "The last wake spent its time rereading the body and stopped with a dirty tree and no painting." The fact was remembered. Confidence high. OBSERVED in the lesson and in the next run.

### 2.2 `20261027_141013` (14:10:13–14:24:33)

Received the `133306` lesson, as above. The markdown has one `start` heading. The jsonl has two chats. Line 1 is `kind: shed` with `"removed": ["agent/config/chats", "agent/data/projects", "streams", "shots", "actions"]`. A later line is `chat_end` for `20261027_141013:1`, reason `rotate`, `completed_calls` 42. The next is `chat_end` for `20261027_141013:2`, reason `failed_agent`, `completed_calls` 0, and then `wake_end` with the same reason. The first chat wrote one story (`story_len` 1021 at 14:24:31) and one lesson. The second chat added no lesson. OBSERVED in `run_20261027_141013.jsonl`. Report 01 section 2.2 quotes the same shed payload and the 183-byte second stream `can't open file '...\job_boot.py'`.

The shed line is the deletion the previous lesson had warned about. `ledger.md` lines 11–12 say an uncommitted `mind.shed` would delete `streams` and `shots`. This wake's first log line says it removed them. The pictures from `133306` were not passed on. The warning text was. The lesson this wake then wrote says the deletion must not happen again.

Wrote the lesson that is still `ledger.md` lines 14–15:

> v2 is the body and root main.py only starts it. shed() is gone, so a wake must not delete agent/ or shots/. ... Do not click PowerShell. Do not --place.

Next. `142947` thinks (`run_20261027_142947.md` lines 31 and 51): "The story checklist confirms v2 is the intended body and shed() is removed" and "do not click PowerShell." It dies at 14:31:19 with no story write and no lesson write (`run_20261027_142947.md` line 170 is the last line, a grep request). Because it wrote no lesson, `newest()` still returned the `141013` lesson to `143208`.

`143208` line 31 still says "v2 is the body", and "Do not --place" appears seven times (first in the index at the 14:40 checkpoint). The `141013` prohibition survived a wake that wrote nothing. OBSERVED.

### 2.3 `20261027_142947` (14:29:51–14:31:19)

Received the `141013` story and the desktop sentence. Windows at 14:30:46 (`run_20261027_142947.md` line 87): File Explorer hwnd `2360410` in front, PowerShell `656304`, Paint `917726`. It read `v2\eye.py` and stopped. No inheritance was added. SINGLE-SOURCE for the cause of death: the file simply ends. There is no `agent-exit` and no `mind` heading, so `drive`'s `finally` did not run. INFERRED, confidence high, from the missing headings plus the absence of a `142947` section in `ledger.md`.

### 2.4 `20261027_143208` (14:32:08–15:05:06)

Received the `141013` lesson (v2, do not `--place`), not a lesson from `142947`. Five story rewrites, four lesson rewrites. The last lesson (`ledger.md` lines 16–17) names drag `fara 1315fbb8eda647d3ace883a2f86795ae`, cursor `[726.7, 497.5]`, capture `150321`, and "a black horizontal line." The run report at 15:05:06 says the same painting sentence is not met.

An intermediate lesson, the 14:39 thin-brush text, is still sitting in `wake_lesson.txt` (758 bytes) and `ledger_wake.txt` is a different leftover. Those files are not what the next prompt reads.

Next. `151228` has zero occurrences of `1315fbb8`, `black horizontal`, or `15:04:08`. It does say "cat, mountains" three times, in its own report that the painting was not done (`run_20261027_151228.md` lines 2492, 2496, 2556). The stroke id was not quoted. The lesson text remained on disk (`ledger.md` lines 16–17 are still that paragraph). SYMPTOM: the next run does not use the stroke. ROOT CAUSE breaks at the sentence: `sentence.txt` is still the rewrite order ("NO RULES DURING THIS WAKE..."), and the wake followed that sentence. The ledger slot was not empty. The chat did not cite it. Confidence high. OBSERVED as an absence in `151228` plus the presence in `ledger.md`.

### 2.5 `20261027_151228` (15:12:28–15:43:39)

This is the one wake that edited the body. The run's say at 15:13:19 is "consolidate this wake into one from-scratch organism." At 15:42:00, `--map` prints `organism_lines=992 total_python_lines=995 under_1000=True` (`run_20261027_151228.md` line 437). Commit `9823582` is 15:42:51. The lesson (`ledger.md` lines 18–19) repeats the map, the commit, `fara-1.5-4b` with alias `retina`, and "Do not grow a second code tree, and do not put full model essays into the run markdown."

`story_this.txt` (1215 bytes, 13 lines) is that checklist, still on disk. `ledger_this.txt` is the same lesson. `story.meta.json` no longer points at this run.

The history archive's last line is this wake's open lesson at 15:43:39. That snapshot still contains `20261026_120416` as the oldest of 10 lessons, sum of part lengths 6777. The new `organism.py` `_enter` does not append `ledger.history.jsonl`. The archive stops here. OBSERVED.

Next. `155202` journals thoughts as `chars=N` and tools as the word `shell` or `grep` (`run_20261027_155202.md` lines 13–31). That is the cut journal from `b142051`'s `heading(..., limit=360)` and `compact_agent`, still in force until 18:35. The markdown has zero occurrences of `9823582`. The say at line 17 is the new sentence: "publish an article on X from this desktop." The rewrite words were still handed to that chat. Stream `130358e138e64b239689d2912068f9c5.stream` line 2 is the user message, and it contains: "This wake's --map said organism_lines=992, total_python_lines=995, under_1000 true, vision http://192.168.16.31:1234. Commit 9823582." The commit hash occurs three times on that line and on no other line of the stream. A later tool result (stream line 256) reads the `map_run` source, which contains the identifier `organism_lines` and not the sentence `organism_lines=992`. The model's thinking deltas do not repeat the hash. OBSERVED: the next chat was shown the checklist, and the markdown it left behind does not quote it. The earlier sentence in this report that said the prompt was only a guess was wrong. Report 04 section 2.6 measured 22,972 thinking characters in this stream against 301 characters stored in the markdown. That is the journal throwing the speech away after it had been generated. It is not evidence the model repeated the map result.

### 2.6 `20261027_155202` (15:52:02–16:02:17)

Received a new sentence, the article. Ledger write at 15:59:40 returns `{"wakes": 11, "lesson_len": 470}` (`run_20261027_155202.md` line 415). Eleven is 10 previous lessons plus this one. The 8000-character cap did not fire. The lesson that survived (`ledger.md` lines 20–21) says Chrome is on grok.com Imagine, the sidebar showed Wojciech, and "No picture, Paint drawing, or X post exists yet."

The run itself, nine minutes later, contradicts the "no picture" half. At 16:00:25 the eye says the Imagine box contains "A quiet living organism at a wooden desk at night..." (`run_20261027_155202.md` line 455). At 16:01:00 it returns `fara edd4fb2cbf4a43e98c5d69249e1e036a`, the send click. That id is still the whole of `hand_goal.txt`. The second chat exits `failed_agent` at 16:02:17 before a second lesson (`run_20261027_155202.md` lines 493–499). The lesson on disk stayed the earlier, staler text. SYMPTOM: the passed-on lesson says no picture exists. ROOT CAUSE: `--ledger-file` ran at 15:59:40, the send click ran at 16:01:00, and the process died before another lesson. Confidence high. OBSERVED in the two timestamps inside one file, and in `ledger.md` line 21 which lacks the send id.

### 2.7 `20261027_160354` (16:03:54–16:37:54)

Received the stale lesson and, independently, the desktop. First useful say (`run_20261027_160354.md` line 127): "The last wake clicked Imagine's send and then failed before it could record the picture." Windows at 16:05:07 (line 135) name the Chrome window with the full Imagine prompt as its title. The eye at 16:07:07 (line 235): "a dark, moody scene of a small, pale, slug-like organism sitting on a wooden desk at night." Two sources for the inherited prompt: the previous run's eye answer (line 455 of `155202`) and this window title. The lesson text "no picture exists yet" was not what this wake followed. It followed the screen. OBSERVED.

Four story rewrites and four lesson rewrites. The first ledger result, 16:09:32, is `{"wakes": 11, "lesson_len": 777}` (line 303). Arithmetic: `155202` had just reported 11, so this write should have reported 12 unless one was deleted. The cap `sum(map(len, kept)) > 8000` then `del kept[0]` is the only deletion in the code that was loaded (`9823582` lines 715–716). The oldest part in the 15:43 snapshot was `20261026_120416` (602 characters). It is absent from `ledger.md`. `20261026_122610` is still line 1. INFERRED that 16:09:32 dropped `120416` and no later write dropped `122610`, because the next full ledger read (`190548`, 19:06:15) begins at `122610`. Confidence high.

The surviving lesson (`ledger.md` lines 22–23) is the dashed box: "Capture 163405 at 16:34:05 shows Untitled - Paint hwnd 1704446 in front, font size 36, and an empty dashed text box." The report at line 2037 says the four lines were not typed. The journal of this run is still `chars=N`. The story body that held the four sentences was not in the run file. It was in `story_wake.txt`, and the next wake overwrote it (see 2.9). The four sentences survive only inside that overwrite's diff, which the `183727` journal stored in full.

### 2.8 `20261027_182210` (18:22:10–18:26:42)

Five-minute wake. Journal still cut. Say at line 19: "FARA is terminated and the inherited story is not this wake's progress." One story write, `story_len` 129 (line 127), which is the four-line Paint plan later read back by `183727` (`story.txt` at 18:38:00 is 129 bytes and those four lines). No ledger heading. The file ends at line 236 on `action=key` for Win+R, with no `agent-exit` and no `mind`. The `160354` lesson stayed newest, because this wake never replaced it. OBSERVED: `183727`'s prompt, copied into its run, still shows that lesson under `LEDGER`.

### 2.9 `20261027_183727` (18:37:27–18:42:06)

Received, and the receipt is inside the run. The prompt read at 18:38:00 (`run_20261027_183727.md` line 57) contains:

- STORY status `inherited; not current progress`, provenance run `20261027_182210`, the four Paint lines.
- FARA still `open`, four `action=key` Win+R / `mspaint` thoughts, the last one cut with `Win...`.
- LEDGER the full `160354` dashed-box lesson, including "Do not click Start."
- LEARNED: "8 earlier wakes (... 20261027_182210), 5 recorded errors, 247 component actions."
- The tool sentence still says "up to five earlier run markdown files."

The mind's say (line 33) follows FARA, not the dashed-box lesson: "the last eye log shows Win+R and `mspaint` never left Paint running." At 18:38:36 it reads `eye_ask.txt`, which is still the dashed-box question, and then overwrites `story_wake.txt`. The diff stored in the run (line 139) is the last copy of the article checklist, including "File C:\Users\eb-wjt\Documents\bkp\bkp-152\download.jpg, 203094 bytes, written 16:18:09" and the four sentences beginning "I am Trident, a living organism." After the write, `story_wake.txt` is only the four figure lines. Those four article sentences are not in `ledger.md` and not in `story.txt`. They exist in this run's diff and nowhere else that was read. OBSERVED in the diff. The picture file itself was not opened. SINGLE-SOURCE for the byte size 203094: that diff.

The lesson this wake wrote is `ledger.md` lines 24–25: taskbar click `[104, 972]`, unparsed `keys [Paint, Enter]`, then "At 18:41:31 windows listed Untitled - Paint hwnd 196850", and "Put words in a type action; Paint is not a key name. Do not press Win+R. Do not continue the article."

Next. `190548` thinks at line 19: "The next subtask is selecting a brush in Paint window 196850." Its ledger read at 19:06:15 (line 57) includes that sentence verbatim, and the story on disk is the three-line brush plan. The fact was remembered. The article sentences were not in that ledger read. Confidence high.

### 2.10 `20261027_190548` (19:05:48–19:15:48)

Received hwnd `196850` and "Paint is not a key", as above. It also read the whole ledger, which by 19:06:15 already starts at `20261026_122610` and has no `120416` (line 57). The number was remembered and the window was already gone. Line 141: "Paint was open at 18:41:31 with hwnd 196850, but it has since closed." Two story writes. No `--ledger-file`. The run ends `deadline` at 19:15:48. The report (line 1149) is the mind's last say: "The drag to [367, 252] ran, then she stopped and reported a blank canvas."

`finally` then stored the empty lesson. `ledger.md` lines 26–27:

> ## wake 20261027_190548 2026-10-27 19:05:48 used 10.0 of 10 min, deadline
> No lesson was written.

SYMPTOM: the stroke to `[367, 252]` and the blank-canvas claim are in the run and not in the lesson the next prompt's `LEDGER` slot will show. ROOT CAUSE: `organism.py` line 979 calls `enter` with `current_lesson`, and no `--ledger-file` had run, so line 739 substitutes `No lesson was written.` Confidence high. OBSERVED in the missing `ledger` heading (`MEASURED` gives this run `story: 2` and no `ledger`) and in `ledger.md` line 27. Shot `191351-6c2a3d897c3b4f58a16224b662e56184.png`, opened in the consultation pass, shows one diagonal line on the white canvas at 7:13:51 PM. It does not show a head, a body, two arms, and two legs. That over-claim died with the empty lesson. It was not what `191902` inherited.

### 2.11 `20261027_191902` (19:19:02–19:27:48)

Received "No lesson was written." The run mentions `No lesson` twice (lines 79 and 103). It does not treat that sentence as the drawing. Say at line 39: "The figure is mid-stroke and FARA is still open." Say at line 149: "Every logged stroke shares x=356, so the canvas is one vertical line." It reconstructed the figure from the open trajectory and the run, not from the lesson. OBSERVED.

It wrote the coordinates into the next lesson (`ledger.md` lines 28–29): left arm "19:22:35 drag to [302, 467]", right arm "[498, 467]", legs "[356, 643]" and "[444, 643]", terminate 19:25:23, and the stop check closed at 19:27:06 only when terminate had an answer and no coordinate. The report (line 931) says the answer was "The figure is visible." Ledger line 29 does not contain that phrase. It says only that the answer was non-empty. The next wake still had the phrase from the trajectory: `run_20261027_194156.md` line 41, "FARA is terminated and claims the figure is visible."

Shot `192556-8bca99829c924677a4256c9d5bf682dc.png` was opened in the consultation pass. Tray clock 7:25:56 PM. Paint is open. The canvas has straight black strokes: verticals, diagonals, one short horizontal, and a V. It does not show a closed head, and it does not show two horizontal arms. The coordinates were passed on. The names "head", "arms", and "legs" are the lesson's labels, and the picture does not show those parts. Report 03 section 2.11 describes the same frame. OBSERVED.

### 2.12 `20261027_194156` (19:41:56–19:51:57)

Received the coordinate lesson. The ledger read at 19:42:52 (`run_20261027_194156.md` line 75) includes the arm and leg sentence verbatim. The same think says: "The previous wake already painted the figure according to the ledger, so I must not claim a result without a record from this wake." Story on disk was still the four-line Paint plan (`plan_story.txt` matches the read at line 83). Campaign.txt is the five lines still on disk (line 79).

It did not continue the strokes. Its own lesson (`ledger.md` lines 30–31) says the campaign sentence ended at 19:44:00 after `mouse_move [227, 309]` and "This wake has no left_click_drag." From 19:47:15 it repeated `left_click` at `[500, 498]`. There are 17 such `action=` lines, the last at 19:51:57. Ledger line 31 stops the series at "through 19:48:38", which is the sixth of the 17. The later eleven clicks are in the run and not in the lesson. Operator commit `233661f` at 19:53:05 names the full interval through 19:51:57. The organism of this wake did not edit `organism.py` (say at line 153: "This wake's record shows no defect in `organism.py`").

Shot `194450-face4b5b90684a5482fbd1046f67f7ad.png` was opened in the consultation pass. Tray clock 7:44:50 PM, fifty seconds after the 19:44:00 terminate that claimed a circle, rectangles, arms, and legs. The picture is a coastline wallpaper. Paint is not open. The lesson records both the claim and "No drag was recorded." The picture agrees with the second sentence.

Next. `195316` has zero occurrences of `500, 498` and zero occurrences of "A click is not a stroke." It does quote the no-drag fact. Line 51: "The inherited story still lists the abandoned Paint task. FARA terminated claiming Paint was done, but the ledger shows no drag strokes were recorded." The same lesson, line 31, also says "Do not start Calculator." The next sentence was Calculator (`run_20261027_195316.md` line 59). The negative instruction was not what the next wake followed. The campaign sentence was. Confidence high. OBSERVED.

### 2.13 `20261027_195316` (19:53:16–20:03:17)

Received the campaign line "Open Calculator and show 2+2" and, as `newest()`, the `194156` lesson. It quoted the no-drag half and not the coordinate, as section 2.12 says. Four story writes. The surviving lesson (`ledger.md` lines 32–33) says terminate at 19:58:17 claimed result 4, the next read said the display showed 2, and "This wake has no record of an open display showing 4." `story_now.txt` is the calculator plan that names the bad equals points `[207, 611]`, `[205, 611]`, `[205, 609]`, `[266, 609]`.

Shot `195907-9f2021b927b94a50ac573238545398cb.png` was opened in the consultation pass. Tray clock 7:59:07 PM. Calculator is open and the large display shows 2. Paint is also open, canvas blank. The 19:58:17 answer said 4. The picture and the lesson agree that the open display was not 4. Report 03 section 2.13 describes the same frame.

Next. `200412` line 245: "The inherited story is still the Calculator line." `shows 4` occurs three times (lines 23, 77, 81). The failure was remembered, and the wake then replaced the story with the Chrome sentence. OBSERVED.

### 2.14 `20261027_200412` (20:04:12–20:14:06)

Received the calculator story and did not continue it. Say at line 245, quoted above. One eye pass. The eye's own terminate, `run_20261027_200412.md` line 326:

> action=terminate coordinate=None answer=Done — Chrome is open and showing the weather for Poland on Google search. Visible details include: 5°C, 0% precipitation chance, wind 8 km/h, and "Bezchmurnie" (clear) for Tuesday 20:00.

The lesson written at 20:13:51 (line 545, `lesson_len` 470, `wakes` 17) copies that claim, including "wind 8 km/h", and adds "This wake has no second look at that page." The report (lines 589–591) says the same and calls it her claim. No PNG is named in the lesson.

Shot `200647-44041906988d464e834b82c5e6645a4e.png` was opened in the consultation pass. Tray clock 8:06:47 PM, seventeen seconds before the 20:07:04 answer. The Google card already shows 5°C, precipitation 0%, wind 6 km/h, Bezchmurnie, and Tuesday 20:00. Calculator is still open on the left. The large display shows 1, and the history line reads `2 + 2 =`. The inherited wind integer was already false when she said it. The temperature, the Polish word, the zero precipitation, and the Tuesday time match the picture. The calculator digit 4 was still absent, one wake after the lesson that said so. Report 03 section 2.14 describes this frame and the later frame `201612` the same way.

### 2.15 `20261027_201442` (20:14:42–20:24:39)

Received the weather lesson and quoted it. Think at lines 270–272:

> The ledger records that run 20261027_200412 had the eye open Chrome and show the weather in Poland. The previous Chrome run had a taskbar click at 20:06:27, typed "Poland weather" at 20:06:36, pressed Enter at 20:06:45, and terminated at 20:07:04 with a weather card reading 5 C, 0% precipitation, wind 8 km/h, and "Bezchmurnie."

It then re-read the previous run and reproduced line 326 (the grep result at `201442` line 322). Two sources, both inside the later run, pointing at the earlier run and the ledger. What was remembered includes the false wind digit. Line 272 says "wind 8 km/h". Shot `200647`, above, shows 6 km/h. The wake did not open a shot. Its own sentence was Notepad.

Wrote. The log stayed open. Clicks at `[283, 66]` and `[223, 66]`, then from 20:18 repeated `left_click` at `[283, 230]` after the same-coordinate note. The say at line 870: "typed the text `Notepad` twice." Ledger line 37: "Two type actions used the text Notepad, not the campaign sentence." The trajectory dump stored in the same run contains `text='Notepad'`. Report 03 section 2.15 marks that string SINGLE-SOURCE because an earlier think is hedged. The say, the trajectory dump, and the ledger are three statements of the same word. The campaign sentence is a separate fact: line 870 also says it was not typed, and ledger line 37 says the same. Operator commit `1900589` at 20:25:48 describes the clicks. The wake's say does not show an edit to `organism.py`.

### 2.16 `20261027_202606` (20:26:06–20:36:01)

Received the Notepad story and the Notepad trajectory, not the weather numbers.

`prompt.txt` lines 7–10, which this chat read as its prompt and which the run's own think repeats:

> {"status": "inherited; not current progress", "provenance": {"run": "20261027_201442", ...}}
> 1. Open Notepad and type one sentence: The campaign is not one program.

`prompt.txt` lines 167–169 are only the `201442` lesson under `LEDGER`. `LEARNED` lists 15 earlier run ids, including `200412`, and does not include the words `Bezchmurnie` or `5 C`. `run_20261027_202606.md` has zero occurrences of `Bezchmurnie` and of `5 C`. The weather fact was in the previous run and in `ledger.md` lines 34–35. It was not in this prompt's lesson slot and not in this run's prose. SYMPTOM: one wake later, the number is gone from the prompt and from the journal. ROOT CAUSE: `newest()` (`organism.py` lines 735–737) passes one lesson, and that lesson was now the Notepad lesson. Confidence high. OBSERVED. The glass still had the card. Shot `202813-442c32c6333947408e90ffd3379e257a.png`, the picture on the eye's first user turn at 20:28:13, shows 5°C, precipitation 0%, wind 6 km/h, Bezchmurnie, and Tuesday 20:00. File Explorer is not the front window. The prompt forgot the card. The desktop had not.

What it did remember from the previous wake: think at line 35, "FARA section already shows terminated with a left-click at coordinate [283, 66]", and line 39, "The inherited story calls for opening Notepad, but the bottom sentence overrides it." Line 441: "Notepad is missing because the previous wake failed to type the sentence." The click it names is `[283, 66]`, which is in the prompt's FARA section (`prompt.txt` lines 25–44). The later repeated coordinate `[283, 230]` (`prompt.txt` lines 70–164, twenty-odd times) is not repeated as a number in this run (zero hits). The fact was in the prompt. The chat cited the first click and the untyped sentence, and did not cite the coordinate the operator's 20:25 commit was about. OBSERVED.

Wrote one story, `story_len` 38 at 20:31:04 (line 513), which is the current `story.txt`: `1. Open File Explorer on this folder.` `story.meta.json` names run `20261027_202606`. One lesson, `wakes` 19, `lesson_len` 498 (line 517), identical to `ledger.md` lines 38–39 and to `ledger_next.txt`.

The eye terminate at 20:28:28 (line 294 of the run, and `fara_traj.json` turn 4): "Done — File Explorer is open to the folder you specified." `--windows` at 20:29:40 (line 376): `787422|This PC - File Explorer|[311, 43, 994, 845]`. The lesson says the title is This PC, not this folder.

Shot check. `fara_traj.json` names shot `202821-71296d903afc4ca782610f427a0b50cb` on the user turn after the click. `screen.json` points at that PNG. The picture was opened. It shows a File Explorer window titled "This PC", address bar "This PC", contents "Local Disk (C:)", not a folder named Trident. Behind it, Chrome's tab reads "Poland weather - Szukaj w Google". The lesson matches the picture on the title. This later frame does not show a readable "5°C" card, because Explorer covers it. The frame eight seconds earlier, `202813`, does, and it shows wind 6 km/h rather than the inherited 8. OBSERVED.

There is no later run. The newest lesson, the story, and the closed trajectory are what the next wake would be handed, and `newest()` would hide the other 18 lessons.

### Did the next run quote the previous lesson?

| Previous lesson | Next run quoted its distinctive words? | Evidence |
| --- | --- | --- |
| 133306 `mind.shed` | Yes | `141013` line 95 and five `mind.shed` hits |
| 141013 `Do not --place` | Yes, including across the dead `142947` | `143208`, seven hits |
| 142947 none written | The previous lesson stayed newest | no `142947` section in `ledger.md` |
| 143208 black line `1315fbb8` | No | zero hits in `151228` |
| 151228 `organism_lines=992` | In the next prompt, not in the next journal | stream `130358e1…` line 2; zero hits for `9823582` in `155202.md` |
| 155202 "no picture exists yet" | The next wake followed the window title instead | `160354` line 127 and line 135 |
| 160354 dashed box `163405` | Yes, as the prompt's `LEDGER` | embedded in `183727` line 57; `182210` wrote no replacement |
| 183727 hwnd `196850`, "Paint is not a key" | Yes, and the window was already closed | `190548` lines 19, 57, and 141 |
| 190548 `No lesson was written.` | The words yes; the stroke no | `191902` rebuilt x=356 from the run |
| 191902 arm `[302, 467]` | Yes, then treated as a finished figure the picture does not show | `194156` lines 51 and 75; shot `192556` |
| 194156 click `[500, 498]` | The coordinate no; "no drag strokes" yes | zero hits for `500, 498` in `195316`; line 51 quotes the ledger |
| 195316 "no record of an open display showing 4" | Yes | `200412` line 245, `shows 4` three times |
| 200412 `Bezchmurnie`, 5 C, wind 8 | The words yes; the wind digit does not match the screen | `201442` line 272; shot `200647` shows 6 km/h |
| 201442 sentence never typed | Yes; `[283, 230]` not restated | `202606` lines 39, 109, 441; zero hits for `283, 230` |
| 202606 This PC | No next run | lesson is `ledger.md` lines 38–39; shot matches |

## 3. Growth, memory, forgetting, inheritance

It is not growing a larger memory. It is replacing a small working set and keeping a ledger the next prompt only samples at the tail.

Working set of a new chat, from `render_prompt` (`organism.py` lines 796–806), confirmed by `prompt.txt` as the `202606` chat received it:

- The whole `story.txt`, flagged `inherited; not current progress` when the meta run is not this wake. One screen of plan. Replaced wholesale 41 times. The previous text is not appended.
- One ledger lesson, the last `## wake` block. Eighteen older lessons are on disk and are not in that slot. `190548` and `194156` read `ledger.md` with their own tools and therefore saw the older lessons. That was the chat choosing to open the file. The prompt did not hand them over. OBSERVED: `183727`'s embedded prompt contains one `## wake` block, while the ledger read 28 minutes later in `190548` contains twelve.
- A `LEARNED` line of run ids and heading counts. After `35863c0` the ids are all of them. Before that, `journals` kept five. The counts do not include the lesson prose.
- The FARA text log of whatever `fara_traj.json` holds at prompt build. A terminated log is wiped on the next `eye_steps` (`organism.py` lines 617–620). Images older than three shots never enter the eye's next request (`organism.py` line 502).

What was remembered, with two sources each.

- `mind.shed` would delete the live session. Lesson `ledger.md` lines 11–12, and `141013` line 95.
- Do not `--place`, and v2 was the body. Lesson `ledger.md` lines 14–15, and `143208` ("v2 is the body", "Do not --place").
- The Imagine prompt string. Eye answer `155202` line 455, and the Chrome window title `160354` line 135.
- Paint hwnd `196850`, and a word is not a key. Lesson `ledger.md` lines 24–25, and `190548` lines 19 and 57. The same wake then says the window has closed (line 141). The number survived. The window did not.
- The figure's coordinates, under names the picture does not support. Lesson `ledger.md` lines 28–29, and the ledger read copied into `194156` line 75. Shot `192556` shows strokes and not the named head and two horizontal arms. `194156` line 51 calls that record a painted figure.
- Calculator never showed 4. Lesson `ledger.md` lines 32–33, `200412` line 245, shot `195907` (display 2), and shot `200647` (display 1, history `2 + 2 =`).
- Weather card "5 C" and "Bezchmurnie", as her claim, with no second look, and with wind copied as 8. Eye line `200412` line 326, and `201442` lines 270–272. Shots `200647` and `202813` show the same card with wind 6 km/h.
- The Notepad sentence was not typed. Lesson `ledger.md` lines 36–37, and `202606` line 441.
- File Explorer came up as This PC. Lesson `ledger.md` lines 38–39, `--windows` at `202606` line 376, and shot `202821-71296d903afc4ca782610f427a0b50cb.png`.

What was forgotten.

- Wake `20261026_120416`. Full text, from the `previous` field of the last `ledger.history.jsonl` record (the 15:43:39 snapshot), and from no line of `ledger.md`: "When fewer than four minutes remain in a 20-minute wake, do not start brushes, strokes, or control learning. At 12:22:43 this wake still ended at 12:24. --windows showed PowerShell hwnd 262984 box [-5, 0, 304, 949] on the left, foreground, and Untitled - Paint hwnd 722004 box [297, 0, 1000, 944] on the right. Neither was clicked or moved. No capture was taken, so the cat, mountains, and house were not painted." Zero of the 16 runs name `120416`. See the chain in section 4.
- The black-line id `1315fbb8` as a working fact. Still in `ledger.md` lines 16–17. Zero hits in the next run.
- The four article sentences and `download.jpg`. Last seen in the `story_wake.txt` diff inside `183727` line 139. Not in any later lesson. The next prompt's `LEDGER` was the figure lesson, which says "Do not continue the article" and does not keep the sentences.
- The `190548` stroke to `[367, 252]`. In the run report, line 1149. Replaced, as the inheritable lesson, by `No lesson was written.`
- `[500, 498]` as a number. In `ledger.md` line 31, and the lesson even cuts the series at 19:48:38 while the run continues to 19:51:57. Zero hits in `195316`. The no-drag sentence was not forgotten. Line 51 of that run quotes it.
- "Bezchmurnie" and 5°C as words in the next prompt after `201442`. Absent from `prompt.txt`'s `LEDGER` and from `202606`. Still on the screen at 20:28:13 in shot `202813`, with wind 6 km/h. The prompt forgot them. The desktop had not. The inherited wind 8 was never on either of those frames.
- `[283, 230]`, the coordinate the 20:25 operator commit is about. Filling `prompt.txt` lines 70–164. Zero hits in `202606`'s own prose. The chat quoted `[283, 66]` instead.
- Within one eye session, every screenshot except the last three (`organism.py` line 502).
- Any story body except the last write. Forty of the 41 story headings survive only as `story_len`.

`goal.txt` is overwritten at each `drive` start (`organism.py` lines 919–920). It is now the File Explorer sentence. `sentence.txt` is still the 15:12 rewrite order. Nothing in `render_prompt` reads `sentence.txt`. It is a fossil. SINGLE-SOURCE: the file itself.

Git grew. The organism's prose memory did not. `organism.py` went from a split tree (`141013`'s "v2 is the body") to 992 lines at 15:42 (`151228`) to 1044 lines now. The extra lines are the eye thread, the uncut journal, `newest` still returning one lesson, and the repeat-click note. Those lines were committed by the operator between wakes, except the 15:42 collapse, which the `151228` run and commit message both describe.

## 4. Symptom / root-cause chains

### Chain A. The oldest lesson disappeared

SYMPTOM. `ledger.md` line 1 is `## wake 20261026_122610`. There is no `20261026_120416`. A search of all 16 run files finds no `120416`.

ROOT CAUSE. `_enter` deleted index 0 while the sum of lesson parts exceeded 8000 characters or the count exceeded 12. The loop is `memory.py` lines 107–108 at `534ce27`, and `organism.py` lines 715–716 from `9823582` through `b142051`. It was removed at `35863c0` (18:36:20), which is after the loss and does not put the lesson back.

The step that fired. History line 35 (15:43:39) still holds `120416` as the oldest of 10 parts, sum of lengths 6777. `155202` at 15:59:40 reports `wakes` 11 (`run_20261027_155202.md` line 415), which is 10 plus itself, so that write did not drop. `160354` at 16:09:32 reports `wakes` 11 (`run_20261027_160354.md` line 303), not 12. One lesson was removed. The oldest was `120416`. The next full ledger read, `190548` line 57, starts at `122610`.

INFERRED at the identity of the dropped lesson, confidence high. OBSERVED at the cap, the two `wakes` counts, the history snapshot, and the absence.

Where it does not break: the archive file still has the text. The organism never reads `ledger.history.jsonl` at HEAD. A search of `organism.py` finds no `ledger.history`. The archive is not inheritance.

### Chain B. A wake's only inheritable sentence became "No lesson was written."

SYMPTOM. `190548` dragged to `[367, 252]` and recorded a blank canvas (`run_20261027_190548.md` line 1149). The next prompt's lesson slot for that wake is `No lesson was written.` (`ledger.md` line 27). `191902` had to re-read the run to learn that the strokes shared x=356.

ROOT CAUSE. `drive`'s `finally` (`organism.py` line 979) writes `current_lesson`. `190548` has no `ledger` heading (`prompt.txt` `MEASURED`: `story` 2, no `ledger`). `current_lesson` returns `''`. Line 739 fills `No lesson was written.`

`182210` and `142947` do not have even that sentence, because their logs have no `mind` heading and `finally` did not run. Their facts survive only in the run file, and only if a later chat opens it. `183727` did open `182210`. Nothing opened `142947` for a lesson, because there was none to open.

### Chain C. The next chat sees one lesson, so a remembered fact dies one wake later

SYMPTOM. `201442` quotes "Bezchmurnie" and 5 C from `200412` (lines 270–272). `202606` does not contain those words. Its prompt's `LEDGER` is the Notepad lesson (`prompt.txt` lines 167–169).

ROOT CAUSE. `newest()` returns `parts[-1]` (`organism.py` lines 735–737). `render_prompt` line 806 puts that string, and not the rest of `ledger.md`, under `LEDGER`. The weather lesson is `ledger.md` lines 34–35, two wakes back by the time `202606` starts. `LEARNED` names the run id and not the prose.

This is not a deletion from disk. It is a deletion from the prompt. A chat that reads `ledger.md` itself, as `190548` and `194156` did, can still see it. `202606` did not.

The same mechanism drops the weather prose between `201442` and `202606`. It does not drop `[500, 498]` in the same way: that coordinate is inside the newest lesson when `195316` starts, the run never quotes the number, and line 51 does quote "no drag strokes were recorded." The `1315fbb8` id is absent from `151228`'s speech. The `992` checklist was in `155202`'s user prompt (stream line 2) and is absent from that wake's markdown. The journal is the loss. The prompt was not.

### Chain D. Story and trajectory are replaced, not extended

SYMPTOM. `story.txt` is one line, the File Explorer plan. Forty earlier story bodies are gone except as lengths and as a few scratch files. `fara_traj.json` is four turns about File Explorer, `open` false. The Notepad clicks that filled the `202606` prompt are not in the file now.

ROOT CAUSE. `story_run` writes the new text over `story.txt` (`organism.py` lines 763–768). `eye_watch` calls `eye_steps(..., fresh=True)` (`organism.py` line 671), and a closed trajectory is replaced even when `fresh` is false (lines 619–620). `ask` keeps three images (line 502).

`202606` knew this. Line 409: "The next wake may inherit the wrong plan if the notepad line remains in the story." It rewrote the story to the File Explorer line so the next prompt would not keep telling the notepad plan. That is the organism passing on a correction, and throwing away the previous plan to do it.

### Chain E. The eye's "done" is inherited as a claim, and the picture sometimes agrees

SYMPTOM. `202606` was told the folder was open (`fara_traj.json`, terminate answer "Done — File Explorer is open to the folder you specified."). The lesson refuses that claim.

ROOT CAUSE is not a memory bug. The windows heading at 20:29:40 and the opened PNG both show "This PC". The lesson copied the window title and not the terminate answer. `organism.py` lines 22–25 tell the mind that a terminate answer is her claim. This wake followed that line. The inheritance matches the picture.

The weather card does have a picture. Shot `200647` at 20:06:47, before the terminate, and shot `202813` at 20:28:13, during the wake that no longer had the words in its prompt, both show 5°C, 0%, Bezchmurnie, Tuesday 20:00, and wind 6 km/h. Shot `202821` hides the card behind File Explorer and only leaves the Chrome tab title. Confidence that she said 8: high, from line 326 and from `201442` line 272. Confidence that the screen showed 8: none. The two opened frames show 6. The organism recorded "no second look", so it passed the wrong integer on.

## 5. Byte-count appendix

Sizes are the file lengths at the time of the full read. Line counts are newline-separated records, with a last partial line counted.

### Code and git-facing files

| Bytes | Lines | File |
| ---: | ---: | --- |
| 65412 | 1044 | `organism.py` (lines 1–1044) |
| 123 | 3 | `main.py` |
| 46 | 1 | `.gitignore` |
| 0 | 0 | `skills\.gitkeep` |

`edit_log.txt`: absent.

Git objects read as text, not working-tree files: `git log --format=fuller -15` (commits `1900589` back through `9823582`, full messages); `memory.py` lines 1–140 and `evidence.py` lines 100–190 at `534ce27`; `organism.py` ranges covering `journals`, `newest`, `_enter`, `heading`, and `eye_log` at `9823582`, `b142051`, `ec3a1d5`, `0bad528`, `35863c0`. `git status` and `git rev-parse HEAD`.

### Ledger, story, prompt, trajectory, scratch

| Bytes | Lines | File |
| ---: | ---: | --- |
| 12147 | 39 | `ledger.md` |
| 156925 | 35 | `ledger.history.jsonl` |
| 38 | 1 | `story.txt` |
| 244 | 5 | `campaign.txt` |
| 284 | 1 | `goal.txt` |
| 16801 | 200 | `prompt.txt` |
| 1343 | 1 | `fara_traj.json` |
| 198 | 1 | `eye_goal.txt` |
| 38 | 1 | `hand_goal.txt` |
| 231 | 4 | `plan_story.txt` |
| 73 | 1 | `wake_story.txt` |
| 159 | 1 | `story.meta.json` |
| 38 | 1 | `story_next.txt` |
| 199 | 1 | `story_now.txt` |
| 1215 | 13 | `story_this.txt` |
| 132 | 4 | `story_wake.txt` |
| 499 | 1 | `ledger_next.txt` |
| 760 | 1 | `ledger_now.txt` |
| 701 | 1 | `ledger_this.txt` |
| 638 | 1 | `ledger_wake.txt` |
| 758 | 1 | `wake_lesson.txt` |
| 623 | 1 | `wake_ledger.txt` |
| 308 | 1 | `eye_ask.txt` |
| 34 | 1 | `eye_now.txt` |
| 73 | 1 | `eye_sentence.txt` |
| 39 | 1 | `hand.txt` |
| 42 | 1 | `hand_now.txt` |
| 44 | 1 | `wake_eye.txt` |
| 38 | 1 | `wake_hand.txt` |
| 35 | 1 | `current_sentence.txt` |
| 930 | 1 | `sentence.txt` |
| 63 | 1 | `journal.path` |
| 1784 | 1 | `screen.json` |
| 4 | 1 | `region.txt` |

### Runs

| Bytes | Lines | File |
| ---: | ---: | --- |
| 653031 | 3220 | `run_20261027_133306.md` |
| 183382 | 1251 | `run_20261027_141013.md` |
| 26575 | 170 | `run_20261027_142947.md` |
| 715069 | 3232 | `run_20261027_143208.md` |
| 539109 | 2573 | `run_20261027_151228.md` |
| 9114 | 500 | `run_20261027_155202.md` |
| 26017 | 2044 | `run_20261027_160354.md` |
| 3856 | 236 | `run_20261027_182210.md` |
| 173499 | 588 | `run_20261027_183727.md` |
| 307577 | 1156 | `run_20261027_190548.md` |
| 185642 | 949 | `run_20261027_191902.md` |
| 174843 | 877 | `run_20261027_194156.md` |
| 176476 | 823 | `run_20261027_195316.md` |
| 142048 | 598 | `run_20261027_200412.md` |
| 310720 | 889 | `run_20261027_201442.md` |
| 136548 | 608 | `run_20261027_202606.md` |

### Shot opened

| Bytes | File | When |
| ---: | --- | --- |
| 275663 | `shots\202821-71296d903afc4ca782610f427a0b50cb.png` | first pass |
| 296592 | `shots\202813-442c32c6333947408e90ffd3379e257a.png` | consultation |
| 306785 | `shots\200647-44041906988d464e834b82c5e6645a4e.png` | consultation |
| 323623 | `shots\191351-6c2a3d897c3b4f58a16224b662e56184.png` | consultation |
| 332125 | `shots\192556-8bca99829c924677a4256c9d5bf682dc.png` | consultation |
| 3000633 | `shots\194450-face4b5b90684a5482fbd1046f67f7ad.png` | consultation |
| 335550 | `shots\195907-9f2021b927b94a50ac573238545398cb.png` | consultation |

### Consultation pass, 27 October 2026

These four reports were read in full. Line counts are the last line the reader returned.

| Bytes | Lines | File |
| ---: | ---: | --- |
| 62041 | 521 | `reports\01-systems.md` |
| 68163 | 460 | `reports\02-prompt.md` |
| 63401 | 721 | `reports\03-behavior.md` |
| 54689 | 560 | `reports\04-cost.md` |

Also opened in this pass, not in full:

- `run_20261027_141013.jsonl` (5547 lines, 1445020 bytes). The first line and the `chat_end` / `wake_end` lines were read. The other lines were not.
- `streams\130358e138e64b239689d2912068f9c5.stream` (586240 bytes). Line 2 and line 256 were read. The other lines were searched for `9823582` and `organism_lines` and not read as prose.
- `ledger.md` was counted again: 19 lines beginning `## wake `, 12147 bytes, 39 lines.
- `run_20261027_190548.md`, `run_20261027_194156.md`, `run_20261027_195316.md`, `run_20261027_201442.md`, and `run_20261027_202606.md` were searched again for the needles in section 6. The hit lines were read. The files were not re-read from line 1.

No public page was fetched in this pass.

Not read, and not used as evidence: `wake_traj_summary.py`, the `actions\` JSON tree, and `download.jpg`. The rest of `streams\` was not opened.

## 6. Consultation

The four reports were read on 27 October 2026. No page was fetched. Where a report says a fact was learned, forgotten, or passed on, the next run and `ledger.md` were checked again. A disagreement that survived that check is kept. A sentence in sections 2–5 that this pass changed is listed under corrections.

### Agreements

- `newest()` hands the next chat one lesson. Report 02 section 2 quotes the same function. `190548` and `194156` saw older lessons only because they opened `ledger.md`. `202606` did not, and its prose has no `Bezchmurnie`.
- `190548` stored `No lesson was written.` (`ledger.md` line 27). Report 01 section 2.10, report 02 section 2, report 03 section 2.10, and report 04 section 2.10 say the same. `191902` rebuilt the drawing from the run. The stroke to `[367, 252]` is not in the lesson. Shot `191351` shows one diagonal line, so the five-part figure claimed at 19:13:29 was not passed on either.
- The 19:44:00 figure claim is false. Shot `194450` is a coastline wallpaper. Ledger line 31 says no drag was recorded. `195316` line 51 still has that sentence.
- The 19:58:17 claim of a displayed 4 is false. Shot `195907` shows 2. Ledger lines 32–33 say there is no record of an open display showing 4. `200412` line 245 still has the calculator story. Shot `200647`, during that next wake, shows display 1 and history `2 + 2 =`.
- File Explorer at 20:28 is This PC. Shot `202821`, `202606` line 376, and ledger lines 38–39 agree. Reports 01, 02, and 03 describe the same frame.
- `142947` and `182210` left no ledger row. Report 01 sections 2.3 and 2.8 give the jsonl and the stream as the second store. This report had called the `142947` ending SINGLE-SOURCE from the markdown. That label stays on this report's own reading: this pass did not re-open `run_20261027_142947.jsonl`.
- The journal of `155202`, `160354`, and `182210` stores `chars=N`. Report 04 section 4.6 shows the matching streams still hold the thinking text (22,972 characters for the first `155202` stream; 19,444 for `182210`). `render_prompt` does not paste that body into the next prompt. Truncation did not save tokens, and it did remove the speech from the file the next wake is told to read.
- `120416` is not discussed in any of the four reports. The loss in section 4, chain A, is unchanged. Ledger line 1 is still `20261026_122610`. A search of `190548` still finds `122610` and not `120416`.

### Disagreements kept

1. How many lessons are on disk. Report 02, in the cognitive-load paragraph of section 4, says `ledger.md` "has 20 wake headings and 12,147 bytes." The file is 12,147 bytes and 39 lines. The string `## wake ` occurs 19 times. The same count is in section 1 of this report. OBSERVED. Their byte size matches, so this is a miscount of one file, not a second ledger.

2. What ledger line 26 says. Report 03 section 2.9 says "Ledger line 26, for the following wake, says Paint was absent." Line 26 is `## wake 20261027_190548 ... deadline`. Line 27 is `No lesson was written.` The sentence "Paint was absent" is line 25, the previous wake's lesson, and the same line then says Untitled - Paint hwnd `196850` was listed at 18:41:31. The following wake's lesson does not mention Paint. OBSERVED.

3. Whether `195316` used the lesson "A click is not a stroke." Report 02, chain G, says the next wake sees that lesson and "uses that distrust." `run_20261027_195316.md` has zero hits for `500, 498` and zero hits for `A click is not a stroke`. Line 51 does say "the ledger shows no drag strokes were recorded." Line 523 says "That terminate answer is her claim," which is the standing prompt, not ledger line 31. The no-drag fact was passed on. The coordinate, the phrase, and the lesson's "Do not start Calculator" were not what the next sentence followed. The next sentence was Calculator. Partial agreement, and the flat "uses that distrust" is not kept.

4. How many clicks at `[500, 498]`. This pass counted 17 `action=` lines, from 19:47:15 through 19:51:57. Report 02 lists the same 17 timestamps. Report 04 section 2.12 counts 17 bodies. Report 01 section 2.12 says 16 further after the first, which is 17. Report 03 section 2.12 says "From 19:47 the primary record is 19 `left_click` actions." The run's primary `left_click` total is 19 because two earlier clicks, at 19:46, are `[111, 972]` and `[149, 542]` (ledger line 31). Those are not the repeated point. The 19-from-19:47 sentence is not kept.

5. Whether the typed word `Notepad` is SINGLE-SOURCE. Report 03 section 2.15 marks it so, because the think at its line 462 is hedged. The say at `run_20261027_201442.md` line 870 says "typed the text `Notepad` twice." The trajectory dump in that run contains `text='Notepad'`. Ledger line 37 says "Two type actions used the text Notepad." Three statements, two files. The hedge is real and earlier. The string is not single-source.

6. What `202606` forgot about the weather. This report's first draft said the card was a text chain only, because shot `202821` does not show a readable 5°C. Reports 01 section 2.16 and 02 section 2 open shot `202813` and describe 5°C, Bezchmurnie, and wind 6 km/h. This pass opened that file and shot `200647`. The words left the prompt. The card was on the glass at 20:06:47 and still at 20:28:13. Wind on both frames is 6 km/h. Ledger line 35 and `201442` line 272 say 8. The passed-on integer is wrong. Report 02 says it will not call 8 a hallucination because 21 minutes had passed. Shot `200647` is seventeen seconds before the answer, so the delay is not available as the cause. Report 03 section 4.4 calls it a narrow confabulation and stops there. That stop is right: the files do not name a model mechanism.

7. Whether the inherited figure was a figure. This report's first draft listed the arm and leg coordinates as a remembered fact and did not open a picture. Shot `192556` shows strokes and not the named head or two horizontal arms. `194156` lines 51 and 105 still say the previous wake painted the figure. The coordinates were inherited. The completed figure was not on the canvas. Report 03 section 3.3 calls that anchoring. The quotes support the label. INFERRED, confidence high.

8. How many chats `151228` had. This report counts two `start` headings, at 15:12:34 and 15:30:09. Report 01 section 2.5 and report 04 section 2.5 say a third chat ended `failed_agent` with `completed_calls` 0, about 0.16 seconds after the second rotate, because `v2\job_boot.py` was gone. This pass did not re-open `run_20261027_151228.jsonl`. The third process stays their observation. The table in section 1 now says "2 `start` headings" so it does not pretend to count that process.

9. Report 02 section 2.8 flags that `182210` says "FARA" and "inherited story" at 18:22, while commit `b142051` is 18:33:08. This report quoted the truncated say and did not pin those words to that commit. The timing warning stands. The story body of 129 bytes is a separate fact: `183727` reads it back with provenance `20261027_182210`. That inheritance does not depend on which commit held the word "FARA".

10. Report 03 section 3.11 quotes `organism.py` line 453, "A word is not a key," as the schema the eye received, and pairs it with the `183727` failure. That sentence was added at `0b353b9` (19:18:51). Report 02 section 3 says the same, and says the 18:39 failure is before the description existed. The later wakes `195316` and `200412` do use `type` for words. The schema line in today's file is not the schema `183727` loaded. OBSERVED from the commit time and the wake start. This is a disagreement with 03's pairing, and an agreement with 02.

### Corrections to this report

- Section 1 table, `141013` chats. Was "1". The jsonl has `chat_end` for `:1` (`rotate`, 42 calls) and `:2` (`failed_agent`, 0 calls). The markdown still has one `start` heading. Changed because the process count and the heading count are different events.
- Section 2.2. Added the shed line. The `133306` lesson warned that `mind.shed` would delete `streams` and `shots`. This wake's first jsonl line says it removed them. The warning was inherited. The pictures were not. The lesson written afterward says not to do it again.
- Section 2.5, the next-wake paragraph. Was: the prompt was overwritten later, so it is unknown whether `155202` was shown the `992` checklist, and the markdown does not quote it. The stream's user message, line 2, contains the checklist and `Commit 9823582`. The hash is on no thinking line. The markdown still has zero hits. Changed because "not shown" and "not in the journal" had been collapsed into one claim.
- Section 2.10. Added line 141. The hwnd was remembered, and the mind recorded that the window had closed.
- Section 2.11. Was: no picture was opened, so the inheritance is text. Shot `192556` was opened. The coordinates match a set of strokes. The part names do not match the picture. Ledger line 29 also omits the words "The figure is visible," which `194156` line 41 still has from the trajectory.
- Section 2.12. Was: the next run did not quote the lesson. It did not quote `[500, 498]` or "A click is not a stroke." It did quote "no drag strokes were recorded" (line 51). The lesson's own interval ends at 19:48:38. The run has 17 clicks through 19:51:57. Shot `194450` shows the 19:44 claim against a wallpaper.
- Section 2.13. Was: the `194156` lesson was not quoted. Replaced with the split in 2.12. Added shot `195907`, display 2.
- Section 2.14 and 2.15. Was: the weather fact was remembered. Split. 5°C, 0%, Bezchmurnie, and Tuesday 20:00 match shots `200647` and `202813`. Wind 8 does not. Those shots show 6 km/h, the first of them before the terminate.
- Section 2.16 and chain E. Was: the card has no picture, so inheritance is text only. Shot `202813` is the picture, taken during the wake whose prompt and journal lack the words. Shot `202821` still hides the card. The prompt forgot it. The desktop had not.
- Section 3, the remembered and forgotten lists, and the quote table. The same splits, so the list no longer calls the wind digit and the finished figure clean memories.
- Chain C. Was: `195316` lost `[500, 498]` the way `202606` lost the weather words. The coordinate was in the newest lesson and was not spoken. The no-drag sentence was spoken. The weather words were absent from the prompt and present on the screen.
- Section 5. The six consultation shots, the four reports, and the partial jsonl and stream reads are now in the appendix. The sentence that said `202813` was not opened is removed.
