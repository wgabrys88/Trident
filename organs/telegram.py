"""The owner's Telegram messages and the voice call.

The account is taken from Telegram Desktop tdata. Desktop stays closed while the session is held and is started again from stop().

start() connects, answers his calls, and reads his messages. dial() places the call.
speak() writes 48 kHz signed-16 mono into the call. send_text and send_photo go to the chat.
hang() ends the call. stop() drops the session.

on_text runs on its own thread with his message.
on_utterance runs on the receive thread with his call audio: float32, 16 kHz, already cut into an utterance.
on_line runs on the asyncio thread with idle, ringing, or up, and should return at once.

python -m organs.telegram connects, prints what arrives, and picks up a call without sending speech.
"""

import asyncio
import ctypes
import io
import os
import random
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
from ntgcalls import (
    AudioDescription, ConnectionState, DhConfig, FrameData, MediaDescription, MediaSource, NTgCalls,
    RTCServer, StreamDevice, StreamMode, VideoDescription, VIDEO_ROTATION_0,
)
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from PIL import Image, ImageGrab
from telethon import events
from telethon.sessions import MemorySession
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest, RequestCallRequest, SendSignalingDataRequest
from telethon.tl.types import (
    InputPhoneCall, PhoneCall, PhoneCallAccepted, PhoneCallDiscarded, PhoneCallDiscardReasonHangup, PhoneCallDiscardReasonMissed,
    PhoneCallProtocol, PhoneCallRequested, PhoneConnection, PhoneConnectionWebrtc, UpdatePhoneCall, UpdatePhoneCallSignalingData,
)

from organs import CONFIG, log
from organs.ears import Segmenter

LOG = log("telegram")
CFG = CONFIG["telegram"]
OWNER = CONFIG["owner"]["telegram_id"]
RATE_TX = 48000
RATE_RX = 16000
FRAME_TX = RATE_TX // 100 * 2
DESK_W, DESK_H = 960, 540


def telegram_home() -> Path:
    for key in ("APPDATA", "LOCALAPPDATA"):
        home = Path(os.environ[key]) / "Telegram Desktop"
        if (home / "Telegram.exe").is_file() and (home / "tdata").is_dir():
            return home
    found = shutil.which("Telegram.exe")
    if found and (Path(found).parent / "tdata").is_dir():
        return Path(found).parent
    raise FileNotFoundError("Telegram Desktop with tdata not found")


def telegram_pids() -> list[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Telegram.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    return [int(line.split(",")[1].strip('"')) for line in out.splitlines() if "Telegram.exe" in line]


def quit_telegram():
    pids = set(telegram_pids())
    if not pids:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    user32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
    threads = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        pid = ctypes.c_ulong()
        thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and thread:
            threads.add(thread)
        return True

    user32.EnumWindows(visit, 0)
    for thread in threads:
        user32.PostThreadMessageW(thread, 0x0012, 0, 0)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and telegram_pids():
        time.sleep(0.2)
    if telegram_pids():
        raise RuntimeError("Telegram Desktop did not quit")
    LOG.info("telegram desktop closed")


def start_telegram():
    if telegram_pids():
        return
    home = telegram_home()
    subprocess.Popen([str(home / "Telegram.exe")], cwd=str(home))
    LOG.info("telegram desktop reopened")


def desk_i420() -> bytes:
    rgb = np.asarray(ImageGrab.grab().convert("RGB").resize((DESK_W, DESK_H), Image.BILINEAR), dtype=np.int32)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    y = np.clip(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16, 16, 235).astype(np.uint8)
    r2 = (r[0::2, 0::2] + r[1::2, 0::2] + r[0::2, 1::2] + r[1::2, 1::2] + 2) >> 2
    g2 = (g[0::2, 0::2] + g[1::2, 0::2] + g[0::2, 1::2] + g[1::2, 1::2] + 2) >> 2
    b2 = (b[0::2, 0::2] + b[1::2, 0::2] + b[0::2, 1::2] + b[1::2, 1::2] + 2) >> 2
    u = np.clip(((-38 * r2 - 74 * g2 + 112 * b2 + 128) >> 8) + 128, 16, 240).astype(np.uint8)
    v = np.clip(((112 * r2 - 94 * g2 - 18 * b2 + 128) >> 8) + 128, 16, 240).astype(np.uint8)
    return y.tobytes() + u.tobytes() + v.tobytes()


def rtc_servers(connections) -> list[RTCServer]:
    servers = []
    for item in connections:
        if isinstance(item, PhoneConnectionWebrtc):
            servers.append(RTCServer(item.id, item.ip, item.ipv6 or "", item.port, item.username, item.password, item.turn, item.stun, False, None))
        elif isinstance(item, PhoneConnection):
            servers.append(RTCServer(item.id, item.ip, item.ipv6 or "", item.port, None, None, False, True, item.tcp, bytes(item.peer_tag)))
    return servers


def media(rate: int, camera: bool) -> MediaDescription:
    video = VideoDescription(MediaSource.EXTERNAL, DESK_W, DESK_H, CFG["desk_fps"], "", True) if camera else None
    return MediaDescription(microphone=AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True), speaker=None, camera=video, screen=None)


