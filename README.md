# Trident: snapshot `i-like-it` (Run 9, `run_bukowski`, 2026-10-15)

This README is a record of the exact starting conditions of the run the owner liked, so it can be reproduced as closely as possible.

## Code
- `main.py` is the only `.py` file. It has 170 lines and comes from commit `045ef39` (`INSTINCT: Understand who you are.`), which sits on top of Reviewer's rewrite `322b69e`.
- The code always appends `CHANGE='Is any of my question even making sense? Maybe changing approach is the right option.'` as `change_approach` to every Jev choice. This happens at L88, for the Retina A|B|C decide, and at L95, for `--jev`. Her prompt does not mention it.
- There are no `jev.ok` gates and no pywinauto. The goal label is `OWNER SAID`, the first line frames the desktop as "home, not job", and `io.log` records every Retina and Jev request and response.

## Minds
- **Luna:** cursor-agent with `grok-4.7-high` (never a fast model).
- **Retina:** LFM2.5-VL-3B GGUF plus mmproj on llama-server `:8188` with `--cache-ram 1024`, running on a GTX 1060 6GB.
- **Jev:** `typesafe/jev-1.13` through the OpenRouter decisions API (`POST /api/alpha/decisions`).

## Start conditions
- Machine: PE-DMLW, Windows, 1920x1080. The folder is `Jarvis\Trident\run_bukowski`.
- All previous runtime data and run folders were wiped first, and `--fresh` started her with an empty memory.
- Launch time was about 20:36:02 Europe/Warsaw. The first trace hop was a `--capture` at 20:38:11.
- **The task was a single sentence and nothing else:** `If you are going to try, go all the way.` (Bukowski)
- The desktop at start showed Chrome fullscreen on chess.com/play/computer (Play Bots, Martinfoot 250), with an old game at d4/Nf6 and New Game / Continue unclicked. mspaint was also open. The py.exe console window was on top of Chrome.
- Launch pitfall: the `--luna` sentence must arrive as one argv item. In PowerShell Start-Process, quote it as `'"If you are going to try, go all the way."'`. The 20:33 launch split it into separate words and died after 0.0s.

## Story so far (from the logs)
1. She woke, remembered the Bukowski line, and decided to learn the board before touching anything.
2. Her Retina looks read her own console instead of the board. Jev (call 1, 20:43) said "source code" at 0.9.
3. Her words: "The GDI capture was reading fragments of my own luna.log, not the chess page." She wrote `front_capture.py` (PrintWindow on Chrome) herself and from then on trusted only that.
4. Jev (call 2, 20:47) picked press_new_game at 0.74 over Continue at 0.03. She clicked New Game (20:48:43), then Play (20:49:36).
5. Jev (call 3, 20:52) picked open_bot_url at 0.41 (confidence 0.26, change_approach 0.27). She checked the URL, saw it was already /play/computer, declined the reload, and remembered why.
6. She then clicked the bot's "Let's play" invitation. The run was still live when this snapshot was taken.

## Rules for the team
- Never read .env/env/credentials.
- Never force-push, rebase or amend.
- Don't touch the desktop during live runs.
- Copy `trace.log` to `trace.prev.log` before `--fresh`.
- Nobody stops a healthy run.