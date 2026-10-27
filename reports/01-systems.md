# 01 — Systems and runtime

Date of review: Tuesday 27 October 2026. Workspace `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. Branch `runner-h`. HEAD `190058900aaafd8e2a8bdb6bbdc9b3dc45671a11` (committed 20:25:48 +0100).

A **wake** is one process started with `--mind-file`. A **chat** is one Cursor agent process inside that wake. A **run** is the markdown file `run_<id>.md` for that wake, plus, for the five wakes before 15:52, a sidecar `run_<id>.jsonl`. After the body was collapsed, the process stream is `streams\<uuid>.stream` and the sidecar jsonl is not written.

## 1. Scope and method

Read in full, as source text: `organism.py` (1044 lines, 65412 bytes), `main.py` (3 lines, 123 bytes), `.gitignore`, `goal.txt`, `campaign.txt`, `story.txt`, `ledger.md`, `prompt.txt`, `fara_traj.json`, `screen.json`, `story.meta.json`, `skills\.gitkeep`. `edit_log.txt` is not in the tree (search of the workspace returned no path; a direct existence check is false).

Every line of the 16 run markdowns, the 5 jsonl files (33166 lines, 0 JSON decode failures), and the 29 files in `streams\` was passed through a sequential line reader. That pass recorded every markdown heading, every jsonl `kind`, and every stream event `type`. Process-event text was then read directly. Entire small journals read as prose: `run_20261027_182210.md` (236 lines) and the open and close of `run_20261027_142947.md`. Quoted spans below were read from the files, not reconstructed.

`git log --format=fuller` was taken for the last 8 commits and for the earlier commits a claim depends on. `git show` of those 8 commits was read, including the extended messages. `git status` was taken on `runner-h`.

Six PNGs named by a run or by `fara_traj.json` / `screen.json` were opened and described from the bitmap in the first pass. Section 6 opened a seventh, `shots\200647-44041906988d464e834b82c5e6645a4e.png`, because another report cited the wind number on that frame. Every other shot id named in a run markdown was checked for presence and byte size. Those pictures were not opened. Missing files are marked missing. A model's caption in a journal is not treated as the picture.

Public pages were retrieved on 27 October 2026. No source was invented. Where a search result did not contain a number the code uses, the number is cited from the code and the page is cited only for the behavior it actually states.

`git status` (observed):

```
On branch runner-h
Your branch is up to date with 'origin/runner-h'.
Untracked files:
	wake_traj_summary.py
```

Nothing staged. HEAD is the commit named above.

## 2. Timeline

Heading counts are markdown headings matched by `^## HH:MM:SS `. Times are local (+0100), taken from the heading text. "Process events" are spawn, rotate, kill, deadline, and stop, as written in that file and, where it exists, the jsonl.

The five jsonl files are not Cursor `stream-json`. Each line is an object with `at`, `run`, `chat`, `kind`, `payload`. The process stdout is a separate file named by `kind: agent_stream`. Eleven of the sixteen runs have no `run_*.jsonl` at all. Their process streams are the `.stream` files whose last-write time matches the run's last heading.

### 2.1 `run_20261027_133306`

- Bytes 653031, lines 3220, headings 316. First `## 13:33:12 start` (line 1). Last `## 14:06:04 exit` (line 3217).
- Kinds: start 4, request 145, exit 43, think 66, say 14, eye 12, hands 3, capture 4, story 10, ledger 8, stop 1, report 1, mind 1, windows 1, region 1, skill 2.
- Jsonl: 5313837 bytes, 9961 lines, 0 bad. Kinds include `wake_start` 1, `chat_prompt` 4, `agent_stream` 4, `agent` 9850, `chat_end` 4, `agent_exit` 4, `wake_end` 1. Model on all four prompts: `grok-4.7-xhigh-fast`.
- Epochs in jsonl line 1: start `1793104386.2582095`, end `1793106786.2582095`. Difference 2400 seconds, 40 minutes. The mind line prints deadline `14:13:06`, which is 13:33:06 plus 40 minutes.
- Process: four chats. Jsonl line 3444 `chat_end` reason `rotate`, `completed_calls` 42. Line 6166 `chat_end` reason `rotate`, `completed_calls` 43, `running_tools` 0. Line 8137 rotate at 42. Line 9958 `stopped` at 18 completed calls. Each rotate `agent_exit` has `returncode` 1. The stop exit has `returncode` 0.
- Stop heading, line 3157, body: `{"stopped": true, "clock": {"now": "14:05:41", "used": "32.6 min", "used_seconds": 1955.317, "remaining_seconds": 444.683, "left": "7.4 min", "deadline": "14:13:06"}}`.
- Mind, line 3213: `{"state": "stopped", "minutes": 33.0, ... "left": "7.0 min", "deadline": "14:13:06"}`.
- All four `agent_stream` paths named in the jsonl are absent on disk (`b6b060e27b2841adb5a0d5ca01b5f7ff.stream` and three others). Seven shot ids in the markdown are also absent. The next wake's jsonl line 1 records why (section 2.2).
- Finding: the 42-call rotate is already a hard process boundary, and it is not a hard cap of 42. Chat 2 rotated with 43 completed calls while `running_tools` was 0. The wake then stopped itself with 444 seconds left, because the prompt's 20 percent line had passed. The process deadline was 14:13:06. The process did not kill the wake at that line; the model called `--stop`.

### 2.2 `run_20261027_141013`

- Bytes 183382, lines 1251, headings 67. First `## 14:10:18 start` (line 1). Last `## 14:24:33 exit` (line 1248).
- Kinds: start 1, request 42, think 15, say 2, story 1, ledger 1, exit 3, report 1, mind 1.
- Jsonl: 1445020 bytes, 5547 lines. Line 1 `shed`: `{"removed": ["agent/config/chats", "agent/data/projects", "streams", "shots", "actions"]}`. That is the deletion of the previous wake's stream files and shots.
- Chat 1 stream `d5cd315a8aec4383957db3b3f2c544bd.stream` (1278033 bytes, mtime 14:24:33): 42 tool starts, 42 completions, no `result` event. Jsonl line 5539: rotate, `completed_calls` 42, then `agent_exit` `returncode` 1.
- Chat 2 stream `3e8984ce791c4490be2070da33d1170a.stream` (183 bytes, mtime 14:24:33), the entire file:

```
C:\Users\eb-wjt\AppData\Local\Python\pythoncore-3.11-64\python.exe: can't open file 'C:\\Users\\eb-wjt\\Downloads\\Jarvis\\Trident\\job_boot.py': [Errno 2] No such file or directory
```

- Jsonl line 5543 `agent_raw` stores that same string. Line 5544: `chat_end` reason `failed_agent`, `completed_calls` 0. Line 5545: `agent_exit` `returncode` 2. Line 5546 `wake_end` reason `failed_agent`. The usage object says `'money': 'not metered'`.
- Mind, line 1244: `{"state": "failed_agent", "minutes": 14.3, ... "left": "25.7 min", "deadline": "14:50:13"}`.
- Finding: chat 1 booted, so `job_boot.py` existed at 14:10. Chat 2, spawned on rotate at 14:24:33, could not open it. The non-zero exit ended the 40-minute wake with 25.7 minutes left. No `err` heading carries the missing-file text. The text exists only in the 183-byte stream and in jsonl `agent_raw`.

### 2.3 `run_20261027_142947`

- Bytes 26575, lines 170, headings 17. First `## 14:29:51 start` (line 1). Last `## 14:31:19 request grep` (line 167).
- Kinds: start 1, think 4, say 1, request 7, skill 1, windows 1, exit 2.
- No `mind`, no `report`, no `stop`. Jsonl: 170471 bytes, 577 lines. Kinds stop at `agent` and `component`. There is no `chat_end`, no `agent_exit`, no `wake_end`.
- The last heading's body is a raw stream event with `"subtype": "started"` for a grep, `timestamp_ms` 1793107878996. There is no matching completed heading after it.
- Stream `0b10ead822cc4433946996d2ad935fa5.stream` (143326 bytes, mtime 14:31:19): 7 starts, 7 completions, `result` is absent. Session `7e8ab919-837f-4326-81a2-24620d586bb3`, which is the `session_id` in the last markdown heading.
- The think at line 13 says the wake lasts 40 minutes until 15:09:47. The file ends at 14:31:19. The next wake's first heading is 14:32:12.
- Finding: the wake process stopped writing both the markdown and the jsonl without the seal every sibling jsonl has (`wake_end`). The agent stream has no `result` event. Observed abandonment, 38 minutes before the deadline named in the think text. The killer is not in the log. INFERRED external termination (confidence medium). The missing seal is observed (confidence high).

### 2.4 `run_20261027_143208`

- Bytes 715069, lines 3232, headings 476. First `## 14:32:12 start` (line 1). Last `## 15:05:06 exit` (line 3229).
- Kinds: start 4, request 139, exit 97, think 126, eye 52, hands 12, capture 8, region 8, windows 4, story 5, ledger 4, stop 1, report 1, mind 1, say 12, skill 2.
- Jsonl: 2174503 bytes, 6046 lines. Four chats, three rotates at `completed_calls` 42 (`returncode` 1), then `stopped` at 13 calls (`returncode` 0). One `component_error`: `region is NONE or left,top,right,bottom on 0-1000` (jsonl line 307).
- Stop, from the heading body at 15:04:58: `left` `7.2 min`, deadline `15:12:08`. Mind: `{"state": "stopped", "minutes": 33.0, ... "deadline": "15:12:08"}`.
- Streams, by mtime: `a9ba1244…` 14:41:24 (42/42), `a1395e42…` 14:52:43 (42/42), `2bcbdcb4…` 15:00:05 (42/42), `7e394b3b…` 15:05:06 (13/13, `result` subtype `success`).
- Shot `150321-5f365ccfaf8544258a485d6f540508ae.png` (136139 bytes), named in the markdown, was opened. On screen: Untitled - Paint, brush tool selected, a light-blue canvas, a faint dark horizontal mark, no figure. The journal's own report says Fara called that mark a black line and that it is not a cat, mountains, and a house. The bitmap matches "not a painting." It does not, by itself, prove the stroke's coordinates.
- Twenty shot ids in this run resolve to files (10453 to 141252 bytes). Seven ids from 133306 do not.

