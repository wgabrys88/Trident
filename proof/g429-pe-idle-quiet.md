# G429 PE idle quiet

Written 2026-09-29. The listener on `0.0.0.0:8765` was not restarted. `gemma.py --stop` was not run. No POST was sent to that listener. `agent -p` was not started. The live `gemma.memory.txt` was not given a work line.

## What changed

The HTTP accept loop runs one `gemma.py --idle` after 60 seconds with no POST, when no connection is waiting and the resident is not already in a generate. `--idle` is still one notice. It does not poll by itself. `--drop` does not run it. A request, including a rejected one, starts the 60 seconds again. A speakable result is written to the worker stdout. The word `idle` is not.

The process already listening on `:8765` does not have this loop. It keeps the code it started with. A later cutover is what loads it.

A result is not spoken. Iris is not called.

## How this was checked

`py_compile` of `nvidia_worker.py` and `gemma.py` succeeded. Offline checks used temp files and a substituted runner. `Popen` was replaced for the resident generate so a tool start could not create a process. No process was started that way.

- A quiet stretch shorter than 60 seconds does not call. At 60 seconds the argv is the venv Python, `gemma.py`, and `--idle`. It has no `--stream` and no fast slug. The next call waits another 60 seconds.
- Stdout `idle` is not written onward. Stdout `The lamp is blue.` is.
- A waiting connection does not call. A resident prompt already in flight does not call. A timed-out notice does not raise out of the accept loop, and the clock still moves. Exit 2 does the same.
- `--drop` does not call the quiet function. A POST does reset the clock.
- A temp memory file with one fact and two work lines: the prompt has `alpha line`, not `beta line`, and no `<|channel>thought`. The spoken pair is `idle: alpha line` / `The lamp is blue`. The fact and `beta line` stay. The live memory file and `gemma.lastprompt.txt` were restored to the same hashes.

Second port, `127.0.0.1` only:

- `:18766`, quiet interval 1 second, substituted runner. One argv, the same `gemma.py --idle` shape, about 1.5 seconds after the bind. The port was not listening afterward.
- `:18767`, quiet interval 2 seconds. `POST {}` returned 400 `empty text`. No notice during the next second. One notice after the quiet interval, and that substitute's stdout was written. The port was not listening afterward. A `TIME_WAIT` socket from that POST remained.

Live `run_quiet_idle`, because the checkout memory had no work line:

```text
nvidia worker: idle
gemma: idle none
nvidia worker: idle 136 ms
```

Stdout `idle`. Memory and `gemma.lastprompt.txt` hashes unchanged.

Live resident generate, temp memory only, work line `Say one short sentence: the lamp is blue.` The resident fingerprint matched, so the running brain was kept. `Popen` was replaced and was not called.

- Stderr `gemma: idle work Say one short sentence: the lamp is blue.` then `gemma: resident pid 2064` and `gemma: idle no tool`.
- The prompt contained `Next work: Say one short sentence: the lamp is blue.` and declared `next`. It had no `<|channel>thought`.
- One generate. `gemma.run.err` appended `resident generate 996 ms`.
- Speakable text: `The lamp is blue. I will wait for instructions.`
- The temp work line was dropped. The stored pair is that idle line and that sentence. No tool call. `grok_bot_spawn.txt` was not written.

Before and after this whole check: `gemma.memory.txt` sha256 `410fb9f2b43ceb8b22cf0da2ff80058cb85c7b8c7d14c78486d67b17d6980546`. `gemma.lastprompt.txt` sha256 `2695898ed12a349cea9c0f9db8b9085726e05652dfec83563e37c8898b76a242`. `grok_bot_spawn.txt` absent. `gemma.pid` pid 2064, state `ready`, fingerprint `c2574aa2b65540eceab59b8c108163af5fe73e3daa1a5197c1b4c5bdc240cced`. `netstat` showed `0.0.0.0:8765` LISTENING, pid 8004. No established connection on that port. `gemma.busy` and `gemma.prompt.txt` were absent.

## Not run

A restart of pid 8004. That process still does not drain a quiet stretch. No POST to `:8765`. No `agent -p`. The speakable sentence was not sent to Iris.

Voice, mouth, and ASR files were not edited.

## Blockers

None.
