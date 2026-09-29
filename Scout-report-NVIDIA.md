# NVIDIA seat scout — 2026-09-29

**VERDICT: BURN-SAFE WITH GAPS**

A fresh Windows install plus a clone of `origin/runner-h` at `87096685ac7978e2f76c202e643e19ff639bcf1c` has the committed Trident program. Every remote branch on GitHub is already inside that commit. Nothing in the live checkout is ahead of `origin/runner-h`. The gaps are files this disk keeps and git does not: three design specs, plus gitignored session state and old run dumps.

Must-save before a wipe, if those notes still matter:

| Path | Bytes | Why it is only on this disk |
| --- | --- | --- |
| `C:\Users\px-wjt\Downloads\Jarvis\Trident\proof\architecture-2026-09-29.md` | 22023 | Noon architecture spec. Whitelist never un-ignores it. Only copy found. |
| `C:\Users\px-wjt\Downloads\Jarvis\Trident\proof\live-voice-plan.md` | 30115 | Noon voice/VAD/ASR spec. Same. |
| `C:\Users\px-wjt\Downloads\Jarvis\Trident\proof\unify-iris-pe.md` | 36332 | Noon work order (`bots/*.txt`, one bare `assistant.py`). Same. `bots/` is absent from tip. |

Those three describe the tree as it was before the afternoon resident/memory/idle commits. Tip code and `README.md` already carry the brain half (resident Gemma, `remember` / `devices` / `cursor` / `next`, 60 s quiet idle, `stream`, image tokens 70–280). The specs still hold research notes and acceptance checks that were never committed, and `unify-iris-pe.md` still specifies a four-bot layout the tree does not contain.

Session state, also only on disk, and recreated empty by a clone:

- `gemma.memory.txt` — 4153 bytes, 32 user blocks, 0 `fact` blocks, 0 `work` blocks. Proof turns from today (lamp, harbor-code drill, quiet-idle drain).
- `iris_outbox.txt` — one leftover line: `Say one short sentence: G429 quiet proof lamp is green.`

Not must-save: the `5e1d235` wave2b commit (wording plus `--host` default `0.0.0.0`; tip already binds `0.0.0.0`), stale Grok memory under `Documents\43t4gdfg\trident-1c7feaa8` (2026-09-23, path `Downloads\3-way\Trident` is gone), and `*.gguf` / `.venv` / built exes ( `install.py install.txt` fetches and builds them).

Checked 2026-09-29 after `git fetch origin --prune`. At audit time live HEAD and `origin/runner-h` were both `8709668`, and `git status` was clean. While this report was being added, `origin/runner-h` fast-forwarded to `22fe86f827f9d94166aa046b3b2316b15d68435b` (`Scout-report-Iris.md` only). The product tree under that commit is still `8709668`. Port 8765 was observed and left running.

`Scout-report-Iris.md` records gaps on the Iris PC (`C:\Users\eb-wjt\Downloads\Jarvis\Trident`), including `proof/iris-start-stop-plan.md`, a stash paragraph, and an mp3. Those paths are not on this PE disk. This report is what a wipe of this machine would lose.

## Disk and repo audit

### Live checkout

`C:\Users\px-wjt\Downloads\Jarvis\Trident`

| Item | Value |
| --- | --- |
| Remote | `https://github.com/wgabrys88/Trident.git` |
| Branch | `runner-h` tracking `origin/runner-h` |
| Tip | `87096685ac7978e2f76c202e643e19ff639bcf1c` — Add Iris iris_outbox consume path on assistant.py |
| Ahead / behind `origin/runner-h` | 0 / 0 |
| Stash | empty |
| Untracked, not ignored | none |
| Worktrees | this directory only |
| Tracked files | 94 |

`.gitignore` is a whitelist (`*` then `!` exceptions). Run logs, weights, binaries, `gemma.memory.txt`, and `iris_outbox.txt` stay untracked on purpose. These proof notes are ignored because they are not in the `!/proof/...` list:

- `proof/architecture-2026-09-29.md`
- `proof/live-voice-plan.md`
- `proof/unify-iris-pe.md`
- `proof/g429-pe-brain.md` (6413 bytes, 14:19) — still says tools `hello` and `cursor` stay. Tip `gemma.py` declares `remember`, `devices`, `cursor`, `next`. Stale next to the tracked proofs.
- `proof/g429-pe-reinstall.md`, `proof/g429-pe-seat-merge.md`, `proof/g429-pe-cutover-pr33.md` — seat ops (pids, merge SHAs). Those SHAs are on `origin/runner-h`.

Tracked proofs on tip include `proof/README.md`, `g429-pe-memory.md`, `g429-pe-memory-live.md`, `g429-pe-tools.md`, `g429-pe-idle.md`, `g429-pe-idle-quiet.md`, `g429-pe-quiet-live.md`, `g429-pe-place.md`, `g429-pe-merge.md`, `g429-pe-stream-flush.md`, `g429-pe-seat-forward.md`, `g429-iris-outbox-consume.md`, and the Iris voice notes.

