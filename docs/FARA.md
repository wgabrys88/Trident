# Fara in Trident: what it is, how to talk to it, what we know

This guide is task-generic. Trident uses Fara on any Windows window: launchers, editors, settings, browsers, drawing programs. Paint shows up below only as one example among others, because most of our recorded runs happened there.

Status words used here:
- **measured**: we saw it in our own runs (journals, `native_results.json`, the 18:33 pilot trace of 2026-10-23).
- **official**: Microsoft says it (model card, the `microsoft/fara` code, the paper, the blog).
- **community**: other people report it (GitHub issues, Hugging Face discussions, Reddit, HN, blog posts).
- **proposed, not implemented**: a roadmap item. Nothing in this file changes code.

All research for this file was done on 2026-10-23 against `microsoft/fara` at commit `a675d6d` and llama.cpp `b11756`.

## 1. What Fara is

- Fara1.5 is a family of computer-use agents from Microsoft Research AI Frontiers, released in three sizes (4B, 9B and 27B). Trident runs **Fara1.5-4B**, a supervised fine-tune of Qwen3.5-4B, as a Q4_K_M GGUF with a Q8_0 mmproj on llama-server ([model card](https://huggingface.co/microsoft/Fara1.5-4B)).
- **It is trained for web browsers.** The card says: "a multimodal computer use agent (CUA) for web browsers". The blog says: "We aim to expand the scope of environments … including desktop and enterprise software" ([blog](https://www.microsoft.com/en-us/research/articles/fara1-5-computer-use-agent/), [paper §4.3](https://arxiv.org/abs/2606.20785)). Desktop use is future work for Microsoft, not a supported target.
- The training mix is about 2M samples: about 60% web trajectories, 12.8% synthetic web-app environments (Mail, Calendar, and so on), 12.5% form filling, 8.8% grounding, 4.9% VQA, and **0.8% GUI drag** ([README](https://github.com/microsoft/fara), paper Fig. 4).
- Grounding is also evaluated on desktop screenshots. Single-step accuracy for the 4B model is 92.3 on ScreenSpot-v2 (mobile, desktop and web), 55.3 on ScreenSpot-Pro (59.9 with zoom), and 68.0 on OSWorld-G Refined (desktop apps) (paper Table 6). Fara can find things on a Windows screen. It was never trained to plan multi-step desktop work.
- The card says it "can also serve as a grounding model for other agents that need pixel-accurate action prediction". That is the Retina role.
- It sees only pixels: no DOM and no accessibility tree. It keeps its reasoning as text, emits one action per turn, and was trained in "instruct" (non-thinking) mode (paper §3.2).

## 2. Native I/O contract

### 2.1 System prompt
- Use Microsoft's prompt verbatim: identity, then critical points, then the `<tools>` JSON schema of the single function `computer_use`, then the `<tool_call>` format lines.
- `main.py` `FARA_SYSTEM` is that prompt character for character. It was built by the harness's `get_computer_use_system_prompt` with `display_size=1000`, so the schema says "The screen's resolution is 1000x1000" (measured: the JSON key order differs, the actions are identical).
- The identity line names "Qwen3.5-9B". That is what the official harness sends for every size (`FARA_QWEN35_IDENTITY` in [`_prompts.py`](https://github.com/microsoft/fara/blob/main/src/fara/agents/fara/_prompts.py)). The 4B card text says "Qwen3.5-4B". Either is "verbatim"; keep the harness text.
- The harness registers a `fara_next_windows` mode name, but the public repo raises `NotImplementedError` for it ("this repo ships the 'fara_next_browser' action space only"). **There is no public Windows prompt.** This corrects an earlier note in RETINA_512.

### 2.2 Request envelope (llama-server, OpenAI-compatible)
```
{"model": "retina", "temperature": 0, "max_tokens": 2048,
 "chat_template_kwargs": {"enable_thinking": false}, "messages": [...]}
```
- `temperature 0` matches the harness. `max_tokens 2048` matches the card.
- `enable_thinking: false` makes the template emit an empty `<think>\n\n</think>\n\n` block. Microsoft's harness sends no template kwargs, so with the shipped template its prompts end in an open `<think>\n`.
- The paper says training and inference were "instruct" mode. Trident's choice matches the paper. Whether the open tag matters for this model is untested (see roadmap R12).
- llama-server with default reasoning extraction puts the whole reply into `reasoning_content` and leaves `content` empty. The community fix is `--reasoning-format none` ([fara#82](https://github.com/microsoft/fara/issues/82), [HF discussion](https://huggingface.co/microsoft/Fara1.5-27B/discussions/4)). Trident's server runs `--reasoning off` and the client sends `enable_thinking:false`. Replies parsed 12/12 in the pilot and 48/48 in the native probe (measured).

### 2.3 Single-turn (Retina, `--retina "GOAL"`)
```
[0] system : FARA_SYSTEM
[1] user   : [image_url (capture fitted by fara_fit), text GOAL]
```
- Fara treats GOAL as a whole task. It answers with one thought and one action for the *first step* of that task, and keeps no memory between calls.

### 2.4 Multi-turn (pilot, `--pilot TASK N`), Microsoft's `Fara15Agent` loop
```
step 1 : [system] [user: image_1, TASK]
step k : [system] ... [assistant: raw reply k-1] [user: image_k, "Here is the next screenshot. Think about what to do next."]
```
- The follow-up sentence, verbatim: `Here is the next screenshot. Think about what to do next.` (`USER_MESSAGE` in [`fara15_agent.py`](https://github.com/microsoft/fara/blob/main/src/fara/agents/fara/fara15_agent.py)).
- In the browser, Microsoft prefixes it with `Current URL: <url trimmed to 100 chars>\n`. Trident has no URL and sends no prefix.
- Microsoft adds an observation line before the sentence only after `read_page_answer_question` and after a failed `visit_url`. After a user answer, the user text replaces the sentence.
- The assistant turn is Fara's raw reply, fed back verbatim.
- **Screenshot window:** only the 3 newest screenshots are sent. An older screenshot turn is dropped *whole*. The exception is the original task turn (and user-answer turns), which keep their text.
- All thoughts and actions are kept. So after pruning, several assistant turns sit back to back. Microsoft's harness does the same, and the paper trains on "the full conversation history" with screenshots only from the last three steps (Fig. 6). This shape is in-distribution.
- Microsoft also feeds no action result back. The "I clicked at …" text is logged only.

### 2.5 Reply format
```
<one short thought in plain English>
<tool_call>
{"name": "computer_use", "arguments": {"action": "<action>", ...}}
</tool_call>
```
Exactly one action per reply (official; measured in every reply of the pilot and the 48-case native probe).

### 2.6 Actions and arguments (Fara1.5 browser schema; `FARA_ACTIONS` in main.py)
| action | args | meaning / Trident mapping |
|---|---|---|
| `left_click`, `right_click`, `double_click`, `triple_click` | `coordinate [x,y]` | click at the point |
| `mouse_move` | `coordinate` | hover / move the cursor |
| `left_click_drag` | `coordinate` (end point only) | press **at the current cursor**, move straight to the end point, release (paper Table 2; Playwright `mouse.down(); mouse.move(x,y); mouse.up()` in the harness) |
| `type` | `text` | type at the current focus |
| `key` | `keys` list, e.g. `["Control","c"]` | key chord |
| `scroll`, `hscroll` | `pixels` (positive = up/left), optional `coordinate` | 0-1000 display units, converted to wheel notches |
| `visit_url`, `web_search`, `history_back` | `url`, `query`, - | browser actions; Trident opens the default browser or presses alt+left |
| `read_page_answer_question` | `question` | needs page text; on the desktop Trident answers with a "not a web page" note |
| `pause_and_memorize_fact` | `fact` | stored only |
| `ask_user_question` | `question` | stops the run (or Microsoft's canned reply with `TRIDENT_PILOT_AUTOREPLY=1`) |
| `wait` | `time` seconds | sleep (capped at 30 s) |
| `terminate` | `answer` | ends the task; the answer is the final text |

There is no box, region, list or path output.

### 2.7 Coordinates and screenshots
- **Coordinates are normalized 0-1000 on both axes** of the image Fara saw (`FARA_DISPLAY_SIZE = 1000` in [`coord_spaces.py`](https://github.com/microsoft/fara/blob/main/src/fara/agents/coord_spaces.py)). The harness maps them as `x * viewport_w / 1000`.
- Trident maps `px = (n*(W-1)+500)//1000` on the capture or region, after rounding and clamping to 0..1000.
- Measured: the 0-1000 hypothesis lands on the target in every identifiable case, and absolute pixels never do.
- Fara-7B was different: it used absolute pixels on a 1428×896 image. Do not mix up the two generations.
- **Resolution:** the harness runs a 1440×900 viewport, smart-resized to multiples of 32 (1440×896, about 1260 image tokens). The card says: "The screen resolution Fara is most commonly trained with is 1440×900."
- Trident sends a 1920×1080 capture fitted to 1440×800 (LANCZOS, 1125 tokens). The 16:9 frame is a small distribution shift. Text is 25% smaller than on screen.
- Screenshots carry **no mouse cursor**, both in the harness (Playwright screenshots; a red dot exists only in an optional animation and is removed afterwards) and in Trident.

## 3. How to phrase requests

Microsoft gives one rule for the system prompt ("Use it verbatim") and no published user-prompt style guide. The paper says intent phrasing trained better ("task: increase the quantity by one" → "I need to click on the plus icon" → click). Everything else below comes from our own measurements.

1. **Ask for one goal, and name the window and one target.** Examples: "In the Settings window, open Bluetooth & devices." "In Notepad, put the cursor at the end of the last line." "In Paint, select the red color." Retina returns the first action and its point.
2. **Intent goals and direct goals both work.** "Close Notepad." returned the close-button click. "Click the Brushes button." returned the Brushes click. Prefer the intent ("increase the font size") when the control has no clear label.
3. **Avoid words that match unrelated visible text.** Fara grounds nouns to on-screen words. "draw a cat" on a Paint ribbon became "click the cat shape tool". Name things by their role or location when the same word is visible elsewhere.
4. **Never ask for boxes, lists or inventories.** Boxes are not in the action space. A box request returns `mouse_move` → `left_click_drag` (a drag gesture, not an answer). "Name every window/control" makes it invent controls (rulers, menus, sliders) and burns tokens. To get a region, ask for two point goals (top-left and bottom-right of X) or use Jev.
5. **Ask questions as questions, and expect `terminate`.** "What does the Notepad window say last?" usually comes back as `terminate` with `answer`. Sometimes Fara first proposes a "look" click. Then either answer with a second turn (same image plus the follow-up sentence), which ended in `terminate` in our runs, or re-ask more narrowly. Keep questions to one fact.
6. **Do not wrap requests in extra instructions.** The `FARA_LOOK` prefix ("Name every window, dialog and overlay you see, then answer…") made answers long, padded and partly invented, and caused the two `length` failures. A trimmed system prompt (P3) turned box questions into `mouse_move`. Keep the verbatim system prompt and send the bare goal.
7. **For multi-step work, give the whole task once.** In the pilot, Fara plans its own steps (Start → search → Paint in 3 accurate clicks). Do not restate the task each turn: the follow-up sentence is what it was trained on.
8. **Critical points apply.** Fara stops with `ask_user_question` before purchases, sending messages, sign-ins, entering personal data and other irreversible steps unless the task says it is authorized (card, "Critical Points").

## 4. Limits (what breaks)

- **Desktop planning is out of distribution.** Single-step grounding on Windows works (measured: Start, search box, Recycle Bin, Close and Brushes all hit). Multi-step desktop strategy is untrained, and drawing or other drag-heavy work is almost absent from training (0.8% drag).
- **Drag is a straight line from wherever the cursor is.** Curves need many segments. The start point is invisible, because screenshots have no cursor. The drag starts wherever the previous action left the cursor, which may be on a toolbar (pilot step 9).
- **Fara does not see what its last action did, other than in the next screenshot.** If the screen does not change, it repeats itself. The native probe re-sent the same image with the follow-up sentence and got the identical click 3 times in 7 multi-turn cases. The pilot looped `mouse_move [173,372]` → drag 4 times.
- **Small targets miss.** Fine-grained grounding error is the largest tool error class for the 4B model: 24.8% of error segments on WebVoyager and 45.4% on Online-Mind2Web (paper Tables 12-13). ScreenSpot-Pro is 55% without zoom. Measured: a ribbon miss in the pilot (step 8).
- **It hallucinates state and success.** The card says it "can hallucinate page state". The paper counts hallucination errors in 46.8% of 4B error segments (output contradiction 34.5%, fabrication 18.4%). Measured: "The Shapes tool is now selected" on an unchanged ribbon, and invented Paint menus.
- **It accumulates errors and varies between runs** (card: "a misclick early in a sequence can compound"; "run-to-run variance … is non-trivial").
- **Quantization:** Microsoft publishes bf16 results only. Community GGUF users report correct grounding at Q4_K_M on simple tasks ([runanywhere GGUF card](https://huggingface.co/runanywhere/Fara1.5-4B-GGUF)). Others report Fara-7B quants getting "tangled into infinite loops unless it's a trivial task" ([fara#46](https://github.com/microsoft/fara/issues/46)). There is no published Q4-vs-bf16 benchmark.
- **llama.cpp specifics:**
  - `--cache-reuse` is disabled whenever an mmproj is loaded ("cache_reuse is not supported by multimodal", `server-context.cpp` at b11756 L1321-1323). Qwen3.5's hybrid recurrent layers also cannot shift.
  - CUDA flash attention is reported to distort Qwen3.5 image embeddings (open [llama.cpp#29629](https://github.com/ggml-org/llama.cpp/issues/29629)). Trident runs `-fa on`.
  - Qwen-VL grounding wants at least 1024 image tokens ([llama.cpp#16842](https://github.com/ggml-org/llama.cpp/issues/16842)).
  - `-n` is only a default; the request's `max_tokens` wins.
- **Speed:** pruning a screenshot changes the prompt prefix, so only the system prompt stays cached. Pilot steps went from about 8 s to about 16-17 s from step 4 on (measured).

## 5. Validation of our findings against outside sources

| # | Our finding | Verdict | Source |
|---|---|---|---|
| F1 | main.py system prompt = Microsoft's verbatim prompt; use it unmodified | CONFIRMED official | [card "Use it verbatim"](https://huggingface.co/microsoft/Fara1.5-4B); `_prompts.py` |
| F2 | One thought + one `<tool_call>` `computer_use` action per turn | CONFIRMED official | card "Outputs"; paper §3.1 "a single atomic action" |
| F3 | Coordinates are 0-1000 normalized | CONFIRMED official + community | `coord_spaces.py` `FARA_DISPLAY_SIZE = 1000`; harness `_proc`; [runanywhere card](https://huggingface.co/runanywhere/Fara1.5-4B-GGUF) |
| F4 | Trained at 1440×900; Trident sends 1440×800 from 16:9 | CONFIRMED official (resolution); the 16:9 shift is our own | card; `Fara15AgentConfig viewport 1440x900` |
| F5 | Boxes are not native; box requests become drag gestures | CONFIRMED official (no box in the action space); the drag reflex is NOT DOCUMENTED | schema in `_prompts.py` |
| F6 | Drag is 2 turns: `mouse_move`, then `left_click_drag` from the current cursor, straight | CONFIRMED official | paper Table 2 "Press the left mouse button at the current cursor position and drag to (x, y)"; `environment.py left_click_drag` |
| F7 | Grounds nouns to visible on-screen text ("cat" → cat shape tool) | NOT DOCUMENTED (consistent with grounding data filtered for unique, text-anchored queries, paper §2) | own: N04 |
| F8 | Invents inventories / controls | CONFIRMED official (general hallucination) | card "can hallucinate page state"; paper Table 12 |
| F9 | Questions are answered via `terminate`, sometimes after a "look" click | CONFIRMED official that `terminate` carries the final answer; the look-click is NOT DOCUMENTED | schema "Terminate the current task and provide the final answer" |
| F10 | `FARA_LOOK` prefix and the trimmed prompt are harmful | NOT DOCUMENTED (consistent with "use verbatim" and intent phrasing, paper §3.2) | own: RETINA_512 |
| F11 | The token cap was a symptom; `-n` is a default, `max_tokens` wins | CONFIRMED (llama.cpp source) | server-schema.cpp / server-context.cpp b11756 |
| F12 | Official loop: 3 newest screenshots, full text history, fixed follow-up sentence | CONFIRMED official | `fara15_agent.py` `max_n_images=3`, `USER_MESSAGE`; paper Fig. 6; blog "Agent Loop" |
| F13 | No cursor in screenshots | CONFIRMED official (the harness has none either) | Playwright screenshots; `add_cursor_box` only with `animate_actions` |
| F14 | No action-result feedback to the model | CONFIRMED official (Microsoft does the same; observations only for `read_page_answer_question` / failed `visit_url`) | `_pending_observation` in `fara15_agent.py` |
| F15 | Loops on unchanged screens | CONFIRMED community | [fara#46](https://github.com/microsoft/fara/issues/46) "infinite loops"; [pmbstyle/fara-agent](https://github.com/pmbstyle/fara-agent) "Loop Guard … warns the model when it oscillates"; [trycua/cua#684](https://github.com/trycua/cua/pull/684) "Infinite loops"; [r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/comments/1p83a4j/linux_alternative_to_microsoft_fara7b_for_agentic/) "Kept scrolling and scrolling" |
| F16 | Small-icon misses | CONFIRMED official | paper Tables 6, 12-14 (fine-grained grounding error is the top tool error) |
| F17 | Stale back-to-back assistant turns after pruning | CONFIRMED official that this is the shape; CONTRADICTED as a *defect*: it is what Microsoft's harness sends and what training shows | `maybe_remove_old_screenshots`; paper Fig. 6 |
| F18 | Slow steps from prompt-cache breakage | CONFIRMED (mechanism); see R7 for the fix | own trace; llama.cpp source |
| F19 | Desktop (non-browser) use | CONFIRMED official that it is unsupported / future work; community: "only works in the browser w/ Playwright" | blog "Looking forward"; [HN](https://news.ycombinator.com/item?id=46061208); [fara#85](https://github.com/microsoft/fara/issues/85) unanswered |
| F20 | Fara is a good launcher/navigator and grounder; a poor painter | CONFIRMED official in spirit (grounding benchmarks on desktop; 0.8% drag data) | paper Table 6, Fig. 4 |
| F21 | Fara as grounding model for another agent | CONFIRMED official | card "can also serve as a grounding model for other agents" |
| F22 | llama.cpp output parsing | CONFIRMED community (`--reasoning-format none`); Trident is unaffected with `--reasoning off` | [fara#82](https://github.com/microsoft/fara/issues/82) |

Nothing we found was flatly contradicted by Microsoft. What *was* contradicted are two of our **proposals** (R6 and R7 below).

## 6. Roadmap (all: proposed, not implemented)

Ordered by expected value. "Deviation" means it goes beyond what Microsoft trained on, so it needs an A/B test.

**R1. Retina = grounding only; the planner decides.**
- Usage: one named goal per call, verbatim prompt, bare goal text, no `FARA_LOOK`, no boxes or lists, questions read from `terminate`.
- Evidence: F1-F10, F21, paper intent phrasing.
- Status: README already says this; keep it.

**R2. Fara as hands under the cursor-agent planner, not the sole brain, for desktop work.**
- The planner (Grok via Cursor CLI) breaks the job into single-screen goals. Retina or a short pilot (≤ 5 steps) executes each one. The planner verifies with a fresh capture.
- Evidence: Microsoft's data pipeline itself used an orchestrator + WebSurfer + verifiers (Fara-7B blog; FaraGen1.5 solver + verifiers). The card endorses the grounding role. Desktop planning is untrained (F19). Our pilot opened Paint in 3 steps and then failed at the creative task.
- Keep `--pilot` for short, browser-like navigation tasks.

**R3. No-change and loop detection with `CHANGE_APPROACH`.**
- Diff consecutive captures. If the screen is near-identical, or the same (action, coordinate) repeats 2 or more times, prepend main.py's `CHANGE_APPROACH` sentence to the follow-up, or stop with status `loop` and hand control to the planner.
- Evidence: F15 (community loop guards); native probe (identical click ×3 on an unchanged screen); pilot S04-S12.
- Deviation: mild, because the text sits *before* the trained sentence, the way Microsoft's observation prefix does.

**R4. Action-result feedback.**
- Put the executed result (e.g. `I dragged from (173,371) to (783,372).`) as an observation line before the follow-up sentence. This uses the same slot Microsoft uses for `read_page_answer_question`.
- Evidence: pilot S09 (the drag started on the ribbon unknowingly); F14.
- Deviation: yes. Microsoft does not do this for pointer actions.

**R5. Cursor overlay on captures.**
- Draw a small cursor marker at `GetCursorPos` before `fara_fit`, so the drag start is visible.
- Evidence: pilot S09. Fara-7B's tool text speaks of "the tip of the cursor" (Qwen boilerplate).
- Deviation: yes. Training screenshots had no cursor. A/B it, and pair it with R4.

**R6. Path drags.**
- Fara cannot emit a path. Options:
  - (a) the planner asks for points and Trident's hand draws `Hand.drag(points)`;
  - (b) the pilot treats `mouse_move` + consecutive `left_click_drag`s as one polyline;
  - (c) an off-schema `path` argument.
- Evidence: F6; the harness environment has a `cua_drag(path)` method, but the Fara agent never calls it.
- Verdict: (c) is CONTRADICTED by the action space (Fara will not produce it). Prefer (a).

**R7. Prompt-cache speed.**
- `--cache-reuse` is CONTRADICTED: llama.cpp disables it with an mmproj (b11756 `server-context.cpp` L1321) and for Qwen3.5's recurrent layers.
- Use instead:
  - prune screenshots in blocks (e.g. keep up to 4, drop 2 at once, so every other step reuses the prefix);
  - and/or lower `TRIDENT_FARA_WIDTH` (1280 ≈ 880 tokens);
  - and/or keep pilot runs short (R2).
- Measure before choosing.

**R8. History pruning that keeps turn order** (insert a text-only user placeholder for dropped screenshot turns).
- Downgraded: Microsoft's harness and training produce the back-to-back assistant shape (F17). Changing it is a deviation with no evidence of benefit. Test only together with R3/R4.

**R9. Zoom for small targets.**
- For icons or ribbon items, run a coarse goal on the full capture, crop a region around the point, and re-ask on the crop (`--region`). Map the point back.
- Evidence: paper §4.2: "using a two-step zoom approach shows noticeable improvements" (ScreenSpot-Pro 55.3 → 59.9 for 4B).

**R10. Server A/B checks** (on the user's GPU PC, when the user runs them):
- `-fa off` for vision (open [llama.cpp#29629](https://github.com/ggml-org/llama.cpp/issues/29629); the GTX 1060 is untested there);
- mmproj f16 instead of Q8_0 (community builds ship f16);
- Q5_K_M/Q6_K instead of Q4_K_M if VRAM allows;
- `--image-min-tokens 1024` (see RETINA_512 §5).

**R11. A window-title prefix** as the desktop analogue of `Current URL:`.
- Example: `Current window: Untitled - Paint\n` before the follow-up sentence.
- Evidence: the paper says the URL "helps disambiguate visually similar pages". Deviation: yes.

**R12. Thinking-tag A/B.**
- Compare `enable_thinking:false` (empty think block, today) against the template default (open `<think>`, what Microsoft's harness produces with vLLM).
- Low priority: today's format parses 100% and matches the paper's "instruct mode".

**R13. Journal helper console fix.**
- Spawn the `--journal` helper with the base interpreter (`sys._base_executable`), or with `CREATE_NO_WINDOW` and without `DETACHED_PROCESS`. Guard the `_port_open` race with a lock file, so blank consoles stop appearing on the screen Fara looks at.
- Evidence: pilot S01-S03 (no effect on that run, but they pollute captures).

## 7. Sources
- Model cards: [Fara1.5-4B](https://huggingface.co/microsoft/Fara1.5-4B), [Fara1.5-27B](https://huggingface.co/microsoft/Fara1.5-27B), [Fara-7B](https://huggingface.co/microsoft/Fara-7B)
- Code: [microsoft/fara](https://github.com/microsoft/fara) (`src/fara/agents/fara/fara15_agent.py`, `_prompts.py`, `agents/coord_spaces.py`, `environments/playwright/environment.py`), commit a675d6d
- Paper: [Fara-1.5: Scalable Learning Environments for Computer Use Agents, arXiv 2606.20785](https://arxiv.org/abs/2606.20785)
- Blogs: [Fara1.5](https://www.microsoft.com/en-us/research/articles/fara1-5-computer-use-agent/), [Fara-7B](https://www.microsoft.com/en-us/research/blog/fara-7b-an-efficient-agentic-model-for-computer-use/)
- Issues: [fara#82](https://github.com/microsoft/fara/issues/82), [#46](https://github.com/microsoft/fara/issues/46), [#58](https://github.com/microsoft/fara/issues/58), [#85](https://github.com/microsoft/fara/issues/85); [llama.cpp#29629](https://github.com/ggml-org/llama.cpp/issues/29629), [#16842](https://github.com/ggml-org/llama.cpp/issues/16842)
- Community: [pmbstyle/fara-agent](https://github.com/pmbstyle/fara-agent), [trycua/cua#684](https://github.com/trycua/cua/pull/684), [runanywhere GGUF](https://huggingface.co/runanywhere/Fara1.5-4B-GGUF), [bartowski GGUF](https://huggingface.co/bartowski/Fara1.5-4B-GGUF), [r/LocalLLaMA Fara-7B](https://www.reddit.com/r/LocalLLaMA/comments/1p5op7z/from_microsoft_fara7b_an_efficient_agentic_model/), [HN Fara-7B](https://news.ycombinator.com/item?id=46061208)
- Our evidence: `RETINA_512.md`, `native/SUMMARY.md` + `native_results.json` (48 cases), `pilot_forensics/FORENSICS.md` (run_20261023_183353)
