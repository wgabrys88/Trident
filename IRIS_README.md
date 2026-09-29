# Iris

Scout of the voice seat, written 2026-09-29 from this checkout. Checked-in code outranks this file, `README.md`, and every note under `proof/`.

Iris is the body. It hears, places one brain, and speaks. The brain is text. The mouth is on this PC.

| Piece | File | Job |
| --- | --- | --- |
| Entry | `run.py` | Start and stop this organism. Re-execs into `.venv`. |
| Loop | `assistant.py` | Hear or inject, place the brain, chunk, speak. |
| Hear | `hear.py` | WASAPI mic or a wav, then `nemo-speech.exe` on `ear.gguf`. |
| Seat | `seat.py` | `status`, `work`, `say`, `stop` in `iris_seat.txt`, `iris_outbox.txt`, `iris_status.txt`. |
| Mouth | `mouth.py` | Chatterbox. English is nano or turbo. Any other span is v3. Playback is `PlaySoundW` (`mouth out: default`). |

`run.py` does not bind, stop, or restart port 8765. A spoken shutdown is a Gemma `stop` tool call (`assistant.organism_stop`). The sentence "Please stop listening." is not that call.

## This checkout

| Item | Measured |
| --- | --- |
| Path | `C:\Users\eb-wjt\Downloads\Jarvis\Trident` |
| Seat | `trident-iris`, machine EB-W, account `eb-wjt` |
| Remote | `https://github.com/wgabrys88/Trident.git` |
| Branch at dig | `runner-h` tracking `origin/runner-h` |
| Base | `1bafab3fb7f8e874414c1597f6717a59681b1c9b` (merge of PR 40), even with `origin/runner-h` |
| This file | one commit on `cursor/iris-readme-scout-4036` above that base |
| Worktree | this directory only |
| `git status --porcelain -uall` | empty before this file |
| Tracked files | 106 before this file |
| `Downloads\Jarvis` | this checkout only |

`main` is `d535349`, 57 commits behind `runner-h`, 0 ahead. `origin/HEAD` still points at `origin/main`.

This dig connected once to `192.168.16.31:8765` (accept) and once to `127.0.0.1:8765` (timeout). No LISTENING socket on 8765 on this PC. The connect was not a POST and not a stop.

## Install

From this directory:

```powershell
python install.py install.txt
```

`install.py` reads that one argument. It builds `.venv`, the mouth (`chatterbox.exe`, `vad.exe`, `ear.exe`, `nemo-speech.exe`), Gemma and `sense.exe`, and bakes nano, turbo, and v3 from `reference.wav`. Weights, exes, and `.venv` stay gitignored. Run programs with `.\.venv\Scripts\python.exe`. This dig did not run install.

## Run

```powershell
.\.venv\Scripts\python.exe .\run.py start
.\.venv\Scripts\python.exe .\run.py start --url http://192.168.16.31:8765/
.\.venv\Scripts\python.exe .\run.py start inject PATH
.\.venv\Scripts\python.exe .\run.py stop
```

`start` with no `inject` is the live microphone (`iris: live mic`, then `assistant.py --nvidia --timeout 180`). `inject` is simulated ASR, one turn per line or blocks split by a line that is only `---`. No microphone. `stop` stops this organism and the mouth and does not open the brain.

The URL is `--url`, else `TRIDENT_NVIDIA_URL`, else `http://192.168.16.31:8765/`. `run.py` does not pass `--model`. English through `run.py` is nano.

## Alone and LAN

`assistant.turn_place` asks `gemma.place`. A peer whose port accepts is `brain post`. `announce_place` then prints `assistant: lan`. A closed peer prints `assistant: peer unreachable` and places this PC: `127.0.0.1:8765` if it accepts, the local Gemma resident if this PC has CUDA, otherwise `brain cpu` (`qwen.py`). That last row prints `assistant: alone`. The computer name is not an input (`gemma.place`).

Measured in `proof/voice-seat.md` (2026-09-29, no microphone, exit 0):

- `run.py start --url http://192.168.16.31:8765/ inject proof/voice-seat-turn.txt` printed `assistant: lan`, spoke Polish on `mouth: v3 pl`, then English on `mouth: nano en`.
- The same turn with `--url http://127.0.0.1:9/` printed `assistant: peer unreachable`, `assistant: alone`, `brain cpu`, and `assistant: qwen`. No `assistant: lan`.
- `run.py stop` exited 0. `192.168.16.31:8765` still accepted. `127.0.0.1:8765` was down.

`proof/voice_seat_check.py` is the offline check of that route, the stop tool, and the mouth tags. `proof/voice-seat.md` records `STATUS PASS`. This dig did not re-run it.

