"""Task-agnostic map of this body. Importing it does not touch the desktop."""

MODULES = (
    ('main.py', 'Runs one tool flag. Skills in skills/*.py sit beside the built-in tools.'),
    ('mind.py', 'Builds the wake prompt, spawns each chat, and rotates at the call limit.'),
    ('memory.py', 'Story, ledger, learn, recall, and stop. learn reuses measured counts; recall is the on-demand journal read.'),
    ('evidence.py', 'Measures each unchanged journal once. The prompt gets counts; context() keeps the id lists.'),
    ('base.py', 'Settings, clock, command line, and the journal.'),
    ('eye.py', 'Asks Fara about one capture. Observations terminate. Actions return a fara id.'),
    ('decide.py', 'Reads a narrative and returns probabilities. A box question is the only one that sends a capture.'),
    ('grounding.py', 'Stores one Fara action and checks a fresh patch before input.'),
    ('desk.py', 'Windows, capture, and input. The hand runs one fara program.'),
    ('measure.py', 'Counts pixels inside bounds the eye already named.'),
    ('state.py', 'Locks and atomic writes.'),
    ('job_boot.py', 'Joins the wake job before vendor code starts.'),
    ('check.py', 'Runs the unit tests. It does not start a wake or send input.'),
)


def map(app, *_args):
    """$CALL --map
Prints each body module and the tool flags. Use it instead of rereading the source at the start of a wake. The sentence chooses the task; this list does not."""
    import main
    return dict(work=str(app.work), modules=[dict(file=name, job=job) for name, job in MODULES],
                tools=[tool.flag for tool in main.BUILTIN] + ['map'])
