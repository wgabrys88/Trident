# Scout report — Iris (EB-W)

**VERDICT: BURN-SAFE WITH GAPS**

A fresh clone of `origin/runner-h` at `87096685ac7978e2f76c202e643e19ff639bcf1c` (2026-09-29 18:21:25 +0200, "Add Iris iris_outbox consume path on assistant.py") has the current Trident product: every remote branch is merged into that commit, every tag name on this disk is already on `origin`, and the live checkout is clean and even with that tip. It does not have three things that exist only on this disk: the untracked start/stop spec, one stashed proof paragraph, and the spoken mp3 in Downloads. Installed weights and exes are gitignored on purpose; `python install.py install.txt` rebuilds them. `main` was not moved.

Seat: Iris, voice. Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. This pass fetched `origin`, read the tree, and did not bind `:8765`, did not open the microphone, and did not start or stop a PE brain.

## Must-save before a wipe

1. `proof/iris-start-stop-plan.md` (2026-09-29, about 18 KB). The only copy of the Iris start/stop and local-Gemma failover spec. `.gitignore` is a whitelist (`*` then `!` lines), so this file is ignored and is not on `origin`. `start.py` and `stop.py` are not in the tree. The contract, short: zero-flag `start.py` probes `192.168.16.31:8765` once, leaves PE alone when the port accepts, calls `gemma.ensure_resident()` only when it does not, warms the nano/en mouth, writes `iris.session.txt`, and exits. `stop.py` stops only what this PC started. The spoken path would drop `--nvidia`, `--url`, and the Qwen default. The full step list is only in that file.
2. Stash `stash@{0}` (`86c01ec`, 2026-09-29 18:02:51 +0200, "On cursor/voice-act-loop-8fe8: iris-merge-pr30-wip"). It adds one section to `proof/voice-g429.md` that the tip file does not contain. Quoted here so this commit keeps the words:

   > ## Fresh-tree closed-mic prove after dual reinstall (17:19)
   >
   > Checkout `runner-h` = `origin/runner-h` tip `175e3f6`. Leave-healthy PE `http://192.168.16.31:8765/` untouched. Command: `assistant.py --nvidia --url http://192.168.16.31:8765/ --text "Reply with exactly two short sentences. The lamp is on. The door is shut." --timeout 180`. Exit 0. Stderr: `nvidia stream`, place peer CUDA/Vulkan note, `mouth out: default`, nano EN ×2, `mouth 2 chunk(s)`. Reply: `The lamp is on. The door is shut.` Request 17:19:08, response 17:19:10, wavs `17-19-12-523` and `17-19-13-803`. Stream `iter_stream` (no mouth) in `proof/fresh_stream_iter.log`. `mouth.py --stop` after. TCP still open.

   Later closed-mic proofs on the tip already cover the same speak path. The paragraph is the only unique text in the stash. No third stash parent (no untracked stash files).
3. `C:\Users\eb-wjt\Downloads\trident-g429-wyjasnienie-pl.mp3` (2,093,472 bytes, 2026-09-29 15:11). The only Trident-named file outside the checkout and the backup folders. It is not in git.

Optional, not product source: gitignored run logs under `proof/` (`fresh_stream_iter.log`, `g429-closed-mic-loop.log`, `g429-iris-outbox-consume.log`, `injected-text.log`, `prefetch-v3.log`, `span-speak.log`, `stream_iter_pr24.log`, `swd-reprove-pr24.*`, `iris-composer-reinstall-9273.log`) and the short merge notes `proof/g429-closed-mic-merge.md`, `proof/g429-iris-outbox-merge.md`, `proof/g429-voice-act-merge.md`. The conclusions of those runs are already in the tracked `proof/g429-*.md` files. `proof/housekeeping-scout-runner-h.md` is a read-only audit of `d535349` from earlier the same day (76 tracked files then; 94 now). `proof/iris-composer-reinstall-g429.md` records a successful `install.py` at `175e3f6`.

## Disk and repo audit

### Live checkout

