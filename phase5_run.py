import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from tts_common import MODELS, ROOT, kill

os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["CHATTERBOX_SAMPLER_LOG"] = "1"
PY = sys.executable
DUMP = {
    "nano": MODELS / "nano_t3_dump.txt",
    "turbo": MODELS / "turbo_t3_dump.txt",
    "v3": MODELS / "v3_t3_dump.txt",
}
KNOBS = {
    "nano": MODELS / "nano.knobs",
    "turbo": MODELS / "turbo.knobs",
    "v3": MODELS / "v3.knobs",
}
PID = {
    "nano": MODELS / "server.pid",
    "turbo": MODELS / "turbo.pid",
    "v3": MODELS / "v3.pid",
}

EN_ONES = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty"]


def en_phrase(n: int) -> str:
    if n < 20:
        return EN_ONES[n - 1]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return EN_TENS[tens]
    return f"{EN_TENS[tens]}-{EN_ONES[ones - 1]}"


def en_list(lo: int, hi: int) -> str:
    parts = [en_phrase(i) for i in range(lo, hi + 1)]
    parts[0] = parts[0].capitalize()
    return ", ".join(parts) + "."


EN40 = en_list(1, 40)
EN50 = en_list(1, 50)
EN_WORDS_40 = [en_phrase(i) for i in range(1, 41)]
EN_WORDS_50 = [en_phrase(i) for i in range(1, 51)]

DE_WORDS = [
    "eins",
    "zwei",
    "drei",
    "vier",
    "fünf",
    "sechs",
    "sieben",
    "acht",
    "neun",
    "zehn",
    "elf",
    "zwölf",
    "dreizehn",
    "vierzehn",
    "fünfzehn",
    "sechzehn",
    "siebzehn",
    "achtzehn",
    "neunzehn",
    "zwanzig",
    "einundzwanzig",
    "zweiundzwanzig",
    "dreiundzwanzig",
    "vierundzwanzig",
    "fünfundzwanzig",
    "sechsundzwanzig",
    "siebenundzwanzig",
    "achtundzwanzig",
    "neunundzwanzig",
    "dreißig",
]
DE30 = DE_WORDS[0].capitalize() + ", " + ", ".join(DE_WORDS[1:]) + "."

PL_WORDS = [
    "jeden",
    "dwa",
    "trzy",
    "cztery",
    "pięć",
    "sześć",
    "siedem",
    "osiem",
    "dziewięć",
    "dziesięć",
    "jedenaście",
    "dwanaście",
    "trzynaście",
    "czternaście",
    "piętnaście",
    "szesnaście",
    "siedemnaście",
    "osiemnaście",
    "dziewiętnaście",
    "dwadzieścia",
    "dwadzieścia jeden",
    "dwadzieścia dwa",
    "dwadzieścia trzy",
    "dwadzieścia cztery",
    "dwadzieścia pięć",
    "dwadzieścia sześć",
    "dwadzieścia siedem",
    "dwadzieścia osiem",
    "dwadzieścia dziewięć",
    "trzydzieści",
]
PL30 = PL_WORDS[0].capitalize() + ", " + ", ".join(PL_WORDS[1:]) + "."

CHUCKLE = (
    "Hi there, Sarah here from MochaFone calling you back [chuckle], "
    "have you got one minute to chat about the billing issue?"
)
TAGS_COUNT = "One, two, three [cough], four, five [sigh], six, seven [laugh], eight, nine, ten."
TAG_NAMES = ("chuckle", "cough", "sigh", "laugh", "shush", "groan", "sniff", "gasp")


