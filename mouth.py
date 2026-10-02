import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DROP_KEYS = ("chatterbox.variant", "chatterbox.language", "chatterbox.play", "chatterbox.cfm-steps")
_PL = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"


def die(message):
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


def physical_lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def kept_lines(raw):
    lines = physical_lines(raw)
    if lines and lines[-1] == "":
        lines = lines[:-1]
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            index += 1
            continue
        if len(stripped) >= 2 and stripped.endswith("<<"):
            key = stripped[:-2].rstrip(" \t")
            if key == "chatterbox.text":
                index += 1
                while index < len(lines) and lines[index] != "<<":
                    index += 1
                if index < len(lines):
                    index += 1
                continue
        key = stripped.split(" ", 1)[0]
        if key in DROP_KEYS or key == "chatterbox.text":
            index += 1
            continue
        kept.append(line)
        index += 1
    return kept


def check_voice(model, lang):
    if model == "nano":
        if lang != "en":
            die("nano speaks en")
        return
    if model == "v3":
        if lang == "en":
            die("english uses nano")
        if not lang or any(ch.isspace() for ch in lang):
            die("v3 asks for a language tag")
        return
    die("unknown model")


def cfm_steps(model):
    return "5" if model == "v3" else "2"


def settings_text(model, lang, sentence, play):
    source = ROOT / "chatterbox.txt"
    if not source.is_file():
        die("missing chatterbox.txt")
    raw = source.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body:
        body += "\n"
    block = "\n".join(physical_lines(sentence))
    body += (
        "chatterbox.variant " + model + "\n"
        "chatterbox.language " + lang + "\n"
        "chatterbox.cfm-steps " + cfm_steps(model) + "\n"
        "chatterbox.play " + play + "\n"
        "chatterbox.text <<\n"
        + block
        + "\n<<\n"
    )
    return body


def write_mouth(payload):
    try:
        (ROOT / "mouth.txt").write_bytes(payload.encode("utf-8"))
    except OSError as exc:
        die("cannot write mouth.txt: " + str(exc))


def vacant(path):
    if not path.exists():
        return path
    for n in range(1000):
        cand = path.with_name(path.stem + "-" + str(n) + path.suffix)
        if not cand.exists():
            return cand
    die("output names exhausted")


def adopt_output(tmp, out_txt):
    name = out_txt.read_text(encoding="utf-8").strip()
    if not name or name != Path(name).name:
        die("bad wav name from chatterbox")
    src = tmp / name
    if not src.is_file():
        die("missing wav named by chatterbox: " + name)
    dest = vacant(ROOT / name)
    shutil.move(str(src), str(dest))
    try:
        vacant(ROOT / out_txt.name).write_text(dest.name + "\n", encoding="utf-8")
    except OSError as exc:
        die("cannot write chatterbox output text: " + str(exc))
    return dest


def model_text(text, lang):
    spoken = " ".join((text or "").split())
    tag = (lang or "").split("-")[0].lower()
    if tag == "en" or not tag:
        return spoken
    return unicodedata.normalize("NFKD", spoken.lower())


def synthesize(model, lang, sentence):
    check_voice(model, lang)
    sentence = model_text(sentence, lang)
    write_mouth(settings_text(model, lang, sentence, "off"))
    path = ROOT / "mouth.txt"
    tmp = Path(tempfile.mkdtemp(prefix="trident-mouth-once-"))
    try:
        try:
            completed = subprocess.run(
                [str(ROOT / "chatterbox.exe"), str(path)],
                cwd=str(tmp),
                shell=False,
            )
        except OSError as exc:
            die("cannot run chatterbox.exe: " + str(exc))
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        new_files = sorted(tmp.glob("*_chatterbox_out_*.txt"))
        if not new_files:
            die("chatterbox wrote no output text")
        if len(new_files) != 1:
            die("chatterbox wrote more than one output text")
        return adopt_output(tmp, new_files[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def language_of(text):
    for ch in text or "":
        if ch in _PL:
            return "pl"
    return "en"