### 2.5 `run_20261027_151228`

- Bytes 539109, lines 2573, headings 141. First `## 15:12:34 start` (line 1). Last `## 15:43:39 exit` (line 2570).
- Kinds: start 2, request 84, exit 3, think 39, say 6, map 1, story 1, ledger 1, report 1, mind 1, skill 2.
- Jsonl: 2857609 bytes, 11035 lines. Three chats. Chat 1 rotate at 42 (line 6303). Chat 2 rotate at 42 (line 11027) at `at` 1793112219.2943015. Chat 3 `chat_end` 0.16 seconds later, `failed_agent`, `completed_calls` 0, `returncode` 2 (lines 11032–11033). `wake_end` reason `failed_agent`. Again `'money': 'not metered'`.
- Jsonl line 11031 `agent_raw`, and the whole of `streams\0c5080e15dbb436b8fcf2d0d23df1a58.stream` (187 bytes, mtime 15:43:39):

```
C:\Users\eb-wjt\AppData\Local\Python\pythoncore-3.11-64\python.exe: can't open file 'C:\\Users\\eb-wjt\\Downloads\\Jarvis\\Trident\\v2\\job_boot.py': [Errno 2] No such file or directory
```

- Mind, line 2566: `{"state": "failed_agent", "minutes": 31.2, ... "remaining_seconds": 529.227, "left": "8.8 min", "deadline": "15:52:28"}`.
- Report, lines 2553–2554: `GET /v1/models on that server returned fara-1.5-4b with alias retina. Port 8765 returned service trident-journal. That process still reported its code path as v2 because it was already running.` Line 2562: `GET http://127.0.0.1:8765/identity returned 200 and service trident-journal.`
- Chat 1 stream `401b0c3c…` (2488082 bytes): tool_call started 43, completed 42, plus one `interaction_query` request and one response. The jsonl counter for that chat is `completed_calls` 42. The extra start is in the file and was not a 43rd completion.
- Finding: the same rotate-then-missing-launcher failure as 141013, with a different path (`v2\job_boot.py`). The wake had 529 seconds left. The journal listener on port 8765 was a process that had been started from the old tree and was not replaced.

### 2.6 `run_20261027_155202`

- Bytes 9114, lines 500, headings 125. First `## 15:52:02 wake` (line 1), body `minutes=40.0`. Last `## 16:02:17 mind` (line 497).
- No jsonl. This is the first run whose headings are the current vocabulary: `wake`, `chat`, `agent-exit`, `tool`.
- Kinds: wake 1, chat 2, start 1, agent-exit 2, tool 42, eye 18, hands 9, capture 9, windows 1, story 1, ledger 1, say 3, think 33, err 1, mind 1.
- Chat 1: `## 15:52:02 chat` / `grok-4.7-xhigh-fast`, start at 15:53:00 `Grok 4.7 256K Extra High Fast`, 42 `tool` headings, `## 16:01:24 agent-exit` / `rotate`. Stream `130358e138e64b239689d2912068f9c5.stream` (586240 bytes, mtime 16:01:23): 42 starts, 42 completions, no `result`.
- One `err` at line 381: `eye the eye must return exactly one computer_use call`.
- Chat 2: `## 16:01:24 chat` / `grok-4.7-xhigh-fast`. No `start` heading. `## 16:02:17 agent-exit` / `failed_agent`. Mind, lines 497–499: `{"state": "failed_agent", "minutes": 10.2}`.
- Stream `41ff88f1f93d4dd6a12fc0546793fb8a.stream` (21 bytes, mtime 16:02:17), the entire file: `Error: [unavailable]`.
- Eighteen shot ids, all present (727595 to 3691916 bytes). Not opened in this pass.
- Finding: a 40-minute wake died at minute 10.2. The second agent printed one non-JSON line and exited non-zero. The markdown does not contain that line. Elapsed time from the `chat` heading to `agent-exit` is 53 seconds. The stream does not show how those 53 seconds were spent; it is 21 bytes.

### 2.7 `run_20261027_160354`

- Bytes 26017, lines 2044, headings 511. First `## 16:03:54 wake` / `minutes=40.0`. Last `## 16:37:54 mind` / `{"state": "stopped", "minutes": 34.0}`.
- No jsonl. Six chats, all `grok-4.7-xhigh-fast`.
- Rotates, from `agent-exit` / `rotate` and the tool counts between chats: 16:10:01 (42), 16:14:26 (42), 16:21:35 (42), 16:27:35 (43), 16:32:38 (42). Then `## 16:37:41 stop` / `{"stopped": true}` and `## 16:37:54 agent-exit` / `stopped` with 37 tool headings in that chat.
- The 43-call chat is stream `793b11300ec14ebd81fa4e1d9ecc2f27.stream` (458033 bytes, mtime 16:27:35): started 43, completed 43, no `result` event.
- Four `err` headings, each `eye the eye must return exactly one computer_use call` (16:05:42, 16:30:24, 16:31:45, 16:34:23).
- Shot `163405-3f1c4f7fda6c44be9fcb2dcf6888dae2.png` (779447 bytes) was opened. On screen at 16:34:05, clock in the tray 4:34:05 PM: Untitled - Paint with an empty dashed text box, font Calibri, size 36. The PowerShell window on the left still shows the previous wake's printed line `{"state": "failed_agent", "minutes": 10.2}` and a `WinError 5` access-denied failure replacing a `journal.path.<hex>.tmp` onto `journal.path`. That WinError text was not found as a heading in this run's markdown. SINGLE-SOURCE for the WinError: the bitmap. The failed_agent line on that same screen matches section 2.6's mind heading.
- Twenty-four shot ids, all present. Only `163405` was opened.

### 2.8 `run_20261027_182210`

- Bytes 3856, lines 236, headings 59. First `## 18:22:10 wake` / `minutes=5.0`. Last `## 18:26:42 eye` (line 233). No `agent-exit`, no `report`, no `mind`, no `stop`.
- One chat, `grok-4.7-xhigh-fast`, start 18:22:17. Tool headings: 28. Think headings are `chars=<integer>`, and `say` lines end in `...`.
- Stream `deaaedef559a4b3c9b793396d1b91f02.stream` (283236 bytes, mtime 18:25:59): started 29, completed 28, no `result`. The file contains the bytes `FARA is terminated`, which is the 18:22:36 `say` text.
- `ledger.md` has no row whose id is `20261027_182210`. The surrounding rows are `160354` and `183727`.
- A 5-minute wake started at 18:22:10 would hit its deadline at 18:27:10. The last heading is 28 seconds earlier. The in-flight 29th tool is the eye: headings at 18:26:07, 18:26:15, 18:26:25, and 18:26:42 are `action=key`, written by the eye child into the markdown while the agent stream had already stopped growing (mtime 18:25:59).
- Finding: `drive`'s `agent-exit` write and `enter()` ledger seal did not run. Two independent stores agree: the markdown has no exit heading, and `ledger.md` has no row. INFERRED the process was terminated without running `finally` (confidence medium). A deadline kill would have written `agent-exit` / `deadline` and a ledger row; those are absent, so this was not the deadline path.

### 2.9 `run_20261027_183727`

- Bytes 173499, lines 588, headings 66. First `## 18:37:27 wake` / `minutes=5.0`. Last `## 18:42:06 mind` / `{"state": "stopped", "minutes": 4.7}`.
- One chat. `## 18:37:27 chat` / `grok-4.7-medium-fast`. Start 18:37:39 `Grok 4.7 256K Medium Fast`. 25 tool headings. `## 18:42:01 stop`. `## 18:42:06 agent-exit` / `stopped`.
- Stream `f1680f9dbc0544b9a5a0771184097188.stream` (322658 bytes, mtime 18:42:06): 25/25, `result` subtype `success`.
- Eye: left_click 2, unparsed 2, key 3, terminate 1. The two unparsed bodies start `<think>` and then prose ("I will type Paint"), with no `computer_use` call. Ledger line 25: `Two key calls with keys [Paint, Enter] were unparsed and typed nothing.`
- No shot id in the markdown.

### 2.10 `run_20261027_190548`

- Bytes 307577, lines 1156, headings 150. First `## 19:05:48 wake` / `minutes=10.0`. Last `## 19:15:48 mind` / `{"state": "deadline", "minutes": 10.0}`.
- Two chats, both `grok-4.7-medium-fast`. Chat 1: 42 tools, `## 19:11:22 agent-exit` / `rotate`. Stream `550a6d96…` mtime 19:11:22, 42/42, no `result`. Chat 2: 19 tools, `## 19:15:48 agent-exit` / `deadline`. Stream `59afdf93…` mtime 19:15:48, 19/19, no `result`.
- Eye headings: 27. A first-action scan counts left_click 8, left_click_drag 8, mouse_move 5, terminate 4, unparsed 2. Seven of those headings are cumulative echoes: a second heading in the same second, body prefixed `open` or `terminated`, repeating the log. Dropping them leaves primary actions left_click 4, left_click_drag 6, mouse_move 4, terminate 4, unparsed 2. The first-pass sentence treated the 19:07:34 and 19:07:59 `left_click` headings at `[110, 972]` as two further clicks. They are echoes. The primary click at that point is 19:07:25, line 219. The unparsed lines (227 and 290) are "I will type the word Paint" / "I will type Paint", same shape as 183727.
- Think at 19:15:48, lines 1141–1143: `The eye hit the 8 action limit and returned "open" (not terminated).` The following line is cut at `The first action is a mouse move to [356,`. The 8-cap is in this journal. The first pass had described it from the code and had not quoted line 1141.
- Ledger line 27, the whole lesson: `No lesson was written.`
- Elapsed from 19:05:48 to 19:15:48 is 10 minutes 0 seconds. The process deadline fired. There was no `--stop` heading.

