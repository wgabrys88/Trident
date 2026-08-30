from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd


ROOT = Path(__file__).resolve().parent
FIRST_TEXT = "Count from twenty five to forty, one number per sentence."
INTERRUPT_TEXT = "Stop and tell me what color the sky is."


def device_index(prefix: str, input_device: bool) -> int:
    channel = "max_input_channels" if input_device else "max_output_channels"
    for index, device in enumerate(sd.query_devices()):
        if device["hostapi"] == 0 and device["name"].startswith(prefix) and device[channel] > 0:
            return index
    raise RuntimeError(f"MME audio endpoint starting with {prefix!r} was not found")


def playback_index() -> int:
    for index, device in enumerate(sd.query_devices()):
        if device["hostapi"] != 0 or device["max_output_channels"] < 1 or device["name"].startswith("CABLE"):
            continue
        try:
            sd.check_output_settings(device=index, samplerate=24000, channels=1, dtype="int16")
            return index
        except sd.PortAudioError:
            pass
    raise RuntimeError("no non-cable MME output accepts Trident's 24 kHz mono stream")


def synthesize(text: str, target: Path) -> None:
    encoded_text = base64.b64encode(text.encode()).decode()
    encoded_path = base64.b64encode(str(target).encode()).decode()
    script = f"""
Add-Type -AssemblyName System.Speech
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_text}'))
$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_path}'))
$voice = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$voice.SelectVoice('Microsoft David Desktop')
$voice.Rate = 0
$voice.Volume = 100
$voice.SetOutputToWaveFile($path)
$voice.Speak($text)
$voice.Dispose()
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
    )


def load_audio(path: Path, target_rate: int = 44100) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        if width != 2:
            raise RuntimeError(f"unexpected fixture sample width: {width}")
        audio = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    audio = audio.reshape(-1, channels).mean(axis=1)
    count = round(len(audio) * target_rate / rate)
    positions = np.arange(count, dtype=np.float64) * rate / target_rate
    audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
    silence = np.zeros(round(target_rate * 0.9), dtype=np.float32)
    audio = np.concatenate((silence[: round(target_rate * 0.25)], audio, silence))
    return np.column_stack((audio, audio))


def inject(path: Path, cable_input: int) -> None:
    audio = load_audio(path)
    with sd.OutputStream(device=cable_input, samplerate=44100, channels=2, dtype="float32", latency="low") as stream:
        for offset in range(0, len(audio), 1024):
            stream.write(audio[offset : offset + 1024])


def events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    parsed = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return parsed


def wait_for(path: Path, predicate, description: str, timeout: float) -> tuple[dict, list[dict]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = events(path)
        found = next((item for item in current if predicate(item)), None)
        if found is not None:
            return found, current
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {description}")


def interrupt_process(process: subprocess.Popen) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(process.pid):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel32.SetConsoleCtrlHandler(None, True)
    try:
        if not kernel32.GenerateConsoleCtrlEvent(0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        process.wait(timeout=60)
    finally:
        kernel32.FreeConsole()


def resident_processes() -> list[dict]:
    encoded_root = base64.b64encode(str(ROOT).encode()).decode()
    script = f"""
$root = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_root}'))
Get-CimInstance Win32_Process | Where-Object {{
    $_.Name -in @('parakeet-server.exe', 'llama-server.exe', 'trident-tts-server.exe') -and
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)
}} | Select-Object ProcessId, Name, ExecutablePath | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return []
    parsed = json.loads(result.stdout)
    return parsed if isinstance(parsed, list) else [parsed]