No `rebirth` / paste file turned up under `Downloads` or `Desktop` (depth 3). `README.md` says the Rebirth appendix is the in-tree copy of that paste.

### Other Trident clones

All of these remotes are `https://github.com/wgabrys88/Trident.git`. Their local `origin/runner-h` refs were stale (not fetched this pass). Each HEAD below is an ancestor of live `8709668` (unique commits vs that tip: 0). Working trees had no unpushed source. Stashes were empty.

| Path | HEAD | Date | Unique vs live tip |
| --- | --- | --- | --- |
| `Documents\6rfthfh\Jarvis\Trident` | `c34daa0` runner-h | 2026-09-28 21:18 | 0. Clean. |
| `Documents\11one11\2\Trident` | `8a367e7` runner-h | 2026-09-28 20:06 | 0. Also local `cursor/wave2b-nvidia-comment-strip-fe13` at `5e1d235` (remote gone). |
| `Documents\11one11\Jarvis\Trident` | same SHAs as `11one11\2` | same | 0, plus the same wave2b branch. |
| `Documents\11one11\1\Trident` | `418975c` runner-h | 2026-09-28 13:39 | 0. Ignored `*_gemma_out_*.txt` dumps. |
| `Documents\435tergdf\Jarvis\Trident` | `4b5db79` runner-h | 2026-09-27 12:01 | 0. Extra untracked `lan-recon\collect_nvidia_inventory.py`, `nvidia-host-capabilities.md`, `nvidia-inventory.json`. Read-only inventory script, not the product. |

`5e1d235` (`wave2b: strip worker-path audio wording; default host 0.0.0.0`, parent `418975c`) is the only Trident commit object found that live `origin/runner-h` does not contain. It edits comments and local names in `gemma.py`, `nvidia_client.py`, and `nvidia_worker.py`, and sets `--host` default to `0.0.0.0`. Tip `nvidia_worker.py` already defaults `--host` to `0.0.0.0` and documents `--drop` as the local file inbox. The patch is superseded. The report `wave2b-nvidia-report.txt` sits only on the two `11one11` trees.

`Documents\11one11\2\Trident` also holds ignored scout text (`scenario-c-scout-nvidia.txt`, `live-mic-nvidia-scout.txt`, `housekeep-audit-nvidia-2026-09-28.txt`, and the `*_gemma_out_000.txt` names `README.md` cites for Scenario C, `18-59-56-328` through `19-23-43-495`). Those are run evidence. The conclusions are in `README.md`.

`Documents\43t4gdfg\trident-1c7feaa8` is a Grok memory export (sqlite + `MEMORY.md`, 2026-09-23), not a git repo. It describes `trident.py` / Vulkan-layer notes and a checkout path that is not on this machine now.

### Not Trident product

| Path | What it is |
| --- | --- |
| `Downloads\Jarvis\Trident\.install\src\llama.cpp` | Detached `84e76d8a2`, clean, upstream ggml-org. |
| `Downloads\Jarvis\Trident\.install\src\ggml` | Detached `7840aaba`, clean. |
| `Downloads\Jarvis\Trident\.install\src\nemo-speech` | Detached `97a15af`, clean, matches `origin/main` of that pin. |
| `Documents\4t3t3t4e\tts\_v3_pin_src` | Upstream Chatterbox checkout, detached `5de7a54`, five deleted `src/chatterbox\*.py` files. |
| `Documents\434terjyft\research-system` | Unrelated `master` `38a3c02`, no remote, untracked `duck_search.py`. |

Searched `Downloads`, `Desktop`, `Documents`, `agent-tools`, `.cursor`, `.grok`, `.codex`, `.grokbot`, `Videos`, `Pictures` for `.git` directories (depth 5), and name-matched `Trident` / `Jarvis` under those trees plus `AppData\Local\Cursor` and `AppData\Roaming\Cursor` (depth 4). Desktop has no clone. `Downloads` has `Jarvis\Trident` and `ComfyUI_windows_portable`. No `D:` or `E:`.

### Brain on :8765 (observed, not restarted)

| Check | Result |
| --- | --- |
| IPv4 | `192.168.16.31` on Ethernet |
| Listen | `0.0.0.0:8765` pid **8708** |
| Parent | pid **8908** `\.venv\Scripts\python.exe nvidia_worker.py --host 0.0.0.0 --port 8765`, started 2026-09-29 18:17:39 |
| Child command | Base `pythoncore-3.11-64\python.exe` (venv launcher re-exec). This process owns the socket. |
| GET `http://127.0.0.1:8765/` | HTTP 501 `Unsupported method ('GET')` |
| GET `http://192.168.16.31:8765/` | HTTP 501, same |
| After the GETs | pid 8708 still listening |
| Resident | pid **2064** `gemma-brain.exe --resident gemma_run.txt`, started 14:23:35, `gemma.pid` state `ready`, fingerprint `c2574aa2b65540eceab59b8c108163af5fe73e3daa1a5197c1b4c5bdc240cced` |

