# Trident

Trident is a living program. It wakes on one owner sentence, does what the sentence asks on this Windows computer (in files, on the screen or on the web), writes what it learned and can change its own tools before it sleeps again.

## Architecture

| Part | What it is | Where |
| --- | --- | --- |
| Mind | Grok (`grok-4.7-xhigh`) run headless by the Cursor CLI (`cursor-agent`). It acts only by running shell commands, and the tools below are among them. | `main.py --mind` |
| Retina | Fara1.5-4B, a vision model served by llama.cpp on the NVIDIA PC, model alias `retina`. It names what is on a capture and where (0-1000 scale) and proposes one action. | `main.py --retina` |
| Jev | `typesafe/jev-1.14` on OpenRouter's Decisions API. It answers yes/no, choice, score and box questions about the story, the ledger and the current capture. | `main.py --jev` |
| Hand | Win32 mouse, keyboard, capture and window queries through ctypes. | `win32.py`, `main.py --hands` |
| Watch | A windowless 4 Hz pixel sampler. Every hand action is checked against it, and an unexpected change interrupts the act. | `scan.py`, `main.py --scan` |
| Memory | `story.txt` holds the checklist and notes of the current wake. `ledger.md` holds one lesson per wake and is fed to every later wake. `--recall` digests the journals of earlier wakes. | `main.py --story/--ledger/--recall` |
| Skills | `skills/NAME.py` files the mind grew. Each one becomes the tool `--NAME` from the next call. | `skills/` |
| Journal | Every call is logged to `run_<id>.md` and served live at `http://<this-pc>:8765`. | `main.py --journal` |

`main.py --help` is not needed: every public method of `Engine` is a flag. Its docstring (`$CALL --flag ARGS`, then what it returns and when to use it) is exactly what the mind is shown under TOOLS.

The design follows the review of the 10-23 wake (deck `ref/page-01..15`):

- The watch gates every act, including Python run through `--hands`. The rule is enforced in code, not asked for in the prompt (p8, p15 rule 1).
- Soft task rules were removed from the prompt, because the mind rationalized them away (p6, p7, p15 rule 2).
- The mind is told to work in short probe-measure-adjust loops (p3, p4, p11).
- The mind is told to read its own captures (p5).
- Retina's answers are corroborated with OS window queries via `--windows` (p9, p15 rule 3).
- Retina answers every question fresh, with no earlier replies to anchor on (p9).
- The final report has three layers: output, trajectory and task (p13).
- Over-claiming is named as a failure (p14).

## Prerequisites