### 2.11 `run_20261027_191902`

- Bytes 185642, lines 949, headings 101. First `## 19:19:02 wake` / `minutes=10.0`. Last `## 19:27:48 mind` / `{"state": "stopped", "minutes": 8.8}`.
- One chat, `grok-4.7-medium-fast`, start 19:19:08. 37 tools. `## 19:27:48 agent-exit` / `stopped` is the single agent-exit. Stream `ea547f76…` mtime 19:27:48, 37/37, `result` success.
- Eye headings: 23. A first-action scan counts mouse_move 9, left_click_drag 7, terminate 2, unparsed 5. Five are echoes. Primary actions: mouse_move 7, left_click_drag 7, terminate 2, unparsed 2. The primary unparsed headings are 19:26:02 and 19:26:32. The other three unparsed headings repeat those bodies.
- At 19:26:02 the eye heading body is the raw reply. Lines 617–618:

```
<tool_call>
{"name": "computer_use", "arguments": {"action": "terminate", "coordinate": [500, 400]}}
</tool_call>
```

The heading above it is `action=unparsed`. The same shape repeats at 19:26:32. Ledger lines 29–30: `The stop check stayed open at 19:26:02 and 19:26:32 because terminate with a coordinate, or terminate without answer, is unparsed. It closed at 19:27:06 only when terminate carried a non-empty answer and no coordinate.`
- No shot id in the markdown.

### 2.12 `run_20261027_194156`

- Bytes 174843, lines 877, headings 115. First `## 19:41:56 wake` / `minutes=10.0`. Last `## 19:51:57 mind` / `{"state": "deadline", "minutes": 10.0}`.
- Two chats, `grok-4.7-medium-fast`. Chat 1: 42 tools, `## 19:51:42 agent-exit` / `rotate`. The 42nd tool (line 834) is an `awaitToolCall` whose result is `runtimeMs` `40000`. Stream `ca2248dc…` mtime 19:51:42, 42/42, no `result`.
- Chat 2: `## 19:51:42 chat`, `## 19:51:47 start`, a `think` at 19:51:56, `## 19:51:56 agent-exit` / `deadline`. Zero `tool` headings. Stream `fd368b963e4e486387f7ba61984e0caa.stream` (17946 bytes, 30 lines, mtime 19:51:56): model `Grok 4.7 256K Medium Fast`, 0 tool calls, no `result`.
- There is no `report` heading and no `stop` heading.
- After the deadline line, line 866 is still an eye heading: `## 19:51:57 eye` / `action=left_click coordinate=[500, 498]`. Then the mind heading.
- The repeat index records 16 further `left_click` headings at `[500, 498]` after the first one at that coordinate, from 19:47:31 through 19:51:57. That is 17 headings at `[500, 498]`. Two other primary `left_click` headings are `[111, 972]` at 19:46:04 and `[149, 542]` at 19:46:13. Line 529's unparsed body is `{"action": "terminate", "coordinate": [500, 498]}`. The think at line 523, written by the mind while this was happening: `Log is open. 288 seconds left, 48%.` / `No eye call. Waiting for strokes.`
- `## 19:49:10 ledger`, line 702: `{"wakes": 15, "lesson_len": 681}`. The edit at lines 688–690 wrote `ledger_now.txt` with the paragraph that is still `ledger.md` line 31, including `From 19:47:15 through 19:48:38`. The lesson was written at 19:49:10, during the click series. The headings at that coordinate continue through 19:51:57. The lesson text stops at 19:48:38 because that is the interval already on disk when `--ledger-file` ran.
- Shot `194709-20692ff2f6a040b9b57e6c1161f16f0c.png` (326352 bytes) was opened. Tray clock 7:47:09 PM. Untitled - Paint, brush selected, canvas white, no figure. That frame is before the 19:47:31 click series. It does not show what those clicks did. Seven shot ids in the run, all present, all timestamped at or before 19:47:17. None of them is from the click series. The series did not write a shot id into the markdown.
- Elapsed 19:41:56 to 19:51:56 is 10 minutes 0 seconds. The second chat was spawned about 14 seconds before that deadline. Five percent of 10 minutes is 30 seconds. 14 is under 30.

### 2.13 `run_20261027_195316`

- Bytes 176476, lines 823, headings 101. First `## 19:53:17 wake` / `minutes=10.0`. Last `## 20:03:17 mind` / `{"state": "deadline", "minutes": 10.0}`.
- One chat, `grok-4.7-medium-fast`, 32 tool headings. Stream `f886a5a6…` mtime 20:03:13, 32/32, no `result`.
- `## 20:03:01 stop` / `{"stopped": true}`. `## 20:03:17 agent-exit` / `deadline`, not `stopped`. The say at line 784: `About 38 seconds remain.`
- The shell that called `--stop` (line 800) passed `--wake-end` `1793127796.9929116` and `--wake-start` `1793127196.9929116`. Difference 600.000 seconds. `completedAtMs` on that tool is `1793127783106`, which is before that end epoch. The agent-exit heading is 16 seconds after the stop heading.
- Ledger line 32 seals the wake as `deadline`, not `stopped`.
- Eye: left_click 20, key 1, type 1, terminate 3, plus 3 headings the action scanner did not parse. Same-coordinate `left_click` counts: `[160, 741]` three times (19:55:07, 19:55:15, 19:55:23), `[205, 611]` three times (19:59:14, 19:59:24, 20:02:36), `[12, 970]` twice, `[107, 552]` twice, `[207, 611]` twice, `[205, 609]` twice, `[266, 609]` twice. `[205, 611]` and `[205, 609]` are different points. The first-pass sentence said `[160, 741]` twice. The headings are three, and `ledger.md` line 33 also says three.
- No shot id in the markdown. The later bitmap `200629` (section 2.14) shows Calculator still open with display `1`.

### 2.14 `run_20261027_200412`

- Bytes 142048, lines 598, headings 74. First `## 20:04:12 wake` / `minutes=10.0`. Last `## 20:14:06 mind` / `{"state": "stopped", "minutes": 9.9}`.
- One chat, medium-fast, 31 tools. Stream `88695386…` mtime 20:14:06, 31/31, `result` success. `## 20:13:51 stop`. `## 20:14:06 agent-exit` / `stopped`.
- Eye: one left_click, one type, one key, one terminate. The report names them: 20:06:27 `[225, 974]`, 20:06:36 text `Poland weather`, 20:06:45 Enter, terminate 20:07:04 claiming a weather card.
- Shot `200629-13c9bcd6d00d454aab3aad5d41f5b9cd.png` (252034 bytes) was opened. Tray clock 8:06:29 PM. Chrome is a Google new-tab page, not a weather result. Calculator is open on the left; the main display shows `1`. This frame is two seconds after the taskbar click and before the type at 20:06:36. It does not show the weather card the terminate claim describes. The other named shot, `200621-…` (324186 bytes), was not opened.
- Shot `200647-44041906988d464e834b82c5e6645a4e.png` (306785 bytes) was opened in the consultation pass. Tray clock 8:06:47 PM. Chrome shows the Poland weather card: 5°C, `Szansa opadów 0%`, `Wiatr: 6 km/h`, `Bezchmurnie`, `wtorek 20:00`. Calculator's main display is `1`, and a history line reads `2 + 2 =`. The eye heading at line 326, 17 seconds later, says `wind 8 km/h` and the same 5°C, 0%, `Bezchmurnie`, and Tuesday 20:00.

### 2.15 `run_20261027_201442`

- Bytes 310720, lines 889, headings 115. First `## 20:14:43 wake` / `minutes=10.0`. Last `## 20:24:39 mind` / `{"state": "stopped", "minutes": 9.9}`.
- One chat, medium-fast, 39 tools. Stream `e9d035b8…` mtime 20:24:39, 39/39, `result` success. `## 20:24:24 stop`. `## 20:24:39 agent-exit` / `stopped`.
- Eye: left_click 25, type 2, key 1, no terminate. Repeats of `[283, 66]`, then `[223, 66]`, then 18 `left_click` headings at `[283, 230]` from 20:18:36 (line 446) through 20:24:29 (line 838). 20:18:54 is the second of those 18. A `key` heading at 20:20:07 sits after the fifth and before the remaining 13. The first pass started this series at 20:18:54 and dropped the 20:18:36 heading.
- A shell at 20:18:54 printed the trajectory. Stdout inside the tool heading, line 484:

```
4 user Done. action=left_click coordinate=[283, 66] That action already happened at this coordinate. A stroke is left_click_drag with the end coordinate.
5 left_click [283, 66]
```

The user turn says both `Done` and `already happened`. The next assistant turn is another `left_click` at the same coordinate. The mind's think at line 500: `The repeated click note is not a clear defect in that file.`
- `open True` is in that same stdout (`turns 21`). The log never reached terminate. Ledger line 37: `From 20:18 left_click [283, 230] repeated after the same-coordinate note and was still repeating at 20:23:49.`
- No shot id in the markdown.

### 2.16 `run_20261027_202606`

