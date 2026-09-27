# Code review checklist

Pass, Fail, or Blocked on the tree in front of you. Read `GOAL.md`, `AGENTS.md`, `RULES.md`, and `BOTS.md` first.

**Pass** cites the source line or the command output. **Fail** names the file, the line, and the contract it breaks. **Blocked** names the missing fact (no cable, no build, no mic). Source wins when this file disagrees. Another machine's run is not a Pass.

No pull request. New commits only. Never amend, rebase, squash, reset, or force-push. A commit body is the delta plus a pointer to the living docs. Iris is the commit checkout.

Record defects. Do not add a resident supervisor, a harness, a mock, or a second copy of a role. `assistant.py` chains the one-shots. It is not that supervisor.

## Items

- [ ] **1. Templates match the readers.** Every key in `vad.txt`, `ear.txt`, `sense.txt`, `gemma.txt`, `chatterbox.txt`, `bake.txt`, and `install.txt` is read by the program that owns that file. Comments, printed lines, and usage lines match the code.
  **Pass:** each key is read and each comment is true.
  **Fail:** an unread key, a false comment, or behavior the template does not name.

- [ ] **2. File values win.** A missing required key is an error. An empty allowed value stays empty.
  **Pass:** the file text is what the call uses.
  **Fail:** a C++ default, a llama.cpp default, or a member initializer replaces a written value.

- [ ] **3. Five residents, no resident supervisor.** Roles are `vad.exe`, `ear.exe`, `sense.exe`, `gemma-brain.exe`, and `chatterbox.exe`. `chatterbox-bake.exe` bakes and exits. `install.py` builds. `mouth.py` starts only `chatterbox.exe`. `hear.py` starts only `nemo-speech.exe transcribe` once. `gemma.py` starts only `gemma-brain.exe`. `qwen.py` starts only `sense.exe`. `assistant.py` starts only `hear.py`, then `qwen.py` or `gemma.py`, then `mouth.py`, through the repo virtualenv. It does not start the five itself.
  **Pass:** nothing starts the five as a supervisor or carries messages between them. `assistant.py` runs those one-shots and lets them exit. It does not keep the residents loaded.
  **Fail:** a resident supervisor, harness, message bus, service, pid file, stop file, or a second synthesizer or recognizer.

- [ ] **4. Files are the meeting place.** Each resident takes one text file and no flags (`usage: program file.txt`). Output is `HH-MM-SS-mmm_<role>_out_NNN` via `CREATE_NEW` in the current directory. The person copies text between resident files. `assistant.py` is outside that path: it passes one-shot stdout into the next one-shot's arguments.
  **Pass:** no resident watches a neighbor.
  **Fail:** a resident copies another role's output into the next, or replaces an existing output file.

- [ ] **5. Text passes through.** Gate and brain write the generation unchanged. Ear writes recognizer stdout unchanged. `hear.py` prints that stdout. The mouth speaks `chatterbox.text` as written.
  **Pass:** the bytes written are the generation or the transcript.
  **Fail:** an added token, a tool schema, a translation, a picture search, or a rewritten transcript.

- [ ] **6. Picture contract.** `gemma.image` is raw base64 or empty. The prompt `gemma-brain.exe` reads already contains `<__media__>` when the image is set.
  **Pass:** C++ does not open a filename as the picture and does not insert the marker.
  **Fail:** a path walk or a kept previous bitmap.

- [ ] **7. `gemma.py`.** From the repo root, venv Python: `gemma.py [--image PATH] [--verbose] "Question."` rewrites `gemma_run.txt` from `gemma.txt`, opens the Gemma 4 thought channel, runs `gemma-brain.exe gemma_run.txt` once with `shell=False`, discards the child's stdout and stderr, and prints the generation including thinking. `--verbose` passes the child's stderr through. `--image` stores raw base64 in `gemma.image` and inserts `<__media__>` when the question lacks it.
  **Pass:** stdout is that generation file. `gemma_run.txt` stays untracked.
  **Fail:** a Python model load, or a committed sidecar.

- [ ] **8. `qwen.py`.** `qwen.py [--verbose] "Question."` rewrites `sense_run.txt` from `sense.txt`, wraps Qwen3 turn markers, runs `sense.exe sense_run.txt` once, discards the child's stdout and stderr, and prints the generation. `--verbose` passes the child's stderr through. `--image` exits 2.
  **Pass:** text-only, stdout is the generation, `sense_run.txt` untracked.
  **Fail:** a vision path or a Python model load.

- [ ] **9. Sense batch threads.** `sense.exe` sets batch threads from `sense.threads`. There is no `sense.threads-batch` key. `gemma.threads-batch` is read from the file.
  **Pass:** those lines still match the source.
  **Fail:** a code default replaces either value.

