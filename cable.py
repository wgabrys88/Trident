from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from config import CABLE_INPUT, CABLE_OUTPUT, TOOLS
from log import note

_SCRIPT = TOOLS / "cable.ps1"


def _ps(action: str, endpoint_id: str | None = None) -> str:
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(_SCRIPT), "-Action", action,
    ]
    if endpoint_id:
        command += ["-EndpointId", endpoint_id]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cable.ps1 {action} failed ({result.returncode}): {(result.stderr or result.stdout).strip()[-2000:]}")
    return result.stdout.strip()


def _active_captures() -> dict[str, str]:
    endpoints = {}
    for line in _ps("list").splitlines():
        if "|" in line:
            endpoint, name = line.split("|", 1)
            endpoints[endpoint] = name
    return endpoints


def default_capture_endpoint() -> tuple[str, str]:
    endpoint = _ps("default")
    return endpoint, _active_captures().get(endpoint, "")


def set_default_capture(endpoint_id: str) -> None:
    _ps("set", endpoint_id)
    note(f"component=cable event=default_set endpoint={endpoint_id} roles=console,multimedia")


def _cable_devices() -> dict[str, tuple[int, str]]:
    found: dict[str, tuple[int, str]] = {}
    names: list[str] = []
    for info in sd.query_devices():
        name = str(info["name"])
        names.append(name)
        lowered = name.lower()
        if info["max_output_channels"] > 0 and lowered.startswith(CABLE_INPUT.lower()):
            found["play"] = (int(info["index"]), name)
        if info["max_input_channels"] > 0 and lowered.startswith(CABLE_OUTPUT.lower()):
            found["record"] = (int(info["index"]), name)
    missing = [label for label in ("play", "record") if label not in found]
    if missing:
        raise RuntimeError(f"VB-CABLE endpoints {missing} not found among PortAudio devices: {names}")
    return found


def _target_capture_id() -> str:
    lowered = CABLE_OUTPUT.lower()
    matches = [(endpoint, name) for endpoint, name in _active_captures().items() if name.lower().startswith(lowered)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one active capture endpoint starting with {CABLE_OUTPUT!r}: {matches}")
    return matches[0][0]


def use() -> dict:
    devices = _cable_devices()
    target = _target_capture_id()
    previous_id, previous_name = default_capture_endpoint()
    changed = previous_id != target
    if changed:
        set_default_capture(target)
        current_id, current_name = default_capture_endpoint()
        if current_id != target:
            raise RuntimeError(f"default capture did not switch to {CABLE_OUTPUT}: now {current_name!r} ({current_id})")
        note(f"component=cable event=capture_routed previous={previous_name} current={current_name}")
    return {"previous": previous_id if changed else None, "changed": changed, "devices": devices}


def restore(previous_id: str) -> None:
    if previous_id:
        set_default_capture(previous_id)


def play_wav(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        rate = audio.getframerate()
        frames = audio.readframes(audio.getnframes())
    samples = np.frombuffer(frames, dtype="<i2")
    device = _cable_devices()["play"][0]
    out_rate = int(sd.query_devices(device)["default_samplerate"])
    if out_rate != rate and samples.size:
        count = max(1, round(samples.size * out_rate / rate))
        samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples).astype("<i2")
    duration = len(samples) / out_rate
    sd.play(samples, out_rate, device=device)
    sd.wait()
    note(f"component=cable event=inject device={device} src_rate={rate} out_rate={out_rate} duration_s={duration:.3f}")
    return duration


def status() -> str:
    devices = _cable_devices()
    endpoint, name = default_capture_endpoint()
    return (
        f"play[{devices['play'][0]}] {devices['play'][1]}\n"
        f"record[{devices['record'][0]}] {devices['record'][1]}\n"
        f"default_capture: {name or '?'} ({endpoint})"
    )
