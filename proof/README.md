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

**Iris contract (consumer on Iris):**

1. Point at the PE file (`TRIDENT_IRIS_OUTBOX` or SMB path) or a local `iris_outbox.txt` for proof.
2. `assistant.py --iris-outbox` reads the trimmed line, clears the file, speaks it through `mouth.py` (no `:8765` POST).
3. Missing or empty file exits 0 with `iris outbox missing` / `iris outbox empty` on stderr (no fake speak).

The running `:8765` worker loads `gemma.py` from disk at start. After a code cutover, the quiet idle path both drains `work` lines and updates `iris_outbox.txt`. Leave-healthy: do not restart `:8765` unless cutover is intentional.

Other proof write-ups in this folder are named `g429-*.md`.