| Item | State |
| --- | --- |
| Path | `C:\Users\eb-wjt\Downloads\Jarvis\Trident` |
| Remote | `origin` `https://github.com/wgabrys88/Trident.git` (fetch and push) |
| Branch | `runner-h` tracking `origin/runner-h` |
| HEAD | `87096685ac7978e2f76c202e643e19ff639bcf1c` |
| Ahead / behind | 0 / 0 after `git fetch origin` (rechecked at the end of this pass) |
| Worktree | one (`git worktree list`). `git status --porcelain -uall` is empty |
| Shallow | no |
| Tracked files | 94 |
| Stash | one, described above |

Ignored beside the sources, by the whitelist, and recreated by install or by a run: `.venv/`, `.install/` (pinned upstream checkouts of ggml `7840aaba`, llama.cpp `84e76d8a2`, NeMo-Speech.cpp `97a15af`, all detached, remotes are upstream), root `*.exe`, `*.gguf`, `*.dll`, `silero_vad.onnx`, `lid.176.ftz`, `onnxruntime.dll`, chatterbox wavs, `nvidia_turn.*.txt`, `iris_outbox.txt`, pid files. `C:\tgemma` is not on this disk; install creates that short build directory when it builds Gemma. Those bytes are the installed seat, not forgotten source.

`Downloads\Jarvis` contains only this `Trident` directory. `Downloads\3-way` and `Downloads\Trident` are gone. Desktop has no clone. One worktree.

