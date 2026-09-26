# RULES

Standing law for every session in this repository. `GOAL.md` is the finish line. `AGENTS.md` is how to work on this Windows machine. This file wins on process.

1. Never open a pull request. Do not create one, do not ask for one, and do not let tooling open one. Land work as commits on `runner-h` and push that branch to `origin` when this clone already has that remote.
2. Only create new commits. Never amend, rebase, squash, reset, or otherwise rewrite history. Never force-push.
3. Proofs are Windows-local. Capture is WASAPI, chosen by friendly name, not by device index. Use a virtual audio cable (VB-Audio; the person may call it a BB cable): play into the cable input, capture from the cable output. Do not use the room microphone. If no cable is installed, install one. During a chain proof the mouth plays on the real speakers, not into the cable input, so capture does not hear the mouth.
4. An extended commit message carries only the delta for this change: what changed, why, and the method used for this step. Point to `GOAL.md`, `AGENTS.md`, and `RULES.md` for the standing handoff. Do not copy those files into the message. Do not dump chat history, an earlier assignment, or another computer's notes into the commit.
5. One role, one executable, one text file. Files are the only meeting place. Do not add an orchestrator. Do not commit generated audio, run outputs, models, the install tree, or the virtual environment.
6. Prefer one commit for one change. A second commit is only to repair a broken commit that was just created for that same change, and it is a new commit, not an amend.
