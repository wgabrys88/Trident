import asyncio
import io
import os
import random
import threading
import time
import numpy as np
from PIL import Image
from ntgcalls import (AudioDescription, ConnectionState, DhConfig, FrameData, MediaDescription,
                     MediaSource, NTgCalls, RTCServer, StreamDevice, StreamMode, VideoDescription, VIDEO_ROTATION_0)
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from telethon.sessions import MemorySession
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import (AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest,
                                       RequestCallRequest, SendSignalingDataRequest)
from telethon.tl.types import (InputPhoneCall, PhoneCall, PhoneCallAccepted, PhoneCallDiscarded,
                              PhoneCallDiscardReasonHangup, PhoneCallDiscardReasonMissed, PhoneCallProtocol,
                              PhoneCallRequested, PhoneConnection, PhoneConnectionWebrtc,
                              UpdatePhoneCall, UpdatePhoneCallSignalingData)
from next import CONFIG
from next.desktop import picture
from next.speech import Segmenter

OWNER = CONFIG["owner"]["telegram_id"]
CFG = CONFIG["telegram"]


def protocol():
    p = NTgCalls.get_protocol()
    return PhoneCallProtocol(udp_p2p=p.udp_p2p, udp_reflector=p.udp_reflector, min_layer=65, max_layer=92,
                             library_versions=list(reversed(p.library_versions)))


def media(rate, size=None):
    camera = VideoDescription(MediaSource.EXTERNAL, *size, CFG["desk_fps"], "", True) if size else None
    return MediaDescription(microphone=AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True),
                            speaker=None, camera=camera, screen=None)


