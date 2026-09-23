"""Feed heard events to a running Trident bus (no microphone)."""
import sys
import time

from components.runtime import Http

SERVER = "http://127.0.0.1:8765"
HTTP = Http(SERVER)

SCENARIOS = [
    {
        "name": "exact_read_long_line",
        "language": "en",
        "text": (
            "Jarvis, this is a speech test for turbo mode. "
            "I am not asking you to summarize or explain anything. "
            "Please say aloud exactly as written the following text, word for word, with no additions. "
            "Do not paraphrase, do not greet me first, and do not comment on the request. "
            "Use the speak tool once with the full string below as the text parameter and language en. "
            "If the string is long, still speak all of it in one speak call. "
            "Here is the text to read verbatim: "
            "The quick brown fox jumps over the lazy dog while sixteen musicians tune violins behind a brick warehouse "
            "and a distant train carries graphite, copper wire, and sealed letters across three counties before dawn."
        ),
    },
    {
        "name": "exact_read_numbers",
        "language": "en",
        "text": (
            "Jarvis, another exact-read test. "
            "Say aloud exactly as written the following text and nothing else before or after. "
            "Use one speak call with language en. "
            "Text: "
            "Version two point zero release candidate build four thousand one hundred twelve reports latency "
            "eighty seven milliseconds median and ninety nine point two percent success on retry."
        ),
    },
    {
        "name": "short_command",
        "language": "en",
        "text": (
            "Jarvis, say aloud exactly as written: "
            "Turbo inbox mode confirmed; mouth queue operational."
        ),
    },
    {
        "name": "shutdown",
        "language": "en",
        "text": "Jarvis, after you finish speaking any queued audio, stop Trident now.",
    },
]


def heard(text: str, language: str = "en") -> dict:
    return HTTP.call("POST", "/heard", {"text": text, "language": language, "timestamps": ""}, timeout=120)


def health() -> dict:
    return HTTP.call("GET", "/health", timeout=5)


def main() -> None:
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 150.0
    for index, scenario in enumerate(SCENARIOS, start=1):
        print(f"scenario {index}/{len(SCENARIOS)}: {scenario['name']}", flush=True)
        row = heard(scenario["text"], scenario["language"])
        print("  heard event", row.get("id"), flush=True)
        if scenario["name"] == "shutdown":
            break
        time.sleep(wait)
    print("done feeding; waiting for stop", flush=True)
    for _ in range(120):
        try:
            if health().get("stop"):
                print("trident stop flag set", flush=True)
                return
        except OSError:
            print("trident http down", flush=True)
            return
        time.sleep(2)
    print("timeout waiting for shutdown", flush=True)


if __name__ == "__main__":
    main()