def fold(s: str) -> str:
    s = s.replace("ß", "ss").replace("-", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = s.replace("ł", "l")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


BARE_TENS = {"twenty", "thirty", "forty", "fifty", "dwadziescia"}
ONES_CONT = {
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "jeden",
    "dwa",
    "trzy",
    "cztery",
    "piec",
    "szesc",
    "siedem",
    "osiem",
    "dziewiec",
}


def phrase_at(toks: list[str], j: int, need: list[str]) -> bool:
    if toks[j : j + len(need)] != need:
        return False
    if len(need) == 1 and need[0] in BARE_TENS:
        nxt = j + 1
        if nxt < len(toks) and toks[nxt] in ONES_CONT:
            return False
    return True


def last_correct(transcript: str, phrases: list[str]) -> tuple[int, list[int]]:
    toks = fold(transcript).split()
    pos = 0
    n = 0
    missing = []
    for i, phrase in enumerate(phrases, 1):
        need = fold(phrase).split()
        found = -1
        for j in range(pos, len(toks) - len(need) + 1):
            if phrase_at(toks, j, need):
                found = j
                break
        if found < 0:
            missing.append(i)
            continue
        pos = found + len(need)
        if not missing:
            n = i
    return n, missing


def parse_dump(path: Path) -> dict[str, str]:
    out = {
        "predicted_count": "",
        "dropped_count": "",
        "eos": "",
        "n_ctx": "",
        "prompt_exceeds": "no",
    }
    if not path.is_file():
        return out
    text = path.read_text(encoding="utf-8", errors="replace")
    for key in ("predicted_count", "dropped_count", "eos", "n_ctx"):
        m = re.search(rf"^{key} (\S+)", text, re.M)
        if m:
            out[key] = m.group(1)
    return out


def speak(tag: str, family: str, text: str, language: str | None, cold: bool) -> dict:
    if cold:
        kill(PID[family])
    cmd = [PY, str(ROOT / f"tts_{family}.py"), text]
    if language:
        cmd.append(language)
    errp = ROOT / f"run-p5-{tag}.err"
    outp = ROOT / f"run-p5-{tag}.out"
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    errp.write_text(proc.stderr, encoding="utf-8")
    outp.write_text(proc.stdout, encoding="utf-8")
    wav = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.lower().endswith(".wav"):
            wav = line
    rtf_line = ""
    for line in proc.stderr.splitlines():
        if line.startswith("wall_s="):
            rtf_line = line.strip()
    dump_src = DUMP[family]
    dump_dst = ROOT / f"run-p5-{tag}-t3_dump.txt"
    if dump_src.is_file():
        shutil.copyfile(dump_src, dump_dst)
        dump = parse_dump(dump_src)
    else:
        dump = parse_dump(dump_dst)
    knobs = KNOBS[family].read_text(encoding="utf-8") if KNOBS[family].is_file() else ""
    (ROOT / f"run-p5-{tag}-knobs.txt").write_text(knobs, encoding="utf-8")
    bytes_ = Path(wav).stat().st_size if wav and Path(wav).is_file() else 0
    wall = dur = rtf = ""
    m = re.search(r"wall_s=([0-9.]+)\s+duration_s=([0-9.]+)\s+rtf=([0-9.]+)", rtf_line)
    if m:
        wall, dur, rtf = m.group(1), m.group(2), m.group(3)
    exceeds = "yes" if "prompt exceeds context" in (proc.stderr + proc.stdout).lower() else "no"
    dump["prompt_exceeds"] = exceeds
    rec = {
        "tag": tag,
        "family": family,
        "language": language or "en",
        "text": text,
        "wav": wav,
        "bytes": bytes_,
        "wall_s": wall,
        "duration_s": dur,
        "rtf": rtf,
        "rtf_line": rtf_line,
        "rc": proc.returncode,
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-20:]),
        "knobs": knobs,
        **dump,
    }
    print(f"SPEAK {tag} rc={proc.returncode} wav={wav} bytes={bytes_} {rtf_line} pred={dump['predicted_count']} eos={dump['eos']}", flush=True)
    if proc.returncode != 0 or bytes_ <= 44:
        print(proc.stderr[-2000:], flush=True)
        print(f"FAILED: {tag} rc={proc.returncode} bytes={bytes_}", flush=True)
        raise SystemExit(1)
    return rec


