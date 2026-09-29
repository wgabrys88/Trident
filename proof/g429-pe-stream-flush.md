# G429 PE stream — sentence flush

Written 2026-09-29. The listener on `:8765` was not restarted. `gemma.py --stop` was not run.

## What was wrong

`gemma.response.txt` already grew token by token during the generate. `gemma.py --stream` flushed those pieces to stdout. The worker's `stdout.read(4096)` is a buffered pipe read. On this Python it waits until 4096 bytes or the process exits. A short reply is one HTTP chunk at the end, then the blank line. That is the ~6.8s single piece.

## What changed

`gemma.py --stream` writes a speakable sentence when the next word starts. Text before `<channel|>` is not written. A tool call is not written. The tail is written when the generate finishes, still before the process exits. A POST without `stream` is still the full generation in `{"text": ...}`.

`nvidia_worker.py` opens the stream pipe with `bufsize=0`, so a flush returns to the worker and goes out as its own chunk. The blank line after the body is unchanged. `nvidia_client.py --stream` reads with `read1` so it does not wait to fill 4096 decoded bytes.

The process already listening on `:8765` (pid 2184) does not load that read. It keeps the buffered read until that process is started again on purpose.

## Proof on a second listener

`127.0.0.1:18765` was a new worker, pid 2388, parent 10676. It used the same resident, `gemma.pid` 2064. `:8765` stayed pid 2184. After the check, 2388 and 10676 were stopped. 2064 was still `ready`. 2184 was still LISTENING. 18765 was closed.

Stream text: count from one to eight, each number its own sentence. Chunk times from the first byte of the response:

```text
1041 ms  One.
1087 ms  Two.
1147 ms  Three.
1206 ms  Four.
1264 ms  Five.
1319 ms  Six.
1378 ms  Seven.
1432 ms  Eight.
1460 ms  blank line
1460 ms  end
```

`One.` arrived 419 ms before the blank line. The later sentences arrived while the generate was still going.

The same port, without `stream`, returned `{"text": "Green.\n"}`.

## The listener Iris uses

A stream to `127.0.0.1:8765` with the new `gemma.py` and the old worker read, count from one to four:

```text
1231 ms  One. Two. Three. Four.
1232 ms  blank line
```

One piece, at the end. Speak-while-decode against this process waits for that piece. The code that sends sentences early is the `bufsize=0` read, and this process does not have it.