Nested `.git` directories under `.install\src\` are the installer pins, not second Trident repos.

### Other Trident git trees on this machine

Searched `Downloads`, `Desktop`, `Documents`, `.cursor\projects`, and `.grok` for `.git`, plus name matches for Trident/Jarvis under the user profile (depth 4). Unrelated repos (ComfyUI, `clone-reliable`, `endgame-ai`) were seen and skipped.

Recent backups whose HEAD is an ancestor of `origin/runner-h` (a clone of the tip already contains that commit):

| Path | Branch | HEAD | Tip date |
| --- | --- | --- | --- |
| `Documents\bkp\bkp-112\1\Jarvis\Trident` | `runner-h` | `226e086` | 2026-09-26 |
| `Documents\bkp\bkp-112\2\Jarvis\Trident` | `runner-h` | `1ae0576` | 2026-09-26 |
| `Documents\bkp\bkp-112\546\exit\Trident` | `runner-h` | `75a9054` | 2026-09-27 |
| `Documents\bkp\bkp-112\546\Jarvis\Trident` | `runner-h` | `4b5db79` | 2026-09-27 |
| `Documents\bkp\bkp-112\Jarvis\Trident` | `runner-h` | `4b5db79` | 2026-09-27 |
| `Documents\bkp\bkp-113\1\Trident` | `runner-h` | `418975c` | 2026-09-28 |
| `Documents\bkp\bkp-113\Jarvis\Trident` | `main` | `d535349` | 2026-09-28 |

Clean. No stash. Same `origin`. Nothing to rescue.

Older checkouts whose commit objects are **not** in this clone (so they are not reachable from `origin/runner-h` or from the tags this clone has). Dirty rows are deletions of files that commit already had, not new source:

| Path | Branch | HEAD | Dirty |
| --- | --- | --- | --- |
| `Documents\bkp\bkp-013\Trident-GROK-zrobil` | `main` | `6233f74` (2026-08-17) | deleted `patches/chatterbox.patch` |
| `Documents\bkp\bkp-031\Trident-POWINNO` | `runner-x` | `6c27531` (2026-08-24) | clean |
| `Documents\bkp\bkp-037\23\Tridentss` | `runner-x` | `627d9b0` (2026-08-27) | deleted `conversation.py`, `installer.py`, `main.py`, `ui.py` |
| `Documents\bkp\bkp-038\Tridentsd` | `runner-x` | `627d9b0` | deleted `installer.py` |
| `Documents\bkp\bkp-053\Trident-fgh` | `runner-x` | `3015637` (2026-09-01) | clean |
| `Documents\bkp\bkp-105\3-way\Trident` | `reduction` | `99f83bf` (2026-09-21) | clean |
| `Documents\bkp\bkp-106\3-way\Trident` | `reduction` | `c4c768b` (2026-09-21) | clean |
| `Documents\bkp\bkp-106\dzban\Trident` | `reduction` | `a820591` (2026-09-22) | clean |
| `Documents\bkp\bkp-108\3-way\Trident` | `reduction` | `968373f` (2026-09-22) | clean |
| `Documents\bkp\bkp-109\Trident` | `runner-zin` | `968373f` | clean |
| `Documents\bkp\bkp-109\ZIP-REPO-MASTER\Trident` | `reduction` | `506fc95` (2026-09-23) | clean |
| `Documents\bkp\bkp-110\1\3-way\Trident` | `runner-h` | `968373f` | clean |
| `Documents\bkp\bkp-110\1\rrr\Trident` | `runner-h` | `b5c5a99` (2026-09-23) | clean |
| `Documents\bkp\bkp-110\3-way\Trident` | `runner-h` | `4909907` (2026-09-23) | clean |
| `Documents\bkp\bkp-110\54t54geg\3-way\Trident` | `runner-h` | `a9fb94b` (2026-09-23) | 3-line edit in `CMakeLists.txt` |

`b5c5a99` has the same subject as tag `MILESTONE-GPU-V` (`bd4ebf6`, "the order matches the tree, and the readme is the manual"), which **is** on `origin`. Those backup SHAs are pre-rewrite snapshots of generations GitHub already keeps as tags. File names present there and absent from the tip (`trident.py`, `tts.py`, `host.py`, `listen.py`, `brain.py`, `asr.py`, `src/server.cpp`, and similar) are the previous program split. The tip replaced them with `assistant.py`, `hear.py`, `mouth.py`, and `gemma.py`.

`Documents\bkp-PHONE\8\Trident_Bare_Metal_Pipelineexkrated` is not a usable git repo. Git metadata was flattened into files named `.git-HEAD`, `.git-objects-…`. Five Python files from 2026-09-04 (`brain.py`, `brain_runtime.py`, `main.py`, `parakeet.py`, `tts_nano.py`, about 34 KB together). That shape is the milestone era already tagged on `origin` (`MILESTONE-33-TRIDENT` and neighbors).

Agent memory under `.grok\sessions\` and `.grok\memory-v2\workspaces\trident-1c7feaa8` is chat state, not the product tree.

## Branch audit

`git rev-list --left-right --count origin/runner-h...<branch>` is `0` unique commits on every remote branch. Each one is fully merged into `origin/runner-h`.

| Remote branch | Tip | Unique vs `runner-h` |
| --- | --- | --- |
| `origin/main` | `d535349` docs: make README the flag manual | 0 (ancestor; `runner-h` is 36 commits ahead) |
| `origin/runner-h` | `8709668` | 0 |
| `origin/cursor/closed-mic-loop-1bce` | `c52076c` | 0 |
| `origin/cursor/iris-outbox-consume-4b6b` | `8709668` | 0 (this is the tip) |
| `origin/cursor/iris-voice-loop-5fab` | `f03b1f1` | 0 |
| `origin/cursor/lang-span-mouth-2f02` | `9274d63` | 0 |
| `origin/cursor/pe-brain-memory-a6f7` | `b4ff0a7` | 0 |
| `origin/cursor/pe-gemma-tools-e2c5` | `b4cdf1e` | 0 |
| `origin/cursor/pe-idle-notice-ab52` | `eb2cfdf` | 0 |
| `origin/cursor/pe-idle-quiet-36b1` | `c2d0cb8` | 0 |
| `origin/cursor/pe-memory-live-0233` | `dce6096` | 0 |
| `origin/cursor/pe-place-devices-741f` | `551bd26` | 0 |
| `origin/cursor/pe-resident-gemma-af6c` | `ecb9f4a` | 0 |
| `origin/cursor/pe-seat-forward-slice-9f6a` | `ee539d6` | 0 |
| `origin/cursor/pe-stack-on-runner-h-a278` | `175e3f6` | 0 |
| `origin/cursor/prefetch-v3-span-e7af` | `123050e` | 0 |
| `origin/cursor/voice-act-loop-8fe8` | `5ee35d4` | 0 |

`origin/HEAD` still points at `origin/main`. The README names `runner-h` as the branch of record. Leaving `main` at `d535349` is intentional.

Local branches match those remotes, plus `pr-34-head` at `8709668` with no upstream. Same commit as the tip. No unique work.

Local tag names all exist on `origin` (`git ls-remote --tags`). Annotated-tag peel entries (`^{}`) show up only on the remote listing; the tag names themselves match. A normal clone receives that history. Milestone and `ARCHIVAL-EXPERIMENT-*` tags are on GitHub, including the pre-rebirth launcher era that `git log --all --not --remotes` still walks locally because tags are not remote-tracking branches.

Unreachable objects (`git fsck`): `7acd75e` is an abandoned merge of `5ee35d4` into `175e3f6` (the fast-forward that landed voice-act made this commit unnecessary). `b65dcdc` / `672c760` are a dropped stash `iris-local-voice-g429` whose extra `proof/voice-g429.md` text is the speak-while-decode section already at the end of the tracked file. Nothing in those objects is still unique.

No side branch is holding forgotten commits.

## Intentional unfinished product gaps

These are on the tip, or they are known follow-ons. They are a different fact from "forgot to push."

- **Live human mic + VAD + PE, one command, is in the source and is only partly proven.** With no `--text`, `--wav`, `--inject`, `--vb-cable`, or `--iris-outbox`, `assistant.py` calls `listen_loop`: `vad.exe --resident` on the default non-cable WASAPI mic, then `hear.py --wav` for each finished utterance, then the placed brain, then the mouth. `--seconds` is gone (`proof/voice-g429.md` records `assistant.py --seconds 5` exiting 2). The human-mic proof in that file is one 5.6 s utterance with the **brain down**, answered by local Qwen, stopped during the first synthesis. The two-PC human session in the README ("Scenario C") is still written as `--seconds 30`, a flag the tip rejects. Closed-mic `--text`, `--inject`, and `--iris-outbox` are what the 2026-09-29 proofs actually ran against `http://192.168.16.31:8765/`.
- **No zero-flag start/stop failover.** `assistant.py --stop` stops the local assistant tree (vad, and a mouth or Qwen this process started) and does not open the brain. There is no `start.py`. A down peer with `--nvidia` exits `peer missing` and does not fall over to local Gemma. Local Gemma runs when no peer is named, nothing is listening on `127.0.0.1:8765`, and a CUDA device is present. This Iris PC has no CUDA device, so that row is Qwen (`qwen.py`, `sense.exe`). The plan in the must-save list is the unfinished design for replacing that.
- **`iris_outbox` is one shot.** `--iris-outbox` reads one line, clears the file, speaks it, and exits. It does not POST and it does not watch the PE share. Missing or empty exits 0. The clear happens before the mouth returns, so a mouth failure has already dropped the line.
- **`--inject` is the closed-mic continuous path** (file or stdin, `---` blocks, `quit`/`exit`/`stop`). Proven in `proof/g429-closed-mic-loop.md`. It does not open the mic.
- **Qwen remains the CPU brain** (`--brain` default `qwen`). `gemma/src/sense.cpp` and `sense.exe` still build. The door and `--nvidia` do not call them.
- **`src/ear.cpp` / `ear.exe` stay off the live hear path.** `hear.py` runs `nemo-speech.exe transcribe` on `ear.gguf`. Install still builds `ear.exe`.