`assistant.remote_whole` posts with `stream` false and speaks after the whole body. `nvidia_client.py --stream` still exists. The voice-seat log shows `assistant: nvidia`, which matches `remote_whole`.

## Mouth

`assistant._voice` and `mouth.check_voice`:

- English span: the fast model. `fast_model` is turbo only when `--model turbo`. Otherwise nano. `run.py` leaves the default, nano.
- Any other tag in `assistant.V3_LANGS`: v3 for that tag. English on v3 exits. nano or turbo with a non-`en` tag exits.
- Budgets: English 65 words, any other tag 55 (`EN_LIMIT`, `PL_LIMIT`).
- `mouth.speak_pieces` keeps the fast resident. A known later v3 span is synthesized beside it (`mouth: prefetch`) and that process exits.

`proof/span-mouth.md` played the mixed line on Speakers (Realtek(R) Audio): nano `en`, then v3 `pl` (`opowiedzieć wam po polsku`), then nano `en` again. Cold v3 ready was 0.83 s. The v3 utterance was 8.60 s. No POST. `192.168.16.31:8765` stayed open.

`proof/voice-seat.md` is the later whole-turn measurement of the same split: `Drzwi są zamknięte.` on v3/pl, `The lamp is on. The door is shut.` on nano/en.

## Live mic

Live mic only after a human GO. `run.py start` with no `inject` opens the default WASAPI mic immediately. There is no code gate. This dig did not start it.

The cued human turn that is on disk is `proof/g429-live-mic.md` (2026-09-29, exit 0, 45.9 s). The command in that note is `start.py --live --cue --once`. `start.py` is not in this tree, and `assistant.py` has no `--cue`. The measurement still stands: mic `Microphone Array (Intel® Smart Sound Technology for Digital Microphones)`, hear tag `pl-PL`, POST to `192.168.16.31:8765`, reply `Hello, Wojciech.` on `mouth: v3 pl`, port still accepting after. Do not treat that command line as the current entry.

`README.md` records an earlier human session, Scenario C on 2026-09-28 (`assistant.py --nvidia --seconds 30`). `--seconds` is not an argument at this tip. That session's wavs are gitignored run output, not a `proof/` note.

## Proofs

Measured notes live under `proof/`. The ones that match this tip's voice path:

| File | What it measured |
| --- | --- |
| `proof/voice-seat.md` | Alone inject, LAN inject, `run.py stop`, port left accepting. |
| `proof/voice_seat_check.py` | Offline seat, stop-tool, mouth tags, alone vs LAN. |
| `proof/span-mouth.md` | English nano and Polish v3 on the default speakers. |
| `proof/g429-run-stop.md` | Injected text, Gemma `call:stop`, organism exit, port left accepting. |
| `proof/g429-live-mic.md` | One cued human turn. Entry command is obsolete. |
| `proof/g429-closed-mic-loop.md` | Two inject lines in `proof/inject-two-turn.txt`. |

`proof/README.md` is the PE outbox contract (`iris_outbox.txt`, `iris_status.txt`). Iris consumes those lines in `seat.py` and `assistant.act_signal`.

## Hard laws

- Code outranks docs.
- Leave a healthy `:8765` alone. Iris does not bind it. `run.py stop` does not contact it. `nvidia_stop.py --cutover` is an owner or PM cutover. This dig did not run it.
- Live mic only after a human GO. Inject is the closed-mic path.
- The computer name is not a route key.
- English is nano, or turbo with `--model turbo`. Every other v3 language is v3.
- A spoken stop is the `stop` tool call.
- The branch of record is `runner-h`. Do not fast-forward `main` from this seat.

## Reality vs tip

**Commit-ready.** Before this file, the worktree matched `origin/runner-h` with nothing to commit. Product source is the tracked tree. This scout is the only intended addition.

**Dirty, gitignored, not for a commit.** `.venv/`, `.install/`, built exes and GGUFs, `lid.176.ftz`, root `*_chatterbox_out_*` and `*_vad_out_*`, `mouth.txt`, `vad_run.txt`, `sense_run.txt`, `iris_status.txt`, `iris_outbox.txt`, `nvidia_turn.request.txt`, `nvidia_turn.response.txt`, `assistant.history.txt`, `__pycache__/`. `git status --ignored` reported 20573 ignored paths, almost all `.venv` and `.install`.

