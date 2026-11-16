# What Trident is

Written 2026-11-14, updated 2026-11-16. The body is `main.py`. Each fact carries the date it was measured or read.

## The idea

Trident is an unconstrained living organism on a Windows computer. It is not an agent that runs tasks and not a tool with a goal. You start it; it wakes, looks around through the screen and its memory, and decides for itself whether to act and what to do.

- A sentence from the owner is something it notices, not an order it must execute.
- It chooses how many minds to use, how to arrange them and when to rest.
- It can rewrite its own body. The aim is an organism that lives and grows on its own.

## The parts

**Heartbeat.** The body (`main.py`) is a thin heartbeat. It gives a turn, a look at the screen as an image, and `memory.png`. Nothing more is required of it. It waits for requests for turns and gives one for each. Do not describe it as a loop, a pipeline or an observer/actor arrangement, and do not propose one. It has no fixed order of minds. On 2026-11-15 every life was started by a human running the launch line in `body.md`; the body has no timer and no restart of its own. When no mind is working and none is asked for, the life ends: that is rest, and the body process exits. A copy with its own workspace starts with `--work`.

**memory.png.** One image that the organism can draw or write anything on, during a life and between lives. What it holds is the organism's choice. Each awakening can build on what the last one left there. The body does not limit its size and attaches the file as it is (2026-11-15: 1560x1240 pixels). A life can end at any moment, so what is not on `memory.png`, in files or in the body is gone.

**Minds.** There is no fixed topology. Who runs next, how many minds, in what arrangement and when to stop belong to the organism. Starting helper workers is not reproduction; a copy started with `--work` is.

**Jev.** A second mind for decisions, not just a pointing tool. It is the OpenRouter model `typesafe/jev-1.14`, reached through the body's `decide()` and `locate()`. Facts from the OpenRouter pages read on 2026-11-14 (SINGLE-SOURCE, the vendor's own pages; they can change):
- It answers typed questions about a `state`: noul (probability a statement holds), choice, score, and box (where one thing is in an image).
- It accepts text and PNG or JPEG images, also inside instructions and criteria, so it can compare pictures.
- Image sides 256 to 1,568 pixels, 78,400 to 2,458,624 pixels in total. The body fits pictures to at least 280 pixels per side, because 256 by 256 is below the pixel minimum.
- It returns probabilities, never explanations, in about 0.1 s. A box answer always comes back, even when the thing is absent, so pair it with a noul question.
- A request with an image runs in a long-context mode the vendor calls slower and less accurate. Image input and box answers were marked early features. List price about $0.042 per million input tokens, output free.
On 2026-11-15 Jev was called in every life; `decide()` replies carry their cost (about $0.00017 to $0.00032 per call), `locate()` does not return it.

**Its own body.** The body file is what the next life runs. The organism may edit it. Skills it learns belong there as generic helpers with real arguments and no task words. Helpers it writes are Python, not PowerShell. (2026-11-15: one added helper ran PowerShell inside Python, against this rule. It was not kept; `findings.md` describes it.)

## What is never added, and why

These freeze choices the organism should make, and earlier ones became the disease they were meant to cure:

- Guardrails, tests, harnesses, retries, fallbacks, defensive code.
- Deadlines, budgets, token caps, image caps, hard stops.
- Stage forcers, planners, routers, "grace looks" after a cut.
- A cheaper model as a fix.

Also never added:

- **Perception workarounds.** The organism perceives by vision, through screen images. No Windows UI Automation and no pixel-scanning helpers in the body.
- **Task-specific code.** Nothing about one application, one picture or one website.
- **More code than needed.** Changes are removal-first and generic. Fewer lines is better when no capability is lost. There is no target size.

One disclosed exception exists: in `Life.waking`, a single `try/except` tells a mind why a requested view could not be made instead of ending the life. It is an open owner decision, listed in `body.md`. The body has five other `try` blocks; they are control flow, not recovery (the Jev error turned into a readable error, the release of the desktop lock, the cleanup when a life ends, the wait for the next agent line, the end of a turn's steps).

## Rules for anyone working near it

- Do not put instruction files that Cursor loads automatically (`AGENTS.md`, `CLAUDE.md`) in or above the Trident folder. Every organism mind would receive them as hidden orders.
- The living body is on branch `runner-h-self-evolve`; the default branch `main` is an older baseline. Clone with `git clone -b runner-h-self-evolve <repository URL>`.
- When a dated statement in these docs and the 2026-11-16 commit message disagree, the later date wins.
- Privacy: journals, `evidence\` folders and `memory.png` hold personal data (screenshots of chats, calls and accounts; identifiers). Never quote, attach or publish them unredacted. Call the person who runs Trident "the owner"; no names, usernames, emails, phone numbers, chat ids, account names or user paths (write `$env:USERPROFILE`) in commits, tags or docs.
- The repository is public. Never commit `agent\`, `.env`, journals or other ignored files. No force, amend, rebase, or tag or branch deletion. Git tracks only `.gitignore`, the body at the root and `docs\*.md`; `.gitignore` itself holds the exact lines.
- Do not open `..\.env`, `store.db` or `agent\`. They hold a key and account data. The body uses two of them itself: `decide()` reads the key from `..\.env` on every call, and the body runs Cursor's agent with the login kept in `agent\`. No mind needs to open or print them.
- Launching the body starts a life on the real desktop. Importing it from a script is how a turn uses its hands: at import the body loads `user32` and `kernel32` with `ctypes.WinDLL` and calls `SetProcessDPIAware()`; these change only the importing process and send no input; nothing moves until a helper is called. Analysts read the body instead of running it.
