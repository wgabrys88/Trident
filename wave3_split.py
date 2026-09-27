"""B1 language-switch splitter. B0 is assistant.chunks_for_mouth.

mouth.py is not on this path. It plays TEXT arguments in order and does not split.
"""

import argparse
import json
import sys
from pathlib import Path

from assistant import CONJUNCTIONS, _bare, chunks_for_mouth, configure_stdio_utf8

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "artifacts" / "reference" / "wave3_fixtures.json"
LEXICON = ROOT / "artifacts" / "reference" / "wave3_lexicon.json"
REFERENCE = ROOT / "artifacts" / "reference" / "tts_chunk_mixed_pl_en.txt"
SIDECAR = ROOT / "wave3_chunks.json"
EN_CAP = 65
PL_CAP = 55


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_lexicon(path=None):
    raw = load_json(path or LEXICON)
    diacritics = set(raw["pl_diacritics"])
    return {
        "diacritics": diacritics,
        "pl": {word.casefold() for word in raw["pl"]},
        "en": {word.casefold() for word in raw["en"]},
    }


def _folded(word):
    return _bare(word).casefold()


def _has_other_letter(folded, diacritics):
    return any(ord(ch) > 127 and ch not in diacritics for ch in folded)


def classify_words(text, document_lang, lexicon):
    words = text.split()
    raw = []
    other = False
    for word in words:
        folded = _folded(word)
        if not folded:
            raw.append("")
            continue
        if _has_other_letter(folded, lexicon["diacritics"]):
            other = True
        if any(ch in lexicon["diacritics"] for ch in folded):
            raw.append("pl")
        elif folded in lexicon["pl"]:
            raw.append("pl")
        elif folded in lexicon["en"]:
            raw.append("en")
        else:
            raw.append("")
    marks = list(raw)
    for index, mark in enumerate(marks):
        if mark or index == 0 or index + 1 >= len(marks):
            continue
        left = raw[index - 1]
        right = raw[index + 1]
        if left and right and left == right:
            marks[index] = left
    langs = [mark if mark else document_lang for mark in marks]
    return words, langs, other


def language_spans(words, langs):
    if not words:
        return []
    spans = []
    start = 0
    for index in range(1, len(words) + 1):
        if index == len(words) or langs[index] != langs[start]:
            spans.append((langs[start], words[start:index]))
            start = index
    return spans


def _cap(lang):
    return PL_CAP if lang == "pl" else EN_CAP


def _piece_kind(piece_index, pieces, lang, prev_lang, document_lang):
    if piece_index < len(pieces) - 1:
        nxt = pieces[piece_index + 1].split()
        cut = _folded(nxt[0]) if nxt else ""
        conj = CONJUNCTIONS["pl" if lang == "pl" else "en"]
        return "breath" if cut in conj else "cap"
    if (
        len(pieces) == 1
        and lang != document_lang
        and prev_lang is not None
        and lang != prev_lang
    ):
        return "switch"
    return "whole"


def split_b1(text, document_lang, lexicon=None):
    lexicon = lexicon or load_lexicon()
    words, langs, other = classify_words(text, document_lang, lexicon)
    chunks = []
    prev = None
    for lang, span_words in language_spans(words, langs):
        pieces = chunks_for_mouth(" ".join(span_words), lang)
        if len(span_words) == 1 and lang != document_lang:
            chunks.append(
                {"text": pieces[0], "lang": lang, "kind": "foreign-word"}
            )
            prev = lang
            continue
        for index, piece in enumerate(pieces):
            chunks.append(
                {
                    "text": piece,
                    "lang": lang,
                    "kind": _piece_kind(index, pieces, lang, prev, document_lang),
                }
            )
        prev = lang
    for index, chunk in enumerate(chunks):
        chunk["i"] = index
    return chunks, other


def split_b0(text, document_lang):
    parts = chunks_for_mouth(text, document_lang)
    return [
        {"i": index, "text": part, "lang": document_lang, "kind": "b0"}
        for index, part in enumerate(parts)
    ]


def lossless(text, chunks):
    joined = " ".join(chunk["text"] for chunk in chunks)
    return joined == " ".join(text.split())


def over_cap(chunk):
    return len(chunk["text"].split()) > _cap(chunk["lang"])


def write_sidecar(path, source, document_lang, splitter, chunks):
    payload = {
        "source": source,
        "document_lang": document_lang,
        "splitter": splitter,
        "chunks": chunks,
    }
    Path(path).write_bytes(
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    )


def pl_marked_count(text, lexicon):
    count = 0
    for word in text.split():
        folded = _folded(word)
        if not folded:
            continue
        if any(ch in lexicon["diacritics"] for ch in folded) or folded in lexicon["pl"]:
            count += 1
    return count


