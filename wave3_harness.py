"""Wave 5 sense matrix. Think closer lives here. qwen.py is not called.

Writes wave3_sense.txt and wave3_run.json. Does not edit sense.txt.
mouth.py starts only from --play-only, after the matrix record exists.
"""

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from assistant import TOKENS, _bare, chunks_for_mouth, configure_stdio_utf8, speakable
from qwen import kept_lines
from wave3_split import (
    FIXTURES,
    LEXICON,
    REFERENCE,
    SIDECAR,
    check_gold,
    lang_miss,
    load_json,
    load_lexicon,
    lossless,
    over_cap,
    reference_report,
    split_b0,
    split_b1,
    write_sidecar,
)

ROOT = Path(__file__).resolve().parent
SENSE_TXT = ROOT / "sense.txt"
CHATTERBOX_TXT = ROOT / "chatterbox.txt"
RUN_JSON = ROOT / "wave3_run.json"
SENSE_RUN = ROOT / "wave3_sense.txt"
MOUTH_ARGV = ROOT / "wave3_mouth_argv.json"
SCENARIOS = ("S1", "S2", "S3", "S4")
ARMS = ("A", "B")
SPEAKERS = "Speakers (Realtek(R) Audio)"
LANG_TOKEN = re.compile(r"\[[a-z]{2,3}\]")
HARD = {"F-THINK-OPEN", "F-THINK-EMPTY", "F-JSON", "F-TRUNC", "F-EXIT"}
ADOPT = "adopt-optional-qwen_chunk"
KEEP = "keep-research"
ABANDON = "abandon"


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(2)


def sha256(blob):
    return hashlib.sha256(blob).hexdigest()


def save(doc):
    payload = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    tmp = RUN_JSON.with_suffix(".json.tmp")
    tmp.write_bytes(payload)
    tmp.replace(RUN_JSON)


def tip_sha():
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "").strip()