- Bytes 136548, lines 608, headings 72. First `## 20:26:06 wake` / `minutes=10.0`. Last `## 20:36:01 mind` / `{"state": "stopped", "minutes": 9.9}`.
- One chat, `grok-4.7-medium-fast` (prompt.txt line 181, and the chat heading). 33 tools. Stream `e0ae089f…` mtime 20:36:00, 33/33, `result` success. `## 20:35:50 stop`. `## 20:36:01 agent-exit` / `stopped`.
- Eye: one left_click `[194, 972]` at 20:28:19, one terminate at 20:28:28. `fara_traj.json` is `open: false`. The terminate answer is `Done — File Explorer is open to the folder you specified.`
- `screen.json` id `202821-71296d903afc4ca782610f427a0b50cb`, foreground window `787422` title `This PC - File Explorer`, box `[311, 43, 994, 845]`.
- Shot `202813-442c32c6333947408e90ffd3379e257a.png` (296592 bytes), the user turn's shot in `fara_traj.json`, was opened. Tray clock 8:28:13 PM. Chrome in front shows a Google results page for `Poland weather`, with a card reading 5°C, precipitation 0%, wind 6 km/h, `Bezchmurnie`, Tuesday 20:00. The Cursor pane says the eye watcher is live and the previous log is terminated. File Explorer is not the front window.
- Shot `202821-…png` (275663 bytes) was opened. Tray clock 8:28:21 PM. File Explorer titled `This PC`, address `This PC`, contents `Local Disk (C:)`. Not the Trident folder. The weather page is partly visible behind it. This is two seconds after the click and seven seconds before the terminate claim.
- `prompt.txt` is this wake's rendered prompt: end time `20:36:06`, `--wake-start` `1793129166.6577363`, `--wake-end` `1793129766.6577363` (difference 600 seconds). The FARA section embedded in it is still the Notepad trajectory from 201442, `terminated`, because the prompt was written at chat start, before this wake's eye ran. Story status in that prompt: `inherited; not current progress`, provenance run `20261027_201442`.

## 3. Systems analysis

### 3.1 What a wake, a chat, and a run are

`main.py` is three lines. It loads `organism.py` with `runpy.run_path` under the name `__main__` (`main.py` lines 1–3). Every child that is "the body" is a new interpreter and a new read of the file on disk. The parent that is already inside `drive` keeps the code it loaded at its own start. The prompt says so. `organism.py` lines 23–24: `The running process keeps the loaded body.`

One wake is `mind_run` → `drive` (`organism.py` lines 982–985 and 913–980). `drive` sets `wake_start` and `wake_end` from `time.time()` plus `minutes * 60` (lines 914–916), deletes `stop`, writes `goal.txt`, and loops. The loop condition is only the deadline and the stop file (line 941). A chat is one pass of that loop: a new `TRIDENT_CHAT_ID` of the form `<run>:<n>` (line 943), a new `prompt.txt`, a new named job, a new agent process (lines 946–949). The chat ends when `read_agent` returns. If the return is anything other than `rotate`, the loop breaks (line 966). The run id is the `--run` stamp. The markdown path is `run_<stamp>.md` (`configure`, lines 122–126). Headings are appended under `run_<stamp>.lock` (lines 168–182).

The historical jsonl is a second log the current `drive` does not write. Current stdout of the agent is `streams\<uuid>.stream`, opened inside `read_agent` (lines 840–846). That split is why 155202 and every later run have no jsonl, and why 133306's four stream paths are gone: 141013's first jsonl line deleted `streams`.

### 3.2 Process lifetime and the job object

`Desk.open_job` (`organism.py` lines 437–442) calls `CreateJobObjectW` with a name `Trident-` plus a uuid, then `SetInformationJobObject` with information class `9` and `LimitFlags = 0x2000`. In the Windows SDK those are `JobObjectExtendedLimitInformation` and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The pages retrieved on 27 October 2026 describe the flag by name and do not print the hex. The public page retrieved 27 October 2026 describes the behavior by name, not by hex: closing the last handle terminates every process in the job. https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects

`spawn` (lines 818–833) does not put the agent in the job directly. It starts `python -B main.py --boot <job name> node.exe …` with `creationflags=0x08000000` (`CREATE_NO_WINDOW`), `stdin=DEVNULL`, `stderr=STDOUT`. `boot` (lines 70–79) opens the job with access `0x0001` (`JOB_OBJECT_ASSIGN_PROCESS`), assigns the bootstrap interpreter, closes that handle, then `subprocess.call`s node. A child of a job member joins the job unless the job allows breakaway. This job sets only `0x2000`, not `JOB_OBJECT_LIMIT_BREAKAWAY_OK` (`0x800`) and not silent breakaway (`0x1000`). INFERRED node stays in the job (confidence high on the flags; the process list was not captured, so membership was not observed).

The parent's handle is the one that keeps the job alive. `boot` closes its own handle before `call`. On the way out, `drive` closes the parent's handle before it `kill()`s the bootstrap process (lines 951–955), and the outer `finally` closes it again (lines 975–978). Microsoft's rule, same page: the kill runs when the last handle closes, not when the parent merely exits. Raymond Chen's note of 9 December 2013, retrieved 27 October 2026, makes the same point and warns that an inherited job handle prevents the kill: https://devblogs.microsoft.com/oldnewthing/20131209-00/?p=2433 . The code closes the child's handle and redirects standard handles, which on current Python makes `close_fds` true, so the job handle should not be inherited. That inheritance was not measured in a live process. INFERRED, confidence medium.

Rotate's `agent_exit` in the jsonl is `returncode` 1 because `read_agent`'s `finish` calls `proc.kill()` while the process is still alive (lines 854–856). The reason string is still `rotate`. The non-zero code is the kill, not a second failure. Observed for every rotate in 133306, 141013, 143208, and 151228.

The journal server is not in the job. `ensure_journal` (lines 159–167) does a plain `Popen` of `main.py --journal` when port 8765 is free, and returns immediately when `GET /identity` already says `trident-journal`. It does not pass `--boot`. 151228 lines 2554 and 2562: the listener answered, and it still reported a `v2` code path, because it had been started before the tree was deleted. Two sources: those lines, and `ensure_journal`'s early return. The listener binds `0.0.0.0:8765` (line 242). That bind was not re-probed in this review.

### 3.3 Kill paths

Paths that end a chat, and which of them end the wake:

| Return from `read_agent` | How it is produced | Wake continues? |
| --- | --- | --- |
| `rotate` | `calls >= 42` and `running == 0`, and not stop, and not deadline (line 902) | Yes. New job, new process. |
| `deadline` | `time.time() >= wake_end` checked at the top of the read loop (line 860), before the stop grace | No. |
| `stopped` | Stop file exists and 60 seconds have passed (lines 862–865), or a `result` event arrives while the stop file exists (line 908) | No. |
| `failed_agent` | `result` with `is_error`, or EOF with a wait status not in `(None, 0)` (lines 875–880 and 906–911) | No. Line 966 breaks. |
| `failed_eof` | EOF, status 0, no `result`, no stop (line 880) | No. Not observed in these 16 runs. |
| `failed_stream` | The pump thread catches `OSError` (lines 849 and 872–874) | No. Not observed as a heading. |
| `completed` | `result` without error, then 0.5 seconds (lines 866–867 and 906–909) | No. |

`finish` kills the agent if `poll()` is still `None` (lines 854–856). The outer `finally` kills again and waits 5 seconds (lines 976–978). Closing the job is the path that is supposed to take node and its children with it. `TerminateProcess` on the bootstrap python alone would not.

A hard kill of the wake process itself skips `finally`. 182210 is the observation: no `agent-exit`, no ledger row, stream has no `result`, 28 seconds before a 5-minute deadline. 142947 is the same shape in the old logger: no `wake_end`, stream has no `result`, last heading is a `started` event.

The eye thread is a daemon (`drive` line 940) joined for 5 seconds after `eye.live` is removed (lines 973–974). 194156 lines 862–872 show an `eye` heading at 19:51:57, one second after `agent-exit` / `deadline`. The heading is written before `execute` (line 643, then 651). `execute` refuses work when `time.time() >= wake_end` (lines 570–571). INFERRED that particular click did not call `SendInput`, because the deadline had already passed (confidence medium). Observed, regardless: the eye thread still mutates the run file after the agent has been declared dead.

### 3.4 Timers

There are six clocks, and they do not all stop the process.

1. Wake deadline. `end = start + minutes * 60` (line 915). Checked in `read_agent` before any stream line (line 860) and in `execute` (line 570). This one kills. 190548 and 194156 end on a heading exactly 10 minutes after `wake`. 195316's `--wake-end` minus `--wake-start` is 600.000 seconds.

2. Stop grace. 60 seconds after the stop file appears, unless the deadline is sooner (lines 862–865). The tool sentence says the same thing (`DOCS`, line 35): `The mind gets up to 60 seconds, or less if the deadline is sooner.` 195316 wrote `stop` at 20:03:01 and was sealed `deadline` at 20:03:17. The grace would have run to about 20:04:01. The deadline won, as the sentence says. The ledger row says `deadline` (ledger line 32), not `stopped`. The model's say says it stopped. Both are in the file. The state string is the process outcome, not the model's verb.

3. The 5 percent line. It exists only in the prompt string (line 802): `Under 5 percent remaining, stop new work...` Before 19:02:57 the same sentence said 20 percent (commit `c3887eba`, message: `A ten-minute wake can keep painting until the final half minute.`). `drive`'s `while` does not compute a fraction (line 941). 194156 spawned chat 2 at 19:51:42 with about 14 seconds left. Five percent of 10 minutes is 30 seconds. The process started a new agent inside the window the prompt calls closed. That chat produced a `start` heading, a `think`, zero tools, and `deadline`.

4. Result drain. After a successful `result` event, `read_agent` waits 0.5 seconds and then kills the process (lines 866–867). Not separately timed in these journals. Several successful `result` events exist (183727, 191902, 200412, 201442, 202606). Their `agent-exit` is `stopped` because the stop file was already set, which rewrites the reason (line 908).

5. Eye HTTP timeout. `ask` uses `urlopen(..., timeout=180)` and does not set `stream` (lines 512–514). The deadline is not checked while that call is blocked. A single eye request can run up to 180 seconds past `wake_end`, and the next `execute` then refuses. No eye heading in these runs was measured at 180 seconds. The 180 is the code's cap, not a measured call.

