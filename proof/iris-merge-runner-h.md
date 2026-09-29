# Iris voice merge onto runner-h (G429)

Date: 2026-09-29. Seat: Iris (`trident-iris`). Checkout: `C:\Users\eb-wjt\Downloads\Jarvis\Trident`.

`origin/main` was not moved. No force-push. Nothing bound `:8765`. The PE listener was not stopped.

## What landed

`origin/runner-h` fast-forwarded from `d535349a8b99fcdd0e42efb14ec269e0d59c54b1` to `123050e556ed193cc1795a5ed182874b510e4ba1`.

That tip is `cursor/prefetch-v3-span-e7af`. It already contained the two branches under it. One fast-forward brought the whole voice stack. No conflict on this merge.

| PR | Head | Base it was opened against | Result |
| --- | --- | --- | --- |
| 21 | `f03b1f147c42d7f89b1bc31b5f80c8de6a64e68d` `cursor/iris-voice-loop-5fab` | `runner-h` | GitHub marked it merged when `runner-h` moved. `merged_at` 2026-09-29T14:30:41Z. Merge commit recorded as that same head. |
| 25 | `9274d632c907926066f4ab2940e14dce113cb4d3` `cursor/lang-span-mouth-2f02` | `cursor/iris-voice-loop-5fab` | Commits are ancestors of `runner-h` (compare: ahead 0, behind 2). GitHub rejects retarget onto `runner-h` because there are no new commits. Closed. `merged` stays false because the base branch was not the one that moved. |
| 27 | `123050e556ed193cc1795a5ed182874b510e4ba1` `cursor/prefetch-v3-span-e7af` | `cursor/lang-span-mouth-2f02` | Head was identical to `runner-h` after the fast-forward. Same retarget rejection. Closed. `merged` stays false for the same reason. |

Commits now on `origin/runner-h`, oldest first:

| SHA | Subject |
| --- | --- |
| `880663714dc76c11fa25379b9eca4bd35d9a0410` | Listen with resident VAD and speak replies on Chatterbox v3. |
| `940d50ff11fc2e626a115b29cd8ad6db2f25a8d0` | Speak each closed brain sentence while the stream is still open. |
| `f03b1f147c42d7f89b1bc31b5f80c8de6a64e68d` | Record the injected-text turn: one late blob, then PlaySound. |
| `35ee885e370897f79c45df5e58fb2abd1735c607` | Speak English spans with nano and other languages with v3. |
| `9274d632c907926066f4ab2940e14dce113cb4d3` | Record the mixed English and Polish mouth playback. |
| `65cfd44680fff71a2c2cee9b355315bfc9fdfd6f` | Prefetch a known non-English span while nano is still speaking. |
| `123050e556ed193cc1795a5ed182874b510e4ba1` | Record the prefetched Polish span on the speakers. |

The voice code is those seven commits. The commit that added this note is `42be2ad0553ae151d6baa71972a298bbc3be8fb4`, parent `123050e556ed193cc1795a5ed182874b510e4ba1`. The commit that records that SHA is the `runner-h` tip for this pass.

## Iris features on that tip

Checked in the tree at `123050e`, not by starting a listener:

- Resident VAD: `src/vad.cpp` `--resident`, `assistant.py` launches `vad.exe --resident vad_run.txt`.
- Listen mouth is Chatterbox v3. `assistant.py --nvidia` dies with `brain down` when the brain port is closed, and does not call `qwen.py` on that flag.
- Stream mouth: `nvidia_client.iter_stream`, `assistant.StreamFeed`. Door posts stay without `stream`.
- Language spans: `lid.176.ftz` in `assistant.py`. English spans use nano. Other languages use v3.
- Prefetch: `mouth.speak_pieces` prints `mouth: prefetch` and starts a v3 one-shot while nano is still speaking.

Proof already on the branch: `proof/voice-g429.md`, `proof/span-mouth.md`. The speak-while-decode re-prove that was only in the Iris working copy (N=2 against leave-healthy `:8765`, branch `cursor/pe-memory-live-0233`) is committed with this note. That re-prove did not edit product code and did not stop the PE listener.

## Left for the PE twin

Not merged. These are the brain stack. Each one conflicts with this `runner-h` in `README.md`, `assistant.py`, and `nvidia_client.py` (`git merge-tree --write-tree` against `123050e`). `.gitignore` auto-merges. `grok_local_bot.py` auto-merges on the two newest.

| PR | Head | Title |
| --- | --- | --- |
| 22 | `ecb9f4a0ee67cad1a823b05254670aebf0d1ceb4` `cursor/pe-resident-gemma-af6c` | Keep one resident Gemma on the PE text worker |
| 23 | `b4ff0a70bb6444b6f003eb84429303801ed7378d` `cursor/pe-brain-memory-a6f7` | Remember text turns in the resident Gemma brain |
| 24 | `dce6096ad4e28fc5ea5d0449aeb6e6d9dfaf6469` `cursor/pe-memory-live-0233` | Replay Gemma turns as speech on the live brain |
| 26 | `b4cdf1e1fd51fb731b0f53417a23e0ffeb2a4212` `cursor/pe-gemma-tools-e2c5` | Start a local Cursor agent only when Gemma calls it |
| 28 | `551bd26cc779464187ea21446106f3b40dff7678` `cursor/pe-place-devices-741f` | Place the brain from devices and fail when the peer is down |

Shared-file voice hooks inside that stack were not cherry-picked. `release_shared_gpu` and `turn_place` call `gemma.adapter_names`, `gemma.stop_resident`, and `gemma.place`, and they are written against the pre-voice `assistant.py` (`--seconds`, local Qwen/`--nvidia` fork). Iris already places a turn by one TCP probe plus `gemma/scripts/detect_gpu.ps1`, and `--nvidia` fails closed when the port is down. The one-GPU flip needs the PE resident Gemma API. Taking it alone would be glue. PM: PE rebases onto this `runner-h` and keeps the Iris `StreamFeed` / span / prefetch mouth.

## Later Composer reinstall

Do not do this in the merge phase. Do not wipe this checkout. Do not delete `.venv`, `.install`, exes, or GGUFs. Do not run `install.py`. Do not `git clean`. Wait until both twins say `origin/runner-h` is ready.

When that phase starts, from this directory:

```powershell
git checkout runner-h
git pull origin runner-h
python install.py install.txt
```

`python install.py install.txt` is the reinstall. It was not run here. Built artifacts stay gitignored beside the tree.