def wire_protocol() -> PhoneCallProtocol:
    protocol = NTgCalls.get_protocol()
    return PhoneCallProtocol(udp_p2p=protocol.udp_p2p, udp_reflector=protocol.udp_reflector, min_layer=65, max_layer=92, library_versions=list(reversed(protocol.library_versions)))


class Line:
    def __init__(self, on_text: Callable[[str], None], on_utterance: Callable[[np.ndarray], None], on_line: Callable[[str], None]):
        self.on_text, self.on_utterance, self.on_line = on_text, on_utterance, on_line
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.owner = None
        self.calls = None
        self.phone: InputPhoneCall | None = None
        self.state = "idle"
        self.began = 0.0
        self.media_up = False
        self.queued_signaling: list[bytes] = []
        self.connected = None
        self.accepted = None
        self.confirmed = None
        self.rx = bytearray()
        self.rx_lock = threading.Lock()
        self.rx_event = threading.Event()
        self.listening = False
        self.segmenter = Segmenter()

    def start(self):
        quit_telegram()
        threading.Thread(target=self._run_loop, name="telegram-loop", daemon=True).start()
        threading.Thread(target=self._rx_worker, name="telegram-rx", daemon=True).start()
        self._await(self._connect(), 180)

    def stop(self):
        if self.state != "idle":
            self.hang()
        if self.client is not None:
            self._await(self._disconnect(), 20)
            start_telegram()
        self.loop.call_soon_threadsafe(self.loop.stop)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _await(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _disconnect(self):
        await self.client.disconnect()

    def _set(self, state: str):
        if state != self.state:
            self.state = state
            LOG.info("line %s", state)
            self.on_line(state)

    @property
    def up(self) -> bool:
        return self.state == "up"

    async def _connect(self):
        tdesk = TDesktop(str(telegram_home() / "tdata"))
        account = next(a for a in tdesk.accounts if int(a.UserId) != OWNER)
        self.client = await TelegramClient.FromTDesktop(account, session=MemorySession(), flag=UseCurrentSession, api=API.TelegramDesktop)
        self.client.add_event_handler(self._on_raw, events.Raw())
        self.client.add_event_handler(self._on_message, events.NewMessage(incoming=True, from_users=[OWNER]))
        await self.client.connect()
        me = await self.client.get_me()
        await self.client.get_dialogs()
        self.owner = await self.client.get_input_entity(OWNER)
        LOG.info("connected as %s %s", me.first_name, me.id)
        self._set("idle")

    async def _on_message(self, event):
        if event.is_private and event.raw_text.strip():
            text = event.raw_text.strip()
            LOG.info("owner wrote: %s", text)
            threading.Thread(target=self.on_text, args=(text,), daemon=True).start()

    def send_text(self, text: str):
        if self.client is None:
            return
        self._await(self.client.send_message(self.owner, text), 30)

    def send_photo(self, png: bytes, caption: str = ""):
        if self.client is None:
            return
        buffer = io.BytesIO(png)
        buffer.name = "desk.png"
        self._await(self.client.send_file(self.owner, buffer, caption=caption, force_document=False), 60)

    def send_file(self, payload: bytes, name: str):
        if self.client is None:
            return
        buffer = io.BytesIO(payload)
        buffer.name = name
        self._await(self.client.send_file(self.owner, buffer, force_document=True), 60)

    async def _on_raw(self, update):
        if isinstance(update, UpdatePhoneCallSignalingData):
            if self.media_up:
                await self.calls.send_signaling_data(OWNER, bytes(update.data))
            else:
                self.queued_signaling.append(bytes(update.data))
            return
        if not isinstance(update, UpdatePhoneCall):
            return
        call = update.phone_call
        if isinstance(call, PhoneCallRequested):
            if call.admin_id != OWNER or self.state != "idle":
                await self.client(DiscardCallRequest(peer=InputPhoneCall(call.id, call.access_hash), duration=0, reason=PhoneCallDiscardReasonMissed(), connection_id=0, video=True))
                return
            asyncio.ensure_future(self._answer(call))
            return
        if isinstance(call, (PhoneCallAccepted, PhoneCall)):
            self.phone = InputPhoneCall(call.id, call.access_hash)
        if isinstance(call, PhoneCallAccepted) and self.accepted is not None:
            self.accepted.set_result(call)
        if isinstance(call, PhoneCall) and self.confirmed is not None and not self.confirmed.done():
            self.confirmed.set_result(call)
        if isinstance(call, PhoneCallDiscarded):
            LOG.info("call discarded by peer")
            await self._teardown()

    async def _dh(self) -> DhConfig:
        cfg = await self.client(GetDhConfigRequest(0, 256))
        return DhConfig(cfg.g, bytes(cfg.p), bytes(cfg.random))

    async def _begin_media(self):
        self.calls = NTgCalls()
        self.calls.on_frames(self._on_frames)
        self.calls.on_connection_change(self._on_connection)
        self.calls.on_signaling_data(lambda _uid, data: asyncio.run_coroutine_threadsafe(self.client(SendSignalingDataRequest(peer=self.phone, data=bytes(data))), self.loop))
        self.connected = self.loop.create_future()
        self.media_up = False
        self.queued_signaling = []
        self.segmenter.reset()
        await self.calls.create_p2p_call(OWNER)
        await self.calls.set_stream_sources(OWNER, StreamMode.CAPTURE, media(RATE_TX, CFG["desk_video"]))
        await self.calls.set_stream_sources(OWNER, StreamMode.PLAYBACK, media(RATE_RX, False))

    async def _link(self, call: PhoneCall):
        self.phone = InputPhoneCall(call.id, call.access_hash)
        custom = call.custom_parameters.data if call.custom_parameters else None
        await self.calls.connect_p2p(OWNER, rtc_servers(call.connections), list(call.protocol.library_versions), call.p2p_allowed, custom)
        self.media_up = True
        for blob in self.queued_signaling:
            await self.calls.send_signaling_data(OWNER, blob)
        self.queued_signaling = []
        await asyncio.wait_for(self.connected, 30)
        self.began = time.time()
        self.listening = True
        self._set("up")
        if CFG["desk_video"]:
            threading.Thread(target=self._desk_video, name="telegram-desk", daemon=True).start()

    async def _answer(self, requested: PhoneCallRequested):
        self._set("ringing")
        try:
            await self._begin_media()
            self.phone = InputPhoneCall(requested.id, requested.access_hash)
            g_b = bytes(await self.calls.init_exchange(OWNER, await self._dh(), bytes(requested.g_a_hash)))
            self.confirmed = self.loop.create_future()
            answered = await self.client(AcceptCallRequest(peer=self.phone, g_b=g_b, protocol=wire_protocol()))
            call = answered.phone_call if isinstance(answered.phone_call, PhoneCall) else await asyncio.wait_for(self.confirmed, 30)
            await self.calls.exchange_keys(OWNER, bytes(call.g_a_or_b), call.key_fingerprint)
            await self._link(call)
        except Exception as exc:
            LOG.error("answer failed: %s", exc)
            await self._teardown()

    async def _place(self):
        self._set("ringing")
        await self._begin_media()
        g_a_hash = bytes(await self.calls.init_exchange(OWNER, await self._dh(), None))
        self.accepted = self.loop.create_future()
        invited = await self.client(RequestCallRequest(user_id=self.owner, random_id=random.randint(0, 2**31 - 1), g_a_hash=g_a_hash, protocol=wire_protocol(), video=True))
        if isinstance(invited.phone_call, PhoneCallDiscarded):
            raise RuntimeError("call discarded")
        self.phone = InputPhoneCall(invited.phone_call.id, invited.phone_call.access_hash)
        try:
            accepted = await asyncio.wait_for(self.accepted, 90)
        except asyncio.TimeoutError:
            await self.client(DiscardCallRequest(peer=self.phone, duration=0, reason=PhoneCallDiscardReasonMissed(), connection_id=0, video=True))
            raise RuntimeError("call missed")
        auth = await self.calls.exchange_keys(OWNER, bytes(accepted.g_b), 0)
        confirmed = await self.client(ConfirmCallRequest(peer=self.phone, g_a=bytes(auth.g_a_or_b), key_fingerprint=auth.key_fingerprint, protocol=wire_protocol()))
        await self._link(confirmed.phone_call)

    async def _teardown(self):
        self.listening = False
        self.media_up = False
        calls, phone, began = self.calls, self.phone, self.began
        self.calls, self.phone = None, None
        if calls is not None:
            try:
                await calls.stop(OWNER)
            except Exception:
                pass
        if phone is not None and self.client.is_connected():
            try:
                await asyncio.wait_for(self.client(DiscardCallRequest(peer=phone, duration=int(time.time() - began) if began else 0, reason=PhoneCallDiscardReasonHangup(), connection_id=0, video=True)), 8)
            except Exception:
                pass
        with self.rx_lock:
            self.rx.clear()
        self._set("idle")

    def dial(self):
        if self.client is None:
            raise RuntimeError("line is down")
        if self.state != "idle":
            raise RuntimeError(f"line is {self.state}")
        try:
            self._await(self._place(), 150)
        except BaseException:
            self._await(self._teardown(), 20)
            raise

    def hang(self):
        if self.state != "idle":
            self._await(self._teardown(), 20)

    def _on_connection(self, _uid, info):
        LOG.info("link %s", str(info.state).rsplit(".", 1)[-1])
        if info.state == ConnectionState.CONNECTED and not self.connected.done():
            self.loop.call_soon_threadsafe(self.connected.set_result, True)
        if info.state in (ConnectionState.CLOSED, ConnectionState.FAILED, ConnectionState.TIMEOUT) and self.state == "up":
            asyncio.run_coroutine_threadsafe(self._teardown(), self.loop)

    def _on_frames(self, _uid, mode, _device, frames):
        if mode != StreamMode.PLAYBACK or not self.listening:
            return
        with self.rx_lock:
            for frame in frames:
                self.rx.extend(frame.data)
        self.rx_event.set()

    def _rx_worker(self):
        while True:
            self.rx_event.wait()
            self.rx_event.clear()
            with self.rx_lock:
                data, self.rx = bytes(self.rx), bytearray()
            if not data or not self.listening:
                continue
            clip = self.segmenter.push(np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0)
            if clip is not None:
                LOG.info("owner spoke %.1fs", clip.size / RATE_RX)
                self.on_utterance(clip)

    async def _send_frame(self, device, data, frame):
        await self.calls.send_external_frame(OWNER, device, data, frame)

    def speak(self, pcm48: bytes):
        """Block until this PCM has been sent into the call. Incoming audio is dropped until that send finishes. The call must already be up."""
        if not self.up:
            raise RuntimeError("line is down")
        self.listening = False
        try:
            start = time.perf_counter()
            for n, offset in enumerate(range(0, len(pcm48), FRAME_TX), start=1):
                chunk = pcm48[offset : offset + FRAME_TX].ljust(FRAME_TX, b"\0")
                self._await(self._send_frame(StreamDevice.MICROPHONE, chunk, FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, 0, 0)), 5)
                delay = start + n * 0.01 - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
        finally:
            with self.rx_lock:
                self.rx.clear()
            self.segmenter.reset()
            self.listening = self.up

    def _desk_video(self):
        while self.up and self.calls is not None:
            try:
                self._await(self._send_frame(StreamDevice.CAMERA, desk_i420(), FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, DESK_W, DESK_H)), 3)
            except Exception:
                return
            time.sleep(1.0 / CFG["desk_fps"])


if __name__ == "__main__":
    line = Line(on_text=lambda t: print("text:", t), on_utterance=lambda c: print("speech", c.size / RATE_RX, "s"), on_line=lambda s: print("line:", s))
    line.start()
    print("connected, Ctrl+C to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        line.stop()