def cleanup_residents() -> None:
    for _ in range(3):
        found = resident_processes()
        if not found:
            return
        for process in found:
            subprocess.run(
                ["taskkill", "/PID", str(process["ProcessId"]), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        time.sleep(0.5)


def wait_for_resident_exit(timeout: float = 10) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = resident_processes()
        if not found:
            return []
        time.sleep(0.2)
    return resident_processes()


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic VB-CABLE talk and barge-in test")
    parser.add_argument("--interrupt-delay-ms", type=int, default=750)
    parser.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args()

    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError(f"Trident virtual environment is missing: {python}")
    cable_output = device_index("CABLE Output", True)
    cable_input = device_index("CABLE Input", False)
    speakers = playback_index()
    sd.check_input_settings(device=cable_output, samplerate=16000, channels=1, dtype="float32")
    sd.check_output_settings(device=cable_input, samplerate=44100, channels=2, dtype="float32")
    stale = resident_processes()
    if stale:
        raise RuntimeError(f"Trident resident processes already exist: {stale}")

    started = time.time()
    existing = set((ROOT / "data" / "runs").glob("*-talk-irisxe-nano-en-*"))
    with tempfile.TemporaryDirectory(prefix="trident-deterministic-") as temporary:
        temporary = Path(temporary)
        first_wav, interrupt_wav = temporary / "first.wav", temporary / "interrupt.wav"
        console_log = temporary / "console.log"
        synthesize(FIRST_TEXT, first_wav)
        synthesize(INTERRUPT_TEXT, interrupt_wav)
        first_hash = __import__("hashlib").sha256(first_wav.read_bytes()).hexdigest()
        interrupt_hash = __import__("hashlib").sha256(interrupt_wav.read_bytes()).hexdigest()

        child = (
            "import runpy,sounddevice as sd,sys;"
            f"sd.default.device=({cable_output},{speakers});"
            f"sys.argv=['main.py','talk','--family','nano'];"
            f"runpy.run_path({str(ROOT / 'main.py')!r},run_name='__main__')"
        )
        with console_log.open("w", encoding="utf-8") as console:
            process = subprocess.Popen(
                [str(python), "-c", child],
                cwd=ROOT,
                stdout=console,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        run_dir = None
        event_log = None
        failure = None
        try:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline and run_dir is None:
                candidates = set((ROOT / "data" / "runs").glob("*-talk-irisxe-nano-en-*")) - existing
                if candidates:
                    candidate = max(candidates, key=lambda item: item.stat().st_mtime_ns)
                    logs = tuple(candidate.glob("*-events.jsonl"))
                    if logs:
                        run_dir, event_log = candidate, logs[0]
                        break
                if process.poll() is not None:
                    raise RuntimeError(f"Trident exited before creating a run: {process.returncode}")
                time.sleep(0.1)
            if run_dir is None or event_log is None:
                raise TimeoutError("Trident did not create a talk run")

            wait_for(event_log, lambda e: e.get("event") == "audio.open", "audio.open", args.timeout)
            inject(first_wav, cable_input)
            first_asr, _ = wait_for(
                event_log,
                lambda e: e.get("event") == "asr.done" and e.get("accepted") and "count" in e.get("text", "").lower(),
                "accepted count transcription",
                args.timeout,
            )
            first_pcm, _ = wait_for(
                event_log,
                lambda e: e.get("event") == "pcm.first" and e.get("epoch") == first_asr.get("epoch"),
                "first response PCM",
                args.timeout,
            )
            time.sleep(args.interrupt_delay_ms / 1000)
            inject(interrupt_wav, cable_input)
            second_asr, _ = wait_for(
                event_log,
                lambda e: e.get("event") == "asr.done" and e.get("accepted") and e.get("epoch", 0) > first_asr.get("epoch", 0) and "sky" in e.get("text", "").lower(),
                "accepted interruption transcription",
                args.timeout,
            )
            wait_for(
                event_log,
                lambda e: e.get("event") == "barge_in" and e.get("epoch") == second_asr.get("epoch"),
                "barge-in event",
                args.timeout,
            )
            wait_for(
                event_log,
                lambda e: e.get("event") == "pcm.first" and e.get("epoch") == second_asr.get("epoch"),
                "interruption response PCM",
                args.timeout,
            )
            time.sleep(1.0)
            interrupt_process(process)
            leaked = wait_for_resident_exit()
            if leaked:
                cleanup_residents()
                raise RuntimeError(f"Trident resident processes survived shutdown: {leaked}")
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cleanup_residents()
            raise
        finally:
            if run_dir is not None:
                (run_dir / "deterministic-test-console.log").write_text(console_log.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                final_events = events(event_log) if event_log is not None else []
                batches = [e for e in final_events if e.get("event") == "tts.batch.begin"]
                t3 = [e for e in final_events if e.get("event") == "t3"]
                s3_starts = [e for e in final_events if e.get("event") == "s3gen.begin" and e.get("chunk_id") == 0]
                s3_chunks = [e for e in final_events if e.get("event") == "s3gen" and e.get("chunk_id") == 0]
                pcm = [e for e in final_events if e.get("event") == "pcm.first"]
                llm = [e for e in final_events if e.get("event") == "llm.done" and not e.get("empty")]
                fail_events = [e for e in final_events if e.get("event") in ("fail", "failure")]
                graceful = all(any(e.get("event") == name for e in final_events) for name in ("shutdown.begin", "shutdown.done", "console.interrupt"))
                assertions = {
                    "accepted_asr_turns": sum(e.get("event") == "asr.done" and bool(e.get("accepted")) for e in final_events),
                    "barge_in_events": sum(e.get("event") == "barge_in" for e in final_events),
                    "all_batches_single_piece": bool(batches) and all(e.get("pieces") == 1 for e in batches),
                    "all_t3_streaming": bool(t3) and all(e.get("stream") is True for e in t3),
                    "s3_first_chunk_tokens": [e.get("tokens") for e in s3_starts],
                    "pcm_first_epochs": [e.get("epoch") for e in pcm],
                    "graceful_shutdown": graceful,
                    "resident_processes_after_shutdown": resident_processes(),
                    "fail_events": fail_events,
                }
                functional_passed = (
                    failure is None
                    and assertions["accepted_asr_turns"] >= 2
                    and assertions["barge_in_events"] >= 1
                    and assertions["all_batches_single_piece"]
                    and assertions["all_t3_streaming"]
                    and bool(assertions["s3_first_chunk_tokens"])
                    and all(token == 12 for token in assertions["s3_first_chunk_tokens"])
                    and len(set(assertions["pcm_first_epochs"])) >= 2
                    and graceful
                    and not assertions["resident_processes_after_shutdown"]
                    and not fail_events
                )
                report = {
                    "passed": functional_passed,
                    "scope": "deterministic functional voice and barge-in test",
                    "failure": failure,
                    "started_unix": started,
                    "run_dir": str(run_dir),
                    "fixtures": {"voice": "Microsoft David Desktop", "first_text": FIRST_TEXT, "first_sha256": first_hash, "interrupt_text": INTERRUPT_TEXT, "interrupt_sha256": interrupt_hash},
                    "routing": {"capture": sd.query_devices(cable_output)["name"], "inject": sd.query_devices(cable_input)["name"], "playback": sd.query_devices(speakers)["name"]},
                    "interrupt_delay_ms_after_first_pcm": args.interrupt_delay_ms,
                    "assertions": assertions,
                    "performance_observations": {
                        "first_chunk_rtf": [e.get("rtf") for e in s3_chunks],
                        "max_native_queue_ms": max((e.get("queue_ms", 0) for e in batches), default=0),
                        "pcm_before_llm_done": [bool(e.get("pcm_first")) for e in llm],
                    },
                }
                (run_dir / "deterministic-test-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
