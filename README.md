# Trident runner-h — spatial-evidence and bounded-episode revision

**Status:** Offline-tested source replacement, **not** live-validated on Windows, Cursor, LFM2.5-VL or Jev. This is a proposed commit for `runner-h` (the branch recorded in the original `.git` metadata); it is not a verified production release.

## What changes

- New `perception.py` defines a structured scene receipt for local Retina: scene, question-premise verdict, UI surfaces (Chrome vs app vs popup vs ad), relevant element boxes, exact readable text, unrestricted rich descriptions, spatial relations, actionable status and uncertainties. A `label`/`description` may contain multiple sentences, not one token. Retina alone receives screenshot bytes; model output is locally parsed and checked. Malformed output is **not** accepted as geometry.
- `observe()` retains its existing name and crop conventions (0..1000 on the **submitted crop**) but now returns `scene`, `structured`, `complete`, `answer`, `raw_answer`, `geometry` and canonical operation receipt identity. The latter is stored by the bus on success.
- `target(observation, element_id, expected_surface='app_content')` converts one grounded element box to physical desktop pixels with receipt, screenshot and window identity. `native(...,targets=bound)` rejects ungrounded pointer operations; Manipulator also checks the window and target box at use. Browser-chrome targeting is possible **when explicitly requested**, not silently mistaken for app content.
- `decide()` fetches the **latest durable structured local look** before sending the state to Jev. A text-only Jev never sees image bytes; it sees rich spatial descriptions directly, not only Luna's paraphrase. If evidence is stale/absent, the request explicitly marks it unavailable. Jev remains a probabilistic adviser, not an action authority.
- Native action reservation and the later visual observation are stored as `memory.pending_action` before/after dispatch. If Luna crashes after Back, the next invocation can see that Back was already attempted, even if Luna failed to refresh the narrative. The post-action look is also recorded without mislabeling it as task success.
- Organism creates a new episode record for every invocation. Default **one native action per Cursor invocation**, then mandatory fresh look and memory handoff. The next process uses persisted memory, not `--resume`. Each Cursor execution now has a 360-second upper bound; timed-out executions are recorded as incomplete and retried in a new episode. This is a per-episode limit, **not** a limit on task length or total number of actions.
- `instinct.txt` explains observation-vs-inference, rich visual descriptions, interface ownership, fresh look, conditional Jev consultation, and synchronous `remember()`.
- Longer vision context/output and timeouts permit richer structured responses. Vision is still a small model; incorrect object detection remains possible.

## Installation: avoid losing your work

1. Commit or back up your current project first. Do **not** copy the replacement's contents over `.git`, your `runtime/`, `run_*/`, or `private/` directories. Never reuse or edit an active run.
2. In a terminal at your own repository: `git status`, `git branch --show-current` and `git switch runner-h`. If your actual branch name differs, resolve that explicitly instead of pushing to the wrong branch.
3. Copy all files from the extracted **`Trident/`** folder into your repository root, replacing matching source files. The archive has no `.git`, `runtime`, large models or execution logs. It does not remove other existing tracked files automatically; review `git status` and remove only obsolete files that you explicitly choose to retire.
4. Offline unit tests: `py -3.11 -B -m unittest discover -s unit_tests -v`. This suite tests parser, GUI-surface provenance, stale receipts, decision evidence and action/lifecycle state. It does not call the models.
5. On a Windows development machine with your original installed runtime, run low-risk smoke tests (observe Notepad or Settings; identify a dialog; verify one click and subsequent look). Then run the chess regression. Ensure the model actually returns parseable structured JSON; adapt the prompt or output token limit if it does not.
6. Review `git diff --check`, `git diff --stat`, the detailed commit message, and `git status`; commit and push on `runner-h` yourself.

## Model protocol and limitations

`Retina.inspect` sends a single screenshot/crop via llama.cpp's `/v1/chat/completions`, with `response_format: {"type":"json_object"}` and a detailed spatial schema in its system prompt. This requests JSON; **it is not an independent guarantee that the model obeys the schema**. `perception.parse_scene` validates structure and bounds, not whether the image description is factually true. Fields include:

