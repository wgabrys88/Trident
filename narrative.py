"""Amory-style narrative memory for long-horizon Trident tasks.

Story threads with headlines, characters, hierarchical events.
Consolidation on idle. Semantic facts as simple triples.
Mind reads relevant headlines instead of full raw log.
"""

import json
import time
from pathlib import Path

class NarrativeMemory:
    def __init__(self, root):
        self.root = Path(root)
        self.store = self.root / 'narrative.json'
        self.data = {'threads': [], 'facts': [], 'last_active': time.time()}
        if self.store.exists():
            self.data = json.loads(self.store.read_text(encoding='utf-8'))

    def save(self):
        self.store.write_text(json.dumps(self.data, indent=2), encoding='utf-8')

    def add_event(self, text, characters=None):
        """Bind a new happening into the best matching thread or start a new one."""
        characters = characters or []
        # Simple coherence: prefer most recent thread for now; real version uses LLM
        if not self.data['threads']:
            self.data['threads'].append({
                'headline': text[:80],
                'characters': characters,
                'events': [text],
                'updated': time.time()
            })
        else:
            self.data['threads'][-1]['events'].append(text)
            self.data['threads'][-1]['updated'] = time.time()
            self.data['threads'][-1]['characters'] = list(set(self.data['threads'][-1]['characters'] + characters))
        self.data['last_active'] = time.time()
        self.save()

    def consolidate_idle(self):
        """On quiet periods, rewrite headlines and form subplots (placeholder for LLM pass)."""
        now = time.time()
        for thread in self.data['threads']:
            if now - thread['updated'] > 300 and len(thread['events']) > 5:  # 5 min idle
                # In full version: call small LLM to summarize into subplot and update headline
                thread['headline'] = 'Consolidated: ' + thread['headline']
                thread['updated'] = now
        self.save()

    def relevant(self, query, k=2):
        """Coherence-driven retrieval: return top headlines and recent events."""
        # Placeholder: most recent threads. Full version reasons over headlines with LLM.
        threads = sorted(self.data['threads'], key=lambda t: t['updated'], reverse=True)[:k]
        return [{'headline': t['headline'], 'characters': t['characters'], 'recent': t['events'][-3:]} for t in threads]

    def add_fact(self, subject, predicate, object_):
        self.data['facts'].append({'s': subject, 'p': predicate, 'o': object_})
        self.save()
