# G429 PE quiet idle drain — live on leave-healthy

Written 2026-09-29 (UTC+2). Worker **trident-nvidia** / My Machine. Checkout `origin/runner-h` tip `900860d`. Listener **not** restarted for this proof (pid **1140**, quiet loop already loaded per `proof/g429-pe-seat-merge.md`).

## Part A — live quiet drain

### Setup

- Listener: `0.0.0.0:8765` → LAN `http://192.168.16.31:8765/`
- Quiet window: **60 s** (`QUIET_S` in `nvidia_worker.py`)
- No `gemma.py --stop`. No `agent -p`.

### 1) Store one `next` work line

First POST only spoke “Acknowledged.” (no `work` block). Second POST via `nvidia_client.py`:

```text
Owner leaves for later. You must call next with line exactly: Say one short sentence: G429 quiet proof lamp is green. Do not answer the work now.
```

The model called `next`. `gemma.memory.txt` gained:

```text
work <<
Say one short sentence: G429 quiet proof lamp is green.
<<
```

(Work text normalized to that single line before the quiet wait.)

### 2) ~60 s quiet

No further POST to `:8765`. Wait started **18:10:56** local; **75 s** elapsed (`proof/g429-quiet-live-log.txt`). POST from step 1 reset the idle clock before normalization.

### 3) Drain once

Evidence after quiet:

- No `work <<` block remains.
- Memory pair from idle notice:

```text
user << idle: Say one short sentence: G429 quiet proof lamp is green.
model << Say one short sentence: G429 quiet proof lamp is green.
```

- `gemma.lastprompt.txt` ends with the idle question (`Next work: Say one short sentence: G429 quiet proof lamp is green.`).
- `gemma.run.err` appended `resident generate 2012 ms` (idle generate; no tool).
- No `grok_bot_spawn.txt`.

### 4) Listener still up

After drain: `netstat` → `0.0.0.0:8765` **LISTENING** pid **1140**.

Memory sha256 after: `F874F7C5285CBCA90EE72CBC62F605FF5F6D636ECC1AE86D4CD6353B73A91B4C`.

Artifacts (gitignored OK): `proof/g429-quiet-live-log.txt`, `proof/g429-quiet-store.out.txt`, `proof/g429-quiet-store2.out.txt`.

## Part B

See `proof/g429-pe-seat-forward.md` and `proof/README.md` (Iris read contract). Implemented on branch `cursor/pe-seat-forward-slice-9f6a`; outbox hook requires a later worker cutover to run inside pid 1140.

## Not done

- Did not send idle speakable text to Iris voice stack.
- Did not edit voice / ASR / mouth files.

## Blockers

None for Part A.
