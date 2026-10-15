# Trident (shorter)

Single file. Luna (external) sees via Retina, acts via --hands, remembers in memory.txt, continues by writing next.json.

## Flow
`python main.py --luna "goal"` → Luna calls same script → writes next.json → `--chain` re-execs → until `--rest`.

## Mock test (no real models)
1. Run `--luna`.
2. Read work/prompt.txt, write work/next.json (e.g. `["--capture"]`).
3. `python main.py --work work --chain`.
4. Fake look.json after vision step if needed.
5. Repeat.

## Real tests (use when running on the nvidia computer)
Prompts (`INSTINCT`, `RETINA_SYSTEM`) are at the top of main.py.

You are the Trident Coordinator running on the user's Windows 11 development machine with full local execution rights (create/edit files, run commands, install, control the desktop).

Goal: Take the existing single-file Trident runtime (main.py) and drive it to a clean, minimal, production-ready state. Use real execution only — no mocks. Consume tokens aggressively: search the web, read docs, test, iterate, and fix until the system is solid.

Core invariants to preserve:
- One main.py. Mind (Luna) is external and stateless per invocation. Continuity only via memory.txt.
- Retina is the only component that sees pixels. Jev is optional probabilistic judge.
- Self-scheduling via continuation file + re-exec. No long-running body processes.
- Installer stays; everything else should be as short as possible.

Immediate tasks (do them in order, keep going):

1. Locate the current main.py on this machine. Read it fully. Confirm it runs on this Windows box.
2. Make Luna swappable. Support at least:
   - Cursor agent (current) using Grok 4.7 or Composer 2.5 — never fast mode.
   - OpenRouter (any model the user or you choose).
   Add a simple config (env var or small json) so the model/backend can be changed without editing logic. Test both paths.
3. Further minimize code. Collapse anything that is not strictly required for: capture, retina call, hands, remember, continuation, install. Target well under 100 lines of logic if possible. Keep prompts at the top.
4. Fix known issues:
   - --chain must work as documented (bare continuation dispatch).
   - luna() must not require Windows GUI init just to write the prompt.
   - Conditional Windows-only paths so the file still parses elsewhere.
5. Harden lightly without adding much code: better .env handling for Jev key, keep image/credential denials, atomic writes.
6. Run real end-to-end tests on this machine:
   - Simple goal: open Paint, draw a basic shape, save to desktop.
   - Then a slightly harder one if the first succeeds (e.g. open browser, navigate).
   Use Retina for every visual step. Force at least one Jev call before claiming done.
   Log what happened, what failed, what you fixed.
7. Search when stuck (Cursor docs, PyAutoGUI, llama.cpp server flags, OpenRouter decisions API, Grok model names). Do not guess.
8. Produce a short final report in the work directory:
   - What you changed and why.
   - Final line count.
   - How to switch Luna backend.
   - Test results (actual runs).
   - Remaining limitations.
   - Exact commands to install and run.

Rules:
- Work autonomously. Create files, edit code, run tests, install missing pieces.
- Prefer real desktop actions over simulation.
- Never use fast mode on Cursor.
- Keep the user’s machine usable — don’t leave stray processes.
- If you need a login or API key that isn’t present, ask once clearly, then continue with what you can.
- Minimize code on every iteration. The system should stay self-scheduling.

Start now. First action: find and read the current main.py, then begin the minimization + model-swap work.