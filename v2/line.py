import asyncio
import os
import secrets
import time

from ntgcalls import (
    AudioDescription, ConnectionState, DhConfig, FrameData, MediaDescription,
    MediaSource, NTgCalls, RTCServer, StreamDevice, StreamMode, VIDEO_ROTATION_0,
)
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from telethon.sessions import MemorySession
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import (
    AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest,
    RequestCallRequest, SendSignalingDataRequest,
)
from telethon.tl.types import (
    InputPhoneCall, PhoneCall, PhoneCallAccepted, PhoneCallDiscarded,
    PhoneCallDiscardReasonHangup, PhoneCallProtocol, PhoneCallRequested,
    PhoneConnection, PhoneConnectionWebrtc, UpdatePhoneCall, UpdatePhoneCallSignalingData,
)

from audio import Utterances
from store import CONFIG


def protocol():
    native = NTgCalls.get_protocol()
    return PhoneCallProtocol(
        udp_p2p=native.udp_p2p, udp_reflector=native.udp_reflector,
        min_layer=65, max_layer=92, library_versions=list(reversed(native.library_versions)),
    )


def media(rate):
    return MediaDescription(
        microphone=AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True),
        speaker=None, camera=None, screen=None,
    )


class Line:
    def __init__(self, inbox, record):
        self.inbox, self.record = inbox, record
        self.owner_id = CONFIG["owner"]["telegram_id"]
        self.client = None
        self.engine = None
        self.peer = None
        self.requested = None
        self.state = "down"
        self.since = 0
        self.signals = []
        self.linked = False
        self.utterances = Utterances()
        self.jobs = set()
        self.generation = 0

    async def open(self):
        self.loop = asyncio.get_running_loop()
        desktop = TDesktop(os.path.expandvars(CONFIG["telegram"]["tdata"]))
        accounts = [a for a in desktop.accounts if int(a.UserId) != self.owner_id]
        if len(accounts) != 1:
            raise RuntimeError(f"Telegram tdata needs exactly one Trident user session distinct from Wojciech; found {len(accounts)}")
        [account] = accounts
        self.client = await TelegramClient.FromTDesktop(
            account, session=MemorySession(), flag=UseCurrentSession, api=API.TelegramDesktop,
        )
        self.client.add_event_handler(self.message, events.NewMessage(incoming=True, from_users=[self.owner_id]))
        self.client.add_event_handler(self.update, events.Raw())
        await self.client.connect()
        identity = await self.client.get_me()
        if identity is None or identity.bot:
            raise RuntimeError("Telegram needs an authorized user session distinct from Wojciech")
        await self.client.get_dialogs()
        self.owner = await self.client.get_input_entity(self.owner_id)

    def emit(self, kind, value):
        self.generation += 1
        self.inbox.put_nowait((kind, value))

    async def message(self, event):
        try:
            if event.is_private:
                if event.media:
                    raise ValueError("Telegram input must be text or a live call")
                self.emit("owner", event.raw_text)
        except Exception as error:
            self.emit("error", error)

    async def update(self, event):
        try:
            if isinstance(event, UpdatePhoneCallSignalingData):
                if self.peer is not None and event.phone_call_id == self.peer.id:
                    if self.linked:
                        await self.engine.send_signaling_data(self.owner_id, bytes(event.data))
                    else:
                        self.signals.append(bytes(event.data))
            elif isinstance(event, UpdatePhoneCall):
                call = event.phone_call
                if isinstance(call, PhoneCallRequested):
                    if call.admin_id != self.owner_id or self.state != "down":
                        raise RuntimeError("Unexpected Telegram caller or overlapping call")
                    self.requested = call
                    self.peer = InputPhoneCall(call.id, call.access_hash)
                    self.state = "ringing"
                    self.emit("ring", "Wojciech is calling. The incoming call is ringing.")
                elif isinstance(call, PhoneCallAccepted) and call.participant_id == self.owner_id and self.state == "dialing":
                    self.peer = InputPhoneCall(call.id, call.access_hash)
                    self.accepted.set_result(call)
                elif self.peer is not None and call.id == self.peer.id:
                    if isinstance(call, PhoneCall):
                        if not self.confirmed.done():
                            self.confirmed.set_result(call)
                    elif isinstance(call, PhoneCallDiscarded):
                        await self.release()
                        self.emit("line", "The Telegram call ended.")
        except Exception as error:
            self.emit("error", error)

    def dispatch(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.jobs.add(task)
        task.add_done_callback(self.job_finished)

    def job_finished(self, task):
        self.jobs.remove(task)
        if not task.cancelled() and task.exception() is not None:
            self.emit("error", task.exception())

    async def signal(self, data):
        await self.client(SendSignalingDataRequest(peer=self.peer, data=data))

    def connection(self, engine, status):
        if engine is not self.engine:
            return
        if status == ConnectionState.CONNECTED:
            if not self.connected.done():
                self.connected.set_result(True)
        elif status in (ConnectionState.FAILED, ConnectionState.TIMEOUT):
            self.emit("error", RuntimeError(f"Telegram transport: {status}"))
        elif status == ConnectionState.CLOSED:
            self.dispatch(self.release())
            self.emit("line", "Telegram audio transport closed.")

    def frames(self, engine, mode, device, frames):
        if engine is self.engine and mode == StreamMode.PLAYBACK and device == StreamDevice.MICROPHONE:
            pcm = b"".join(bytes(frame.data) for frame in frames)
            self.loop.call_soon_threadsafe(self.receive, engine, pcm)

    def receive(self, engine, pcm):
        if engine is self.engine and self.state == "up":
            try:
                for utterance in self.utterances.feed(pcm):
                    self.emit("audio", utterance)
            except Exception as error:
                self.emit("error", error)

    async def prepare(self):
        self.connected = self.loop.create_future()
        self.accepted = self.loop.create_future()
        self.confirmed = self.loop.create_future()
        self.engine = engine = NTgCalls()
        engine.on_frames(lambda uid, mode, device, frames: self.frames(engine, mode, device, frames))
        engine.on_connection_change(lambda uid, info: self.loop.call_soon_threadsafe(self.connection, engine, info.state))
        engine.on_signaling_data(lambda uid, data: self.loop.call_soon_threadsafe(self.dispatch, self.signal(bytes(data))))
        await engine.create_p2p_call(self.owner_id)
        await engine.set_stream_sources(self.owner_id, StreamMode.CAPTURE, media(48000))
        await engine.set_stream_sources(self.owner_id, StreamMode.PLAYBACK, media(16000))
        dh = await self.client(GetDhConfigRequest(0, 256))
        return DhConfig(dh.g, bytes(dh.p), bytes(dh.random))

    async def dial(self):
        if self.state != "down":
            raise RuntimeError(f"Cannot dial: call is {self.state}")
        self.state = "dialing"
        dh = await self.prepare()
        exchange = bytes(await self.engine.init_exchange(self.owner_id, dh, None))
        response = await self.client(RequestCallRequest(
            user_id=self.owner, random_id=secrets.randbelow(2**31), g_a_hash=exchange,
            protocol=protocol(), video=False,
        ))
        if isinstance(response.phone_call, PhoneCallDiscarded):
            raise RuntimeError("Outgoing Telegram call was discarded")
        self.peer = InputPhoneCall(response.phone_call.id, response.phone_call.access_hash)
        accepted = await asyncio.wait_for(self.accepted, 90)
        keys = await self.engine.exchange_keys(self.owner_id, bytes(accepted.g_b), 0)
        response = await self.client(ConfirmCallRequest(
            peer=self.peer, g_a=bytes(keys.g_a_or_b), key_fingerprint=keys.key_fingerprint, protocol=protocol(),
        ))
        await self.connect(response.phone_call)
        return {"call": self.state, "direction": "outgoing"}

    async def answer(self):
        if self.state != "ringing":
            raise RuntimeError(f"Cannot answer: call is {self.state}")
        dh = await self.prepare()
        exchange = bytes(await self.engine.init_exchange(self.owner_id, dh, bytes(self.requested.g_a_hash)))
        response = await self.client(AcceptCallRequest(peer=self.peer, g_b=exchange, protocol=protocol()))
        call = response.phone_call
        if not isinstance(call, PhoneCall):
            call = await asyncio.wait_for(self.confirmed, 30)
        await self.engine.exchange_keys(self.owner_id, bytes(call.g_a_or_b), call.key_fingerprint)
        await self.connect(call)
        return {"call": self.state, "direction": "incoming"}

    async def connect(self, call):
        servers = []
        for endpoint in call.connections:
            if isinstance(endpoint, PhoneConnectionWebrtc):
                servers.append(RTCServer(
                    endpoint.id, endpoint.ip, endpoint.ipv6, endpoint.port, endpoint.username,
                    endpoint.password, endpoint.turn, endpoint.stun, False, None,
                ))
            elif isinstance(endpoint, PhoneConnection):
                servers.append(RTCServer(
                    endpoint.id, endpoint.ip, endpoint.ipv6, endpoint.port, None, None,
                    False, True, endpoint.tcp, bytes(endpoint.peer_tag),
                ))
            else:
                raise TypeError(f"Unsupported Telegram connection: {type(endpoint).__name__}")
        await self.engine.connect_p2p(
            self.owner_id, servers, list(call.protocol.library_versions), call.p2p_allowed,
            call.custom_parameters.data if call.custom_parameters is not None else None,
        )
        self.linked = True
        for signal in self.signals:
            await self.engine.send_signaling_data(self.owner_id, signal)
        self.signals.clear()
        await asyncio.wait_for(self.connected, 30)
        self.state = "up"
        self.since = time.monotonic()
        self.record.append("call_connected", {"id": self.peer.id})

    async def speak(self, pcm):
        if self.state != "up":
            raise RuntimeError("Speech requires a connected Telegram call")
        started = self.loop.time()
        for offset in range(0, len(pcm), 960):
            await self.engine.send_external_frame(
                self.owner_id, StreamDevice.MICROPHONE, pcm[offset:offset + 960].ljust(960, b"\0"),
                FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, 0, 0),
            )
            await asyncio.sleep(max(0, started + (offset + 960) / 96000 - self.loop.time()))
        return {"transmitted_seconds": len(pcm) / 96000}

    async def chat(self, text):
        messages = []
        for offset in range(0, len(text), 4000):
            message = await self.client.send_message(self.owner, text[offset:offset + 4000], parse_mode=None)
            messages.append(message.id)
        return {"telegram_messages": messages}

    async def hang(self):
        if self.peer is None:
            raise RuntimeError("There is no Telegram call to hang up")
        await self.client(DiscardCallRequest(
            peer=self.peer, duration=int(time.monotonic() - self.since) if self.since else 0,
            reason=PhoneCallDiscardReasonHangup(), connection_id=0, video=False,
        ))
        await self.release()
        return {"call": self.state}

    async def release(self):
        engine, self.engine = self.engine, None
        self.peer = self.requested = None
        self.state, self.since, self.linked = "down", 0, False
        self.signals.clear()
        tail = self.utterances.finish()
        if tail:
            self.emit("audio", tail)
        if engine is not None:
            await engine.stop(self.owner_id)
        self.record.append("call_closed", {})

    async def close(self):
        if self.peer is not None:
            await self.hang()
        elif self.engine is not None:
            await self.release()
        for task in list(self.jobs):
            task.cancel()
        for task in list(self.jobs):
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.client is not None:
            await self.client.disconnect()