**Orphan refs, no unique commits.** Every local branch is an ancestor of `runner-h` (`git branch --no-merged runner-h` is empty). Remotes are gone on `cursor/closed-mic-loop-1bce`, `cursor/iris-outbox-consume-4b6b`, `cursor/iris-start-stop-dc91`, `cursor/iris-voice-loop-5fab`, `cursor/lang-span-mouth-2f02`, `cursor/live-mic-cue-7fcb`, `cursor/prefetch-v3-span-e7af`, `cursor/run-voice-stop-5750`, `cursor/voice-act-loop-8fe8`. Local only, also merged: `cursor/acoustic-loopback-2aa4` (`4214626`), `pr-34-head` (`8709668`). `origin/cursor/pe-brain-finish-df7c` and `origin/cursor/voice-seat-finish-4e30` are ancestors of `origin/runner-h` (the PE branch is 0 commits ahead, 4 behind).

**Stash, orphan text.** `stash@{0}` (`86c01ec`, parent `5ee35d4` on `cursor/voice-act-loop-8fe8`, message `iris-merge-pr30-wip`) adds a "Fresh-tree closed-mic prove" section to `proof/voice-g429.md`. That section is not in the tip file. The same paragraph is already copied inside tracked `Scout-report-Iris.md`. Later proofs cover the speak path. Do not apply the stash onto the tip.

**Forgotten on this disk, ignored by the whitelist.** Not on `origin`.

| Path | Why it is not tip |
| --- | --- |
| `proof/iris-start-stop-plan.md` | Plan for `start.py` / local Gemma failover. `run.py` is the entry. Alone on this PC is `brain cpu`. |
| `proof/housekeeping-scout-runner-h.md` | Audit of `d535349`. |
| `proof/iris-composer-reinstall-g429.md` | Install log note at `175e3f6`, plus `proof/iris-composer-reinstall-9273.log`. |
| `proof/g429-iris-start-stop-merge.md`, `proof/g429-closed-mic-merge.md`, `proof/g429-iris-outbox-merge.md`, `proof/g429-voice-act-merge.md` | Merge slips. Conclusions are in the tracked `g429-*.md` notes. |
| `proof/g429-acoustic-loopback.md` | Acoustic loopback **FAIL** (empty transcript). Scripts `proof/acoustic_loopback.py`, `proof/owner_cue.py`, `proof/beep_loud_test.py` are ignored. |
| `proof/g429-beep-probe.md` | Owner-cue probe. Not the product mouth. |
| `proof/voice-seat-lan.log`, `proof/voice-seat-alone.log`, `proof/voice-seat-turn.txt` | Raw logs for the tracked `proof/voice-seat.md`. |
| `proof/g429-live-mic-*.txt`, `proof/g429-live-mic-utterance.wav`, `proof/g429-live-mic.log` | Raw copies for the tracked live-mic note. |

**Outside the checkout.** `C:\Users\eb-wjt\Downloads\trident-g429-wyjasnienie-pl.mp3` (2,093,472 bytes). `C:\Users\eb-wjt\Downloads\TRIDENT_FROM_ZERO_BOOTSTRAP.md` and `TRIDENT_FROM_ZERO_BOOTSTRAP.legacy-G429.md` (both written 2026-09-29 23:02). Team paste, not product source.

## Tip-cleaner (do not delete from this scout)

Tracked files a later cleaner can drop or archive. They disagree with `run.py`, `assistant.turn_place`, and `assistant.remote_whole` at `1bafab3`, or they are seat snapshots of an older tip. Leave `proof/g429-pe-*.md` for the brain seat. Those are PE measurements in the shared tree, not Iris scratch.

| Path | Why it is stale |
| --- | --- |
| `Scout-report-Iris.md` | Snapshot of `8709668`. Says a down `--nvidia` peer exits `peer missing` and that there is no start script. Tip falls through to this PC and starts with `run.py`. |
| `Scout-report-NVIDIA.md` | PE-disk snapshot of the same commit. Not this machine. |
| `proof/iris-merge-runner-h.md` | Describes tip `123050e`. Says `--nvidia` dies with `brain down` and that the listen mouth is v3. Those sentences are false at `1bafab3`. |
| `proof/g429-iris-start-stop.md` | Records `start.py` and `stop.py`. Those files are absent. `run.py` replaced them. The port result (listener left accepting) is still a valid old run. |
| `proof/voice-g429.md` | Early voice log, including `brain down` and a live turn on Chatterbox v3. Superseded for route and mouth by `proof/voice-seat.md` and `proof/span-mouth.md`. |
| `proof/g429-live-mic.md` | Keep the measurement. The `start.py --live --cue` command is not runnable. |

`proof/inject-two-turn.txt` is a two-line fixture cited by `proof/g429-closed-mic-loop.md`. It is source for that note, not scratch.
