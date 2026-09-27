# BOTS

Index and routes for the live tip-family seats. Paste-ready recreate text is one file per seat under `artifacts/reference/seats/`. Read that file before creating or replacing a seat. A Cursor cook inherits no Grok memory: name branch `runner-h` and these paths in every prompt.

If this index and a seat file disagree, stop and fix both in one docs change.

Chat history is not law. Durable changes land in this file, the seat file, and the living docs.

## Live seats

One agent per role. Channel capacity is six. The seated set is SPOC, Mouth, Executor, Ask, Ear, and Local_IT_Guy. If a further participant would exceed six, Ear leaves and HEAR stays one-to-one. Membership rules are in `artifacts/reference/seats/WAR_ROOM.md`.

| Seat | Display name | Recreate file | Runtime |
| --- | --- | --- | --- |
| SPOC | Trident_Android_SPOC V4 | `artifacts/reference/seats/SPOC.md` | Grok bot |
| Mouth | Trident Mouth V6 | `artifacts/reference/seats/MOUTH.md` | Grok bot |
| Ear | Trident Ear V2 | `artifacts/reference/seats/EAR.md` | Grok bot |
| Ask | Trident Ask V2 | `artifacts/reference/seats/ASK.md` | Grok bot. Nvidia ask is one Cloud Agent. |
| Executor | Trident Executor V4 | `artifacts/reference/seats/EXECUTOR.md` | Cursor agent on `trident-iris` |
| Local_IT_Guy | Local_IT_Guy | `artifacts/reference/seats/LOCAL_IT_GUY.md` | Design seat. Not a Trident cook. |
| War room | Trident War Room | `artifacts/reference/seats/WAR_ROOM.md` | Channel, max 6. Not a cook. |

Trident seats that take routed work: SPOC, Mouth, Ear, Ask, Executor. SPOC routes. Executor is the only repo cook. Local_IT_Guy is design-only.

## Routes

| Go | To | From | Gate |
| --- | --- | --- | --- |
| SPEAK | Mouth only | SPOC or Wojciech | Mouth is the only speaker. Executor does not speak. |
| HEAR | Ear only | SPOC only | After the Mouth Speakers cue and SPOC CONFIRM when the listen is live. |
| COOK, FIX, DOCS | Executor only | SPOC only | Iris checkout. No C++ without Wojciech's explicit go. |
| ASK | Trident Ask V2 only | SPOC or Wojciech | Iris: `qwen.py` in the worker shell. Nvidia: only when the go names Nvidia. |

Chat text with Wojciech is English. SPEAK stays on Mouth.

Local_IT_Guy takes jobs only from Wojciech in Local_IT_Guy's own chat. SPOC does not send COOK, SPEAK, HEAR, or ASK there.

## Shared stack

- Iris cwd: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`. EB-W machine id: `84403f85-8162-436b-9567-dd9255e82a60`. Worker: `trident-iris`.
- Nvidia worker: `trident-nvidia`. Checkout: `C:\Users\px-wjt\Downloads\Jarvis\Trident`. Leave it alone unless a route names it.
- Repo: https://github.com/wgabrys88/Trident , branch `runner-h`.
- Living laws: `GOAL.md`, `AGENTS.md`, `RULES.md`, this file, and `CODE_REVIEW_CHECKLIST.md` when reviewing.
- Shell is PowerShell. `Set-Location` to the cwd, then run the command. PowerShell on Iris does not accept `&&`.
- Commits are new commits on `runner-h` from the Iris checkout. Never open a pull request. Never set `starting_ref`. Never amend, rebase, squash, or force-push. Never reset, except the local Nvidia Ask teardown in `artifacts/reference/seats/ASK.md` and `RULES.md`.
- No C++ / `.cpp` edit without Wojciech's explicit go.
- `sense.exe` sets batch threads from `sense.threads`. There is no `sense.threads-batch` key.
- Locked LAN addresses and the three pools are in `artifacts/reference/tracks/DEVICE_ROUTER.md`. That router is not built.

## Model pins

Pins live in the seat file. The index records the ones that are fixed:

- Executor, Cursor on `trident-iris`: `grok-4.7`, 256k context, `reasoning_effort` `xhigh`, `fast` false.
- Ask, Nvidia Cloud Agent only: `composer-2.5`, `fast` false.
- SPOC, Mouth, Ear, and Local_IT_Guy are Grok bots. This repo does not pin a Cursor model for them.

## Local assistant

On Iris, from the cwd above. The track file is `artifacts/reference/tracks/IRIS_ASSISTANT.md`. Inference stays on this machine once the weights are local. Grok is off this path. The script makes no network call. Default brain is `qwen`.

```
.\.venv\Scripts\python.exe assistant.py
.\.venv\Scripts\python.exe assistant.py --once --text "Say only: ready." --model nano
```

`assistant.py` chains `hear.py`, then `qwen.py` (default) or `gemma.py`, then `mouth.py`. It is not a seat. It does not start the five residents and does not keep them loaded. Program flags are in `GOAL.md`.

## Pictures

`artifacts/reference/design/README.md` captions the five architecture PNGs. They are reference drawings. The markdown is the law.

## Handoff

Mouth, Executor, and Ask ack, do the work, and return FINAL or a blocker to SPOC. Ear acks, then FINAL only: one line for exit, seconds, and device, then the original full stdout. An Iris Ask FINAL is the exit code, the generation stdout, and a short stderr summary. A Nvidia Ask FINAL adds the tip SHA and nvidia git clean yes/no after the teardown. They stay idle until the next go.

Live listen: Mouth plays the Speakers cue. SPOC confirms. SPOC sends Ear HEAR GO. Ear returns the raw stdout. SPOC brings that stdout to Wojciech unchanged.

HARD STOP to Mouth cancels the remaining chunks. Do not resume them.

## Wipe and Hide

After a recreate proves the new seats: Executor, on a DOCS GO, updates this file and `artifacts/reference/seats/` to the live names and pushes the tip. Mouth then announces on Speakers, in Polish, that the new team is ready. Wojciech may hide or delete the war room and the old bot versions. Prefer Hide over Delete when a transcript may still help. The same order is in every seat file.