`gemma-brain.exe --resident` is already in `gemma/src/brain.cpp`. The local plan's assumption that Gemma has no resident mode is stale. PE's worker is what keeps that resident. Iris does not start `nvidia_worker.py`.

## Code review of `runner-h` at `8709668`

Review only. No product edit in this pass. Iris owns hear, mouth, and playback. The brain worker stays PE's.

### Architecture

Two seats, one repo. Sound stays on Iris. Text crosses the LAN.

`assistant.py` (~1419 lines) is the loop. `gemma.place()` decides the brain once from a TCP connect plus CUDA/Vulkan device names, with no computer-name branch. `--nvidia` plus a URL that accepts is `brain=post` (`nvidia_client.py`, stream on, `StreamFeed` speaks closed atoms while the body is still open). Same adapter and a local peer sets `flip` and waits for the whole reply. A missing peer dies. `hear.py` (~277) is the mic or wav transcribe. `mouth.py` (~915) keeps one `chatterbox.exe --resident` and plays with `PlaySoundW` unless `--no-play` or the cable path. English spans use nano (turbo if `--model turbo`); other languages use a one-shot v3. `grok_local_bot.py` (~1018) is the wav door and the PE file-team (`--proof`, `--inbox`). `nvidia_worker.py` (~656) is the `:8765` listener and is not an Iris process.