def i420(png):
    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.int32)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    y = np.clip(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16, 16, 235).astype(np.uint8)
    r, g, b = [((c[::2, ::2] + c[1::2, ::2] + c[::2, 1::2] + c[1::2, 1::2] + 2) >> 2) for c in (r, g, b)]
    u = np.clip(((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128, 16, 240).astype(np.uint8)
    v = np.clip(((112 * r - 94 * g - 18 * b + 128) >> 8) + 128, 16, 240).astype(np.uint8)
    return y.tobytes() + u.tobytes() + v.tobytes(), (rgb.shape[1], rgb.shape[0])


class Line:
    def __init__(self, events_queue):
        self.events = events_queue
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="telegram", daemon=True)
        self.client = self.owner = self.calls = self.phone = None
        self.connected = self.accepted = self.confirmed = None
        self.state, self.began = "down", 0
        self.listening = self.linked = False
        self.signals = []
        self.segmenter = Segmenter()

    @property
    def up(self):
        return self.state == "up"

    def wait(self, coroutine, timeout=60):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    def start(self):
        self.thread.start()
        self.wait(self.connect(), 180)

    async def connect(self):
        desktop = TDesktop(os.path.expandvars(CFG["tdata"]))
        [account] = [a for a in desktop.accounts if int(a.UserId) != OWNER]
        self.client = await TelegramClient.FromTDesktop(account, session=MemorySession(),
                                                        flag=UseCurrentSession, api=API.TelegramDesktop)
        self.client.add_event_handler(self.message, events.NewMessage(incoming=True, from_users=[OWNER]))
        self.client.add_event_handler(self.raw, events.Raw())
        await self.client.connect()
        me = await self.client.get_me()
        if me is None or me.bot:
            raise RuntimeError("Telegram requires an authorized user account.")
        await self.client.get_dialogs()
        self.owner = await self.client.get_input_entity(OWNER)

    async def message(self, event):
        if not event.is_private:
            return
        try:
            text = event.raw_text
            if event.document:
                content = (await event.download_media(bytes)).decode("utf-8-sig")
                text = text + "\n" + content if text else content
            if text:
                self.events.put(("text", text))
        except Exception as error:
            await self.client.send_message(self.owner, str(error), parse_mode=None)

    def send_text(self, text):
        for offset in range(0, len(text), 2000):
            self.wait(self.client.send_message(self.owner, text[offset:offset + 2000], parse_mode=None))

    def send_file(self, data, name):
        stream = io.BytesIO(data)
        stream.name = name
        self.wait(self.client.send_file(self.owner, stream, force_document=True))

    async def raw(self, update):
        try:
            if isinstance(update, UpdatePhoneCallSignalingData):
                if self.phone and update.phone_call_id == self.phone.id:
                    if self.linked:
                        await self.calls.send_signaling_data(OWNER, bytes(update.data))
                    else:
                        self.signals.append(bytes(update.data))
                return
            if not isinstance(update, UpdatePhoneCall):
                return
            call = update.phone_call
            if isinstance(call, PhoneCallRequested):
                if call.admin_id == OWNER and self.state == "down":
                    self.state = "ringing"
                    asyncio.create_task(self.answer(call))
                else:
                    await self.client(DiscardCallRequest(peer=InputPhoneCall(call.id, call.access_hash), duration=0,
                                      reason=PhoneCallDiscardReasonMissed(), connection_id=0, video=call.video))
            elif (isinstance(call, PhoneCallAccepted) and call.participant_id == OWNER and
                  self.accepted and not self.accepted.done() and self.state == "ringing"):
                self.phone = InputPhoneCall(call.id, call.access_hash)
                self.accepted.set_result(call)
            elif self.phone and call.id == self.phone.id:
                if isinstance(call, PhoneCall) and self.confirmed and not self.confirmed.done():
                    self.confirmed.set_result(call)
                elif isinstance(call, PhoneCallDiscarded):
                    await self.close()
        except Exception as error:
            await self.client.send_message(self.owner, str(error), parse_mode=None)

    async def dh(self):
        cfg = await self.client(GetDhConfigRequest(0, 256))
        return DhConfig(cfg.g, bytes(cfg.p), bytes(cfg.random))

    async def begin(self):
        self.calls = NTgCalls()
        engine = self.calls
        engine.on_frames(lambda uid, mode, device, frames: self.frames(engine, mode, device, frames))
        engine.on_connection_change(lambda uid, info: self.connection(engine, info))
        engine.on_signaling_data(lambda uid, data: self.signaling(engine, data))
        self.connected = self.loop.create_future()
        self.accepted = self.confirmed = None
        self.linked, self.signals = False, []
        self.segmenter.reset()
        self.video_lock = asyncio.Lock()
        size = Image.open(io.BytesIO(picture())).size
        await self.calls.create_p2p_call(OWNER)
        await self.calls.set_stream_sources(OWNER, StreamMode.CAPTURE, media(48000, size))
        await self.calls.set_stream_sources(OWNER, StreamMode.PLAYBACK, media(16000))

    def signaling(self, engine, data):
        if self.calls is engine and self.phone:
            asyncio.run_coroutine_threadsafe(self.client(SendSignalingDataRequest(peer=self.phone, data=bytes(data))), self.loop)

    def connection(self, engine, info):
        self.loop.call_soon_threadsafe(self.connection_changed, engine, info.state)

    def connection_changed(self, engine, state):
        if self.calls is not engine:
            return
        if state == ConnectionState.CONNECTED and self.connected and not self.connected.done():
            self.connected.set_result(True)
        elif state in (ConnectionState.CLOSED, ConnectionState.FAILED, ConnectionState.TIMEOUT):
            asyncio.create_task(self.failed(engine, state))

    async def failed(self, engine, state):
        await self.client.send_message(self.owner, f"Call: {state}", parse_mode=None)
        if self.calls is engine:
            await self.close()

    async def link(self, call):
        servers = []
        for c in call.connections:
            if isinstance(c, PhoneConnectionWebrtc):
                servers.append(RTCServer(c.id, c.ip, c.ipv6 or "", c.port, c.username, c.password, c.turn, c.stun, False, None))
            elif isinstance(c, PhoneConnection):
                servers.append(RTCServer(c.id, c.ip, c.ipv6 or "", c.port, None, None, False, True, c.tcp, bytes(c.peer_tag)))
        self.phone = InputPhoneCall(call.id, call.access_hash)
        custom = call.custom_parameters.data if call.custom_parameters else None
        await self.calls.connect_p2p(OWNER, servers, list(call.protocol.library_versions), call.p2p_allowed, custom)
        self.linked = True
        for data in self.signals:
            await self.calls.send_signaling_data(OWNER, data)
        self.signals = []
        await asyncio.wait_for(self.connected, 30)
        self.state, self.began, self.listening = "up", time.monotonic(), True
        asyncio.create_task(self.video(self.calls))

    async def answer(self, requested):
        try:
            self.phone = InputPhoneCall(requested.id, requested.access_hash)
            await self.begin()
            gb = bytes(await self.calls.init_exchange(OWNER, await self.dh(), bytes(requested.g_a_hash)))
            self.confirmed = self.loop.create_future()
            result = await self.client(AcceptCallRequest(peer=self.phone, g_b=gb, protocol=protocol()))
            call = result.phone_call if isinstance(result.phone_call, PhoneCall) else await asyncio.wait_for(self.confirmed, 30)
            await self.calls.exchange_keys(OWNER, bytes(call.g_a_or_b), call.key_fingerprint)
            await self.link(call)
        except Exception as error:
            await self.client.send_message(self.owner, str(error), parse_mode=None)
            await self.close()

    async def place(self):
        if self.state != "down":
            raise RuntimeError(f"Call: {self.state}")
        self.state = "ringing"
        try:
            await self.begin()
            ga = bytes(await self.calls.init_exchange(OWNER, await self.dh(), None))
            self.accepted = self.loop.create_future()
            result = await self.client(RequestCallRequest(user_id=self.owner, random_id=random.randrange(2**31),
                                      g_a_hash=ga, protocol=protocol(), video=True))
            if isinstance(result.phone_call, PhoneCallDiscarded):
                raise RuntimeError("The call was discarded.")
            self.phone = InputPhoneCall(result.phone_call.id, result.phone_call.access_hash)
            accepted = await asyncio.wait_for(self.accepted, 90)
            auth = await self.calls.exchange_keys(OWNER, bytes(accepted.g_b), 0)
            result = await self.client(ConfirmCallRequest(peer=self.phone, g_a=bytes(auth.g_a_or_b),
                                      key_fingerprint=auth.key_fingerprint, protocol=protocol()))
            await self.link(result.phone_call)
        except BaseException:
            await self.hang()
            raise

    async def hang(self):
        engine = self.calls
        try:
            if self.phone:
                await self.client(DiscardCallRequest(peer=self.phone, duration=int(time.monotonic() - self.began) if self.began else 0,
                                  reason=PhoneCallDiscardReasonHangup(), connection_id=0, video=True))
        finally:
            if self.calls is engine:
                await self.close()

    async def close(self):
        self.listening = self.linked = False
        self.state = "down"
        calls, self.calls, self.phone, self.began = self.calls, None, None, 0
        clip = self.segmenter.finish()
        if clip is not None:
            self.events.put(("audio", clip))
        for pending in (self.connected, self.accepted, self.confirmed):
            if pending and not pending.done():
                pending.cancel()
        if calls:
            await calls.stop(OWNER)
        self.events.put(("call_down", ""))

    async def reserve_restart(self):
        if self.state != "down":
            return False
        self.state = "restarting"
        return True

    def frames(self, engine, mode, device, frames):
        if self.calls is engine and mode == StreamMode.PLAYBACK and device == StreamDevice.MICROPHONE:
            self.loop.call_soon_threadsafe(self.audio, engine, b"".join(bytes(frame.data) for frame in frames))

    def audio(self, engine, pcm):
        if self.calls is engine and self.listening:
            try:
                for clip in self.segmenter.push(pcm):
                    self.events.put(("audio", clip))
            except Exception as error:
                asyncio.create_task(self.client.send_message(self.owner, str(error), parse_mode=None))

    async def frame(self, device, data, size=(0, 0)):
        if not self.up:
            raise RuntimeError("Call: down")
        await self.calls.send_external_frame(OWNER, device, data,
                                             FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, *size))

    async def show(self, png):
        if self.up:
            async with self.video_lock:
                data, size = i420(png)
                await self.frame(StreamDevice.CAMERA, data, size)

    async def video(self, calls):
        try:
            while self.up and self.calls is calls:
                await self.show(await asyncio.to_thread(picture))
                await asyncio.sleep(1 / CFG["desk_fps"])
        except Exception as error:
            await self.client.send_message(self.owner, str(error), parse_mode=None)

    async def speak(self, pcm):
        if not self.up:
            raise RuntimeError("Call: down")
        started = time.monotonic()
        for n, offset in enumerate(range(0, len(pcm), 960), 1):
            await self.frame(StreamDevice.MICROPHONE, pcm[offset:offset + 960].ljust(960, b"\0"))
            await asyncio.sleep(max(0, started + n * 0.01 - time.monotonic()))

    async def disconnect(self):
        try:
            await self.hang()
        finally:
            if self.client:
                await self.client.disconnect()

    def stop(self):
        if self.thread.is_alive():
            try:
                self.wait(self.disconnect(), 30)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(5)