`gemma-brain.exe` mtime is 14:13. Last commit that touches `gemma/src/brain.cpp` is `ecb9f4a` at 14:20 (no later `brain.cpp` commit). The process has stayed up through the later `gemma.py` edits. Each POST and each quiet idle spawns `gemma.py` from disk, so Python changes apply on the next call. A later `brain.cpp` edit would stay out of pid 2064 until a resident restart. This scout did not restart it.

`nvidia_worker.py` mtime is 18:03, before pid 8908 started at 18:17. Tip commit `8709668` (18:21) changes `assistant.py`, `.gitignore`, and two proof files. The running worker does not load `assistant.py`.

## Branch audit

`git fetch origin --prune` on the live checkout. `origin/HEAD` → `origin/main`.

`origin/main` is `d535349` (2026-09-28 21:59, README flag manual). It is an ancestor of `origin/runner-h`: 36 commits only on `runner-h`, **0** commits only on `main`. Left untouched.

Remote branches other than `main` and `runner-h`, unique commits vs `origin/runner-h` (`git rev-list --count origin/runner-h..<branch>`):

| Remote branch | Tip | Unique commits |
| --- | --- | --- |
| `origin/cursor/iris-outbox-consume-4b6b` | `8709668` | 0 (product tip under the Iris scout commit) |
| `origin/cursor/closed-mic-loop-1bce` | `c52076c` | 0 |
| `origin/cursor/pe-seat-forward-slice-9f6a` | `ee539d6` | 0 |
| `origin/cursor/voice-act-loop-8fe8` | `5ee35d4` | 0 |
| `origin/cursor/pe-idle-quiet-36b1` | `c2d0cb8` | 0 |
| `origin/cursor/pe-idle-notice-ab52` | `eb2cfdf` | 0 |
| `origin/cursor/pe-stack-on-runner-h-a278` | `175e3f6` | 0 |
| `origin/cursor/prefetch-v3-span-e7af` | `123050e` | 0 |
| `origin/cursor/pe-place-devices-741f` | `551bd26` | 0 |
| `origin/cursor/pe-gemma-tools-e2c5` | `b4cdf1e` | 0 |
| `origin/cursor/lang-span-mouth-2f02` | `9274d63` | 0 |
| `origin/cursor/pe-memory-live-0233` | `dce6096` | 0 |
| `origin/cursor/iris-voice-loop-5fab` | `f03b1f1` | 0 |
| `origin/cursor/pe-brain-memory-a6f7` | `b4ff0a7` | 0 |
| `origin/cursor/pe-resident-gemma-af6c` | `ecb9f4a` | 0 |

Local branches on the live checkout match those remote tips where they exist. `pr-29` (`eb2cfdf`) and `pr-31` (`c2d0cb8`) have no upstream and also have 0 unique commits. No stash. Reflog on this clone is fast-forwards and merges that are already in `8709668`.

The gone GitHub branch `cursor/wave2b-nvidia-comment-strip-fe13` survives only as a local branch on the two `11one11` clones (see above).

## Intentional unfinished product gaps

These are in the tip (or called out there) and are separate from “forgot to push”:

- Live human microphone. `assistant.py` `listen_loop` starts `vad.exe --resident` and transcribes with `hear.py`. `README.md` still records Scenario C (2026-09-28, `--seconds 30`) as the proven human session, and the Rebirth appendix still waits for Wojciech’s go before a live mic. This scout did not open a mic or run ASR.
- `unify-iris-pe.md` asks for `bots/jarvis.txt`, `bots/spock.txt`, `bots/iris.txt`, `bots/nvidia.txt`, a `warroom` tool, and `assistant.py` with no `--nvidia`. Tip has one `gemma.memory.txt` and still documents `--nvidia`. No `bots/` directory.
- HTTP has `do_POST` only. Health is a TCP connect. GET returns 501. That matches `README.md`.
- Quiet idle runs only when a `work` line is stored. The live memory file has none, so the next quiet stretch prints `idle` and leaves `iris_outbox.txt` as it is.
- `main` stays behind on purpose (ancestor, 0 unique commits).

## Code review (tip `8709668`, PE brain path)

Seat boundary: brain and `nvidia_worker.py` on `:8765`. No mic or ASR run. The listener and the resident were not killed.

### Path

