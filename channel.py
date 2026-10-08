import asyncio
import io
import logging
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
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import (
    AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest,
    RequestCallRequest, SendSignalingDataRequest,
)
from telethon.tl.types import (
    InputPhoneCall, PhoneCall, PhoneCallDiscarded,
    PhoneCallDiscardReasonHangup, PhoneCallProtocol, PhoneCallRequested,
    PhoneConnection, PhoneConnectionWebrtc, UpdatePhoneCall, UpdatePhoneCallSignalingData,
)


def servers(call):
    found = []
    for endpoint in call.connections:
        if isinstance(endpoint, PhoneConnectionWebrtc):
            found.append(RTCServer(
                endpoint.id, endpoint.ip, endpoint.ipv6, endpoint.port,
                endpoint.username, endpoint.password, endpoint.turn, endpoint.stun, False, None,
            ))
        elif isinstance(endpoint, PhoneConnection):
            found.append(RTCServer(
                endpoint.id, endpoint.ip, endpoint.ipv6, endpoint.port,
                None, None, False, True, endpoint.tcp, bytes(endpoint.peer_tag),
            ))
        else:
            raise RuntimeError(f"Unsupported Telegram connection: {type(endpoint).__name__}")
    return found


class Channel:
    def __init__(self, cfg, session):
        self.cfg = cfg
        self.session = session
        self.owner_id = int(cfg["telegram_id"])
        self.client = None
        self.entity = None
        self.cursor = 0
        self.engine = None
        self.peer = None
        self.requested = None
        self.state = "down"
        self.since = 0
        self.serial = 0
        self.signals = []
        self.linked = False
        self.accepted = None
        self.confirmed = None
        self.connected = None
        self.dh = None
        self.jobs = set()
        self.inbox = []
        self.mic = []
        self.trouble = []
        self.arrived = asyncio.Event()
        self.lock = asyncio.Lock()
        self.releasing = False
        self.loop = None

    def _push(self, kind, text):
        self.inbox.append((kind, text))
        self.arrived.set()

    def _pcm(self, pcm):
        self.mic.append(pcm)
        self.arrived.set()

    def _protocol(self):
        native = NTgCalls.get_protocol()
        return PhoneCallProtocol(
            udp_p2p=native.udp_p2p, udp_reflector=native.udp_reflector,
            min_layer=65, max_layer=native.max_layer,
            library_versions=list(reversed(native.library_versions)),
        )

    def _media(self, rate):
        return MediaDescription(
            AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True),
            None, None, None,
        )

    async def _call(self, engine, method, *arguments):
        pending = getattr(engine, method)(*arguments)
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            await pending
            raise

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self.jobs.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task):
        self.jobs.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.trouble.append(error)
            self.arrived.set()

    def _frames(self, serial, mode, device, frames):
        if serial != self.serial or mode != StreamMode.PLAYBACK or device != StreamDevice.MICROPHONE:
            return
        pcm = b"".join(bytes(frame.data) for frame in frames)
        if pcm:
            self.loop.call_soon_threadsafe(self._pcm, pcm)

    def _connection(self, serial, status):
        if serial != self.serial:
            return
        if status == ConnectionState.CONNECTED:
            if self.connected is not None and not self.connected.done():
                self.connected.set_result(True)
        elif status in (ConnectionState.FAILED, ConnectionState.TIMEOUT):
            self.trouble.append(RuntimeError(f"Telegram transport: {status}"))
            self.arrived.set()
        elif status == ConnectionState.CLOSED:
            self._spawn(self._release(True))

    def _outgoing(self, serial, data):
        if serial != self.serial:
            return
        self._spawn(self._signal(serial, data))

    async def _signal(self, serial, data):
        if serial != self.serial or self.peer is None:
            return
        await self.client(SendSignalingDataRequest(peer=self.peer, data=data))

    async def _prepare(self):
        self.serial += 1
        serial = self.serial
        self.accepted = self.loop.create_future()
        self.confirmed = self.loop.create_future()
        self.connected = self.loop.create_future()
        self.linked = False
        self.engine = NTgCalls()
        self.engine.on_frames(lambda uid, mode, device, frames: self._frames(serial, mode, device, frames))
        self.engine.on_connection_change(
            lambda uid, info: self.loop.call_soon_threadsafe(self._connection, serial, info.state))
        self.engine.on_signaling_data(
            lambda uid, data: self.loop.call_soon_threadsafe(self._outgoing, serial, bytes(data)))
        await self._call(self.engine, "create_p2p_call", self.owner_id)
        await self._call(self.engine, "set_stream_sources", self.owner_id, StreamMode.CAPTURE, self._media(48000))
        await self._call(self.engine, "set_stream_sources", self.owner_id, StreamMode.PLAYBACK, self._media(16000))
        dh = await self.client(GetDhConfigRequest(0, 256))
        self.dh = DhConfig(dh.g, bytes(dh.p), bytes(dh.random))

    async def _connect(self, call):
        custom = call.custom_parameters.data if call.custom_parameters is not None else None
        await self._call(
            self.engine, "connect_p2p", self.owner_id, servers(call),
            list(call.protocol.library_versions), call.p2p_allowed, custom,
        )
        self.linked = True
        for signal in self.signals:
            await self._call(self.engine, "send_signaling_data", self.owner_id, signal)
        self.signals = []
        await asyncio.wait_for(self.connected, 30)
        self.state = "up"
        self.since = time.monotonic()

    async def _release(self, note):
        if self.releasing or (self.state == "down" and self.engine is None):
            return
        self.releasing = True
        try:
            was_up = self.state == "up"
            engine, self.engine = self.engine, None
            self.serial += 1
            self.peer = None
            self.requested = None
            self.state = "down"
            self.linked = False
            self.since = 0
            self.signals = []
            for item in (self.accepted, self.confirmed, self.connected):
                if item is not None and not item.done():
                    item.cancel()
            if was_up:
                self.mic.append(None)
                if note:
                    self._push("note", "The call ended.")
                else:
                    self.arrived.set()
            if engine is not None:
                await self._call(engine, "stop", self.owner_id)
        finally:
            self.releasing = False

    async def dial(self):
        if self.state != "down":
            raise RuntimeError(f"Cannot dial: call is {self.state}")
        self.state = "dialing"
        try:
            await self._prepare()
            exchange = bytes(await self._call(self.engine, "init_exchange", self.owner_id, self.dh, None))
            response = await self.client(RequestCallRequest(
                user_id=self.entity, random_id=secrets.randbelow(2**31), g_a_hash=exchange,
                protocol=self._protocol(), video=False,
            ))
            if isinstance(response.phone_call, PhoneCallDiscarded):
                await self._release(False)
                return False
            ringing = response.phone_call
            self.peer = InputPhoneCall(ringing.id, ringing.access_hash)
            try:
                accepted = await asyncio.wait_for(self.accepted, 90)
            except TimeoutError:
                await self.hang(False)
                return False
            except asyncio.CancelledError:
                if self.state != "down":
                    await self._release(False)
                return False
            keys = await self._call(self.engine, "exchange_keys", self.owner_id, bytes(accepted.g_b), 0)
            response = await self.client(ConfirmCallRequest(
                peer=self.peer, g_a=bytes(keys.g_a_or_b),
                key_fingerprint=keys.key_fingerprint, protocol=self._protocol(),
            ))
            await self._connect(response.phone_call)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.state != "down":
                await self._release(False)
            raise

    async def _answer(self):
        if self.state != "ringing" or self.requested is None or self.peer is None:
            raise RuntimeError(f"Cannot answer: call is {self.state}")
        await self._prepare()
        exchange = bytes(await self._call(
            self.engine, "init_exchange", self.owner_id, self.dh, bytes(self.requested.g_a_hash),
        ))
        response = await self.client(AcceptCallRequest(peer=self.peer, g_b=exchange, protocol=self._protocol()))
        call = response.phone_call
        if not isinstance(call, PhoneCall):
            call = await asyncio.wait_for(self.confirmed, 30)
        await self._call(self.engine, "exchange_keys", self.owner_id, bytes(call.g_a_or_b), call.key_fingerprint)
        await self._connect(call)
        self._push("note", "The call is up.")

    async def hang(self, note=True):
        peer = self.peer
        if peer is not None and self.state != "down":
            await self.client(DiscardCallRequest(
                peer=peer, duration=int(time.monotonic() - self.since) if self.since else 0,
                reason=PhoneCallDiscardReasonHangup(), connection_id=0, video=False,
            ))
        await self._release(note)

    async def play(self, pcm):
        if self.state != "up" or self.engine is None:
            return False
        engine = self.engine
        serial = self.serial
        started = self.loop.time()
        for offset in range(0, len(pcm), 960):
            if serial != self.serial or self.state != "up":
                return False
            chunk = pcm[offset:offset + 960].ljust(960, b"\0")
            try:
                await self._call(
                    engine, "send_external_frame", self.owner_id, StreamDevice.MICROPHONE, chunk,
                    FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, 0, 0),
                )
            except Exception:
                if serial != self.serial or self.state != "up":
                    return False
                raise
            await asyncio.sleep(max(0, started + (offset + 960) / 96000 - self.loop.time()))
        return True

    async def send(self, text):
        text = "" if text is None else str(text)
        if not text:
            return
        async with self.lock:
            for offset in range(0, len(text), 4000):
                await self.client.send_message(self.entity, text[offset:offset + 4000], parse_mode=None)

    async def send_wav(self, payload):
        stream = io.BytesIO(payload)
        stream.name = "answer.wav"
        async with self.lock:
            await self.client.send_file(self.entity, stream, voice_note=False, force_document=False)

    async def _message(self, event):
        try:
            if not event.is_private or event.id <= self.cursor:
                return
            self.cursor = event.id
            text = (event.raw_text or "").strip()
            if text:
                self._push("wrote", text)
        except Exception as error:
            self.trouble.append(error)
            self.arrived.set()

    async def _update(self, event):
        try:
            if isinstance(event, UpdatePhoneCallSignalingData):
                if self.peer is not None and event.phone_call_id == self.peer.id:
                    if self.linked:
                        await self._call(self.engine, "send_signaling_data", self.owner_id, bytes(event.data))
                    else:
                        self.signals.append(bytes(event.data))
                return
            if not isinstance(event, UpdatePhoneCall):
                return
            call = event.phone_call
            if isinstance(call, PhoneCallRequested):
                if call.admin_id != self.owner_id or self.state != "down":
                    raise RuntimeError("Unexpected Telegram caller or overlapping call")
                self.requested = call
                self.peer = InputPhoneCall(call.id, call.access_hash)
                self.state = "ringing"
                self._spawn(self._answer())
            elif isinstance(call, PhoneCallAccepted) and call.participant_id == self.owner_id and self.state == "dialing":
                self.peer = InputPhoneCall(call.id, call.access_hash)
                if self.accepted is not None and not self.accepted.done():
                    self.accepted.set_result(call)
            elif self.peer is not None and call.id == self.peer.id:
                if isinstance(call, PhoneCall):
                    if self.confirmed is not None and not self.confirmed.done():
                        self.confirmed.set_result(call)
                elif isinstance(call, PhoneCallDiscarded):
                    self._spawn(self._release(True))
        except Exception as error:
            self.trouble.append(error)
            self.arrived.set()

    async def start(self):
        self.loop = asyncio.get_running_loop()
        logging.getLogger("telethon").setLevel(logging.CRITICAL)
        desktop = TDesktop(os.path.expandvars(self.cfg["tdata"]))
        accounts = [item for item in desktop.accounts if int(item.UserId) != self.owner_id]
        if len(accounts) != 1:
            raise RuntimeError(
                f"Telegram tdata needs one user session besides the owner; found {len(accounts)}"
            )
        user = accounts[0]
        self.session.parent.mkdir(parents=True, exist_ok=True)
        self.client = await TelegramClient.FromTDesktop(
            user, session=str(self.session), flag=UseCurrentSession, api=API.TelegramDesktop,
            request_retries=0, connection_retries=0, auto_reconnect=True, flood_sleep_threshold=0,
            raise_last_call_error=True, catch_up=True,
        )
        self.client.add_event_handler(
            self._message, events.NewMessage(incoming=True, from_users=[self.owner_id]),
        )
        self.client.add_event_handler(self._update, events.Raw())
        await self.client.connect()
        identity = await self.client.get_me()
        if identity is None or identity.bot or identity.id == self.owner_id or identity.id != int(user.UserId):
            raise RuntimeError("Telegram needs an authorized user session distinct from the owner")
        await self.client.get_dialogs()
        self.entity = await self.client.get_input_entity(self.owner_id)
        latest = await self.client.get_messages(self.entity, limit=1)
        self.cursor = latest[0].id if latest else 0

    async def stop(self):
        if self.state != "down" or self.engine is not None:
            await self._release(False)
        for task in list(self.jobs):
            task.cancel()
        if self.jobs:
            await asyncio.gather(*self.jobs, return_exceptions=True)
        if self.client is not None:
            await self.client.disconnect()
            self.client = None
