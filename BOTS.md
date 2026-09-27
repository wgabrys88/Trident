# BOTS

Index and routes for the live tip-family seats. Paste-ready recreate text is one file per seat under `artifacts/reference/seats/`. Read that file before creating or replacing a seat. A Cursor cook inherits no Grok memory: name branch `runner-h` and these paths in every prompt.

If this index and a seat file disagree, stop and fix both in one docs change.

Chat history is not law. Durable changes land in this file, the seat file, and the living docs.

## Live seats

One agent per role. Channel capacity is six, and the live roster fills it (6/6). Membership and the route table are in `artifacts/reference/seats/WAR_ROOM.md`.

| Seat | Display name | Live id | Recreate file | Runtime |
| --- | --- | --- | --- | --- |
| SPOC | Trident_Android_SPOC V4 | `cbe4184d-9dba-4d25-8edf-34a0adef377b` | `artifacts/reference/seats/SPOC.md` | Grok bot. Single Android entry, Europe/Warsaw. |
| Mouth | Trident Mouth V6 | `d4b20334-7c9a-4a9f-bd6b-0507ed0b665e` | `artifacts/reference/seats/MOUTH.md` | Grok bot. Speakers only. |
| Ear | Trident Ear V2 | `e8a04669-9fd5-4caa-834a-dc667b181842` | `artifacts/reference/seats/EAR.md` | Grok bot. |
| Ask | Trident Ask V2 | `79eb7d72-4fdc-460d-b912-4ae151f748b2` | `artifacts/reference/seats/ASK.md` | Grok bot. Iris is direct `qwen.py`. Nvidia is one Cloud Agent. |
| Executor | Trident Executor V4 | `de881925-68ed-446a-8626-78809b345aec` | `artifacts/reference/seats/EXECUTOR.md` | Cursor agent on `trident-iris`. |
| Local_IT_Guy | Local_IT_Guy | `c840638b-c461-4710-b2b0-20a4c399a935` | `artifacts/reference/seats/LOCAL_IT_GUY.md` | Design seat. Not a Trident cook. |
| War room | Trident War Room | `a79c735a-6a4e-47bb-a4b8-c64b2b6b8aa7` | `artifacts/reference/seats/WAR_ROOM.md` | Channel, 6/6. Not a cook. |

Cook seats: SPOC V4, Mouth V6, Ear V2, Ask V2, Executor V4. SPOC routes. Executor is the only repo cook. Local_IT_Guy is design-only.

## Routes

| Go | To | From | Gate |
| --- | --- | --- | --- |
| SPEAK | Mouth only | SPOC or Wojciech | Mouth is the only speaker. Executor does not speak. |
| HEAR | Ear only | SPOC only | After the Mouth Speakers cue and SPOC CONFIRM when the listen is live. |
| COOK, FIX, DOCS | Executor only | SPOC only | Iris checkout. No C++ without Wojciech's explicit go. |
| ASK | Trident Ask V2 only | SPOC or Wojciech | Iris: direct `qwen.py`, no Cloud Agent. Nvidia: one Cloud Agent, `composer-2.5`, `fast` false, execute-only `gemma.py`, only when the go names Nvidia. FINAL to SPOC only. |

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
- Locked LAN addresses, pool ids `iris_cpu`, `iris_vulkan`, and `nvidia_cuda`, and the future job surface are in `artifacts/reference/tracks/DEVICE_ROUTER.md`. That router is not built.
- `artifacts/reference/WAVE3_PLAN.md` and the wave3 scripts are a research note (keep-research). They are not the product path.

## Model pins

Pins live in the seat file. The index records the ones that are fixed:

- Executor, Cursor on `trident-iris`: `grok-4.7`, 256k context, `reasoning_effort` `xhigh`, `fast` false.
- Ask, Nvidia Cloud Agent only: `composer-2.5`, `fast` false, execute-only `gemma.py`. Iris ask has no Cloud Agent pin.
- SPOC, Mouth, Ear, and Local_IT_Guy are Grok bots. This repo does not pin a Cursor model for them.

## Local assistant

On Iris, from the cwd above. Near term, `assistant.py` runs only on Iris. It does not move to Nvidia. The track file is `artifacts/reference/tracks/IRIS_ASSISTANT.md`. Inference stays on this machine once the weights are local. Grok is off this path. The script makes no network call. Default brain is `qwen`. Wave 3 is not this path.

```
.\.venv\Scripts\python.exe assistant.py
.\.venv\Scripts\python.exe assistant.py --once --text "Say only: ready." --model nano
```

`assistant.py` chains `hear.py`, then `qwen.py` (default) or `gemma.py`, then `mouth.py`. It is not a seat. It does not start the five residents and does not keep them loaded. Program flags are in `GOAL.md`.

## Pictures

`artifacts/reference/design/README.md` captions the five architecture PNGs. They are reference drawings. The markdown is the law.

## Handoff

Mouth, Executor, and Ask ack, do the work, and return FINAL or a blocker to SPOC. Mouth FINAL names exit code, model, chunk count, and wall-clock seconds. Executor FINAL is dense: paths, tip SHA, and the proof or the blocker. Ear acks, then FINAL only: one line for exit, seconds, and device, then the original full stdout. An Iris Ask FINAL is the exit code, the generation stdout, and a short stderr summary of at most 20 non-tensor lines. A Nvidia Ask FINAL adds the tip SHA and nvidia git clean yes/no after the teardown. Ask FINAL goes to SPOC only. They stay idle until the next go.

Live listen: Mouth plays the Speakers cue. SPOC confirms. SPOC sends Ear HEAR GO. Ear returns the raw stdout. SPOC brings that stdout to Wojciech unchanged.

HARD STOP to Mouth cancels the remaining chunks. Do not resume them.

## Wipe and Hide

After a recreate proves the new seats: Executor, on a DOCS GO, updates this file and `artifacts/reference/seats/` to the live names and pushes the tip. Mouth then announces on Speakers, in Polish, that the new team is ready. Wojciech may hide or delete the war room and the old bot versions. Prefer Hide over Delete when a transcript may still help. The same order is in every seat file.
