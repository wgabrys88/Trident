# NVIDIA seat (PE)

Checked-in code outranks this file. If this note, `README.md`, `Scout-report-NVIDIA.md`, or a file under `proof/` disagrees with `nvidia_worker.py`, `nvidia_start.py`, `nvidia_stop.py`, `nvidia_client.py`, or `gemma.py`, follow those programs.

This checkout is the PE brain seat: `C:\Users\px-wjt\Downloads\Jarvis\Trident` on `trident-nvidia`, account `px-wjt`. It owns the Gemma resident and the HTTP worker. It does not own the microphone or the mouth. Iris posts here. This seat does not bind a second listener to take a turn.

## Reality vs tip (2026-09-29)

| Item | State |
| --- | --- |
| Branch | `runner-h` at `1bafab3`, same as `origin/runner-h` (PR #40). Not ahead, not behind. |
| Uncommitted product | none. `git status` clean before this note. |
| Untracked, not ignored | none before this note. `.gitignore` is a whitelist (`*` then `!`). |
| Stashes | none. |
| Worktrees | this directory only. |
| Local branches | `runner-h`, `main` (`d535349`, ancestor of tip), `cursor/pe-brain-finish-df7c` (`51bf933`, ancestor of tip, also on origin). No local-only branch. |
| `Downloads\Jarvis` | this `Trident` directory only. |

`0.0.0.0:8765` was LISTENING, pid **2636**, `python.exe` `nvidia_worker.py --host 0.0.0.0 --port 8765`, started 2026-09-29 19:21:25. A TCP connect to `127.0.0.1:8765` accepted. The process was left up. No POST, no `nvidia_stop.py`, no restart.

That process is the worker loaded at 19:21. `nvidia_worker.py` on disk was last written 18:03 and last committed `d77f577` (17:53), so the listener matches the worker source on this tip. Each turn and each quiet notice starts a new `gemma.py`. `gemma.py` was committed again at 22:03, 22:06, and 22:25 (`81a90a5`, `d7f279c`, `51bf933`). The next POST runs that file. Do not restart `:8765` to load `gemma.py`.

`gemma.pid` says resident pid **2064**, state `ready`. That file, `gemma.memory.txt` (14674 bytes), `iris_outbox.txt`, `iris_status.txt`, `nvidia_turn.*`, and `*.run.err` are gitignored run state. They are not a commit.

Commit classes on this disk:

| Class | What |
| --- | --- |
| Commit-ready | Tip product tree, plus this note once it is the only addition. |
| Dirty, leave untracked | Live memory, outbox, status, turn files, worker and resident logs, built exes, weights, `.venv`, `.install`. |
| Orphan, not a patch | Ignored noon specs and old brain notes listed at the bottom. They are not in git and they are not the tool contract. |

Other Trident folders under `Documents` (`6rfthfh`, `11one11\1`, `11one11\2`, `11one11\Jarvis`, `435tergdf`) are clean `runner-h` checkouts whose HEADs are ancestors of `1bafab3`. `Documents\11one11\2\Trident` also has local `cursor/wave2b-nvidia-comment-strip-fe13` at `5e1d235` with upstream gone. That commit is not in this repo. Tip `nvidia_worker.py` already defaults `--host` to `0.0.0.0`. Do not resurrect it. `Documents\43t4gdfg\trident-1c7feaa8` is not a git checkout.

## Start, stop, leave healthy

`nvidia_start.py` takes no arguments. It runs `.venv\Scripts\python.exe nvidia_worker.py --host 0.0.0.0 --port 8765` only when port 8765 is free (listen pid, or a TCP accept on `127.0.0.1`). If the port is busy it exits 2 and prints `leaving it alone`. It does not stop a listener.

`nvidia_worker.py` defaults to `--host 0.0.0.0` and `--port 8765`. If that port already accepts, or the bind fails because it is in use, the process prints that it is leaving the listener alone and returns. It does not kill it. `allow_reuse_address` is false.

`nvidia_stop.py` without `--cutover` exits 2 and stops nothing. `--cutover` is the owner or PM cutover. It stops `python.exe` / `pythonw.exe` whose command line is `nvidia_worker.py` and that own the listen socket, including the launcher parent. It refuses a listen pid that is not that worker. It does not stop `gemma-brain.exe`. Digs do not pass `--cutover`.

`gemma.py --stop` unloads the resident. That is not the HTTP stop.

## Text POST and early flush

Production is HTTP POST. `--drop` is a local `nvidia_turn.request.txt` / `nvidia_turn.response.txt` inbox (`id`, then `ok` or `err`). It is not the LAN path. There is no GET.

POST body is JSON, at most 16_000_000 bytes. `text` is a non-empty string. Optional `id`. Optional `image` (a path on this machine) or `image_b64` (standard base64, written to a temp file, then passed as `--image`). Optional `stream`: `true` or `false`.

The worker returns text. It does not return audio.

| `stream` | Response |
| --- | --- |
| absent or false | `200` `application/json` `{"text": ...}` after `gemma.py` exits. |
| true | `200` `text/plain` chunked. Each piece is one chunk, then a blank line (`\n\n`) and the chunk terminator. |

Early flush is the stream path. `gemma.py --stream` uses `SpeakFlush`: a speakable sentence is written when the next word starts, tool markup and text before `<channel|>` are not written, and the tail is written when generation finishes. The worker reads that pipe with `bufsize=0` and `write_chunk` flushes each piece (`wfile.flush()`). `nvidia_client.py --stream` prints pieces as they arrive and does not print the blank line. A POST without `stream` waits for the full generation.

`gemma.py` timeout default on the worker is 600 seconds. A timeout kills that `gemma.py` process, not a healthy listen socket by itself.

When no POST has arrived for 60 seconds, no connection is pending, and the resident is not busy (`gemma.busy` or `gemma.prompt.txt`), the accept loop runs one `gemma.py --idle`. `--drop` does not. The word `idle` is not written to the worker stdout. A speakable notice is.

## Alone vs LAN

`gemma.place` is one decision: device names and one TCP connect. The computer name is not a key. It does not bind port 8765.

| Situation | `Place.brain` | Who runs the turn |
| --- | --- | --- |
| A peer URL accepts | `post` | `nvidia_client.py` to that URL. `assistant.py` prints `assistant: lan`. |
| No peer URL, and `127.0.0.1:8765` accepts | `post` to `http://127.0.0.1:8765/` | Same POST. `assistant.py` prints `assistant: lan`. |
| Port closed, this PC has a CUDA device | `resident` | `gemma.py` on this PC. `assistant.py` prints `assistant: alone`. |
| Port closed, no CUDA device | `cpu` | `qwen.py`, or `gemma.py` when `--brain gemma`. `assistant.py` prints `assistant: alone`. |
| A named peer does not accept, or the URL is not http(s) | `missing` | `grok_local_bot.py` returns `peer missing` and does not start `--drop`. `assistant.py` prints `assistant: peer unreachable`, then calls `place` with no URL and uses the row above. `nvidia_client.py` says `peer missing` when the TCP open fails. |

`where` is `local` when the host is an address on this PC, otherwise `peer`. `flip` is true when the brain is on this PC, CUDA and Vulkan device 0 are the same adapter, and a mouth here would share that GPU. `assistant.py` then runs the same stop as `gemma.py --stop` before `mouth.py`. The worker does not stop the resident between turns.

On this seat the listener is up, so a local placement is `post` / `lan`. Iris, with no local listener, is `lan` only when `192.168.16.31:8765` accepts. If that NIC address changes, change the URL. Keep port 8765.

## Memory and tools

Text turns replay `gemma.memory.txt` and declare tools. Python runs a tool only when the generation contains `<|tool_call>call:NAME{...}<tool_call|>`. It does not match keywords in the question. Image turns skip tools and memory replay, then append the reply. Facts and work lines are clipped to 200 characters. Duplicate facts and duplicate work lines are kept once. Prompt cap is 80_000 characters. Oldest turns drop first. A fact drops only after every turn is gone and the prompt still does not fit.

The store is blocks `fact`, `work`, `user`, `model`, each closed by a line `<<`.

| Call | Meaning |
| --- | --- |
| `remember` `line` | Append one fact on the machine that ran `gemma.py`. Ask the brain once more. |
| `place` | Report CUDA device 0, Vulkan device 0, whether they are the same adapter, whether `127.0.0.1:8765` accepts, and whether a mouth here would share that GPU. Ask once more. No hostname. |
| `cursor` `task` | Start one local `agent` (`composer-2.5`, worktree base `runner-h`) and do not wait. A missing CLI is `BLOCKED` and `grok_bot_spawn.txt`. A fast model id is refused. Empty task does not start a follow-up. |
| `next` `line` | Append one work line. Do not run it. `iris_status.txt` gains `work` plus that line. Ask once more. |
| `stop` | Leave the call in the answer. Do not ask again. Do not unload `gemma-brain.exe`. Do not close port 8765. Empty `line`, or a line that is not waiting work: `iris_status.txt` gains `stop voice`. A line that matches waiting work drops that line and the status line is `stop` plus the line. |

`--idle` with no work line prints `idle` and does not generate. One work line is one prompt. The line drops after a speakable answer. A failed tool leaves the line. An empty generation is an error and leaves the line. An empty follow-up is replaced by the tool result, not a made-up sentence. A speakable idle answer replaces `iris_outbox.txt` (one line, 2000 characters, via `iris_outbox.txt.tmp`) and appends `say` to `iris_status.txt`. Status keeps the last 40 lines, each clipped to 500 characters.

`remember`, `place`, and `next` are the tools that ask again. `stop` does not. There is no `hello` tool and no `devices` tool. `place` is the GPU report. `devices_report` is the function that formats that report.

## Proofs

Dated notes live under `proof/`. They record a run. They are not the contract when `gemma.py` has moved.

Still aligned with the code above: `proof/README.md` (outbox and status lines), `proof/g429-pe-brain-finish.md` (`remember`, `place`, `next`, `stop`, quiet drain, pid 2636 left up), `proof/g429-pe-stream-flush.md` (sentence flush and `bufsize=0`; its listen pid 2184 was an earlier process), `proof/g429-pe-idle.md`, `proof/g429-pe-idle-quiet.md`, `proof/g429-pe-memory.md`.

## Later tip-cleaner

Do not delete these in a dig. They are tracked and stale as a brain manual:

| Path | Why it is stale |
| --- | --- |
| `Scout-report-NVIDIA.md` | Audits tip `8709668` and names tools `remember` / `devices` / `cursor` / `next`. Live tip is `1bafab3`. The tool is `place`. `stop` is missing from that list. |
| `Scout-report-Iris.md` | Iris disk scout, not this seat. Same stale remote table. |
| `proof/g429-pe-tools.md` | Says text turns declare `devices`. |
| `proof/g429-pe-memory-live.md` | Records `call:devices`. |
| `proof/g429-pe-place.md` | Title and body still say tool `devices` / `run_devices`. |

`proof/g429-pe-merge.md` and `proof/iris-merge-runner-h.md` are merge ledgers. The branch name `pe-place-devices` in those tables is history, not the live tool.

Ignored on this disk, not commit-ready, and not the current brain (noon specs and the hello-tool note):

- `proof/architecture-2026-09-29.md`
- `proof/live-voice-plan.md`
- `proof/unify-iris-pe.md`
- `proof/g429-pe-brain.md`

Ignored logs, stdout captures, and memory backups under `proof/` (`g429-*.txt`, `nvidia-worker-*.txt`, `g429-pe-start-stop.md`) are run debris. `proof/g429-pe-start-stop.md` is the note of the 19:21 cutover that left pid 2636. It is evidence, not a source patch.
