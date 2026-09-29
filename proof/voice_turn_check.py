"""Closed-mic checks for the turn gate. No microphone. No port bind. No agent CLI."""

import argparse
import inspect
import os
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import assistant
import gemma
import run

CREATE_NO_WINDOW = 0x08000000


def fail(message):
    print("FAIL " + message)
    raise SystemExit(1)


def port_open(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def snapshot():
    return {
        ("127.0.0.1", 8765): port_open("127.0.0.1", 8765),
        ("192.168.16.31", 8765): port_open("192.168.16.31", 8765),
    }


def write_wav(path, frames, rate, loud):
    sample = 20000 if loud else 0
    payload = b"".join(int(sample).to_bytes(2, "little", signed=True) for _ in range(frames))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(payload)


def check_question():
    polish = "Jaka jest pogoda?"
    english = "What time is it?"
    if assistant.voice_question(None, polish) != polish:
        fail("polish question was rewritten")
    if assistant.voice_question("pl-PL", polish) != polish:
        fail("asr tag changed the polish question")
    if assistant.voice_question("en-US", english) != english:
        fail("english question was rewritten")
    if assistant.voice_question(None, "  " + english + "  ") != english:
        fail("question was not the transcript")
    src = (ROOT / "assistant.py").read_text(encoding="utf-8")
    brain = (ROOT / "gemma.py").read_text(encoding="utf-8")
    for banned in ("PL_WORDS", "TOOL_KEYS", "Reply in Polish.", "cursor_asked", "_CURSOR_PHRASES"):
        if banned in src or banned in brain:
            fail("phrase list still present: " + banned)
    header = gemma.tool_header([], [])
    for name in ("remember", "place", "next", "cursor", "stop"):
        if name not in header:
            fail("tool missing from prompt: " + name)
    if "do not call a tool" not in header:
        fail("prompt does not keep a tools question as speech")
    if "language of the user's words" not in header:
        fail("prompt does not leave language to the model")
    if "No code agent has been started." not in header:
        fail("prompt has no code-agent fact")
    print("ok question")


def check_post_body():
    posted = []
    spoken = []
    real = (
        assistant.place_turn,
        assistant.fetch_reply,
        assistant.say_text,
        assistant.remember_turn,
        gemma.run_cursor_job,
    )

    def fake_place(args):
        del args
        return gemma.Place("cpu", "", None, "Intel", "unknown", False, "local")

    def fake_fetch(py, found, args, question):
        del py, found, args
        posted.append(question)
        return "The lamp is on.\n"

    def fake_say(args, raw, hold, flip, play=True):
        del args, hold, flip, play
        spoken.append(raw)
        return []

    def fake_remember(user, spoken_text):
        del user, spoken_text

    def no_cursor(*args, **kwargs):
        del args, kwargs
        fail("cursor started from a question")

    args = argparse.Namespace(once=True, model="nano", brain="qwen", image=None, timeout=5)
    assistant.place_turn = fake_place
    assistant.fetch_reply = fake_fetch
    assistant.say_text = fake_say
    assistant.remember_turn = fake_remember
    gemma.run_cursor_job = no_cursor
    try:
        if not assistant.turn_after_transcript(None, args, "   ", None, False):
            pass
        if posted:
            fail("empty transcript posted")
        posted.clear()
        assistant.turn_after_transcript(None, args, "What time is it?", "pl-PL", False)
        assistant.turn_after_transcript(None, args, "Jaka jest pogoda?", None, False)
        assistant.turn_after_transcript(None, args, "what tools do you have", None, False)
        assistant.turn_after_transcript(None, args, "please use cursor to fix the code", None, False)
    finally:
        (
            assistant.place_turn,
            assistant.fetch_reply,
            assistant.say_text,
            assistant.remember_turn,
            gemma.run_cursor_job,
        ) = real
    if posted != [
        "What time is it?",
        "Jaka jest pogoda?",
        "what tools do you have",
        "please use cursor to fix the code",
    ]:
        fail("post body " + " | ".join(posted))
    if gemma.parse_tool_call("please use cursor to fix the code") is not None:
        fail("keyword became a tool call")
    if not assistant.organism_stop('<|tool_call>call:stop{}<tool_call|>'):
        fail("stop tool")
    if assistant.organism_stop("Please stop listening."):
        fail("plain sentence counted as stop")
    print("ok post")


def check_wav():
    with tempfile.TemporaryDirectory(prefix="vad_") as tmp:
        folder = Path(tmp)
        short_quiet = folder / "short-quiet.wav"
        short_loud = folder / "short-loud.wav"
        long_quiet = folder / "long-quiet.wav"
        long_loud = folder / "long-loud.wav"
        write_wav(short_quiet, 1600, 16000, False)
        write_wav(short_loud, 1600, 16000, True)
        write_wav(long_quiet, 16000, 16000, False)
        write_wav(long_loud, 16000, 16000, True)
        if assistant.judge_wav(short_quiet)[0] != "short":
            fail("short quiet kept")
        if assistant.judge_wav(short_loud)[0] != "short":
            fail("click kept")
        if assistant.judge_wav(long_quiet)[0] != "quiet":
            fail("silence kept")
        kind, ms = assistant.judge_wav(long_loud)
        if kind != "keep" or ms < 400:
            fail("speech dropped " + kind)
    print("ok wav")


def card_value(name):
    for line in (ROOT / "vad.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + " "):
            return line.split(" ", 1)[1].strip()
    fail("missing " + name)


def check_card():
    if float(card_value("vad.threshold")) < 0.65:
        fail("threshold")
    if int(card_value("vad.min-silence-ms")) < 700:
        fail("hangover")
    print("ok card")


def check_hold():
    path = ROOT / "vad.hold"
    if path.exists():
        fail("vad.hold already present")
    assistant.HOLD_DEPTH = 0
    try:
        assistant.set_hold(True)
        assistant.set_hold(True)
        assistant.set_hold(False)
        if not path.is_file():
            fail("inner release opened the mic")
        assistant.set_hold(False)
        if path.exists():
            fail("hold left behind")
    finally:
        assistant.HOLD_DEPTH = 0
        if path.exists():
            path.unlink()
    print("ok hold")


def check_hot():
    with tempfile.TemporaryDirectory(prefix="seat_") as tmp:
        path = Path(tmp) / "iris_status.txt"
        path.write_text("say The kettle finished.\n", encoding="utf-8")
        args = argparse.Namespace(once=True, model="nano", brain="qwen", image=None, timeout=5)
        ok = assistant.drain_file(None, args, path, False, time.monotonic() + 30)
        if not ok:
            fail("hot seat stopped")
        if path.read_text(encoding="utf-8") != "say The kettle finished.\n":
            fail("hot seat claimed the line")
    print("ok hot")


def assert_cursor_idle():
    data = gemma.read_cursor_status()
    if not data:
        return
    try:
        pid = int(data.get("pid", "0"))
    except ValueError:
        pid = 0
    if pid and gemma.cursor_state(pid) == "alive":
        fail("cursor.status.txt already has a live pid")
    try:
        gemma.CURSOR_STATUS.unlink()
    except OSError:
        pass


def kill_pid(pid):
    if not pid:
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        cwd=str(ROOT),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def check_cursor():
    assert_cursor_idle()
    sleeper = None
    started = 0
    try:
        with tempfile.TemporaryDirectory(prefix="spawn_") as tmp:
            spawn = Path(tmp) / "spawn.txt"
            blocked = gemma.run_cursor_job("fix the code", agent_path="", spawn_path=spawn)
            if blocked != "BLOCKED":
                fail("missing cli returned " + blocked)
            if "BLOCKED" not in spawn.read_text(encoding="utf-8"):
                fail("spawn note")
            if gemma.CURSOR_STATUS.is_file():
                fail("BLOCKED wrote a pid")
            started_text = gemma.run_cursor_job(
                "fix the code",
                agent_path=sys.executable,
                spawn_path=Path(tmp) / "start.txt",
            )
        if not started_text.startswith("started local pid "):
            fail("start " + started_text)
        started = int(started_text.rsplit(" ", 1)[1])
        data = gemma.read_cursor_status()
        if data is None or int(data["pid"]) != started:
            fail("status pid")
        if data.get("task") != "fix the code":
            fail("status task")
        if data.get("model") != gemma.CURSOR_MODEL:
            fail("status model")
        if data.get("cwd") != str(ROOT):
            fail("status cwd")
        speech = gemma.cursor_status_speech()
        if str(started) not in speech or "fix the code" not in speech:
            fail("speech " + speech)
    finally:
        kill_pid(started)
        if gemma.CURSOR_STATUS.is_file():
            try:
                gemma.CURSOR_STATUS.unlink()
            except OSError:
                pass
    try:
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=str(ROOT),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        gemma.write_cursor_status(sleeper.pid, "fix the code")
        if gemma.cursor_state(sleeper.pid) != "alive":
            fail("sleeper not alive")
        speech = gemma.cursor_status_speech()
        if "alive" not in speech or str(sleeper.pid) not in speech:
            fail("alive speech " + speech)
        with tempfile.TemporaryDirectory(prefix="spawn_") as tmp:
            busy = gemma.run_cursor_job(
                "fix the code",
                agent_path=sys.executable,
                spawn_path=Path(tmp) / "spawn.txt",
            )
        if busy != "busy":
            fail("cap returned " + busy)
        if int(gemma.read_cursor_status()["pid"]) != sleeper.pid:
            fail("cap replaced the pid")
    finally:
        if sleeper is not None:
            kill_pid(sleeper.pid)
        if gemma.CURSOR_STATUS.is_file():
            speech = ""
            deadline = time.time() + 3
            while time.time() < deadline:
                speech = gemma.cursor_status_speech()
                if sleeper is None or "not running" in speech:
                    break
                time.sleep(0.05)
            if sleeper is not None and "not running" not in speech:
                fail("dead speech " + speech)
            try:
                gemma.CURSOR_STATUS.unlink()
            except OSError:
                pass
    print("ok cursor")


def check_stop(before):
    text = inspect.getsource(run.cmd_stop) + "\n" + inspect.getsource(assistant.stop_tree)
    for banned in ("8765", "nvidia_stop", "nvidia_worker", "nvidia_start"):
        if banned in text:
            fail("stop mentions " + banned)
    saved = os.environ.get("TRIDENT_NVIDIA_URL")
    os.environ.pop("TRIDENT_NVIDIA_URL", None)
    try:
        command, url, inject = run.parse_args(["start"])
    finally:
        if saved is not None:
            os.environ["TRIDENT_NVIDIA_URL"] = saved
    if command != "start" or inject is not None or url != "http://192.168.16.31:8765/":
        fail("default url " + url)
    after = snapshot()
    if after != before:
        fail("port state changed")
    if after[("127.0.0.1", 8765)]:
        fail("iris bound 8765")
    print("ok stop")


def main():
    before = snapshot()
    assert_cursor_idle()
    check_question()
    check_post_body()
    check_wav()
    check_card()
    check_hold()
    check_hot()
    check_cursor()
    check_stop(before)
    print("STATUS PASS")


if __name__ == "__main__":
    main()
