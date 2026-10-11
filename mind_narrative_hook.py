"""Example hook: how mind.py can consult Amory narrative before calling the model."""
from narrative import NarrativeMemory

def prepare_context(root, query):
    mem = NarrativeMemory(root)
    mem.consolidate_idle()
    relevant = mem.relevant(query)
    return "Relevant narrative:\n" + "\n".join(
        f"- {t['headline']} (characters: {', '.join(t['characters'])})\n  recent: {'; '.join(t['recent'])}"
        for t in relevant
    )