Native code under `src/` is the mouth (gpt2 nano/turbo, llama v3, S3, Vulkan, bake). `gemma/src/brain.cpp` is the brain exe. `install.py` plus `install.txt` is the only installer.

### Risks

- **`assistant.py` calls `cable_turn` with the arguments swapped.** Definition at line 1313 is `cable_turn(py, found, args)`. The `--vb-cable` branch at line 1588 calls `cable_turn(py, args, found)`. The first use inside is `args.text`. `found` is a `Place` namedtuple (`brain`, `url`, `cuda`, `vulkan`, `adapter`, `flip`, `where`) and has no `text`. `--vb-cable` on this tip raises `AttributeError` before `loopback.py` starts. The README still cites an older `STATUS PASS` for that harness. This pass did not run it.
- **README Scenario C and the flag manual disagree.** The narrative command is `assistant.py --nvidia --url … --seconds 30`. Later in the same file the listen command is `assistant.py --nvidia` with the URL and no `--seconds`. The code matches the later sentence. Someone following the narrative block gets "unrecognized arguments: --seconds".
- **`proof/voice-g429.md` still describes the listen mouth as v3** in its opening command section. The tip chunker uses nano for English. The later sections of that file (span, stream, injected text) match the tip more closely. Checked-in code outranks the note, which the README already says, and the note is easy to follow by mistake.
- **Outbox clear-before-speak** can drop a PE idle line if the mouth fails after the file is emptied.
- **Whitelist gitignore hides new work.** `git status` on this disk was clean while `proof/iris-start-stop-plan.md` sat ignored. A file is committed only after a `!` line names it. That is how the spec stayed local.
- **Default with no flags opens the microphone** (`listen_loop`). A probe or a smoke test has to pass `--text`, `--help`, or `--stop`.

### Dead glue and weight

The live Iris path is `vad.exe` → `hear.py` → `nvidia_client.py` → `mouth.py`. Still compiled and installed beside it:

- `ear.exe` (`src/ear.cpp`), unused by `hear.py`.
- `qwen.py` (~612) and `sense.exe` (`gemma/src/sense.cpp`, ~317), the CPU fallback, with their own resident pid/lock copy.
- `mouth.py`, `qwen.py`, and `gemma.py` each carry a resident supervisor (lock file, fingerprint, pid, ready wait). Three copies of the same shape.
- `src/common/wasapi_capture.cpp` and `silero_vad.cpp` are live now, because `listen_loop` launches `vad.exe`. The morning housekeeping note that called `vad.exe` off the live path is out of date. `vad.txt` itself is still the VB-Cable one-shot card; the listen loop rewrites `vad_run.txt` for the real mic.

`grok_local_bot.py` is large because it is three programs (door, proof, inbox) in one file. It is the door the README runs. It is not a leftover stub.

The native mouth is most of the C++ line count and it is the product. The Python growth since `d535349` (36 commits, 76 tracked files to 94) is the voice loop, span mouth, inject loop, outbox consumer, and the PE brain stack, plus their proof notes. That is the day's work sitting on the tip, not a second copy of the tree.

### Seat boundary

Iris code posts to `192.168.16.31:8765` or probes `127.0.0.1:8765`. It does not call `serve_http`. `assistant.py --stop` kills local pids only. `grok_local_bot.py` records `local_8765` and refuses to be the brain. A healthy PE listener stays a remote POST target. This scout left it that way.

## How this was checked

`git fetch origin --prune`, then `git rev-parse origin/runner-h`, `git status --porcelain -uall`, `git stash show -p stash@{0}`, `git branch -r` with `git rev-list --left-right --count origin/runner-h...<branch>`, `git ls-remote --tags origin`, `git for-each-ref`, `git fsck --unreachable`, and `git -C <backup> rev-parse` plus `git merge-base --is-ancestor` against this clone. Tip re-fetched immediately before this file was written: still `87096685ac7978e2f76c202e643e19ff639bcf1c`, ahead/behind 0/0. This report is force-added (`git add -f`) because the root `*` whitelist would otherwise leave `Scout-report-Iris.md` ignored.