6. `eye_run`'s 3-second poll (lines 679–685). If `eye.live` exists, `--eye-file` writes `eye_now.txt` and returns the log within 3 seconds. It does not wait for terminate. 194156's mind wrote `No eye call. Waiting for strokes.` at 19:47:16 while eye headings continued until 19:51:57. That is the split working as written: the mind returned, the thread kept acting.

`brief_result` drops the `clock` object from every kind except windows, capture, hands, eye, and map (line 193). Later mind lines are `{"state": "...", "minutes": N}` with no deadline. The deadline is in the wake heading's `minutes=` plus the start time, and in `prompt.txt`'s `--wake-end`. Early runs (133306, 151228) still printed the clock, because that strip was not in the body they had loaded.

### 3.5 Stream parsing

`read_agent` (lines 881–911) decodes each stdout line as UTF-8 with replacement, `json.loads` it, and ignores non-objects. A `JSONDecodeError` is `continue` (lines 884–885). There is no `err` heading for that line.

Cursor's current CLI document, retrieved 27 October 2026, says `stream-json` is NDJSON and lists `system` / `init`, `user`, `assistant`, `tool_call` / `started` and `completed`, and a terminal `result`: https://cursor.com/docs/cli/reference/output-format . The same document says that on failure the process can exit non-zero and end the stream early, with an error on stderr. This process merges stderr into stdout (line 833). A failure line is therefore on the same pipe, and the parser throws it away if it is not JSON.

The spawn argv (line 832) passes `--output-format stream-json` and does not pass `--stream-partial-output`. The document says that without the partial flag, one `assistant` event is the whole message between tool calls. The code writes each of those as a `say` heading (lines 893–895). That matches the runs: `say` counts are small (4 to 23), while `think` counts are large.

`thinking` / `delta` is concatenated and flushed as one `think` heading when a non-delta arrives (lines 889–892). The document's forum note, retrieved the same day, describes thinking deltas as a real event type: https://forum.cursor.com/t/stream-partial-output-assistant-events-have-multiple-undocumented-forms-how-should-consumers-parse-them/156289 . The code handles `delta` and does not special-case `thinking` / `completed`. A `completed` event falls through and flushes the buffer. That is harmless.

`interaction_query` is not in the event list on the output-format page. Two streams contain it anyway (`401b0c3c…` for 151228 chat 1, and `83ad9070…` for 151228 chat 2). Both are `webSearchRequestQuery`, and both have a following response `approved`. The parser has no branch for this type, so it neither counts nor displays them. `cli-config.json` is written with `autoAcceptWebSearch: true` (line 824), which is the likely reason a response exists with `stdin` tied to `DEVNULL`. INFERRED (confidence medium). If a future query type is not auto-approved, nothing can answer it: stdin is `DEVNULL` (line 833). That stall was not observed. The observed queries completed.

`lean` (lines 812–817) drops `parsingResult` and `hookAdditionalContexts`, and drops a duplicated shell command and a duplicated stdout. Commit `02cf66dd` (19:05:12) is that change. 142947's last heading still contains `hookAdditionalContexts`, because that wake ran the older writer. 151228's last request heading still contains a full `parsingResult`. The current writer had not loaded.

### 3.6 The call counter

`running` increments on `tool_call` / `started` and decrements on `completed` (lines 896–899). `calls` increments only on `completed`. Rotate happens when `calls >= 42` and `running == 0` (line 902). The check is after the increment, so the 42nd completion rotates if nothing else is in flight.

It is not a cap of 42 when tools overlap. If the 42nd completion sees `running > 0`, the function does not return. The next completion, with `calls == 43` and `running == 0`, rotates. Observed twice:

- Jsonl `run_20261027_133306.jsonl` line 6166: `completed_calls` 43, `running_tools` 0, reason `rotate`.
- Stream `793b11300ec14ebd81fa4e1d9ecc2f27.stream`, mtime 16:27:35, started 43 and completed 43, matched to `run_20261027_160354.md` whose chat from 16:21:36 has 43 `tool` headings and `agent-exit` `rotate`.

The other way around is also in a stream. `401b0c3c…` has 43 starts and 42 completions, while the jsonl counter for that chat is 42. `finish` kills the process at the 42nd completion. The pump thread can still write a start that was already in the pipe (lines 842–848 run until the pipe closes). That extra start is not a 43rd counted call.

The counter counts harness tools: Read, Shell, Await, and the organism's own flags, all of them. 194156's 42nd completion is an await of 40000 ms (line 834). Eye actions on the background thread do not increment `calls`. 201442 has 25 eye clicks and 39 harness tools in one chat, and it did not rotate.

`--learn` does not count these. `measured` (lines 700–711) counts only headings whose kind is in `COMPONENTS` (line 11): eye, decide, hands, capture, windows, region, place, story, ledger, stop, measure, map. `tool`, `chat`, `think`, and `agent-exit` are invisible to it. `prompt.txt` line 177 therefore says `429 component actions` for 15 earlier wakes. That number is not the call counter.

At 18:22 the loaded `read_agent` still used `calls = running = thought = 0` and wrote `chars=<n>` (commit `6739ed1`, lines 794 and 814 of that file). `run_20261027_182210.md` line 15 is `chars=1803`. Commit `26f2fa07` at 18:37:15 replaced the count with the text. 183727, which started at 18:37:27, stores full tool JSON. The 12 seconds between the commit and the wake are enough for a new process to load it. The journal agrees.

### 3.7 Race: who loads the file the parent is about to spawn

`spawn` builds the command from the function the parent already loaded. The child then `runpy`-loads whatever is on disk.

At commit `534ce279` (13:45:46), `mind.py` line 135 is:

```
return subprocess.Popen([str(PYTHON), '-B', str(CODE / 'job_boot.py'), job_name, *argv], ...
```

141013 chat 1 completed 42 calls, so that file opened. Chat 2's entire stdout is `can't open file '...\job_boot.py'`, return code 2, and the wake ends. The file was removed between the two spawns, by the same wake. The parent did not reload `mind.py`. It kept calling the path it had loaded.

151228 is the same race one generation later. Chats 1 and 2 ran. Chat 3, 0.16 seconds after chat 2's rotate, printed `can't open file '...\v2\job_boot.py'` and returned code 2. Commit `9823582` at 15:42:51, during that wake, is the commit that deletes the v2 tree. The in-memory parent still named `v2\job_boot.py`. The file was gone.

Current `spawn` names `main.py --boot` (line 833), and `boot` is inside `organism.py` (lines 70–79 and 1034–1038). Deleting `job_boot.py` cannot recur; that file is gone. Editing or breaking `organism.py` during a wake still can: the prompt tells the mind to edit it (`organism.py` lines 23–24), the parent keeps `drive`, and the next chat's child loads the disk. 155202 is the post-collapse observation of the same exit policy, with a different stdout: `Error: [unavailable]`.

### 3.8 The eye thread, the 8-step cap, and the duplicate click

Before 19:41:33, `eye_run` was a loop of at most 8 actions inside the `--eye-file` process, and that process blocked the mind. Commit `657392c` replaced it with `eye_watch` / `eye_steps`. The commit message records the wakes it was answering (190548 and 191902) and the intended split: an eye thread until terminate, and Grok reading the log.

`eye_watch` (lines 664–672) polls `eye_now.txt` every 0.4 seconds and calls `eye_steps` with `limit=None` and `fresh=True`. `fresh=True` replaces `fara_traj.json` with an empty open trajectory (lines 617–620). The loop's only caps are the deadline, the stop file, and the removal of `eye.live` (lines 625–626). There is no action limit while the wake eye is live.

`validate` (lines 455–460) rejects any argument that is not in the required set. `terminate` requires `answer` and nothing else. A `coordinate` on a terminate is an extra key, so it raises `Fail`. The handler (lines 637–641) writes `action=unparsed`, appends the error as a user turn, and `continue`s. It does not set `open` to false. Only a parsed terminate does (lines 645–646).

191902 lines 617–618 are that extra key: `terminate` plus `coordinate` `[500, 400]`. The heading is `unparsed`. Ledger lines 29–30 say the log stayed open until a later terminate had an answer and no coordinate. 194156 line 537 is the same shape with `[500, 498]`, and the thread then clicked that point until the deadline. The mind was obeying the prompt (`While her log says open, do not call --eye-file again`) and waiting. The thread was not waiting.

The duplicate-click guard moved during the evening, and the runs show both positions.

- At 19:41 (the code 194156 loaded) there was no guard. Seventeen `left_click` headings at `[500, 498]`.
- Commit `233661f` at 19:53:05 appended a sentence after `execute` returned: `That action already happened at this coordinate.` The click had already been sent. 201442's own tool stdout (line 484) shows `Done. ... already happened` on turn 4 and another `left_click` at the same point on turn 5. The mind wrote, at 20:19:01, that the note was not a defect.
- Commit `1900589` at 20:25:48 moved the comparison to before `execute` (current lines 648–650) and `continue`s without `SendInput`. 202606 started at 20:26:06, so it loaded that commit. It has one click and one terminate. The guard was not exercised.

The guard compares only the previous assistant turn, skipping `unparsed` (line 648). A different action between two identical clicks re-arms the click. That pattern was not re-run after 20:25. It remains in the file. Confidence that it would still fire: high on the line, untested on a wake.

`eye_run` while the thread is live waits at most 3 seconds for `turns[0].text` to equal the new sentence (lines 679–684). If the thread is inside a previous `eye_steps`, it will not see `eye_now.txt` until that call returns. The 3-second wait can expire and hand the mind the previous log. Not isolated as its own incident in these runs. The 194156 wait is the related observation: the mind saw `open` and correctly did not call `--eye-file` again, while the thread kept going.

### 3.9 Wake versus run, and what the prompt's deadline is

The prompt deadline is a sentence. The process deadline is `wake_end`.

