# G429 PE memory — live multi-turn

Written 2026-09-29 after two injected text POSTs through the listener that was already up. The listener was not restarted. `gemma.py --stop` was not run. No second `gemma-brain.exe`.

## For the Iris stream seat

`nvidia_worker.py` is unchanged. `stream: true` is still chunked `text/plain` of the sampled pieces, then a blank line. This check's stream client exited 0. The body was `Blue.` and it was not JSON.

Text turns no longer prefill `<|channel>thought`. A stream body can be the answer with no `<channel|>` in it. `assistant.speakable` still returns that text. Image turns still close an empty thought channel. A `<<trident-inbox>>` question still leaves the channel open and does not touch `gemma.memory.txt`.

Turns already in `gemma.memory.txt` were left there. One of them, the lamp sentence, was posted again from `192.168.16.45` between the two harbor posts below.

## What changed

Past model turns in the text prompt are the speakable line only. They are not wrapped in a thought channel. The current text turn starts at `<|turn>model`. `remember` still writes one fact, and a duplicate fact is not written twice. Facts are labeled `Remembered:`.

`devices` reports the CUDA device, the Vulkan device, and whether they are the same adapter. It uses the names `gemma.py` already reads. No hostname. `cursor` is unchanged.

`gemma.lastprompt.txt` is the last text prompt, rewritten each text turn, after `gemma.prompt.txt` has been consumed. It is gitignored.

## Live turns

Before and after: `gemma.pid` was pid 2064, state `ready`, same fingerprint. `netstat` showed `0.0.0.0:8765` LISTENING, pid 2184.

POST `http://127.0.0.1:8765/` without `stream`. The first text was: the harbor code is `HARBOR-KITE-029`, do not call a tool, reply with exactly `Harbor code noted.` The body was `Harbor code noted.`

The next text did not contain `HARBOR-KITE-029`. It asked which harbor code had been given and asked for that code only. The body was `HARBOR-KITE-029`.

`gemma.lastprompt.txt` for that second POST had the code only in the earlier user turn, then the plain model line `Harbor code noted.`, then the question. There was no `<|channel>thought` in that prompt. The file ended:

```text
<|turn>user
The harbor code is HARBOR-KITE-029. Do not call a tool. Reply with exactly these three words: Harbor code noted.<turn|>
<|turn>model
Harbor code noted.<turn|>
<|turn>user
What harbor code did I give you earlier? Reply with that code only.<turn|>
<|turn>model
```

`gemma.memory.txt` appended that pair. The earlier hello and lamp pairs were still above it.

A later POST asked for the `devices` tool and then only the adapter word. The follow-up prompt contained:

```text
<|tool_call>call:devices{}<tool_call|><|tool_response>response:devices{adapter:<|"|>same<|"|>,cuda:<|"|>NVIDIA GeForce GTX 1060 6GB<|"|>,vulkan:<|"|>NVIDIA GeForce GTX 1060 6GB<|"|>}<tool_response|>
```

The body was `same`.

Stream POST, same URL, text `Say the word blue.` Client exit 0. Stdout was `Blue.`

## Not run

PE-alone speech. CUDA device 0 and Vulkan device 0 are both `NVIDIA GeForce GTX 1060 6GB`, so `assistant.speak_raw` would stop this resident before the mouth. That stop was not called. The worker does not flip between turns.
