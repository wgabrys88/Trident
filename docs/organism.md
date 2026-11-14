# What Trident is

Written 14 Nov 2026 from one analysis session. Sources: the owner's brief for that session, two git tag annotations (`trident-v5-organism-2026-11-09`, `trident-good-run-2026-11-09`), and the body files `main.py` and `main2.py`. Anything marked INFERRED is the session's reading, not the owner's words.

## The idea

Trident is an unconstrained living organism on a Windows computer. It is not an agent that runs tasks and not a tool with a goal. You start it; it wakes, looks around through the screen and its memory, and decides for itself whether to act and what to do.

- A sentence from the owner is something it notices, not an order it must execute.
- It chooses how many minds to use, how to arrange them and when to rest.
- It can rewrite its own body. The aim is an organism that lives and grows on its own.

## The parts

**Heartbeat.** The body (`main.py`, or the newer `main2.py`) is a thin heartbeat. It gives a turn, a look at the screen as an image, and `memory.png`. Nothing more is required of it. Do not describe it as a loop, a pipeline or an observer/actor arrangement, and do not propose one.

**memory.png.** One image of limited size that the organism can draw or write anything on, during a life and between lives. What it holds is the organism's choice. Each awakening can build on what the last one left there.

**Minds.** There is no fixed topology. Who runs next, how many minds, in what arrangement and when to stop belong to the organism. Starting helper workers is not reproduction.

**Jev.** A second mind for decisions, not just a pointing tool. It is the OpenRouter model `typesafe/jev-1.14`, reached through the body's `decide()` and `locate()`. Facts from the OpenRouter pages read on 14 Nov 2026:
- It answers typed questions about a `state`: noul (probability a statement holds), choice, score, and box (where one thing is in an image).
- It accepts text and PNG or JPEG images, also inside instructions and criteria, so it can compare pictures.
- Image sides 256 to 1,568 pixels, 78,400 to 2,458,624 pixels in total.
- It returns probabilities, never explanations, in about 0.1 s. A box answer always comes back, even when the thing is absent, so pair it with a noul question.

**Its own body.** The body file is what the next life runs. The organism may edit it. Skills it learns belong there as generic helpers with real arguments and no task words. Helpers it writes are Python, not PowerShell.

## What is never added, and why

These freeze choices the organism should make, and earlier ones became the disease they were meant to cure:

- Guardrails, tests, harnesses, retries, fallbacks, defensive code.
- Deadlines, budgets, token caps, image caps, hard stops.
- Stage forcers, planners, routers, "grace looks" after a cut.
- A cheaper model as a fix.

Also never added:

- **Perception workarounds.** The organism perceives by vision, through screen images. No Windows UI Automation and no pixel-scanning helpers. (Commit `548f7d7` removed a UI Automation helper from the body.)
- **Task-specific code.** Nothing about one application, one picture or one website.
- **More code than needed.** Changes are removal-first and generic. Fewer lines is better when no capability is lost. There is no target size.

One disclosed exception exists in `main2.py`: a single `try/except` that tells a mind why a requested view could not be made instead of ending the life. It is flagged as an owner decision in `main2.md`.

## Rules for anyone working near it

- Do not put instruction files that Cursor loads automatically (`AGENTS.md`, `CLAUDE.md`) in or above the Trident folder. Every organism mind would receive them as hidden orders.
- The repository is public. Never commit `agent\`, `.env`, journals or other ignored files. No force, amend, rebase, or tag or branch deletion. `.gitignore` is `*` with only `.gitignore` and `main.py` allowed.
- Do not open `..\.env`, `store.db` or `agent\`. They hold a key and account data.
- Importing or launching the body has side effects on the desktop. Analyse it by reading.