- [ ] **10. Mouth one-shot.** `mouth.py` validates every chunk, writes `mouth.txt` with `chatterbox.play off`, runs `chatterbox.exe` per chunk, and plays wavs with `PlaySoundW` (`SND_FILENAME | SND_NODEFAULT`) on the default speakers while the next chunk synthesizes. `chatterbox.txt` stays untouched.
  **Pass:** sound from the real speakers.
  **Fail:** Python synthesis, play left `on`, or playback into the cable input.
  **Blocked:** no speakers.

- [ ] **11. Hear one-shot.** `hear.py SECONDS` records the PC mic, runs `nemo-speech.exe` once, prints stdout, and exits with that code.
  **Pass:** raw stdout. The mic is the PC microphone unless the command names the cable.
  **Fail:** a rewritten transcript, or the script speaks.
  **Blocked:** no mic, or `nemo-speech.exe` is missing.

- [ ] **12. PlaySound gate is shipped.** `chatterbox.play` is `on` or `off`. `on` calls `PlaySoundW` inside `chatterbox.exe`. `off` writes the wav and skips PlaySound. `mouth.py` owns Speakers with `PlaySoundW`.
  **Pass:** that contract matches `src/chatterbox.cpp` and `mouth.py`. The diff adds no `.cpp`.
  **Fail:** play left on for a Mouth one-shot, or a new C++ playback edit without Wojciech's go.

- [ ] **13. C++ is gated.**
  **Pass:** the diff is Python and markdown, or the task records Wojciech's explicit go for `.cpp`.
  **Fail:** a `.cpp` edit with no go.

- [ ] **14. Process.** Branch `runner-h`. No pull request. No `starting_ref`. No amend, rebase, squash, reset, or force-push. The commit message is the delta only.
  **Pass:** history is intact.
  **Fail:** a PR, rewritten history, or a commit that pastes the living docs.

- [ ] **15. Docs are atemporal.** `GOAL.md`, `AGENTS.md`, `RULES.md`, `BOTS.md`, and this file match the tree: current contracts, no incident log, no commit hash as law.
  **Pass:** a cold session can continue from those files.
  **Fail:** a doc describes behavior the code does not have.

- [ ] **16. Git whitelist.** `.gitignore` ignores `*` until a `!` rule. `mouth.py`, `hear.py`, `gemma.py`, `qwen.py`, `assistant.py`, `BOTS.md`, and this file stay tracked. Models, `.install`, `.venv`, `C:\tgemma`, `mouth.txt`, `gemma_run.txt`, `sense_run.txt`, `hear_*` leftovers, wavs, and `*_out_*.txt` stay untracked.
  **Pass:** `git status` shows only the intended files.
  **Fail:** a proof artifact or a model is staged.

- [ ] **17. Proofs use the real executables.** One program, then the cable chain: speech in on the cable, text files carry the words, one utterance out of the real speakers. `hear.py` is a separate PC-mic proof.
  **Pass:** files from this machine.
  **Fail:** a mock, or a script that pretends an exe ran. A cable item marked Pass from the room mic.
  **Blocked:** no cable or no build here.

- [ ] **18. Residency stays inside the executable.** Each resident still does one unit of work and exits. A later stay-loaded change is that same executable. `assistant.py` repeats one-shot turns and does not become that resident.
  **Pass:** the review adds no resident supervisor.
  **Fail:** a watcher or a second process is the proposed resident.

- [ ] **19. Publish stays off.** `install.publish` is `off`.
  **Pass:** the key is off.
  **Fail:** a review step publishes a release.

- [ ] **20. `assistant.py`.** From the repo root, venv Python. Interactive: `hear.py` for `--seconds` (default 8), then `qwen.py` unless `--brain gemma`, then one `mouth.py` with every chunk. `--once` is one heard turn. `--text` skips the mic and runs one brain-then-mouth round. `--image` requires `--brain gemma` and otherwise exits 2. Omitted `--lang` matches `mouth.py` (`en`, or `pl` for `v3`). Brain stdout is printed unchanged. `--verbose` is passed to the brain one-shot and that stderr is printed only on a non-zero exit. The mouth gets the speakable span: after the last `</think>`, else after the last `<channel|>`, else the generation with control tokens removed. An unclosed `<think>` is not spoken. Chunk cap is 65 words, or 55 when the mouth language is `pl`. A non-zero child exit is the assistant exit, with the stage on stderr. No network call.
  **Pass:** the dry command `assistant.py --once --text` runs the real `qwen.py` and `mouth.py`.
  **Fail:** a Python model load, a second recognizer or synthesizer, a resident supervisor, or a mock that pretends an exe ran.
  **Blocked:** `sense.exe` or `chatterbox.exe` missing. Speakers not checked from this agent is a manual Speakers confirm, not a mock.

## Closeout

List each id as Pass, Fail, or Blocked with the evidence. Leave this file out of the commit message. A Fail is a defect report. Edit code only when the task also asks for a fix, inside that role's executable and its text file.
