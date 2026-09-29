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

## Status lines (`iris_status.txt`)

Same directory. Iris can read these lines. They are not audio. The outbox line above is the one `assistant.py --iris-outbox` speaks.

| Line | When |
| --- | --- |
| `say <sentence>` | Quiet drain finished. Same sentence as `iris_outbox.txt`. |
| `work <line>` | `next` stored a waiting line. |
| `stop voice` | The owner asked the voice to go quiet. Iris can act on this line. The brain stays up. |
| `stop <line>` | `stop` dropped that waiting work line. |

The file keeps the last 40 lines. No hostname. No JSON.

**Iris contract (consumer on Iris):**

1. Point at the PE file (`TRIDENT_IRIS_OUTBOX` or SMB path) or a local `iris_outbox.txt` for proof. `TRIDENT_IRIS_SEAT` is the same exchange with `status`, `work`, and `say` blocks.
2. A plain line is `say`. Iris claims the file, speaks `say` through the mouth, and asks the brain about `status` and `work`. A failed act writes the signal back. No audio comes from the brain.
3. Missing or empty file exits 0 with `iris outbox missing` / `iris outbox empty` on stderr (no fake speak).

The running `:8765` worker loads `gemma.py` from disk at start. After a code cutover, the quiet idle path both drains `work` lines and updates `iris_outbox.txt`. Leave-healthy: do not restart `:8765` unless cutover is intentional.

Other proof write-ups in this folder are named `g429-*.md`.
