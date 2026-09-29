"""Offline checks for the voice seat. No microphone. No port bind."""

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import assistant
import mouth
import seat
PEER = "http://192.168.16.31:8765/"


def fail(message):
    print("FAIL " + message)
    raise SystemExit(1)


def expect_exit(label, fn):
    try:
        fn()
    except SystemExit:
        print("ok " + label)
        return
    fail(label + " did not exit")


def check_seat():
    signals = seat.parse_signals("The door is shut.\n")
    if signals != [seat.Signal("say", "The door is shut.")]:
        fail("legacy say")
    raw = "status <<\npeer ready\n<<\nwork <<\nkettle\n<<\nsay <<\nDrzwi są zamknięte.\n<<\n"
    kinds = [item.kind for item in seat.parse_signals(raw)]
    if kinds != ["status", "work", "say"]:
        fail("blocks " + " ".join(kinds))
    with tempfile.TemporaryDirectory(prefix="seat_") as tmp:
        path = Path(tmp) / "iris_seat.txt"
        seat.store(path, seat.parse_signals(raw))
        claimed = seat.claim(path)
        if [item.kind for item in claimed] != ["status", "work", "say"]:
            fail("claim")
        if path.exists():
            fail("claim left the file")
        seat.give_back(path, claimed[:1])
        if [item.kind for item in seat.claim(path)] != ["status"]:
            fail("give back")
    print("ok seat")


def check_tools():
    if assistant.organism_stop("Please stop listening."):
        fail("plain sentence counted as stop")
    call = '<|tool_call>call:stop{}<tool_call|>'
    if not assistant.organism_stop(call):
        fail("stop tool")
    import gemma

    parsed = gemma.parse_tool_call("remember this stop keyword")
    if parsed is not None:
        fail("keyword became a tool")
    parsed = gemma.parse_tool_call('<|tool_call>call:remember{line:<|"|>lamp<|"|>}<tool_call|>')
    if parsed is None or parsed[0] != "remember":
        fail("remember tool")
    print("ok tools")


def check_mouth():
    text = "The lamp is on. Drzwi są zamknięte."
    parts = assistant.chunks_for_mouth(text, "nano")
    if not parts:
        fail("no chunks")
    langs = set()
    for _chunk, model, lang in parts:
        langs.add(lang)
        if lang == "en" and model not in ("nano", "turbo"):
            fail("english model " + model)
        if lang != "en" and model != "v3":
            fail("non-english model " + model)
        mouth.check_voice(model, lang)
    if "en" not in langs or "pl" not in langs:
        fail("spans " + " ".join(item[2] + ":" + item[1] for item in parts))
    expect_exit("v3 english", lambda: mouth.check_voice("v3", "en"))
    expect_exit("nano polish", lambda: mouth.check_voice("nano", "pl"))
    expect_exit("fast v3", lambda: assistant.chunks_for_mouth("Hello.", "v3"))
    print("ok mouth " + " ".join(item[1] + " " + item[2] for item in parts))


def check_route(url, want):
    args = argparse.Namespace(
        nvidia=True,
        url=url,
        timeout=5,
        model="nano",
        brain="qwen",
        image=None,
        once=True,
    )
    found = assistant.turn_place(args)
    route = "lan" if found.brain == "post" else "alone"
    if route != want:
        fail(url + " route " + route + " brain " + found.brain)
    print("ok route " + want + " " + found.brain + " " + found.where)


def main():
    check_seat()
    check_tools()
    check_mouth()
    check_route("http://127.0.0.1:9/", "alone")
    check_route(PEER, "lan")
    print("STATUS PASS")


if __name__ == "__main__":
    main()