def lang_miss(answer, scenario, lexicon):
    words = answer.split()
    if scenario == "S2":
        has_diacritic = any(
            ch in lexicon["diacritics"] for word in words for ch in _folded(word)
        )
        en_hit = any(_folded(word) in lexicon["en"] for word in words)
        return (not has_diacritic) or en_hit
    if scenario == "S3":
        return pl_marked_count(answer, lexicon) < 2
    if scenario == "S4":
        yellow = 0
        others = False
        for word in words:
            folded = _folded(word)
            if not folded:
                continue
            if folded == "żółty":
                yellow += 1
            if any(ch in lexicon["diacritics"] for ch in folded) and folded != "żółty":
                others = True
        return yellow != 1 or others
    return False


def _expect_b1(spec):
    return [
        {"text": item["text"], "lang": item["lang"], "kind": item["kind"]}
        for item in spec["b1"]
    ]


def _got_b1(chunks):
    return [
        {"text": chunk["text"], "lang": chunk["lang"], "kind": chunk["kind"]}
        for chunk in chunks
    ]


def check_gold(fixtures=None, lexicon=None):
    fixtures = fixtures or load_json(FIXTURES)
    lexicon = lexicon or load_lexicon()
    errors = []
    for spec in fixtures["gold"]:
        name = spec["id"]
        source = spec["source"]
        lang = spec["document_lang"]
        b0 = split_b0(source, lang)
        b1, _other = split_b1(source, lang, lexicon)
        b0_texts = [chunk["text"] for chunk in b0]
        live = chunks_for_mouth(source, lang)
        if b0_texts != live:
            errors.append(name + " F-B0-DRIFT")
        if b0_texts != spec["b0"]:
            errors.append(name + " B0 texts " + json.dumps(b0_texts, ensure_ascii=False))
        if _got_b1(b1) != _expect_b1(spec):
            errors.append(name + " B1 " + json.dumps(_got_b1(b1), ensure_ascii=False))
        if not lossless(source, b0) or not lossless(source, b1):
            errors.append(name + " F-B1-LOSS")
        if any(over_cap(chunk) for chunk in b1):
            errors.append(name + " F-B1-CAP")
    words, langs, _other = classify_words("Żółta xx yy łodzi", "en", lexicon)
    if langs != ["pl", "en", "en", "pl"]:
        errors.append("bridge-span " + json.dumps(list(zip(words, langs)), ensure_ascii=False))
    return errors


def reference_report(text, lexicon=None):
    lexicon = lexicon or load_lexicon()
    report = {"source_words": len(text.split()), "by_lang": {}}
    for lang in ("en", "pl"):
        b0 = split_b0(text, lang)
        b1, other = split_b1(text, lang, lexicon)
        report["by_lang"][lang] = {
            "b0_n": len(b0),
            "b1_n": len(b1),
            "b0_lossless": lossless(text, b0),
            "b1_lossless": lossless(text, b1),
            "b0_over_cap": sum(1 for chunk in b0 if over_cap(chunk)),
            "b1_over_cap": sum(1 for chunk in b1 if over_cap(chunk)),
            "f_lid_other": other,
            "b0": b0,
            "b1": b1,
        }
    return report


def main():
    configure_stdio_utf8()
    parser = argparse.ArgumentParser(prog="wave3_split.py")
    parser.add_argument("--gold", action="store_true")
    parser.add_argument("--reference", action="store_true")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--splitter", choices=("B0", "B1"), default="B1")
    parser.add_argument("--text", default=None)
    args = parser.parse_args()
    if args.lang not in ("en", "pl"):
        die("document lang must be en or pl")
    if args.gold:
        errors = check_gold()
        if errors:
            print("\n".join(errors), file=sys.stderr)
            raise SystemExit(2)
        print("S5 gold pass")
        return
    if args.reference:
        text = REFERENCE.read_text(encoding="utf-8-sig")
        report = reference_report(text)
        chosen = report["by_lang"][args.lang]
        write_sidecar(SIDECAR, text, args.lang, "B1", chosen["b1"])
        print(
            "REF en B0 "
            + str(report["by_lang"]["en"]["b0_n"])
            + " B1 "
            + str(report["by_lang"]["en"]["b1_n"])
            + " | pl B0 "
            + str(report["by_lang"]["pl"]["b0_n"])
            + " B1 "
            + str(report["by_lang"]["pl"]["b1_n"])
        )
        return
    if args.text is None:
        die("usage: wave3_split.py --gold | --reference | --text TEXT")
    lexicon = load_lexicon()
    if args.splitter == "B0":
        chunks = split_b0(args.text, args.lang)
    else:
        chunks, _other = split_b1(args.text, args.lang, lexicon)
    write_sidecar(SIDECAR, args.text, args.lang, args.splitter, chunks)
    print(json.dumps(chunks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
