# G429 PE idle notice

Written 2026-09-29. The listener on `:8765` was not restarted. `gemma.py --stop` was not run. No POST was sent. `agent -p` was not started. A work line was not given to the resident.

## What changed

`next` stores one work line in `gemma.memory.txt`, at most 200 characters. A duplicate is kept once. A spoken turn does not replay those lines. `remember` still stores facts, and a trim of old turns leaves the work lines in the file.

`gemma.py --idle` is one notice. It is not a loop, and the process on port 8765 does not call it.

- No work line: stdout is `idle`. The resident is not asked.
- One work line: one prompt. The line is the user turn. Tools stay declared. Python does not choose the tool.
- A tool runs only when that generation contains the call. `cursor` is the same local `agent` start as a spoken turn: `composer-2.5`, worktree base `runner-h`, no fast slug.
- The line is dropped after a speakable answer. A failed tool leaves the line. An empty generation is an error and leaves the line.
- An empty follow-up is the tool's own result line. It is not the word `done`.
- A second stored line waits for another `--idle`.

## How this was checked

Offline, temp files only, then one live `--idle` because the checkout memory had no work line. `Popen` was replaced so the started path returned pid 9 and did not create a process. `gemma.memory.txt` was not rewritten. `grok_bot_spawn.txt` was not created.

- An old memory file with a fact and one pair still parses. A `work` block round-trips.
- One stored line is in the file. The same line stored again stays one line. A 250-character line stores as 200.
- An empty `next` line, and a `next` call that only has `task`, do not write a work line.
- A spoken prompt hides `WORK-LINE-ORANGE`, declares `next` and `cursor`, and has no `<|channel>thought`. The idle prompt shows that line. An inbox prompt and an image prompt declare no tools.
- No work line does not call generate. Memory pairs stay put.
- A generation that says `I should use cursor.` does not start `agent`. The work line is dropped and the pair is `idle: fix the door prompt`.
- Two lines: the prompt has the first and not the second. After the answer, only the second remains.
- A `cursor` call with a stub process is one follow-up. The argv is `composer-2.5`, `--worktree-base runner-h`, and the port warning. It has no `--list-extensions` and no fast slug. The spawn text says `started`. The follow-up prompt contains the tool response and does not open a thought channel.
- An empty follow-up after that start is the line `started local pid 9`. The work line is dropped. The stdout is not `done`.
- A missing agent writes `BLOCKED` and leaves the line. No follow-up. A `job` of `extensions` is `fail empty task` and leaves the line. Replacing the model id with `composer-2.5-fast` is `fail fast forbidden` and leaves the line. The real argv guard still rejects that id.
- `next` during the notice stores `beta line` and drops `alpha line`.
- An unknown tool is `unknown tool nope` and leaves the line.
- An empty generation exits 2 and leaves the line.
- `remember` of `lamp is blue` keeps the work line. Appending a spoken pair keeps it. A trim that drops a long pair keeps `KEEP-WORK`.

`py_compile` of `gemma.py` succeeded. The offline script exited 0.

Live, because `oldest` work was empty:

```text
.\.venv\Scripts\python.exe .\gemma.py --idle
```

Stderr `gemma: idle none`. Stdout `idle`. Exit 0.

`gemma.py --idle --stream` exited 2 with `usage: gemma.py --idle`. `gemma.py --stop --idle` exited 2 with `usage: gemma.py --stop`.

Before and after: `gemma.memory.txt` sha256 `568b0deea57a288d2b1c50f48feab942a82589bf2d7799e703105a755173160d`. `gemma.lastprompt.txt` sha256 `55cfa448e9268553a7539758befa5163c0178bdf300d9333dd10aa88f967878c`. `grok_bot_spawn.txt` absent. `gemma.pid` pid 2064, state `ready`, fingerprint `c2574aa2b65540eceab59b8c108163af5fe73e3daa1a5197c1b4c5bdc240cced`. `netstat` showed `0.0.0.0:8765` LISTENING, pid 8004.

## Not run

A generate that sees a work line. That would use the resident this listener already has. Starting `agent -p --force` would open a worktree. No POST. No second worker.

The next text POST loads `gemma.py` from disk, so that prompt declares `next`. This check did not send one.

Voice, mouth, and ASR files were not edited.

## Blockers

None.
