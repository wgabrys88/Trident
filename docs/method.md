# How to analyse awakenings

Written 14 Nov 2026. This is the method the owner set for one analysis of six awakenings, plus the traps met while applying it. "Awakening", "life" and "run" mean one launch of the body.

## Ground rules

- Read only. Do not launch or import the body; its top level touches the desktop.
- Do not open `..\.env`, `store.db` or `agent\`. No git writes. No personal content in output.
- Scratch scripts are Python files in a temp folder, reading evidence and writing only there.
- Settle mechanics (what reaches the model, what is stored, what Python writes) from the body code and the journal structure, not from what the organism says.

## Evidence

- Label every claim: **OBSERVED** (verbatim quote with file:line, or an image you opened), **INFERRED** (with confidence), **SINGLE-SOURCE**, or **NOT ESTABLISHED**.
- Two logs of one event are one observation. The launch console echoes the journal, so it is not a second witness.
- Accepted is not happened. Exit codes, returned paths and confident summaries prove nothing; open the PNG.
- Missing evidence is not evidence of absence. Never claim certainty.
- A mistake is not a verdict. Trace it as a chain: where detected, how repaired, kept by later lives or not.
- Look for one disease behind many symptoms, keep competing hypotheses, and try to refute the leading one. Name symptoms it does not explain separately.

## Verdicts for body changes

Each needs a code line and a journal quote: **FIXED-PROVEN**, **NOT-EXERCISED**, **EFFECT-ABSENT**, **REGRESSED**. An edit is not activated behaviour, and activated behaviour is not a benefit.

## Lenses

Answer each on its own: code (what fired, what is dead), runtime (minute-scale timeline against pixels and files), behaviour (what it noticed, asked, did; no feelings or personality), memory (found, wrote, used by the next life), cost (tokens, turns, images, seconds, Jev calls; dollars only with billing rows).

## Flow shapes (vocabulary, not laws)

T1 birth, T2 heartbeat, T3 deadline cut, T4 judging cut, T5 closure, T6 reproduction, T7 self-evolution, T8 inner eye (capture, look and act in one turn), T9 criterion conflict. Worker fan-out is not reproduction; editing a report or a helper is not self-evolution. (In this session two helper additions to the body were counted as T7 because the body file changed. A stricter reading would count neither.)

## The ledger

Keep one markdown file and append after every few reads: each read with its exact call and whether it came back whole, open hypotheses with confidence, every file:line you intend to cite, and what you did not read. It survives context loss and becomes the "what I read" list.

## Reading journals

`main.py` journals (`run_<id>.md`):
- A record starts `## <seq> <role> <channel> <session> request=unknown context=unknown`. Body lines start with `|`. A line `.. <seq>` continues an earlier record.
- Channels seen: goal, sessions, prompt, thought, text, tool, permission, prompt-result, final.
- The prompt record holds the full text sent and the screen image path. It does not say whether `memory.png` was attached.
- The prompt-result holds only a stop reason and seconds. No token counts anywhere.

`main2.py` journals (`life_<id>.md`): `## HH:MM:SS <who> <what>`, with what = life, wakes, thought, reply, tool, turn, rest.

Working order that held up:
1. Script an index of every record: line span, type, tool title, status, time, exit code, size. Read the index itself.
2. Script an extract of every thought and text line, keeping journal line numbers. Read it whole, life by life.
3. Classify the organism's scripts by which helpers they call; read in full the ones that carry a finding.
4. Before writing, re-open every cited line in the journal itself and fix wrong numbers.

## Reading images

- Open them as images. Pixel statistics may support a look, never replace it.
- A chat may reject more than about 20 images. Join many into contact sheets under 2000 px per side.
- One life's screen is not evidence for another life's.

## Traps met

- **Ignored files are invisible to Cursor search.** `.gitignore` is `*`, so Glob and Grep skip everything but `main.py`. List and search with your own scripts. The organism's own searches found ignored files in the root but not inside `evidence\`; why is NOT ESTABLISHED.
- **Truncated reads.** "N lines not shown" and cut long lines are not reads. Re-read exactly those lines.
- **Tool-opened images.** All six lives said images opened with Cursor's Read came back as descriptions without positions. The mechanism is NOT ESTABLISHED (a third-party page says the tool returns pixels). Do not side with either voice: twice the organism called a real shape a "hallucination" after a pixel scan, and the image showed the description was right.
- **Coordinate frames.** `main.py` helpers mix client pixels (`stroke`) and screen pixels (`click`); the organism added window pixels. A "wrong" remembered coordinate was often a right one in another frame.
- **Memory misreads.** Small text in `memory.png` came back with wrong digits (168 read as 188, 795 as 706, by two lives).
- **State-dependent facts.** "Second menu row" was true only while a menu was scrolled; it was stored as if fixed.
- **Clocks.** Journal times are UTC; the screen clock is local (UTC+1). One life misread its own deadline.
- **Which body ran.** Console traceback line numbers shifted by 3 between two lives, showing a self-edit was live.
- **Handed-over observations can be short.** "Decided to clear the canvas" turned out to be "cleared it" once the script and exit record were read.
- **DPI.** A scratch script that is not DPI aware saw 1536x864 on a 1920x1080 screen.
- **Journal weight.** One edit of the body echoes the whole file (about 109 KB each time).
- **Tooling.** Plan mode refuses to write `.py` files; auto-review may block scratch scripts in temp until approved.