def asr(tag: str, wav: str) -> str:
    if not wav or not Path(wav).is_file():
        return ""
    proc = subprocess.run(
        [PY, str(ROOT / "listen.py"), wav],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    (ROOT / f"run-p5-{tag}-asr.txt").write_text(line + "\n", encoding="utf-8")
    (ROOT / f"run-p5-{tag}-asr.err").write_text(proc.stderr, encoding="utf-8")
    print(f"ASR {tag} rc={proc.returncode} {line}", flush=True)
    return line


class Tee:
    def __init__(self, *files):
        self.files = files

    def write(self, s):
        for f in self.files:
            f.write(s)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()


def main():
    (ROOT / "run-phase5.pid").write_text(str(os.getpid()), encoding="ascii")
    logf = open(ROOT / "run-phase5-console.log", "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, logf)
    sys.stderr = Tee(sys.__stderr__, logf)
    jobs = [
        ("nano-en-40", "nano", EN40, None, True, EN_WORDS_40),
        ("nano-en-50", "nano", EN50, None, False, EN_WORDS_50),
        ("nano-chuckle", "nano", CHUCKLE, None, False, None),
        ("nano-tags-count", "nano", TAGS_COUNT, None, False, [en_phrase(i) for i in range(1, 11)]),
        ("turbo-en-40", "turbo", EN40, None, True, EN_WORDS_40),
        ("turbo-en-50", "turbo", EN50, None, False, EN_WORDS_50),
        ("turbo-chuckle", "turbo", CHUCKLE, None, False, None),
        ("turbo-tags-count", "turbo", TAGS_COUNT, None, False, [en_phrase(i) for i in range(1, 11)]),
        ("v3-en-40", "v3", EN40, "en", True, EN_WORDS_40),
        ("v3-en-50", "v3", EN50, "en", False, EN_WORDS_50),
        ("v3-de-30", "v3", DE30, "de", True, DE_WORDS),
        ("v3-pl-30", "v3", PL30, "pl", True, PL_WORDS),
    ]
    rows = []
    for tag, family, text, lang, cold, words in jobs:
        rec = speak(tag, family, text, lang, cold)
        rec["transcript"] = asr(tag, rec["wav"]) if rec["wav"] else ""
        if words and rec["transcript"]:
            n, missing = last_correct(rec["transcript"], words)
            rec["last_correct"] = n
            rec["missing"] = missing
        else:
            rec["last_correct"] = ""
            rec["missing"] = []
        spoken_tags = [t for t in TAG_NAMES if t in fold(rec["transcript"]).split()]
        rec["spoken_tags"] = spoken_tags
        rows.append(rec)

    lines = ["# Phase 5 drift", ""]
    lines.append("| tag | family | lang | wav | bytes | wall_s | duration_s | rtf | predicted_count | dropped_count | eos | prompt_exceeds | last_correct | missing | spoken_tags |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|")
    for r in rows:
        miss = ",".join(str(x) for x in r["missing"][:12])
        if len(r["missing"]) > 12:
            miss += ",..."
        lines.append(
            f"| {r['tag']} | {r['family']} | {r['language']} | `{r['wav']}` | {r['bytes']} | {r['wall_s']} | {r['duration_s']} | {r['rtf']} | {r['predicted_count']} | {r['dropped_count']} | {r['eos']} | {r['prompt_exceeds']} | {r['last_correct']} | {miss} | {','.join(r['spoken_tags'])} |"
        )
    lines.append("")
    lines.append("## Transcripts")
    for r in rows:
        lines.append(f"### {r['tag']}")
        lines.append(f"text: {r['text']}")
        lines.append(f"rtf_line: {r['rtf_line']}")
        lines.append(f"transcript: {r['transcript']}")
        lines.append(f"rc: {r['rc']} eos: {r['eos']} pred: {r['predicted_count']} last_correct: {r['last_correct']}")
        lines.append("")
    report = "\n".join(lines)
    (ROOT / "run-phase5.md").write_text(report, encoding="utf-8")
    print(report, flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
