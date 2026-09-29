# G429 PE memory — brain seat

Written 2026-09-29 after the store was built and checked without a model call. Port 8765 was already listening. It was not restarted. No POST was sent.

## What changed

`gemma.py` keeps one file, `gemma.memory.txt`, on the machine that runs the brain.

- A text turn builds the prompt from that file: system line, `remember` facts, closed earlier turns, then the new question. The thought channel stays closed.
- After the speakable answer, the turn is appended. Each side is clipped to 200 words. Tool markup stays out.
- `remember` appends one fact, at most 200 characters, then one follow-up. Facts are dropped only after every turn is already gone and the prompt is still over 80_000 characters.
- `cursor` is unchanged: one Cursor CLI job, `grok_bot_spawn.txt`, `BLOCKED` when the CLI is missing, no repo edit.
- The `hello` write is gone.
- `nvidia_worker.py` is unchanged. `do_POST` still has no session. The listening worker already starts `gemma.py` per turn, so the next text POST uses this file without a restart.
- `<<trident-inbox>>` does not read or write the file, including an inbox turn that also carries an image.
- An image turn does not replay the file and does not declare tools. It appends the question and the speakable reply.

Stream bytes are still the sampled pieces. This file does not add a trailer.

## Note for the Iris stream seat

Do not put a session in `nvidia_worker.do_POST`. The POST body stays `id`, `text`, `image`, `image_b64`, and optional `stream`. `stream: true` is still chunked text, then a blank line. Memory is the next text prompt inside `gemma.py`.

## How this was checked

Offline, temp files only. `gemma.memory.txt` was not created in the checkout.

- Empty store, round-trip of one fact and one pair.
- Second prompt contains the first user line and the first reply.
- `remember` parse, fact in the next prompt, follow-up prompt still has the earlier turn.
- Trim at a tight limit keeps the newest pair and `FACT-KEEP`, and rewrites the file.
- Oldest fact drops only when no turns are left and the prompt still does not fit.
- Inbox prompt has no tool declaration and leaves the thought channel open.
- Image prompt has `<__media__>`, no `remember` declaration, thought channel closed.
- 250 words store as 200. A 250-character fact stores as 200.
- Speakable text is the part after `<channel|>`.

`py_compile` of `gemma.py` succeeded.

## Left alone

`netstat` showed `0.0.0.0:8765` LISTENING, pid 2184, before and after the check. `gemma.pid` stayed pid 2064, state `ready`. No `gemma.py --stop`. No second worker. No POST.

## Not verified

A live second answer that uses the first. That needs a generate on the resident, and the resident is the healthy listener's brain. `--once` refuses while that process is up. The prompt check above is the part that does not touch it.
