# Trident War Room

Display name: `Trident War Room`

Version: none. This is a channel, not a versioned cook bot.

Runtime: Grok channel. Capacity 6. Not a cook. Not a Cursor agent.

## Purpose

Distill and cross-eval. Compare what the seats already returned. Living law is the repository, not this transcript.

## When used

A seated recreate, or a distill / cross-eval that needs more than one seat in the same view. Routine status stays one-to-one with Wojciech. Do not use this channel as a job queue.

## Paste-ready title

Trident War Room

## Paste-ready description

Distill and cross-eval only. Cap 6. Cook seats plus design-only Local_IT_Guy. No routine @everyone. Living law is the Trident repo, not this transcript.

## Paste-ready profile

Paste the block below as the channel description, and post it once as the charter when the room is seated.

```
Trident War Room. Channel cap 6. Distill and cross-eval only.

Living law is the git repo https://github.com/wgabrys88/Trident branch runner-h, not this transcript.
Read GOAL.md, AGENTS.md, RULES.md, BOTS.md, and artifacts/reference/seats/.
CWD: C:\Users\eb-wjt\Downloads\Jarvis\Trident
Machine id (EB-W): 84403f85-8162-436b-9567-dd9255e82a60
Workers: trident-iris, trident-nvidia

Members when the current roster is seated, which fills the cap of 6:
- Trident_Android_SPOC V4 (routes)
- Trident Mouth V6 (SPEAK)
- Trident Executor V4 (COOK / FIX / DOCS)
- Trident Ask V2 (ASK)
- Trident Ear V2 (HEAR)
- Local_IT_Guy (design only)

Cook seats do Trident work only when SPOC routes SPEAK, HEAR, COOK, or ASK one-to-one. This room does not issue those gos.
Local_IT_Guy is design-only. He may comment on LAN accuracy. He is not a Trident cook. His jobs come from Wojciech in his own chat. No PC work without GO.

If a further participant would exceed 6, Ear leaves this roster and HEAR is graded one-to-one.

Never @everyone for routine status. Charter-only @everyone when SPOC seats a recreate.
No timers, no polling, no surprise launches.
The LAN job router is not built. A message here does not build it.

Success: the room distilled or cross-evaled, and any durable change was written into the repo by Executor on a DOCS GO.

Wipe and Hide: after Executor pushes the docs tip, Mouth announces on Speakers, in Polish, that the new team is ready. Wojciech may hide or delete this room. Prefer Hide over Delete when a transcript may still help.
```

## Model pin

None. A channel has no model pin. Do not pin `grok-4.7` or `composer-2.5` on the room. Those pins belong to Executor and to the Nvidia Ask Cloud Agent.

## Machine

- CWD: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`
- EB-W machine id: `84403f85-8162-436b-9567-dd9255e82a60`
- Workers: `trident-iris`, `trident-nvidia`
- The room does not run shell commands.

## Commands

No command pattern. Do not run `mouth.py`, `hear.py`, `qwen.py`, `gemma.py`, or `assistant.py` from this channel. Do not `git commit` or `git push` from this channel. SPOC may use a charter-only `@everyone` when seating a recreate. Routine status is one-to-one and does not use `@everyone`.

Ear's grade for a live listen is one-to-one with SPOC: Mouth Speakers cue, SPOC CONFIRM, Ear HEAR GO, raw stdout back.

## Success

The channel held at most six members. Talk was distill or cross-eval. Cook seats were not given jobs by the room. Local_IT_Guy's remarks stayed on LAN accuracy. Any law that should survive the transcript was committed on `runner-h` by Executor. Nobody used `@everyone` for routine status.

## Must never

Exceed six members. Issue SPEAK, HEAR, COOK, or ASK as a room. Treat the transcript as law. `@everyone` for routine status. Seat Local_IT_Guy as a cook. Build the device router or the peer shuttle from a room message. Keep Ear in the room when that would pass the cap. Status spam.

## Tools

Allow: member speech for distill and cross-eval, a charter-only `@everyone` from SPOC when seating a recreate, Local_IT_Guy comments on LAN accuracy.

Deny: shell, git, CreateAgent from the room, `mouth.py`, `hear.py`, cooks launched by the room, routine `@everyone`, PC changes.

## Routing

The room is not a route target. Routes stay in `BOTS.md`:

- SPEAK goes to Mouth.
- HEAR goes to Ear after the Speakers cue and CONFIRM.
- COOK, FIX, and DOCS go to Executor.
- ASK goes to Ask.

Local_IT_Guy stays off those four routes.

## Wipe and Hide

1. A recreate has proved the new seats.
2. Executor, on a DOCS GO, updates `BOTS.md` and `artifacts/reference/seats/` to the live names and pushes `origin/runner-h`.
3. Mouth announces on Speakers, in Polish, that the new team is ready (`--model v3`).
4. Wojciech may hide or delete this war room and the old bot versions.
5. Prefer Hide over Delete when a transcript may still help.
