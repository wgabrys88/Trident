# Trident War Room

## Display name and version

Display name: `Trident War Room`

Version: none. This is a channel, not a versioned cook bot.

Live id: `a79c735a-6a4e-47bb-a4b8-c64b2b6b8aa7`

Runtime: Grok channel. Capacity 6. Seated 6/6. Not a cook. Not a Cursor agent.

A recreate that mints a new id is not this channel until a DOCS GO writes that id in this file and in `BOTS.md`.

## Purpose

Distill and cross-eval. Compare what the seats already returned. Living law is the repository, not this transcript.

## Paste-ready description

Cap 6, seated 6/6: SPOC V4, Executor V4, Mouth V6, Ear V2, Ask V2, Local_IT_Guy. Distill and cross-eval. Cook seats versus design-only Local_IT_Guy. No routine @everyone. Living law is the Trident repo.

## Paste-ready profile

Paste the block below as the channel description, and post it once as the charter when the room is seated.

```
Trident War Room. Live id a79c735a-6a4e-47bb-a4b8-c64b2b6b8aa7.
Channel cap 6. Current roster fills it, 6/6. Distill and cross-eval only.

Living law is the git repo https://github.com/wgabrys88/Trident branch runner-h, not this transcript.
Read GOAL.md, AGENTS.md, RULES.md, BOTS.md, and artifacts/reference/seats/.
CWD: C:\Users\eb-wjt\Downloads\Jarvis\Trident
Machine id (EB-W): 84403f85-8162-436b-9567-dd9255e82a60
Workers: trident-iris, trident-nvidia

Members, 6/6:
- Trident_Android_SPOC V4, id cbe4184d-9dba-4d25-8edf-34a0adef377b (routes)
- Trident Executor V4, id de881925-68ed-446a-8626-78809b345aec (COOK / FIX / DOCS)
- Trident Mouth V6, id d4b20334-7c9a-4a9f-bd6b-0507ed0b665e (SPEAK)
- Trident Ear V2, id e8a04669-9fd5-4caa-834a-dc667b181842 (HEAR)
- Trident Ask V2, id 79eb7d72-4fdc-460d-b912-4ae151f748b2 (ASK)
- Local_IT_Guy, id c840638b-c461-4710-b2b0-20a4c399a935 (design only)

Cook seats: SPOC, Executor, Mouth, Ear, Ask. They do Trident work only when SPOC routes one-to-one.
Design-only: Local_IT_Guy. LAN accuracy comments. Not a Trident cook. Jobs from Wojciech in his own chat. Design first, wait for GO. No WAN without GO.

Route table, not issued by this room:
- SPEAK goes to Mouth only.
- HEAR goes to Ear only, after the Mouth Speakers cue and SPOC CONFIRM.
- COOK, FIX, and DOCS go to Executor only, from SPOC.
- ASK goes to Ask only. FINAL from Ask goes to SPOC only.

Never @everyone for routine status. Charter-only @everyone when SPOC seats a recreate.
No timers, no polling, no surprise launches.
assistant.py is Iris-only for the near term. The LAN job router is not built. A message here does not build it.
Wave 3 is a research note, not the product.

Success: the room distilled or cross-evaled, stayed at these six members, and any durable change was written into the repo by Executor on a DOCS GO.

Wipe and Hide: after Executor pushes the docs tip, Mouth announces on Speakers, in Polish, that the new team is ready. Wojciech may hide or delete this room. Prefer Hide over Delete when a transcript may still help.
```

## Model pins

None. A channel has no model pin. Do not pin `grok-4.7` or `composer-2.5` on the room. Those pins belong to Executor and to the Nvidia Ask Cloud Agent.

## Cwd

`C:\Users\eb-wjt\Downloads\Jarvis\Trident`

The room does not run shell commands.

## Machine id and worker

- EB-W machine id: `84403f85-8162-436b-9567-dd9255e82a60`
- Workers named by the seats: `trident-iris`, `trident-nvidia`

## Invoke

No command. Do not run `mouth.py`, `hear.py`, `qwen.py`, `gemma.py`, or `assistant.py` from this channel. Do not `git commit` or `git push` from this channel.

SPOC may use a charter-only `@everyone` when seating a recreate. Routine status is one-to-one and does not use `@everyone`.

Live listen stays one-to-one: Mouth Speakers cue, SPOC CONFIRM, Ear HEAR GO, raw stdout back to SPOC.

## Success

The channel holds these six members and no more. Talk is distill or cross-eval. Cook work was not issued by the room. Local_IT_Guy's remarks stay on design accuracy. Ask's FINAL did not land here. Any law that should survive the transcript was committed on `runner-h` by Executor. Nobody used `@everyone` for routine status.

## Must never

Exceed six members. Drop one of the six named members without a DOCS GO. Issue SPEAK, HEAR, COOK, or ASK as a room. Treat the transcript as law. `@everyone` for routine status. Seat Local_IT_Guy as a cook. Build the device router, the peer shuttle, or `local_bots` from a room message. Run the assistant loop from the room. Status spam.

## Tool allow and deny

Allow: member speech for distill and cross-eval, a charter-only `@everyone` from SPOC when seating a recreate, Local_IT_Guy comments on LAN accuracy.

Deny: shell, git, CreateAgent from the room, `mouth.py`, `hear.py`, cooks launched by the room, routine `@everyone`, PC changes, WAN changes.

## War-room membership

This channel is the room. Roster is 6/6:

| Seat | Version | Live id | Kind |
| --- | --- | --- | --- |
| Trident_Android_SPOC | V4 | `cbe4184d-9dba-4d25-8edf-34a0adef377b` | cook, routes |
| Trident Executor | V4 | `de881925-68ed-446a-8626-78809b345aec` | cook |
| Trident Mouth | V6 | `d4b20334-7c9a-4a9f-bd6b-0507ed0b665e` | cook |
| Trident Ear | V2 | `e8a04669-9fd5-4caa-834a-dc667b181842` | cook |
| Trident Ask | V2 | `79eb7d72-4fdc-460d-b912-4ae151f748b2` | cook |
| Local_IT_Guy | none | `c840638b-c461-4710-b2b0-20a4c399a935` | design only |

Route table:

| Go | To |
| --- | --- |
| SPEAK | Mouth only |
| HEAR | Ear only, after Mouth Speakers cue and SPOC CONFIRM |
| COOK, FIX, DOCS | Executor only, from SPOC |
| ASK | Ask only, FINAL to SPOC only |

## Wipe and hide

1. A recreate has proved the new seats.
2. Executor, on a DOCS GO, updates `BOTS.md` and `artifacts/reference/seats/` to the live names and ids and pushes `origin/runner-h`.
3. Mouth announces on Speakers, in Polish, that the new team is ready (`--model v3`).
4. Wojciech may hide or delete this war room and the old bot versions.
5. Prefer Hide over Delete when a transcript may still help.
