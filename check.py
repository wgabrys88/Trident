import inspect
import os
import tempfile
from pathlib import Path

import assistant
import gemma
import hear
import mouth
import nvidia_client
import nvidia_worker
import run
import seat

ROOT = Path(__file__).resolve().parent


def expect_exit(fn):
    try:
        fn()
    except SystemExit:
        return
    raise AssertionError("expected exit")


def test_card_roundtrip():
    fields = {"from": "192.0.2.1:8765", "to": "192.0.2.2:8765"}
    card = seat.make("node", "caps", "a\nb", **fields)
    again = seat.parse(seat.render(card))
    assert again["body"] == "a\nb"
    assert again["id"] == card["id"]
    assert again["module"] == "node"
    empty = seat.make("node", "caps", "", **fields)
    assert seat.parse(seat.render(empty))["body"] == ""


def test_queue_fifo():
    fields = {"from": "192.0.2.1:8765", "to": "192.0.2.1:8765", "lang": "en"}
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        one = seat.make("mouth", "say", "one", **fields)
        two = seat.make("mouth", "say", "two", **fields)
        seat.write_new(one, directory)
        seat.write_new(two, directory)
        assert len(list(directory.glob("*.card"))) == 2
        first = seat.claim_next(directory, {"mouth"})
        assert first is not None
        assert seat.read_card(first)["body"] == "one"
        second = seat.claim_next(directory, {"mouth"})
        assert second is not None
        assert seat.read_card(second)["body"] == "two"
        try:
            seat.write_new(one, directory)
        except seat.CardError as exc:
            assert "duplicate" in str(exc)
        else:
            raise AssertionError("duplicate id")
        path = directory / "lease.txt"
        seat.atomic_write(path, "state running\n")
        assert path.read_text(encoding="utf-8") == "state running\n"
        assert list(directory.glob("*.tmp.*")) == []


def test_mouth_lang():
    assert mouth.language_of("zażółć gęślą jaźń") == "pl"
    assert mouth.language_of("Iris can hear") == "en"
    try:
        mouth.say("", "en")
    except mouth.MouthError:
        pass
    else:
        raise AssertionError("empty say")
    try:
        mouth.say("hi", "")
    except mouth.MouthError:
        pass
    else:
        raise AssertionError("missing lang")


def test_follow_and_say_status():
    expect_exit(lambda: gemma.follow_reply("", None))
    assert gemma.follow_reply("", "kept") == "kept"
    assert gemma.follow_reply("hello", "") == "hello"
    src = (ROOT / "gemma.py").read_text(encoding="utf-8")
    assert 'or "done"' not in src
    assert 'write_iris_status("say"' not in src
    expect_exit(lambda: gemma.write_iris_status("say", "hi"))


def test_run_url():
    os.environ.pop("TRIDENT_PEER", None)
    os.environ.pop("TRIDENT_NVIDIA_URL", None)
    command, url, inject = run.parse_args(["start"])
    assert command == "start"
    assert url == ""
    assert inject is None
    src = (ROOT / "run.py").read_text(encoding="utf-8")
    assert "192.168.16.31" not in src


def test_assistant_source():
    src = (ROOT / "assistant.py").read_text(encoding="utf-8")
    assert "lid.176" not in src
    assert "urllib.request" not in src
    assert "voice.memory" not in src
    assert "assistant.history" not in src
    assert list(inspect.signature(assistant.say_text).parameters) == ["text", "hold", "flip", "fast"]
    try:
        assistant.say_text("hi", False, False, "nano", "extra")
    except TypeError:
        pass
    else:
        raise AssertionError("extra positional")
    original = nvidia_client.post_card

    def posted(*_args, **_kwargs):
        raise AssertionError("posted")

    nvidia_client.post_card = posted
    try:
        expect_exit(lambda: assistant.say_text("   ", False, False))
    finally:
        nvidia_client.post_card = original


def test_route_brain():
    seen = []

    def place_missing(url):
        seen.append(url)
        return type("Place", (), {"brain": "missing"})()

    expect_exit(lambda: assistant.route_brain("http://192.0.2.1:8765/", place_missing))
    assert seen == ["http://192.0.2.1:8765/"]
    seen.clear()

    def place_cpu(url):
        seen.append(url)
        return type("Place", (), {"brain": "cpu"})()

    found = assistant.route_brain("", place_cpu)
    assert found.brain == "cpu"
    assert seen == [None]


def test_hear_contract():
    argv = hear.asr_argv("x.wav")
    assert argv.count("--format") == 1
    assert argv[argv.index("--format") + 1] == "json"
    assert "--verbatim" in argv
    assert "--language" not in argv
    ear = (ROOT / "ear.txt").read_text(encoding="utf-8")
    assert "ear.format json" in ear
    assert "ear.verbatim on" in ear
    try:
        hear.parse_transcript('{"text":"","languages":["en"]}')
    except hear.EarError as exc:
        assert "hear empty" in str(exc)
    else:
        raise AssertionError("empty transcript")
    assert hear.parse_transcript('{"text":"hi","languages":["en-US"]}') == ("hi", "en-US")


def test_missing_image():
    try:
        nvidia_worker.resolve_image({"image": "no-such-image-file.png"})
    except nvidia_worker.WorkerError as exc:
        assert "missing image" in str(exc)
    else:
        raise AssertionError("missing image")
    src = (ROOT / "nvidia_worker.py").read_text(encoding="utf-8")
    assert "text only" not in src


def test_words_once():
    calls = []
    original = hear.transcribe

    def fake(wav):
        calls.append(wav)
        return "hi", "en-US"

    hear.transcribe = fake
    try:
        assert assistant.words_from_wav("a.wav") == ("hi", "en-US")
        assert calls == ["a.wav"]
    finally:
        hear.transcribe = original


def main():
    test_card_roundtrip()
    test_queue_fifo()
    test_mouth_lang()
    test_follow_and_say_status()
    test_run_url()
    test_assistant_source()
    test_route_brain()
    test_hear_contract()
    test_missing_image()
    test_words_once()
    print("check: ok")


if __name__ == "__main__":
    main()
