from __future__ import annotations

import re
import sys
from pathlib import Path

SENTENCES = re.compile(r"(?<=[.?!])\s+")


def split_sentences(text: str) -> list[str]:
    text = " ".join(text.split())
    return [part.strip() for part in SENTENCES.split(text) if part.strip()]


def chunk_sentences(sentences: list[str], per_file: int = 2) -> list[str]:
    chunks = []
    for i in range(0, len(sentences), per_file):
        chunks.append(" ".join(sentences[i : i + per_file]))
    return chunks


def write_chunks(text: str, outdir: Path) -> list[Path]:
    sentences = split_sentences(text)
    if not sentences:
        raise SystemExit("source text has no sentences")
    chunks = chunk_sentences(sentences)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.txt"):
        old.unlink()
    paths = []
    for i, chunk in enumerate(chunks, start=1):
        path = outdir / f"{i}.txt"
        path.write_text(chunk + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: python tts_split.py SOURCE.txt OUTDIR")
    source = Path(argv[1]).expanduser().resolve()
    outdir = Path(argv[2]).expanduser().resolve()
    paths = write_chunks(source.read_text(encoding="utf-8"), outdir)
    for path in paths:
        print(f"{path.name}\t{path.read_text(encoding='utf-8').strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
