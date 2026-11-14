# What is open, and what to look at next

Written 14 Nov 2026, after six awakenings under `main.py` were analysed and a new body, `main2.py`, was written but not yet run. Each question has the cheapest observation that settles it.

## Open questions

| Question | Cheapest observation |
|---|---|
| Does Cursor's Read give this model pixels or words? | In the next journal, compare what it says about a tool-opened crop and an attached region of the same area. |
| At what size do attached images arrive? | A region view containing small text, read back by the mind. |
| Why was Jev never called in six lives? | Whether the next life's scripts call `decide(` or `locate(`, and what it says if not. |
| Does the handshake work in a body that was never run? | The first launch. A failure names its line. |
| Does the mind's shell inherit `TRIDENT_WAKES`? | The first `wake()` call from a script: it works or raises a KeyError naming the variable. |
| Do the mouse and keyboard helpers act as written? | The first input pass, checked against a capture taken after it. |
| Why were the last two pictures flat ribbons while earlier ones were smeared oil? | Open the Brushes menu in Paint and look at the selected brush. |
| Tokens and cost per life? | Cursor's usage rows for 11 Nov 2026, 17:19 to 18:13 Warsaw time. |
| Why is `evidence\` invisible to Cursor search while root files are not? | One search for `evidence/**/*.py` in a Cursor session. |
| Is `locate()` accurate enough to click small controls? | Its `x, y` for a known control against a look at the same region. |

## The first main2 journal: what to look at

The journal is `life_<id>.md` in the workspace; the life's folder is `evidence\<id>\`.

**Does it live at all**
- A `life` record, then a `wakes` record for the mind `trident` with a screen view and, if present, `memory.png`.
- No traceback in the console. A final `rest` record with `error: null`.

**Does it ask for turns (F2)**
- More than one `wakes` record. Under `main.py` there was exactly one per life.
- Lines in `evidence\<id>\wakes.jsonl` written by the organism, some with region views.
- Any view with `failed` in its state, and what the mind did about it.

**Does it see (F1)**
- `decide(` or `locate(` in its scripts (0 of 97 before).
- Fewer scripts reading pixels with PIL (58 of 97 before).
- Time from waking to the first action that changed something on screen (2 to 6 minutes before).
- What it says about pictures: "description" and "hallucinated" were its words before.

**Does it take the sentence as an order (F3)**
- Its first thought. Four of six lives began "The user wants me to act as Trident".
- With finished work on screen and the same sentence, whether it wipes it, and its stated reason.
- With no sentence at all, whether it looks around and chooses something or rests.

**Does memory work (F4, F5)**
- `memory.png` changed before the last minute of the life.
- Copies named `memory_<time>.png` in the life's folder, one per version shown.
- Whether memory holds anything other than coordinates for one application.
- Whether the next life reads memory correctly and acts on it without re-measuring.
- Old memory still names `span`, `row_runs` and client coordinates, which `main2.py` does not have. Watch how the organism handles that.

**Does it grow (F6, F7)**
- Whether it edits its body, and whether a helper it adds shows up in the next life's wake text.
- Whether it uses more than one mind, a subagent or a copy. None was used before.

## What would overturn the analysis

- A life that reads small exact details from a tool-opened PNG: F1's mechanism is wrong.
- Lives that never ask for a second turn and do just as well: F2 is not the disease.
- A life that wipes finished work under the same sentence: the sentence, not the body, causes F3.
- Lives with one coordinate frame and readable memory that still re-measure everything: F4 is not explained.
- Lives that still end on a provider error with memory unwritten: F5 stands and telling the organism was not enough.

## How to do the next analysis

See `method.md`. In short: read only, label every claim, open the images yourself, keep a ledger, re-open every line you cite, and give each part of `main2.py` a verdict with a code line and a journal quote. An edit is not activated behaviour, and activated behaviour is not a benefit.