`render_prompt` (line 802) tells the model the end clock and the 5 percent rule. `read_agent` does not read that sentence. It compares `time.time()` to the float stored in `SET['wake_end']`. A model that stops early is calling `--stop`, which writes an empty file (`stop_run`, lines 779–782). A model that does not call `--stop` is killed at `wake_end` even if it is mid-thought. 190548 is that case: `deadline` at 10.0 minutes, ledger lesson `No lesson was written.`

A rotate inside the last 5 percent throws away the previous chat's `said`. Line 949 assigns `outcome, said = read_agent(...)`. Chat 2 of 194156 returned no assistant text (`last` stays `''`, lines 837 and 894). Line 969 writes `report` only when `said` is non-empty. 194156 has no report heading. The first chat's last words are not in a `report` heading. Two sources: the assignment at line 949, and the heading sequence 838–876 (rotate, chat, start, think, deadline, eye, mind, and no report).

### 3.10 Clipboard, capture lock, and the journal replace

`type_text` (lines 350–352) puts the string on the Windows clipboard with PowerShell and then sends Ctrl+V. That is a global clipboard, not a queue. No wake in this set was shown to have lost a character because another process wrote the clipboard at the same moment. Latent. The unparsed `Paint` keystrokes are a different bug: `validate` requires every key to be in `VK` (lines 466–468), and `Paint` is not a key. 183727 and 190548 both show the prose "I will type Paint" stored as `unparsed`, and the ledger says nothing was typed.

`atomic` (lines 103–113) writes a temp file and `os.replace`s it. The bitmap `163405` shows `WinError 5` access denied on replacing `journal.path.<tmp>` onto `journal.path`. `point_journal` (lines 154–158) is the writer of that path. The error is on the screen of the 16:34 capture and is not a heading in `run_20261027_160354.md`. SINGLE-SOURCE. A plausible cause is the journal server, which is a different process and reads `journal.path` in a loop (lines 211–214 and 226–234) without the run lock. That cause was not confirmed by a handle dump. Confidence low on the cause, high that the pixels contain the error text.

`heading` holds `run_<id>.lock` for the append (lines 180–182). The journal's `/events` loop reads the same file without that lock (lines 226–234). A large `think` or `tool` heading can be observed mid-write. Not measured as a torn event in these files. Latent.

### 3.11 What the local eye server actually is, on this path

`ask` posts one non-streaming JSON body to `SET['vision_url'] + '/v1/chat/completions'` with `timeout=180`, `temperature` 0, `max_tokens` 2048, and `chat_template_kwargs.enable_thinking` false (lines 511–514). It reads `choices[0].message.content` (line 515). It does not set `stream: true`.

llama.cpp's server README, retrieved 27 October 2026, describes `stream` as off unless requested, and describes the full completion as the non-stream response: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md . Streaming `finish_reason` behavior is not on this path. I am not asserting that the process at `192.168.16.31:1234` is llama.cpp. The only identification in the corpus is 151228's report, lines 2554 and 2561: `GET /v1/models` returned `fara-1.5-4b` with alias `retina`. The code's request is OpenAI-shaped chat completions. That is what the runtime depends on.

`enable_thinking: false` did not stop `<think>` tags. 191902 lines 612–616 and 182210's eye headings contain them, and several of those replies still parsed. The ones that did not parse are the `unparsed` headings. The flag is in the request. The tags are in the journal. The server's reason for emitting them was not visible from here.

## 4. Symptom / root-cause chains

Ordered by severity. "Still in HEAD" means the cited lines are in `organism.py` at `1900589`.

### 4.1 A non-JSON, non-zero agent exit ends the wake, and the journal hides the text

Still in HEAD.

Symptom. `run_20261027_155202.md` lines 489–499: a second chat starts at 16:01:24, never writes `start`, and at 16:02:17 the wake is `failed_agent` at minute 10.2 of 40. The same shape, with the text preserved only because a jsonl existed: 141013 mind line `failed_agent` at 14.3 of 40 minutes, 25.7 minutes left; 151228 mind line `failed_agent` at 31.2 of 40, 8.8 minutes left.

Chain.