- Windows 10/11 and Python 3.11.
- Node-based Cursor CLI installed (`%LOCALAPPDATA%\cursor-agent\versions\<ver>\index.js`). Trident starts it with a private profile in `agent\` (ignored by git). Sign in once with that profile, or set `CURSOR_API_KEY`.
- An OpenRouter key: put the bare key as the only content of `..\.env`, the folder above this repo (for example `Jarvis\.env`). Trident reads it itself, and the mind never sees it in its environment.
- The NVIDIA PC running Retina (below), reachable at `TRIDENT_VISION_URL`.

## Setup

```powershell
cd C:\Users\eb-wjt\Downloads\Jarvis\Trident
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install opencv-python pillow pywin32 pytest pyflakes
```

The watch runs as `.venv\Scripts\pythonw.exe scan.py`, so the venv must exist in this folder.

## Starting Retina (on the NVIDIA PC)

Use llama.cpp b11756 with the CUDA 12.4 build (sm_61, GTX 1060); the Vulkan build is not used. `--image-min-tokens` must equal `FARA_MIN_TOKENS` (256) in `main.py`. The first look after a cold cache is slow.

```powershell
$d = 'C:\Users\px-wjt\AppData\Local\Programs\llama.cpp-cuda-12.4'
$m = 'C:\Users\px-wjt\AppData\Local\Programs\llama.cpp\models'
Set-Location $d
& "$d\llama-server.exe" -m "$m\Fara1.5-4B-Q4_K_M.gguf" --mmproj "$m\mmproj-Fara1.5-4B-Q8_0.gguf" --host 0.0.0.0 --port 1234 --alias fara-1.5-4b,retina --device CUDA0 --split-mode none -ngl all --fit off --override-tensor 'token_embd.weight=CUDA0' --mmproj-offload --mmproj-device CUDA0 --kv-offload --op-offload -c 28672 -np 1 -fa on -ctk q8_0 -ctv q8_0 -t 8 -b 2048 -ub 1024 --image-min-tokens 256 --image-max-tokens 4096 --mtmd-batch-max-tokens 4096 --reasoning off -n 512
```

## Waking Trident

```powershell
& 'C:\Users\eb-wjt\AppData\Local\Python\pythoncore-3.11-64\python.exe' 'C:\Users\eb-wjt\Downloads\Jarvis\Trident\main.py' --work 'C:\Users\eb-wjt\Downloads\Jarvis\Trident' --mind "SENTENCE" [MINUTES]
```

- `SENTENCE` is the task, in plain words. It is saved to `goal.txt`, and an empty sentence keeps the previous one.
- `MINUTES` is optional. With a budget, every tool result carries `clock.used` and `clock.left`, and the mind plans around it. Without one, only `clock.used` is shown, and a budget named in the sentence is kept by the mind.
- `--mind` clears a leftover `stop` file. `main.py --stop` (from the mind or from you) ends the wake: the mind process tree is killed with its job object, the watch stops and the ledger is sealed.
- Ctrl+C in the console interrupts the wake the same way.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRIDENT_MINUTES` | empty | Budget used when `--mind` gets no MINUTES. |
| `TRIDENT_VISION_URL` | `http://192.168.16.31:1234` | llama.cpp server for Retina. |
| `TRIDENT_VISION_MODEL` | `retina` | Model alias on that server. |
| `TRIDENT_FARA_WIDTH` | `1440` | Width captures are scaled to for Retina. |
| `TRIDENT_SHOT_DELAY` | `4` | Seconds `--capture` waits before grabbing. |
| `TRIDENT_JEV_MODEL` | `typesafe/jev-1.14-20261014` | Jev model on OpenRouter. |
| `TRIDENT_RUN` | start time | Wake id; names `run_<id>.md`. Set by `--mind` for its children. |
| `TRIDENT_START`, `TRIDENT_END` | set by `--mind` | Wake clock; `TRIDENT_END` is empty without a budget. |

## How the organism evolves

1. **Story.** During a wake the mind keeps its numbered checklist and its notes in `story.txt` (`--story`). Jev reads it with every question.
2. **Ledger.** Before it ends, the mind writes one lesson (`--ledger "LESSON"`, at most 1200 characters). When the wake ends, Trident seals the entry as `## wake <id> <date> used N[ of M] min, <state>`. The last 12 entries (at most 8000 characters) are put into the next wake's prompt under LEDGER, so the mind starts from what failed and what to do first.
3. **Recall.** `--recall` digests the journals `run_*.md` of earlier wakes: for each one the time span, the sentence, the count of each tool call, the errors and the start of the final message.
4. **Skills.** When a tool fails it, the mind should fix the tool or grow one instead of working around it. A skill is `skills/NAME.py` defining `NAME(engine, *args)`, with a docstring that starts with `$CALL --NAME`. From the next call it is a flag and is listed with the other tools. A skill may not shadow an existing tool.
5. **Self-edit.** The mind may edit `main.py`, `win32.py` and `scan.py`, which are its body. Commit what proved itself; `git diff` shows what a wake changed.

## Files

- `main.py`: tools, prompts and the mind runner.
- `win32.py`: the hand.
- `scan.py`: the watch.
- `tests/`: pytest suite. It runs on Windows, and on Linux with a ctypes stub.
- `pyproject.toml`: dependencies and pytest settings.

Runtime files (`story.txt`, `ledger.md`, `goal.txt`, `run_*.md`, `shots/`, `scan.json`, `agent/`, `prompt.txt`) are ignored by git.

## Tests

```powershell
.venv\Scripts\python.exe -m pyflakes main.py win32.py scan.py tests
.venv\Scripts\python.exe -m pytest
```
