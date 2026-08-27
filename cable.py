from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from config import ASR_RATE, CABLE_INPUT, CABLE_OUTPUT, TOOLS
from log import note

_SCRIPT = TOOLS / "cable.ps1"


def _host_tag(name: str) -> str:
    start = name.rfind("(")
    end = name.rfind(")")
    if 0 <= start < end:
        return name[start + 1:end].strip().lower()
    return ""


def _ps(action: str) -> str:
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(_SCRIPT), "-Action", action,
        ],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
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


def _cable_devices() -> dict[str, tuple[int, str]]:
    names: list[str] = []
    plays: list[tuple[int, str, int, int]] = []
    records: list[tuple[int, str, int]] = []
    listed = list(sd.query_devices())
    for info in listed:
        name = str(info["name"])
        names.append(name)
        index = int(info["index"])
        host = int(info["hostapi"])
        lowered = name.lower()
        if info["max_output_channels"] > 0 and (
            lowered.startswith(CABLE_INPUT.lower()) or lowered.startswith("cable in ")
        ):
            plays.append((index, name, host, 0))
        if info["max_input_channels"] > 0 and lowered.startswith(CABLE_OUTPUT.lower()):
            records.append((index, name, host))
    tags = {_host_tag(name) for _, name, _ in records if _host_tag(name)}
    for info in listed:
        name = str(info["name"])
        if info["max_output_channels"] > 0:
            tag = _host_tag(name)
            if name.lower().startswith("output (") and tag and tag in tags:
                plays.append((int(info["index"]), name, int(info["hostapi"]), 1))
    if not plays or not records:
        missing = [label for label, found in (("play", plays), ("record", records)) if not found]
        raise RuntimeError(f"VB-CABLE endpoints {missing} not found among PortAudio devices: {names}")
    record_by_host = {host: (index, name) for index, name, host in records}
    for index, name, host, _rank in sorted(plays, key=lambda row: (row[3], row[0])):
        if host in record_by_host:
            return {"play": (index, name), "record": record_by_host[host]}
    rec_index, rec_name, _ = records[0]
    play_index, play_name, _, _ = min(plays, key=lambda row: (row[3], row[0]))
    return {"play": (play_index, play_name), "record": (rec_index, rec_name)}


class Microphone:
    def __init__(self, feed) -> None:
        self._feed = feed
        self._device = _cable_devices()["record"][0]
        info = sd.query_devices(self._device)
        self._rate = int(info["default_samplerate"])
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            note(f"component=cable event=mic_overflow detail={status}")
        audio = np.asarray(indata[:, 0], dtype=np.float32)
        if self._rate != ASR_RATE and audio.size:
            count = max(1, round(audio.size * ASR_RATE / self._rate))
            audio = np.interp(np.linspace(0, audio.size - 1, count), np.arange(audio.size), audio).astype(np.float32)
        self._feed(audio.tobytes())

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("cable microphone is already capturing")
        self._stream = sd.InputStream(
            samplerate=self._rate, channels=1, dtype="float32",
            device=self._device, blocksize=int(self._rate * 0.02), callback=self._callback,
        )
        self._stream.start()
        note(f"component=cable event=mic_start device={self._device} rate={self._rate} channels=1")

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
            note("component=cable event=mic_stop")


def wav_pcm(path: Path, rate: int) -> bytes:
    with wave.open(str(path), "rb") as audio:
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        source_rate = audio.getframerate()
    if rate != source_rate and samples.size:
        count = max(1, round(samples.size * rate / source_rate))
        samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples)
    return samples.astype(np.float32, copy=False).tobytes()


def play_wav(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        rate = audio.getframerate()
        channels = audio.getnchannels()
        frames = audio.readframes(audio.getnframes())
    samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels)[:, 0]
    device = _cable_devices()["play"][0]
    info = sd.query_devices(device)
    out_rate = int(info["default_samplerate"])
    out_channels = max(1, int(info["max_output_channels"]))
    if out_rate != rate and samples.size:
        count = max(1, round(samples.size * out_rate / rate))
        samples = np.interp(np.linspace(0, samples.size - 1, count), np.arange(samples.size), samples).astype("<i2")
    duration = len(samples) / out_rate
    if out_channels == 1:
        playback = samples
    else:
        playback = np.zeros((len(samples), out_channels), dtype="<i2")
        playback[:, 0] = samples
    sd.play(playback, out_rate, device=device)
    sd.wait()
    note(
        f"component=cable event=inject device={device} src_rate={rate} out_rate={out_rate}"
        f" out_ch={out_channels} duration_s={duration:.3f}"
    )
    return duration


def status() -> str:
    devices = _cable_devices()
    endpoint, name = default_capture_endpoint()
    return (
        f"play[{devices['play'][0]}] {devices['play'][1]}\n"
        f"record[{devices['record'][0]}] {devices['record'][1]}\n"
        f"default_capture: {name or '?'} ({endpoint})"
    )
