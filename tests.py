"""Sequential Trident task runner.

Run it with no arguments:
    py -3.11 -B tests.py

It steps through a hardcoded list of task entries (TASKS). For each one it
launches the REAL Trident system (run.py) with that task as the owner goal, lets
Luna carry it out on the desktop, then requests a clean stop and moves on to the
next task. No mocks and no fault injection: each entry is a real task the system
actually performs. Edit TASKS to change what gets exercised.

Each entry is (task, budget_seconds, hold):
  - budget_seconds: soft time limit for that task's run.
  - hold=False: short, single-answer tasks. The run stops SETTLE seconds after
    Luna's first completed answer.
  - hold=True: long, multi-phase tasks (chess, drawing). The run is left alive
    for the whole budget so Luna keeps iterating, observing and refining, then
    is stopped cleanly.

The original scenario + fault-injection suite is preserved, commented out, below.
"""
import subprocess, sys, time
from pathlib import Path

sys.dont_write_bytecode = True
from core import Session

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / "runtime" / "python" / "Scripts" / "python.exe"

SETTLE = 6  # grace after the first completed answer before stopping a hold=False run

# Task strings are owner messages to Luna, written in her operating tone: real
# operation, predict/act/compare, keep narrative so a later invocation continues.
# Chess runs before the drawing; the drawing is last. (task, budget_seconds, hold)
TASKS = [
    ("Play and win a full game of chess against the computer on chess.com using Chrome. "
     "Bring up Chrome, go to chess.com, and start a new game against the chess.com bot "
     "(Play then Computer): choose a bot and begin, then make the first move so the game "
     "actually starts. Play the whole game to a result: read the board with local vision "
     "before every move, predict each move's effect and verify it with a fresh look, and "
     "consult decide whenever the position or a plan is unclear, weighing several candidate "
     "moves by meaning. The bot replies quickly, so after each of your moves take a fresh "
     "look to see its reply before moving again. Keep a running narrative of the position, "
     "your plan and the bot's threats so a later you can continue the same game. Play "
     "soundly, aim to win, and tell me the final result.",
     900, True),
    ("Look at the screen and tell me which application windows are open.",
     180, False),
    ("Open Notepad and type a four line poem about rain, then tell me what you did.",
     240, False),
    ("Open the Windows calculator, then check with a fresh look and tell me whether it is open.",
     240, False),
    ("Use your decision engine to choose between a haiku or a limerick about autumn, then tell me which and why.",
     240, False),
    ("Send me a short voice note saying that the test is running.",
     180, False),
    ("Draw an advanced, detailed picture of a house with a cat in Microsoft Paint. Open Paint, then build "
     "the drawing in phases, each more detailed than the last: rough shapes first, then clean outlines, then "
     "features (door, windows, roof, the cat's face and body), then shading and color, using different "
     "brushes, line widths and colors. After each phase, look with local vision, compare what you see to "
     "what you intended, and refine before moving on. Keep a narrative of which phase you are in and what "
     "remains so a later you can continue. Work for up to about ten minutes; stop once the house and the cat "
     "are clearly recognizable and reasonably detailed, and tell me what you drew.",
     600, True),
]


def run_task(index, total, task, budget, hold):
    print(f"\n===== [{index}/{total}] (budget {budget}s, hold={hold}) {task[:70]}... =====", flush=True)
    before = set(ROOT.glob("run_*"))
    proc = subprocess.Popen([str(PYTHON), "-u", "-B", str(ROOT / "run.py"), task],
                            cwd=ROOT, stdin=subprocess.DEVNULL)
    session = None
    deadline = time.time() + 60
    while session is None and time.time() < deadline:
        fresh = [d for d in set(ROOT.glob("run_*")) - before if list(d.glob("*_state.json"))]
        if fresh:
            session = Session(fresh[0])
        else:
            time.sleep(0.3)
    if session is None:
        proc.terminate()
        print(f"[{index}] FAILED to start a run.", flush=True)
        return
    started, done = time.time(), None
    while time.time() - started < budget and proc.poll() is None:
        if not hold:
            answered = any(e["sender"] == "luna" and e["at"] >= started and e["outcome"] in ("completed", "failed", "unknown")
                           for e in session.load()["executions"].values())
            if answered:
                done = done or time.time()
                if time.time() - done >= SETTLE:
                    break
        time.sleep(2)
    session.request_stop()
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.terminate()
    final = session.load()
    minds = [e for e in final["executions"].values() if e["sender"] == "luna"]
    print(f"[{index}] run={session.directory.name} process_state={final['process_state']} "
          f"mind_invocations={len(minds)} elapsed={round(time.time()-started)}s", flush=True)


if __name__ == "__main__":
    for i, (task, budget, hold) in enumerate(TASKS, 1):
        run_task(i, len(TASKS), task, budget, hold)
    print("\nAll tasks finished.", flush=True)