def assert_untracked():
    names = [
        "wave3_sense.txt",
        "wave3_run.json",
        "wave3_chunks.json",
        "wave3_mouth_argv.json",
    ]
    proc = subprocess.run(
        ["git", "ls-files", "--"] + names,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    listed = (proc.stdout or "").strip()
    if listed:
        die("tracked run artifact:\n" + listed)


def key_map(text):
    keys = {}
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        stripped = lines[index].lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if len(stripped) >= 2 and stripped.endswith("<<"):
            key = stripped[:-2].rstrip(" \t")
            index += 1
            buf = []
            while index < len(lines) and lines[index] != "<<":
                buf.append(lines[index])
                index += 1
            keys[key] = "\n".join(buf)
            if index < len(lines):
                index += 1
            continue
        part = stripped.split(" ", 1)
        keys[part[0]] = part[1] if len(part) > 1 else ""
        index += 1
    return keys


def assert_run_keys(text, npredict):
    keys = key_map(text)
    for key in keys:
        tokens = set()
        for part in key.split("."):
            tokens.update(part.split("-"))
        if tokens & {"grammar", "stop", "seed"}:
            die("F-KEY " + key)
    temp = keys.get("sense.temp")
    if temp is None or float(temp) == 0.0:
        die("F-TEMP0")
    expected = {
        "sense.temp": 0.7,
        "sense.top-p": 0.8,
        "sense.top-k": 20,
        "sense.n-predict": npredict,
        "sense.gpu-layers": 0,
        "sense.ctx": 4096,
        "sense.threads": 4,
        "sense.batch": 512,
    }
    for key, value in expected.items():
        got = keys.get(key)
        if got is None:
            die("missing " + key)
        if abs(float(got) - float(value)) > 1e-6:
            die(key + " is " + got)


def knob_text():
    raw = SENSE_TXT.read_text(encoding="utf-8-sig")
    body = "\n".join(kept_lines(raw))
    if body and not body.endswith("\n"):
        body += "\n"
    return body


def with_npredict(body, npredict):
    lines = body.split("\n")
    found = False
    out = []
    for line in lines:
        stripped = line.lstrip(" \t")
        if stripped == "sense.n-predict" or stripped.startswith("sense.n-predict "):
            out.append("sense.n-predict " + str(npredict))
            found = True
        else:
            out.append(line)
    if not found:
        die("sense.n-predict missing")
    return "\n".join(out)


def arm_prompt(body, arm):
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if arm == "A":
        user = text + "<|im_end|>\n"
    elif arm == "B":
        user = text + "\n/no_think<|im_end|>\n"
    else:
        die("unknown arm")
    return "<|im_start|>user\n" + user + "<|im_start|>assistant\n"


def settings_text(knobs, prompt):
    body = knobs
    if body and not body.endswith("\n"):
        body += "\n"
    body += "sense.text <<\n" + prompt
    if not prompt.endswith("\n"):
        body += "\n"
    body += "<<\n"
    return body


def has_markers(answer):
    if any(token in answer for token in TOKENS):
        return True
    lines = answer.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return any(line == "<<" for line in lines)


def score_generation(raw, scenario, lexicon):
    if "<think>" in raw and "</think>" not in raw:
        return {
            "parse_class": "F-THINK-OPEN",
            "failures": ["F-THINK-OPEN"],
            "answer": "",
            "span": "",
        }
    span = speakable(raw)
    if not span.strip():
        return {
            "parse_class": "F-THINK-EMPTY",
            "failures": ["F-THINK-EMPTY"],
            "answer": "",
            "span": span,
        }
    if "}" not in span:
        return {
            "parse_class": "F-TRUNC",
            "failures": ["F-TRUNC"],
            "answer": "",
            "span": span,
        }
    try:
        obj = json.loads(span.strip())
    except json.JSONDecodeError:
        obj = None
    if not isinstance(obj, dict) or not isinstance(obj.get("answer"), str):
        return {
            "parse_class": "F-JSON",
            "failures": ["F-JSON"],
            "answer": "",
            "span": span,
        }
    answer = obj["answer"]
    failures = []
    if has_markers(answer):
        failures.append("F-MARKERS")
    if not answer.strip():
        failures.append("F-EMPTY")
    if lang_miss(answer, scenario, lexicon):
        failures.append("F-LANG-MISS")
    if "F-MARKERS" in failures:
        klass = "F-MARKERS"
    elif "F-EMPTY" in failures:
        klass = "F-EMPTY"
    elif "F-LANG-MISS" in failures:
        klass = "F-LANG-MISS"
    else:
        klass = "PASS"
    return {
        "parse_class": klass,
        "failures": failures,
        "answer": answer,
        "span": span,
    }


def mouth_blocks(chunks):
    blocks = []
    for chunk in chunks:
        text = chunk["text"]
        if not text.strip():
            blocks.append("empty")
        if any(line == "<<" for line in text.replace("\r\n", "\n").split("\n")):
            blocks.append("<<")
        if LANG_TOKEN.search(text):
            blocks.append("lang-token")
    return blocks


def attach_splits(cell, lexicon):
    if cell.get("parse_class") != "PASS":
        cell["b0"] = None
        cell["b1"] = None
        return
    answer = cell["answer"]
    lang = cell["document_lang"]
    b0 = split_b0(answer, lang)
    b1, other = split_b1(answer, lang, lexicon)
    failures = list(cell.get("failures") or [])
    live = [chunk["text"] for chunk in b0]
    if live != chunks_for_mouth(answer, lang):
        failures.append("F-B0-DRIFT")
    if not lossless(answer, b1):
        failures.append("F-B1-LOSS")
    if any(over_cap(chunk) for chunk in b1):
        failures.append("F-B1-CAP")
    if other:
        failures.append("F-LID-OTHER")
    if cell["scenario"] == "S4":
        own = any(
            chunk["kind"] == "foreign-word"
            and len(chunk["text"].split()) == 1
            and _bare(chunk["text"]).casefold() == "żółty"
            for chunk in b1
        )
        if not own:
            failures.append("F-B1-FOREIGN")
    if mouth_blocks(b1):
        failures.append("F-MOUTH-PRE")
    cell["failures"] = list(dict.fromkeys(failures))
    cell["b0"] = b0
    cell["b1"] = b1


def is_isolated(cell):
    if cell.get("parse_class") != "PASS":
        return False
    b0 = cell.get("b0") or []
    b1 = cell.get("b1") or []
    if len(b0) != 1 or not b1:
        return False
    blocked = {"F-B1-LOSS", "F-B1-CAP", "F-B1-FOREIGN", "F-MOUTH-PRE", "F-B0-DRIFT"}
    if blocked & set(cell.get("failures") or []):
        return False
    if cell["scenario"] == "S4":
        return True
    if cell["scenario"] == "S3":
        langs = {chunk["lang"] for chunk in b1}
        return "pl" in langs and "en" in langs and len(b1) >= 2
    return False


def final_cells(doc):
    chosen = {}
    for cell in doc["cells"]:
        scenario = cell.get("scenario")
        arm = cell.get("arm")
        if scenario not in SCENARIOS or arm not in ARMS:
            continue
        key = (scenario, arm)
        if key not in chosen or cell.get("h") == "H1":
            chosen[key] = cell
    return chosen


def bad_count(cell):
    codes = set(cell.get("failures") or [])
    codes.add(cell.get("parse_class"))
    return sum(code in codes for code in ("F-THINK-OPEN", "F-JSON"))


def decide(doc, mouth_rejected=False):
    reasons = []
    s5 = next(cell for cell in doc["cells"] if cell["id"] == "S5")
    if s5["parse_class"] != "PASS":
        return {"id": ABANDON, "reasons": ["S5 gold failed"], "winner_arm": None}
    ref = doc.get("reference") or {}
    for lang, block in (ref.get("by_lang") or {}).items():
        if not block.get("b1_lossless", True):
            return {
                "id": ABANDON,
                "reasons": ["reference B1 join mismatch (" + lang + ")"],
                "winner_arm": None,
            }
    chosen = final_cells(doc)
    if any("F-B1-LOSS" in (cell.get("failures") or []) for cell in chosen.values()):
        return {
            "id": ABANDON,
            "reasons": ["B1 join mismatch on a model answer"],
            "winner_arm": None,
        }
    scores = {}
    patterns = {}
    nonempty = {}
    for arm in ARMS:
        cells = [chosen[(scenario, arm)] for scenario in SCENARIOS if (scenario, arm) in chosen]
        scores[arm] = sum(bad_count(cell) for cell in cells)
        patterns[arm] = tuple(cell["parse_class"] for cell in cells)
        nonempty[arm] = sum(bool((cell.get("answer") or "").strip()) for cell in cells)
    winner = "A" if scores["A"] <= scores["B"] else "B"
    h1_used = any(cell.get("h") == "H1" for cell in doc["cells"])
    isolated = [cell for cell in chosen.values() if is_isolated(cell)]
    mixed = scores["A"] == scores["B"] and patterns["A"] != patterns["B"]
    adopt_ok = (
        not h1_used
        and not mixed
        and not mouth_rejected
        and nonempty[winner] >= 3
        and len(isolated) >= 1
        and len(patterns[winner]) == 4
    )
    reasons.append(
        "winner "
        + winner
        + " bad "
        + str(scores[winner])
        + " nonempty "
        + str(nonempty[winner])
        + " isolated "
        + str(len(isolated))
    )
    reasons.append(
        "unmarked ASCII foreign words stay research; diacritic and lexicon hits are the adopt gate"
    )
    if mouth_rejected:
        reasons.append("mouth.py rejected a chunk")
    if h1_used:
        reasons.append("9th H1 spent on truncation")
    if mixed:
        reasons.append("arm scores mixed")
    if not isolated:
        reasons.append("no S3 or S4 foreign span isolated while B0 stayed one chunk")
    if nonempty[winner] < 3:
        reasons.append("winning arm has fewer than 3 non-empty JSON answers")
    return {
        "id": ADOPT if adopt_ok else KEEP,
        "reasons": reasons,
        "winner_arm": winner,
        "scores": scores,
        "nonempty": nonempty,
        "patterns": {arm: list(patterns[arm]) for arm in ARMS},
    }


def plan_prove(doc):
    decision = doc.get("decision") or {}
    if decision.get("id") == ABANDON:
        return {"status": "skipped", "reason": "abandon"}
    winner = decision.get("winner_arm") or "A"
    chosen = final_cells(doc)
    pick = None
    for arm in (winner, "B" if winner == "A" else "A"):
        for scenario in ("S4", "S3"):
            cell = chosen.get((scenario, arm))
            if cell and is_isolated(cell):
                pick = cell
                break
        if pick:
            break
    if pick is None:
        return {"status": "skipped", "reason": "no isolated S3 or S4 cell"}
    lang = pick["document_lang"]
    texts = [chunk["text"] for chunk in pick["b1"]]
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    argv = [str(py), str(ROOT / "mouth.py"), "--model", "v3", "--lang", lang, "--"]
    argv.extend(texts)
    note = (
        "--lang "
        + lang
        + " is shared by every chunk. "
        + "Optional play only. mouth.py does not switch language per chunk. "
        + "Foreign chunks stay in this order."
    )
    return {
        "status": "planned",
        "scenario": pick["scenario"],
        "arm": pick["arm"],
        "h": pick["h"],
        "lang": lang,
        "chunks": texts,
        "argv": argv,
        "lang_note": note,
    }


def launch_cell(doc, scenario, arm, hyper, body, document_lang, knobs, lexicon, sense_bytes, chatter_bytes):
    npredict = 256 if hyper == "H1" else 128
    knob_body = with_npredict(knobs, npredict) if hyper == "H1" else knobs
    payload = settings_text(knob_body, arm_prompt(body, arm))
    assert_run_keys(payload, npredict)
    if SENSE_TXT.read_bytes() != sense_bytes or CHATTERBOX_TXT.read_bytes() != chatter_bytes:
        die("sense.txt or chatterbox.txt changed")
    SENSE_RUN.write_bytes(payload.encode("utf-8"))
    before = set(ROOT.glob("*_sense_out_*.txt"))
    started = time.perf_counter()
    proc = subprocess.run(
        [".\\sense.exe", "wave3_sense.txt"],
        cwd=ROOT,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    seconds = round(time.perf_counter() - started, 1)
    new_files = sorted(set(ROOT.glob("*_sense_out_*.txt")) - before)
    out = new_files[-1] if new_files else None
    raw = out.read_text(encoding="utf-8-sig") if out else ""
    cell = {
        "id": scenario + "-" + arm + "-" + hyper,
        "scenario": scenario,
        "arm": arm,
        "h": hyper,
        "document_lang": document_lang,
        "exit": proc.returncode,
        "seconds": seconds,
        "out_file": out.name if out else "",
        "generation": raw,
        "launch_index": doc["launches"] + 1,
    }
    doc["launches"] += 1
    if proc.returncode != 0 or out is None:
        cell["parse_class"] = "F-EXIT"
        cell["failures"] = ["F-EXIT"]
        cell["answer"] = ""
        cell["span"] = ""
    else:
        cell.update(score_generation(raw, scenario, lexicon))
    doc["cells"].append(cell)
    save(doc)
    print(
        "wave3: "
        + cell["id"]
        + " exit "
        + str(cell["exit"])
        + " "
        + cell["parse_class"]
        + " "
        + str(seconds)
        + "s",
        flush=True,
    )
    return cell


def ref_cell(report):
    failures = []
    for lang, block in report["by_lang"].items():
        if not block["b1_lossless"]:
            failures.append("F-B1-LOSS")
        if block["b1_over_cap"]:
            failures.append("F-B1-CAP")
        if block["f_lid_other"]:
            failures.append("F-LID-OTHER")
    return {
        "id": "REF",
        "parse_class": "COMPARE",
        "failures": list(dict.fromkeys(failures)),
        "b0_n_en": report["by_lang"]["en"]["b0_n"],
        "b1_n_en": report["by_lang"]["en"]["b1_n"],
        "b0_n_pl": report["by_lang"]["pl"]["b0_n"],
        "b1_n_pl": report["by_lang"]["pl"]["b1_n"],
    }


def run_matrix():
    configure_stdio_utf8()
    assert_untracked()
    if not SENSE_TXT.is_file() or not CHATTERBOX_TXT.is_file():
        die("missing sense.txt or chatterbox.txt")
    lexicon = load_lexicon()
    fixtures = load_json(FIXTURES)
    sense_bytes = SENSE_TXT.read_bytes()
    chatter_bytes = CHATTERBOX_TXT.read_bytes()
    doc = {
        "tip": tip_sha(),
        "lexicon": str(LEXICON.relative_to(ROOT)).replace("\\", "/"),
        "sense_txt_sha256": sha256(sense_bytes),
        "chatterbox_txt_sha256": sha256(chatter_bytes),
        "launches": 0,
        "ceiling": 9,
        "stopped": "",
        "cells": [],
    }
    errors = check_gold(fixtures, lexicon)
    doc["cells"].append(
        {
            "id": "S5",
            "parse_class": "PASS" if not errors else "FAIL",
            "failures": errors,
        }
    )
    save(doc)
    if errors:
        doc["decision"] = {"id": ABANDON, "reasons": ["S5 gold failed"], "winner_arm": None}
        save(doc)
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(2)
    print("wave3: S5 PASS", flush=True)
    if not (ROOT / "sense.exe").is_file() or not (ROOT / "sense.gguf").is_file():
        doc["stopped"] = "missing sense.exe or sense.gguf"
        doc["decision"] = {
            "id": KEEP,
            "reasons": ["sense.exe or sense.gguf missing"],
            "winner_arm": None,
        }
        save(doc)
        die("missing sense.exe or sense.gguf")
    knobs = knob_text()
    assert_run_keys(settings_text(knobs, arm_prompt("ping", "A")), 128)
    text = REFERENCE.read_text(encoding="utf-8-sig")
    report = reference_report(text, lexicon)
    doc["reference"] = report
    doc["cells"].append(ref_cell(report))
    write_sidecar(SIDECAR, text, "en", "B1", report["by_lang"]["en"]["b1"])
    save(doc)
    print(
        "wave3: REF en B0 "
        + str(report["by_lang"]["en"]["b0_n"])
        + " B1 "
        + str(report["by_lang"]["en"]["b1_n"])
        + " pl B0 "
        + str(report["by_lang"]["pl"]["b0_n"])
        + " B1 "
        + str(report["by_lang"]["pl"]["b1_n"]),
        flush=True,
    )
    if not report["by_lang"]["en"]["b1_lossless"] or not report["by_lang"]["pl"]["b1_lossless"]:
        doc["decision"] = decide(doc)
        save(doc)
        die("reference B1 join mismatch")
    consecutive = 0
    prompts = fixtures["prompts"]
    for scenario in SCENARIOS:
        for arm in ARMS:
            if doc["launches"] >= 8:
                break
            launch_cell(
                doc,
                scenario,
                arm,
                "H0",
                prompts[scenario]["body"],
                prompts[scenario]["document_lang"],
                knobs,
                lexicon,
                sense_bytes,
                chatter_bytes,
            )
            cell = doc["cells"][-1]
            if cell["exit"] != 0:
                consecutive += 1
                if consecutive >= 2:
                    doc["stopped"] = "two non-zero sense exits"
                    save(doc)
                    break
            else:
                consecutive = 0
        if doc["stopped"]:
            break
    if not doc["stopped"] and doc["launches"] == 8:
        h0 = [cell for cell in doc["cells"] if cell.get("h") == "H0"]
        truncs = [cell for cell in h0 if cell["parse_class"] == "F-TRUNC"]
        if len(truncs) == 1:
            one = truncs[0]
            twins = [
                cell
                for cell in h0
                if cell["scenario"] == one["scenario"] and cell["arm"] != one["arm"]
            ]
            twin = twins[0] if len(twins) == 1 else None
            if twin and twin["exit"] == 0 and twin["parse_class"] not in HARD:
                launch_cell(
                    doc,
                    one["scenario"],
                    one["arm"],
                    "H1",
                    prompts[one["scenario"]]["body"],
                    one["document_lang"],
                    knobs,
                    lexicon,
                    sense_bytes,
                    chatter_bytes,
                )
            else:
                twin_note = "missing" if twin is None else twin["id"] + " " + twin["parse_class"]
                doc["stopped"] = "H1 skipped; twin " + twin_note
        elif len(truncs) > 1:
            doc["stopped"] = "two or more F-TRUNC"
    for cell in doc["cells"]:
        if cell.get("scenario") in SCENARIOS and cell.get("parse_class") == "PASS":
            attach_splits(cell, lexicon)
    if SENSE_TXT.read_bytes() != sense_bytes or CHATTERBOX_TXT.read_bytes() != chatter_bytes:
        die("sense.txt or chatterbox.txt changed during the matrix")
    doc["decision"] = decide(doc)
    doc["prove"] = plan_prove(doc)
    if doc["prove"]["status"] == "planned":
        MOUTH_ARGV.write_bytes(
            json.dumps(
                {
                    "argv": doc["prove"]["argv"],
                    "lang_note": doc["prove"]["lang_note"],
                    "scenario": doc["prove"]["scenario"],
                    "arm": doc["prove"]["arm"],
                    "lang": doc["prove"]["lang"],
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            + b"\n"
        )
    save(doc)
    print("wave3: decision " + doc["decision"]["id"], flush=True)


def _u32(handle):
    blob = handle.read(4)
    if len(blob) != 4:
        raise ValueError("short gguf")
    return int.from_bytes(blob, "little")


def _u64(handle):
    blob = handle.read(8)
    if len(blob) != 8:
        raise ValueError("short gguf")
    return int.from_bytes(blob, "little")


def _skip_gguf(handle, vtype):
    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    if vtype in sizes:
        handle.seek(sizes[vtype], 1)
        return
    if vtype == 8:
        handle.seek(_u64(handle), 1)
        return
    if vtype == 9:
        elem = _u32(handle)
        count = _u64(handle)
        for _ in range(count):
            _skip_gguf(handle, elem)
        return
    raise ValueError("gguf type " + str(vtype))


def language_tokens(path):
    with path.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            raise ValueError("not gguf")
        version = _u32(handle)
        if version < 2:
            raise ValueError("gguf version")
        _u64(handle)
        count = _u64(handle)
        for _ in range(count):
            key = handle.read(_u64(handle)).decode("utf-8", "replace")
            vtype = _u32(handle)
            if key == "chatterbox.tokenizer.language_tokens" and vtype == 8:
                return handle.read(_u64(handle)).decode("utf-8", "replace")
            _skip_gguf(handle, vtype)
    return ""


def default_speakers():
    import ctypes
    import uuid
    from ctypes import (
        POINTER,
        Structure,
        byref,
        c_int,
        c_long,
        c_ubyte,
        c_ulong,
        c_ushort,
        c_void_p,
        c_wchar_p,
        windll,
    )

    class GUID(Structure):
        _fields_ = [
            ("Data1", c_ulong),
            ("Data2", c_ushort),
            ("Data3", c_ushort),
            ("Data4", c_ubyte * 8),
        ]

    def make_guid(text):
        value = uuid.UUID(text)
        data4 = (c_ubyte * 8).from_buffer_copy(value.bytes_le[8:])
        return GUID(value.time_low, value.time_mid, value.time_hi_version, data4)

    ole32 = windll.ole32
    if ole32.CoInitialize(None) < 0:
        return ""
    clsid = make_guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
    iid = make_guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
    enumerator = c_void_p()
    hr = ole32.CoCreateInstance(byref(clsid), None, 23, byref(iid), byref(enumerator))
    if hr != 0 or not enumerator:
        return ""
    vtbl = ctypes.cast(enumerator, POINTER(POINTER(c_void_p)))[0]
    get_default = ctypes.WINFUNCTYPE(c_long, c_void_p, c_int, c_int, POINTER(c_void_p))(vtbl[4])
    device = c_void_p()
    if get_default(enumerator, 0, 0, byref(device)) != 0 or not device:
        return ""
    dev_vtbl = ctypes.cast(device, POINTER(POINTER(c_void_p)))[0]
    open_store = ctypes.WINFUNCTYPE(c_long, c_void_p, c_ulong, POINTER(c_void_p))(dev_vtbl[4])
    store = c_void_p()
    if open_store(device, 0, byref(store)) != 0 or not store:
        return ""
    store_vtbl = ctypes.cast(store, POINTER(POINTER(c_void_p)))[0]

    class PROPERTYKEY(Structure):
        _fields_ = [("fmtid", GUID), ("pid", c_ulong)]

    class PROPVARIANT(Structure):
        _fields_ = [
            ("vt", c_ushort),
            ("r1", c_ushort),
            ("r2", c_ushort),
            ("r3", c_ushort),
            ("psz", c_wchar_p),
        ]

    key = PROPERTYKEY()
    key.fmtid = make_guid("A45C254E-DF1C-4EFD-8020-67D146A850E0")
    key.pid = 14
    value = PROPVARIANT()
    get_value = ctypes.WINFUNCTYPE(
        c_long, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT)
    )(store_vtbl[5])
    if get_value(store, byref(key), byref(value)) != 0 or value.vt != 31 or not value.psz:
        return ""
    return value.psz


def validation_reject(stderr):
    text = stderr or ""
    return (
        "empty text" in text
        or "text line is only <<" in text
        or "empty language" in text
        or "unknown model" in text
    )


def play_once(argv):
    before = set(ROOT.glob("*_chatterbox_out_*.txt"))
    proc = subprocess.run(
        argv,
        cwd=ROOT,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    new_files = sorted(set(ROOT.glob("*_chatterbox_out_*.txt")) - before)
    err = proc.stderr or ""
    if len(err) > 2000:
        err = err[:1000] + "\n...\n" + err[-1000:]
    return {
        "exit": proc.returncode,
        "out_files": [path.name for path in new_files],
        "stderr": err,
        "playsound": "PlaySoundW" in (proc.stderr or ""),
        "rejected": validation_reject(proc.stderr or ""),
    }


def play_only():
    configure_stdio_utf8()
    assert_untracked()
    if not RUN_JSON.is_file():
        die("missing wave3_run.json")
    doc = load_json(RUN_JSON)
    prove = doc.get("prove") or {}
    if prove.get("status") != "planned":
        print("wave3: prove " + prove.get("status", "missing"), flush=True)
        return
    chunks = [{"text": text} for text in prove.get("chunks") or []]
    if mouth_blocks(chunks) or not chunks:
        prove["status"] = "skipped"
        prove["reason"] = "F-MOUTH-PRE"
        doc["prove"] = prove
        save(doc)
        return
    try:
        device = default_speakers()
    except Exception as exc:
        device = ""
        prove["device_error"] = str(exc)
    prove["device"] = device
    if device != SPEAKERS:
        prove["status"] = "MANUAL"
        prove["reason"] = "playback device was not confirmed as " + SPEAKERS
        doc["prove"] = prove
        save(doc)
        print("wave3: prove MANUAL device " + repr(device), flush=True)
        return
    gguf = ROOT / "v3-t3.gguf"
    try:
        tokens = language_tokens(gguf)
    except Exception as exc:
        prove["status"] = "MANUAL"
        prove["reason"] = "language tokens unconfirmed: " + str(exc)
        doc["prove"] = prove
        save(doc)
        print("wave3: prove MANUAL tokens", flush=True)
        return
    have = {part.strip() for part in tokens.split(",")}
    prove["language_tokens_have_en_pl"] = "[en]" in have and "[pl]" in have
    if not prove["language_tokens_have_en_pl"]:
        prove["status"] = "MANUAL"
        prove["reason"] = "[en] and [pl] not both in v3 language tokens"
        doc["prove"] = prove
        save(doc)
        print("wave3: prove MANUAL tokens missing", flush=True)
        return
    chatter = CHATTERBOX_TXT.read_bytes()
    sense = SENSE_TXT.read_bytes()
    attempts = []
    for _ in range(2):
        attempts.append(play_once(prove["argv"]))
        if attempts[-1]["exit"] == 0 and len(attempts[-1]["out_files"]) == len(chunks):
            break
    prove["attempts"] = attempts
    last = attempts[-1]
    ok = last["exit"] == 0 and len(last["out_files"]) == len(chunks)
    prove["status"] = "played" if ok else "failed"
    if CHATTERBOX_TXT.read_bytes() != chatter or SENSE_TXT.read_bytes() != sense:
        prove["status"] = "failed"
        prove["reason"] = "sense.txt or chatterbox.txt changed"
        ok = False
    if any(item["rejected"] for item in attempts):
        doc["decision"] = decide(doc, mouth_rejected=True)
        prove["reason"] = "mouth.py rejected a chunk"
    elif not ok and all(item["playsound"] for item in attempts):
        prove["reason"] = "PlaySoundW failed twice; text decision stands"
    elif not ok:
        prove["reason"] = "mouth exit " + str(last["exit"])
    doc["prove"] = prove
    save(doc)
    print("wave3: prove " + prove["status"] + " exit " + str(last["exit"]), flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--play-only":
        play_only()
        return
    if len(sys.argv) > 1:
        die("usage: wave3_harness.py [--play-only]")
    run_matrix()


if __name__ == "__main__":
    main()
