# G429 PE tools — the brain calls them

Written 2026-09-29. The listener on `:8765` was not restarted. `gemma.py --stop` was not run. No POST was sent. `agent -p` was not started.

## What changed

Text turns still declare `remember`, `devices`, and `cursor`. `remember` and `devices` still ask the resident once more. `cursor` runs only when the generation contains that call.

`cursor` takes `task`. It starts one local `agent -p --force --trust --worktree --worktree-base runner-h --model composer-2.5` and returns without waiting. The prompt appended in the process tells it to open the pull request into `runner-h` and not to kill a listening port 8765. The argv has no fast model id and no fast flag. `composer-2.5-fast` is refused before the process starts.

A missing `agent` writes `BLOCKED` and `agent cli missing` to `grok_bot_spawn.txt` and does not start a follow-up. It does not run `cursor --list-extensions` or `cursor --version`. An empty task is `fail empty task`. The old `job` argument is not a task, and an empty task is not rewritten to `extensions`. An unknown tool name is the spoken line `unknown tool` plus that name. There is no second generate for those failures.

`grok_local_bot.py` no longer calls the cursor tool when the model did not. The reasoner prompt no longer orders that call. `--proof` no longer requires a spawn for PASS.

## How this was checked

Offline, temp files only. `gemma.memory.txt` was not rewritten. `grok_bot_spawn.txt` was not created.

- A cursor call parses `task`. A call that only has `job` does not.
- One fact stored in a temp file is in the next prompt. That prompt declares `task`, has no `list-extensions`, and has no `<|channel>thought`.
- An inbox prompt declares no tools.
- `devices` on this machine: CUDA `NVIDIA GeForce GTX 1060 6GB`, Vulkan `NVIDIA GeForce GTX 1060 6GB`, adapter `same`. The line has no hostname.
- The built argv is `composer-2.5`, worktree base `runner-h`, and the port warning. It does not contain `--list-extensions` or a fast slug. Replacing the model id with `composer-2.5-fast` is refused.
- Empty task, a `job` of `extensions`, a missing `agent`, and a path that is not an executable all stop before a follow-up. The spawn text does not mention the IDE shim.
- A started result (the function returned `started local pid 9` without creating a process) is one follow-up. The follow-up prompt contains the tool response and does not open a thought channel.
- An unknown tool is `unknown tool nope` and is not skipped.
- `spawn_kind` reads `BLOCKED`, `fail`, a `started` pid, and an empty log.
- `gemma.py` has no `list-extensions`. `grok_local_bot.py` has no `run_cursor_job`.

`where agent` resolves first to `C:\Users\px-wjt\AppData\Local\cursor-agent\agent.cmd`. A second `agent.exe` under `.grok\bin` is later on `PATH` and is not the one `find_agent` returns. `agent models` exited 0. The list contains both `composer-2.5` and `composer-2.5-fast`. The argv uses `composer-2.5` only.

`py_compile` of `gemma.py` and `grok_local_bot.py` succeeded.

Before and after: `gemma.memory.txt` sha256 `b29679a284c7a9177ef2345c9edf9f2cfe67d78ccfbae42251c85341cc30e4a2`. `grok_bot_spawn.txt` absent. `gemma.pid` pid 2064, state `ready`, same fingerprint. `netstat` showed `0.0.0.0:8765` LISTENING, pid 10044.

## Not run

A generate that emits `cursor`. Starting `agent -p --force` would open a worktree and edit code. A devices or remember POST would write the live memory file the listener already uses. The earlier devices turn on this resident is in `proof/g429-pe-memory-live.md`. This check did not add another one.

Voice, mouth, and ASR files were not edited.