1. The agent stdout is not JSON. 155202's stream `41ff88f1f93d4dd6a12fc0546793fb8a.stream` is the 21 bytes `Error: [unavailable]`. 141013's stream `3e8984ce…` and jsonl line 5543 are `can't open file '...\job_boot.py'`. 151228's stream `0c5080e1…` and jsonl line 11031 are `can't open file '...\v2\job_boot.py'`.
2. `read_agent` lines 884–885 `continue` on `JSONDecodeError`. No `err` heading is written. 155202 has no `err` between the chat heading and `failed_agent`.
3. The process exits non-zero. The code path that returns the string `failed_agent` on EOF is line 880: status not in `(None, 0)`. 141013 and 151228 record `returncode` 2, which matches a Python "can't open file" exit. 155202 has no jsonl; the heading is the string `failed_agent`, so the status was not 0 and not a clean `result`. INFERRED for 155202's numeric code (confidence high that it was non-zero, because the other branch would have written `failed_eof`, which is not the heading).
4. `drive` line 966 breaks on any outcome other than `rotate`. The rest of the minutes are dropped.
5. Why was the file missing in the two earlier wakes? The parent had already loaded a spawn path, and the wake deleted that file before the next chat. `mind.py` line 135 at `534ce279` names `CODE / 'job_boot.py'`. 141013 chat 1 ran, chat 2 could not open it. 151228 chats 1 and 2 ran, and chat 3 could not open `v2\job_boot.py` 48 seconds after commit `9823582` deleted that tree. The current launcher no longer names `job_boot.py`. The exit policy in steps 2–4 is unchanged, and 155202 hit it after the collapse.

Two sources for the highest-severity statement: `organism.py` lines 884–885 and 966, and `streams\41ff88f1f93d4dd6a12fc0546793fb8a.stream` together with `run_20261027_155202.md` lines 493–499. The boot-file pair is the same policy on the previous launcher: jsonl line 5543 against `mind.py:135`, and jsonl line 11031 against the `v2\job_boot.py` stream.

### 4.2 The eye thread does not stop on a malformed terminate, and until 20:25 a repeated click was sent again

The malformed-terminate hole is still in HEAD. The send-it-again hole was closed at 20:25 for the immediate previous action only.

Symptom. 194156: `terminate` with `coordinate` `[500, 498]` is `unparsed` at 19:47:24 (lines 529–538), then `left_click` at that same point from 19:47:31 through 19:51:57, and the wake ends `deadline` with no `left_click_drag` (ledger line 31). 201442: the trajectory stdout at line 484 shows the note `already happened` on a user turn whose text also says `Done`, and the next assistant action is the same click. Ledger line 37 says the `[283, 230]` series was still running at 20:23:49.

Chain.

1. `validate` line 460 rejects extra keys. `coordinate` on `terminate` is extra. `Fail`.
2. Lines 637–641 store `unparsed` and `continue`. `data['open']` stays true. Lines 645–646 are the only assignment of `open` to false.
3. After 19:41, `eye_steps` is called with `limit=None` (line 671) and the loop does not count to 8 (line 625). 194156's mind saw the open log and did not send another sentence (think, line 523). The thread was the thing still clicking.
4. Why the clicks were delivered: the comparison that notices a repeat ran only while building the user string after `execute`, in commit `233661f` (19:53:05). 201442 loaded that commit (wake start 20:14:43, commit 20:25:48 still in the future) and the stdout quotes `Done` plus the note on the same user turn. `execute` had returned. Commit `1900589` moved the comparison above `execute` (current lines 648–650). That is why 202606, started at 20:26:06, is not evidence the hole remains: it never repeated a coordinate.
5. The chain for "a different action in between re-arms the same coordinate" stops at line 648. It was not hit after the move. Marked untested, not observed.

Two sources: `organism.py` lines 460 and 637–641, and `run_20261027_191902.md` lines 617–618 plus ledger lines 29–30. For the delivered repeat: `run_20261027_201442.md` line 484, and commit `233661f`'s diff, which places the note after `execute` and before the 20:25 move.

### 4.3 The 5 percent rule does not gate a new chat, and the new chat erases the report

Still in HEAD.

Symptom. 194156 chat 2 starts at 19:51:42. The wake deadline is 19:51:56. The second stream (`fd368b96…`, 17946 bytes) has an init and no tool call. The markdown has no `report` heading. Mind state is `deadline`.

Chain.

1. Line 802 puts "under 5 percent, stop new work" in the prompt only.
2. Line 941's loop condition is the deadline and the stop file. At 19:51:42 the deadline had not arrived, so a new job and a new process were started (lines 946–949). 14 seconds is less than 30 seconds, which is 5 percent of the 10 minutes in the wake heading.
3. Line 949 replaces `said` with the new chat's text. The new chat produced a `think` and no `say` (lines 850–860). `said` is empty.
4. Line 969 writes `report` only if `said` is non-empty. The heading is absent. Line 860 then returns `deadline` because `now >= end` is tested before the stop grace.

Two sources: `organism.py` lines 941 and 949, and `run_20261027_194156.md` lines 838–876.

### 4.4 A wake that is killed from outside leaves no seal

Still in HEAD, because the seal is in `finally` and after `drive` returns, not in a handler for a hard terminate.

Symptom. 182210's last heading is an `eye` line at 18:26:42. There is no `agent-exit` and no `mind`. `ledger.md` has no `20261027_182210` row. The stream `deaaedef…` has 29 starts, 28 completions, and no `result`. 142947's jsonl has no `wake_end`; its last heading is a `started` grep; its stream has no `result`.

Chain.

1. `agent-exit` is written in `drive` after `read_agent` returns (line 962 in the current file; line 907 in the 15:43 body that 182210 actually ran).
2. The ledger row is written in `drive`'s `finally` via `enter` (current line 979; the 15:43 body line 923).
3. The `mind` heading is written only after `drive` returns, in `main` (lines 1030–1031). 182210's body did not yet have the mirror join, but it did have `emit` after `drive`.
4. None of those three writes exist for 182210. A deadline return would have written all three. The deadline was 28 seconds later. The chain stops at "the process did not reach those lines." The log does not name the killer. INFERRED `TerminateProcess` or an equivalent that skips `finally` (confidence medium). 142947 is the same missing-seal observation on the older jsonl writer (confidence high that the seal is missing; the writer of that era did emit `wake_end` for 133306, 141013, 143208, and 151228).

Two sources for 182210: the markdown's last heading (line 233) with no later exit, and the absence of that run id in `ledger.md` between lines 23 and 24.

### 4.5 The 42-count allows 43 when a tool is still marked running

Still in HEAD. Lower severity: one extra harness call, then rotate still happens.

Symptom. 133306 chat 2 rotated at `completed_calls` 43. 160354's 16:21:36 chat has 43 `tool` headings and stream `793b113…` has 43 completions.

Chain. Lines 898–902 increment `calls` on completion and rotate only when `running == 0`. A completion that lands while another `started` is outstanding does not rotate. The following completion does, at 43. The opposite race (a start written to the stream after `finish` has already decided) is `401b0c3c…`, 43 starts and 42 completions, with the jsonl counter at 42. That one did not become an extra journal tool.

Two sources: `organism.py` lines 898–902, and jsonl line 6166 together with stream `793b11300ec14ebd81fa4e1d9ecc2f27.stream`.

### 4.6 Chains that were checked and are specified, not defects

195316 wrote `stop` at 20:03:01 and was sealed `deadline` at 20:03:17. `read_agent` tests the deadline before the 60-second grace (lines 860–865). The tool sentence (line 35) says the grace is shortened when the deadline is sooner. The ledger says `deadline`. The say says the model stopped. Both match the code. Not filed as a defect.

Rotate's `returncode` 1 is `proc.kill()` inside `finish` (lines 854–856). The reason string stays `rotate`, and the wake continues. Observed on every jsonl rotate. Specified.

`money` is the string `not metered` in the `wake_end` payloads of 133306, 141013, 143208, and 151228. That rollup refuses to sum. A `type=result` usage object is present on the eight chats that were not killed first. 133306 chat 4, jsonl line 9957: `{"inputTokens": 73067, "outputTokens": 30941, "cacheReadTokens": 292608, "cacheWriteTokens": 0}`. The same keys are on 143208 chat 4 (stream `7e394b3b…`) and on the `result` lines of 160354's last chat, 183727, 191902, 200412, 201442, and 202606. Rotate and deadline call `finish`, which kills the process before `result`, so those chats have no usage object. No dollar figure is claimed. The sentence that said later runs have no usage object at all was wrong. Section 6 records that correction.

## 5. Byte-count appendix

Every path below was read by the line pass, by a direct read, or both. Byte counts are `stat` on 27 October 2026. "Opened" means the PNG was passed to an image read and described from the bitmap.

| Bytes | Path | How |
| --- | --- | --- |
| 123 | `main.py` | full |
| 65412 | `organism.py` | full, 1044 lines |
| 46 | `.gitignore` | full |
| 12147 | `ledger.md` | full |
| 38 | `story.txt` | full |
| 244 | `campaign.txt` | full |
| 284 | `goal.txt` | full |
| 16801 | `prompt.txt` | full |
| 1343 | `fara_traj.json` | full (one JSON value) |
| 1784 | `screen.json` | full |
| 159 | `story.meta.json` | full |
| 0 | `skills\.gitkeep` | full, empty |
| 653031 | `run_20261027_133306.md` | every line indexed; process headings read |
| 5313837 | `run_20261027_133306.jsonl` | every line, 9961, 0 decode errors |
| 183382 | `run_20261027_141013.md` | every line indexed; mind and report read |
| 1445020 | `run_20261027_141013.jsonl` | every line, 5547 |
| 26575 | `run_20261027_142947.md` | every line indexed; open and close read |
| 170471 | `run_20261027_142947.jsonl` | every line, 577 |
| 715069 | `run_20261027_143208.md` | every line indexed |
| 2174503 | `run_20261027_143208.jsonl` | every line, 6046 |
| 539109 | `run_20261027_151228.md` | every line indexed; report and close read |
| 2857609 | `run_20261027_151228.jsonl` | every line, 11035 |
| 9114 | `run_20261027_155202.md` | every line indexed; close read |
| 26017 | `run_20261027_160354.md` | every line indexed |
| 3856 | `run_20261027_182210.md` | full |
| 173499 | `run_20261027_183727.md` | every line indexed; unparsed lines read |
| 307577 | `run_20261027_190548.md` | every line indexed |
| 185642 | `run_20261027_191902.md` | every line indexed; unparsed span read |
| 174843 | `run_20261027_194156.md` | every line indexed; close read |
| 176476 | `run_20261027_195316.md` | every line indexed; close read |
| 142048 | `run_20261027_200412.md` | every line indexed |
| 310720 | `run_20261027_201442.md` | every line indexed; note span read |
| 136548 | `run_20261027_202606.md` | every line indexed |
| 14864248 | `streams\` 29 files | every line indexed; three plain-text files read in full |

The three plain streams, full text: `3e8984ce791c4490be2070da33d1170a.stream` (183), `0c5080e15dbb436b8fcf2d0d23df1a58.stream` (187), `41ff88f1f93d4dd6a12fc0546793fb8a.stream` (21).

Absent, checked: `edit_log.txt`; `run_20261027_155202.jsonl` through `run_20261027_202606.jsonl` (11 files); the four stream paths named in `run_20261027_133306.jsonl`; seven shot ids named in `run_20261027_133306.md`.

PNGs opened:

| Bytes | Path |
| --- | --- |
| 136139 | `shots\150321-5f365ccfaf8544258a485d6f540508ae.png` |
| 779447 | `shots\163405-3f1c4f7fda6c44be9fcb2dcf6888dae2.png` |
| 326352 | `shots\194709-20692ff2f6a040b9b57e6c1161f16f0c.png` |
| 252034 | `shots\200629-13c9bcd6d00d454aab3aad5d41f5b9cd.png` |
| 296592 | `shots\202813-442c32c6333947408e90ffd3379e257a.png` |
| 275663 | `shots\202821-71296d903afc4ca782610f427a0b50cb.png` |
| 306785 | `shots\200647-44041906988d464e834b82c5e6645a4e.png` |

The `200647` row was opened during the consultation in section 6. The first pass had not opened it.

Reports read for section 6, every line, on 27 October 2026:

| Bytes | Lines | Path |
| --- | --- | --- |
| 68163 | 460 | `reports\02-prompt.md` |
| 63401 | 722 | `reports\03-behavior.md` |
| 54689 | 560 | `reports\04-cost.md` |
| 46542 | 487 | `reports\05-evolution.md` |

Shot ids named in run markdowns and not opened: 20 in 143208 (other than 150321), 18 in 155202, 24 in 160354 (other than 163405), 7 in 194156 (other than 194709), 2 in 200412 (other than 200629). Each of those files exists; sizes were recorded in the index and are not repeated here. 133306's seven ids are missing.

Git objects read as text: `git log --format=fuller` for the last 8 commits and the preceding commits back through `534ce279`; `git show` of `1900589`, `233661f`, `657392c`, `0b353b9`, `4c12416`, `02cf66d`, `866dc42`, `c3887eb`; `mind.py` line 135 at `534ce279`; `organism.py` lines 70–80 and 793–980 at `6739ed1`. `git status`.

Pages retrieved 27 October 2026:

- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
- https://devblogs.microsoft.com/oldnewthing/20131209-00/?p=2433
- https://cursor.com/docs/cli/reference/output-format
- https://cursor.com/docs/cli/reference/parameters
- https://cursor.com/docs/cli/headless
- https://forum.cursor.com/t/stream-partial-output-assistant-events-have-multiple-undocumented-forms-how-should-consumers-parse-them/156289
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md

## 6. Consultation

Tuesday 27 October 2026, still. Read in full: `reports\02-prompt.md` (460 lines), `reports\03-behavior.md` (722 lines), `reports\04-cost.md` (560 lines), `reports\05-evolution.md` (487 lines). No page was retrieved for this round. The published token prices in the cost report were not re-fetched. The usage integers below were remeasured in the local streams and in `run_20261027_133306.jsonl`.

OBSERVED means the quote or the count was taken from the file again in this pass. INFERRED is marked. A claim that only one artifact supports is marked SINGLE-SOURCE.

### 6.1 Agreements

Each item was checked again against the run, the jsonl, or the stream. Where that check changed a sentence in sections 2–4, section 6.3 names the change.

- Sixteen run markdowns, the same byte sizes, `edit_log.txt` absent, HEAD `1900589`, five jsonl files and eleven absent, 33166 jsonl lines, 0 decode errors. All four reports.
- `run_20261027_155202.md` line 3 is `minutes=40.0`. Mind lines 497–499 are `failed_agent` at 10.2. The cost report's correction of a 10-minute misread agrees with section 2.6.
- 160354's markdown span is 16:03:54–16:37:54, 2040 seconds, and `ledger.md` line 22 says 34.0 of 40. Five `agent-exit` / `rotate` headings, then `stopped`. The cost report's chat table agrees. Section 6.2 keeps the prompt report's word "four".
- 133306 chat 2 rotated at `completed_calls` 43 (jsonl line 6166). The cost report agrees.
- The 5 percent rule is the prompt string at `organism.py` line 802. `drive`'s loop (line 941) does not compute a fraction. 195316 wrote `stop` at 20:03:01 and was sealed `deadline` at 20:03:17. `DOCS` line 35 says the grace is shorter when the deadline is sooner. The cost report and the prompt report treat that as the specified order. This file already did (section 4.6).
- The duplicate-click note in `233661f` (19:53:05) is appended after `execute`. `1900589` (20:25:48) skips before `execute`. 201442 sits between those commits. 202606, after the skip, has one click and one terminate. Prompt, cost, and behavior agree. This file already said the guard was not exercised.
- A `terminate` that also carries `coordinate` stays `unparsed` and leaves the log open. 191902 lines 617–618 and 194156 line 529. Prompt and behavior agree.
- 190548 lines 1141–1143: `The eye hit the 8 action limit and returned "open" (not terminated).` The prompt report quotes that think. The first pass had the cap in the code and did not quote the line. Section 2.10 now does.
- `[500, 498]` in 194156 is 17 eye headings whose action is `left_click`, from 19:47:15 through 19:51:57. Two other primary `left_click` headings are `[111, 972]` at 19:46:04 and `[149, 542]` at 19:46:13. The behavior report's 19 primary `left_click` actions are those 17 plus those two. The prompt report's 17 and the cost report's 17 are the one coordinate. Scoped, the three counts are the same measurement.
- 201442 `[283, 230]` is 18 eye headings, 20:18:36 through 20:24:29, with the `key` at 20:20:07 after the fifth. The cost report's 18 agrees after the correction in section 2.15.
- 182210 and 142947 have no `mind` heading and no ledger row. 142947's jsonl has no `wake_end`, and stream `0b10ead…` has no `result`. The evolution report agrees that those two wakes wrote no lesson. Section 6.2 keeps the behavior report's SINGLE-SOURCE label.
- `190548` stored `No lesson was written.` because `drive` calls `enter` with `current_lesson` (`organism.py` line 979) and line 739 fills the empty string. The evolution report's chain B agrees.
- `newest` returns one lesson (`organism.py` lines 735–737). The evolution report's inheritance account does not contradict a systems count in this file.
- Zero of the 16 run markdowns contain the string `120416`. The evolution report's search of those files stands. `run_20261027_133306.jsonl` contains `120416` 21 times, inside the prompt payload. That jsonl is a third store. The evolution report did not read the jsonl. This does not reverse their run-file claim.
- Shot `202821` is File Explorer titled This PC. Shot `202813` is the Poland weather card with wind 6 km/h. The prompt report and the evolution report agree on the This PC title. The evolution report did not open `202813`.
- The eight `usage` objects remeasured here match the integers the cost report quotes. 133306 jsonl line 9957: `inputTokens` 73067, `outputTokens` 30941, `cacheReadTokens` 292608, `cacheWriteTokens` 0. Stream results: 143208 chat 4 `134680 / 21767 / 364800 / 0`; 160354 last chat `114156 / 26063 / 662016 / 0`; 183727 `45981 / 14292 / 530048 / 0`; 191902 `130621 / 13548 / 955008 / 0`; 200412 `78179 / 22031 / 914432 / 0`; 201442 `107311 / 23585 / 2132224 / 0`; 202606 `129589 / 22590 / 881024 / 0`. `wake_end` still says `not metered`. The cost report's dollar column is a list-price inference. This file does not adopt it.

### 6.2 Disagreements kept

Each one was checked against the file again. The other report's sentence is quoted. The local line is quoted. They are not averaged.

1. Prompt report, `run_20261027_160354.md` section: "Six chats, four of them `rotate` (16:10:01, 16:14:26, 16:21:35, 16:27:35, 16:32:38)." The parentheses list five timestamps. Remeasured `agent-exit` headings: `rotate` at 16:10:01, 16:14:26, 16:21:35, 16:27:35, and 16:32:38, then `stopped` at 16:37:54. The word "four" does not match the list or the file. Section 2.7 keeps five.

2. Same paragraph: "Three errs, each … (16:05:42, 16:30:24, 16:31:45, 16:34:23 — four headings)." The file has four `err` headings with that text. The behavior report also says four. The word "three" is the disagreement. Section 2.7 keeps four.

3. Prompt report, 194156: "No ledger lesson from the model; the lesson now in `ledger.md` lines 30–31 was written later." `run_20261027_194156.md` line 702 is `## 19:49:10 ledger` with `{"wakes": 15, "lesson_len": 681}`. Lines 688–690 are the edit that wrote that paragraph into `ledger_now.txt`, and `ledger.md` line 31 is still that paragraph, including `From 19:47:15 through 19:48:38`. The evolution report's count of one ledger heading for this wake agrees with line 702. The lesson was written at 19:49:10, while clicks continued through 19:51:57. "Written later" does not match the heading.