1. Iris (or `nvidia_client.py`) POSTs JSON `id`, `text`, `image`, `image_b64`, optional `stream` to `http://192.168.16.31:8765/`. Body cap is 16_000_000 bytes. No session on the worker.
2. `nvidia_worker.py` writes `image_b64` to a temp file when present, then runs `.venv\Scripts\python.exe gemma.py` (plus `--stream` when asked). Timeout default 600 s kills that Python process. `README.md` states it does not kill `gemma-brain.exe`.
3. `gemma.py` keeps one resident: `gemma-brain.exe --resident gemma_run.txt`. Prompts go through `gemma.prompt.txt`. Pieces come back on `gemma.response.txt`. `--once` is still the old one-shot.
4. Text turns replay `gemma.memory.txt` (facts, then pairs clipped to 200 words, prompt cap 80_000 characters). Tools: `remember`, `devices`, `cursor`, `next`. Inbox marker `<<trident-inbox>>` skips tools and memory. Image turns skip tools and memory, then append the reply.
5. `WorkerServer.service_actions` calls `pump_quiet`. After `QUIET_S` (60) with no POST, no pending accept, and no `gemma.busy` / `gemma.prompt.txt`, it runs `gemma.py --idle` once.
6. `idle_notice` takes the oldest `work` line, generates once, runs a tool only if that generation contains the call, appends the speakable pair, drops the line, and `write_iris_outbox` replaces `iris_outbox.txt` (cap 2000 characters, via `iris_outbox.txt.tmp`). Empty idle prints `idle` and does not generate.
7. `assistant.py --iris-outbox` is the Iris consumer (mouth, no POST). That landed in `8709668`.

`gemma/src/brain.cpp` `serve` publishes `gemma.pid`, polls every 20 ms, and `handle_prompt` writes length-prefixed pieces then `ok` or `err`. `place()` in `gemma.py` is one TCP connect plus CUDA/Vulkan names. It does not key off a hostname.

### Risks

- `0.0.0.0:8765` has no authentication. Any host that can route to `192.168.16.31` can POST a turn. A `cursor` tool call starts a local agent (`composer-2.5`) with a task suffix that forbids killing port 8765. Quiet idle will run that tool if the stored work line’s generation emits the call. The live file has no work line right now.
- `idle_notice` returns the raw generation when no tool was parsed, and returns the cleaned follow-up when a tool ran. Memory and `iris_outbox.txt` get `answer_text(...)` (text after `<channel|>`, tool markup stripped). Worker stdout is whatever was returned. `README.md` says the speakable result is what the worker prints. On a no-tool idle those two strings can differ. The Iris handoff is the outbox line.
- `drop_work` runs before `write_iris_outbox`. An outbox `OSError` is logged and swallowed. The work line is already gone. The pair is in `gemma.memory.txt`.
- `pump_quiet` sets `last_post` before `gemma.py --idle` returns. A failed idle waits another 60 s. A connection that arrives during the idle is not checked again until the next loop.
- `proof/README.md` says the running worker loads `gemma.py` at process start. The worker spawns `gemma.py` per POST and per idle. `nvidia_worker.py` itself is the code frozen at 18:17 in pid 8908/8708.
- Resident pid 2064 has been up since 14:23. Python on disk is tip. The exe is the 14:13 resident build. That matches the last `brain.cpp` commit. It will not pick up a newer native binary by itself.

### Dead glue and weight

- `hello` / `write_hello` are gone from `gemma.py`. `README.md` says the 2026-09-28 hello write is gone and `remember` is the write. Local `proof/g429-pe-brain.md` still describes hello.
- `--drop` remains a local `nvidia_turn.request.txt` / `nvidia_turn.response.txt` inbox. Help text says it is not the LAN path. `--drop` does not run quiet idle. Production while 8765 is up is POST.
- `--once` remains beside the resident. That is the documented one-shot.
- `grok_local_bot.py` (about 1,000 non-blank lines) stays as the file team and wav door, per `README.md`. The live `--nvidia` loop does not send `grok_bot_history.txt`.
- `gemma.py` is about 1,600 non-blank lines: resident lock, memory, four tools, `place()`, stream flush, and idle. `nvidia_worker.py` is about 650: HTTP, image temp file, stream framing, quiet pump. `brain.cpp` is the smaller native loop. The weight on this seat is the Python client, not the C++ server.
- The noon specs are the bulky duplicate. They still say every POST starts `gemma-brain.exe` and that tools are `hello` and `cursor`. Tip has moved past that. Keeping them matters as the only copy of the writeup. Treating them as the current manual would fight `README.md` and the code.

`qwen.py` remains the no-listener, no-CUDA row. `devices` / `place` report whether a mouth on this PC would share the 1060. The worker does not stop the resident between turns. Same-GPU flip lives in `assistant.py` (`release_shared_gpu`), which is the voice PC’s call, and this seat did not exercise it.
