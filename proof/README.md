# Proof notes (Trident PE seat)

## Iris reads `iris_outbox.txt` (PE → Iris one-line handoff)

PE (brain machine, this repo on `trident-nvidia`) writes **one UTF-8 line** when a quiet idle notice finishes with a speakable answer. Iris does not need a second owner turn on PE for that line to appear.

| Field | Value |
|-------|--------|
| File on PE | `iris_outbox.txt` in the Trident repo root (same directory as `gemma.py`) |
| LAN host | `192.168.16.31` (re-check with `ipconfig` if the NIC changes) |
| Example UNC | `\\192.168.16.31\Users\px-wjt\Downloads\Jarvis\Trident\iris_outbox.txt` when `Users` is shared read-only to Iris |
| Format | Single line of text, newline-terminated; no JSON; no hostname inside the file |
| When written | `gemma.py` `idle_notice` after a successful quiet drain (work line dropped, speakable answer stored in `gemma.memory.txt`) |
| Max length | 2000 characters (truncated) |
| Replace semantics | Each successful idle overwrites the whole file atomically via `iris_outbox.txt.tmp` |

**Iris contract (consumer, not implemented in this PR):**

1. Poll or watch `iris_outbox.txt` on the PE path (SMB/robocopy/manual copy — no coordinator on PE).
2. When the file is non-empty, treat the trimmed line as **one action** for the Iris loop (for example post as `--text` to the brain URL, speak locally, or enqueue in Cursor).
3. After acting, clear or rename the file on PE so the same line is not replayed (Iris-side policy; PE only writes).

The running `:8765` worker loads `gemma.py` from disk at start. After a code cutover, the quiet idle path both drains `work` lines and updates `iris_outbox.txt`. Leave-healthy: do not restart `:8765` unless cutover is intentional.

Other proof write-ups in this folder are named `g429-*.md`.