4. Prompt report, 200412: "Wind on that later frame is given in the image description as 6 km/h, not the 8 km/h she claimed at 20:07. … I do not call 8 a hallucination." The later frame is `202813` at 20:28. Shot `200647` (306785 bytes), opened in this pass, shows `Wiatr: 6 km/h` at tray clock 8:06:47 PM. The eye heading at `run_20261027_200412.md` line 326, 20:07:04, says `wind 8 km/h`. Seventeen seconds separate the bitmap from the answer. The behavior report's wind claim matches this bitmap. The prompt report's refusal does not. Section 2.14 now records the frame. The first pass had opened `200629` (20:06:29), which is the new-tab page before the card, and had not opened `200647`.

5. Prompt report, 155202: "Chat 2 at 16:01:24 is `rotate`, then `failed_agent` at 16:02:17." `run_20261027_155202.md`: `## 16:01:24 agent-exit` / `rotate` closes chat 1, and `## 16:01:24 chat` starts chat 2. Chat 2's exit is `## 16:02:17 agent-exit` / `failed_agent`. The rotate heading belongs to chat 1.

6. Evolution report, table row 151228: "chats seen" = 2. Markdown `start` headings are 2 (section 2.5). Jsonl `chat_prompt` lines are three: line 2 chat `:1`, line 6305 chat `:2`, line 11029 chat `:3`. `agent_exit` lines are three: 6304 `rotate`, 11028 `rotate`, 11033 `{"returncode": 2, "reason": "failed_agent"}`. The 2 matches `start`. It drops the spawn that died before a `start` heading. Section 2.5 keeps three chats.

7. Behavior report, 142947: "SINGLE-SOURCE for the abrupt end: only this journal records it." And section 4.9: "No second file explains the kill." The missing seal is in four places: the markdown ends at line 167 on a `started` grep; the jsonl has no `chat_end`, no `agent_exit`, and no `wake_end`; stream `0b10ead822cc4433946996d2ad935fa5.stream` has no `result`; `ledger.md` has no `20261027_142947` row. The killer's name is still absent. That absence is the inference in section 2.3 (confidence medium). The end itself is not single-source. The evolution report's section 2.3 calls the cause of death SINGLE-SOURCE and then cites the missing headings plus the missing ledger row, which is already two files.

8. Behavior report, section 4.8, on 141013: "Why the agent failed is SINGLE-SOURCE (the ledger's status word)." `ledger.md` line 14 says `failed_agent`. The cause text is also stream `3e8984ce791c4490be2070da33d1170a.stream` and jsonl line 5543: `can't open file '...\job_boot.py'`. The markdown has no `err` heading for that string. That narrower gap is real. The cause is not single-source.

9. Cost report, 195316: "`[205, 611]` twice". The eye headings at that coordinate are three: 19:59:14, 19:59:24, and 20:02:36. `ledger.md` line 33 names the 20:02:36 click separately (`then [205, 611] at 20:02:36`). Section 2.13 keeps three. The same remeasure is why this file no longer says `[160, 741]` twice.

### 6.3 Corrections to this file

What changed, and why.

- Section 4.6. Removed "Later runs have no usage object at all." The `wake_end` string `not metered` is the rollup refusing to sum (`run_20261027_133306.jsonl` line 9960 and the three sibling `wake_end` lines). `type=result` still carries `usage` on the eight chats listed in section 6.1. Rotate and deadline still kill the process before that line. No dollar figure was added. No price page was opened.

- Section 2.10. The first-pass eye counts (left_click 8, left_click_drag 8, mouse_move 5, terminate 4, unparsed 2) counted seven cumulative echo headings as new actions. Primary actions are left_click 4, left_click_drag 6, mouse_move 4, terminate 4, unparsed 2, which is the behavior report's split. The two `[110, 972]` headings at 19:07:34 and 19:07:59 are echoes of the 19:07:25 click. Added the 19:15:48 think quote, which the first pass had not copied.

- Section 2.11. The first-pass counts (mouse_move 9, unparsed 5) included five echo headings. Primary actions are mouse_move 7, left_click_drag 7, terminate 2, unparsed 2.

- Section 2.12. Added the `## 19:49:10 ledger` heading and the identity of `ledger.md` line 31 with the 19:49:04 edit. The first pass had not said the lesson was written later. The sentence was added because the prompt report says it was, and the heading says 19:49:10.

- Section 2.13. `[160, 741]` is three headings, not two. Added the pairs the first pass omitted, including `[266, 609]` twice and `[205, 611]` three times. The left_click total of 20 was already the primary count and was left as printed.

- Section 2.15. `[283, 230]` is 18 headings beginning 20:18:36, line 446. The first pass began the series at 20:18:54 and missed one heading. The cost report's 18 is the remeasure.

- Section 2.14, the method paragraph, and the PNG appendix. Opened `shots\200647-44041906988d464e834b82c5e6645a4e.png` (306785 bytes). The first pass had described `200629`, the new-tab page at 20:06:29, and had not looked at the card that was already on screen at 20:06:47.

Section 3.8's "Seventeen `left_click` headings at `[500, 498]`" was remeasured and left as printed. The "16 further" sentence in section 2.12 is those 17 minus the 19:47:15 heading. Both stand.

`[500, 498]` in section 4.2 still starts the continued series at 19:47:31, after the unparsed terminate at 19:47:24. The first click of the seventeen is 19:47:15, before that unparsed heading. The sentence was not rewritten. The count of seventeen is in section 3.8 and section 2.12.