```json
{
  "scene": "The Chrome profile menu overlays a chess.com online-game page.",
  "premise": "contradicted",
  "answer": "A bot selection dialog is not shown.",
  "surfaces": [
    {"id":"s1","kind":"browser_chrome","box":[700,0,1000,400],
     "description":"Chrome profile menu, separate from page content."}
  ],
  "elements": [
    {"id":"e1","surface_id":"s1","kind":"button","box":[810,100,930,160],
     "text":"Your Chrome","description":"The profile button sits in the browser popup near the top-right. It belongs to Chrome rather than chess.com and is not an opponent selector.",
     "relations":"Above the underlying game page.","actionable":"yes"}
  ],
  "uncertainties": ["Underlying controls are partly obscured."]
}
```

This is a **synthetic example**, not a claim that LFM returned it. Surface categorization, occlusion and model hallucinations are still potential failure points. Purely visual chess information should not be conflated with a reliable legal-move generator. If playing chess becomes a priority, a rules-based engine would be a separate task-specific capability, not a prerequisite for the general-purpose desktop loop.

Mouse fencing uses a PyAutoGUI facade and canonical receipt validation; it is **not a security sandbox**. Arbitrary Python in `native()` can still import other input libraries or run operating-system commands. Do not claim that this implementation prevents a sufficiently unconstrained agent from circumventing targeting. The main protection is the agent's tool protocol combined with auditable traces and constrained click methods.

## Evidence and Windows acceptance tests

The real chess.com log showed: online matchmaking instead of a bot game, Chrome profile UI interpreted as a bot choice, an unexpected testlibrary.com navigation and a recovery that did not return to the predicted setup. The replacement specifically demands surface-separated evidence before a click, detects contradictory premise text, and stores recovery progress across new episodes.

Before deploying:

- **LFM live:** Confirm output JSON parses and `complete=true` at 8192 context and 1600 output tokens, within the 60-second look timeout, on your actual GPU. Test unreadable text and a deliberately loaded question.
- **Click smoke:** Confirm `target()` selects an actual visible app button, coordinate conversion matches its box, a switched window cancels clicking, and the five-second settle yields a new screenshot.
- **Mismatch smoke:** After a harmless wrong-page action, confirm fresh Retina response says `contradicted` and subsequent Luna memory records the discrepancy.
- **Jev:** Inspect a real `discern_to_jev_*_request.json` and verify its `state.retina_evidence.scene.elements[].description` contains multi-sentence spatial context, not simply the Luna hypothesis. Confirm decision API accepts payload size.
- **State continuity:** Stop after Alt+Left execution but before a narrative rewrite; restart and check `pending_action`, `post_action_look`, and two distinct Cursor session IDs. A single growing Cursor invocation is not cross-invocation reuse.
- **Cross-app:** Try Paint canvas, Calculator, Notepad, browser navigation, and app overlays. Validate mouse target policy doesn't overconstrain legitimate workflows.
- **Telephony and voice:** Perform supervised authorization and delivery tests; original relay/phonation remain unchanged, but they were not tested here.

## Research and protocol grounding (checked 2026-10-14)

- Liquid AI, *LFM2.5-VL-3B* model card: https://huggingface.co/LiquidAI/LFM2.5-VL-3B — visual grounding, layout regions and normalized coordinates; experimental format, not guaranteed accuracy.
- ggml-org, llama.cpp server API: https://llama.app/docs/api — JSON response format available; output still validated in Python.
- Cheng et al., *SeeClick: Harnessing GUI Grounding for Advanced Visual GUI Agents*: https://aclanthology.org/2024.acl-long.505/ — GUI element grounding matters beyond language fluency.
- Cursor CLI documentation: https://docs.cursor.com/en/cli/reference/output-format — per-session `init` and terminal `result`; `--resume` needed to intentionally resume a previous conversation.

The ordered notebook files and imported papers mentioned earlier were not in the supplied archives. This revision is based on the provided source/run captures and the independently cited documentation, not invented notebook citations.
