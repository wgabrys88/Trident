import asyncio
import secrets
import time
import wave
from collections import deque
from math import gcd

import numpy as np
import soundfile
from ntgcalls import (AudioDescription, ConnectionState, DhConfig, FrameData, MediaDescription,
                     MediaSource, NTgCalls, RTCServer, StreamDevice, StreamMode, VIDEO_ROTATION_0)
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from scipy.signal import resample_poly
from telethon import events
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import (AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest,
                                       RequestCallRequest, SendSignalingDataRequest)
from telethon.tl.types import (DocumentAttributeAudio, InputPhoneCall, PhoneCall, PhoneCallAccepted, PhoneCallDiscarded,
                              PhoneCallDiscardReasonHangup, PhoneCallDiscardReasonMissed, PhoneCallProtocol, PhoneCallRequested,
                              PhoneConnection, PhoneConnectionWebrtc, UpdatePhoneCall,
                              UpdatePhoneCallSignalingData)

from i2c import QueuedDevice, Nack, Refusal

CALL_DOWN, CALL_DIALING, CALL_UP = range(3)


class SpeechBuffer:
    def __init__(self, settings):
        self.settings = settings
        self.frame_bytes = settings["receive_rate"] * settings["frame_ms"] * settings["sample_bytes"] // 1000
        self.lead = deque(maxlen=settings["padding_ms"] // settings["frame_ms"])
        self.pending, self.speech, self.quiet, self.voiced = b"", [], 0, 0

    def push(self, audio):
        self.pending += audio
        while len(self.pending) >= self.frame_bytes:
            frame, self.pending = self.pending[:self.frame_bytes], self.pending[self.frame_bytes:]
            samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / (np.iinfo(np.int16).max + 1)
            loud = np.sqrt(np.mean(samples * samples)) >= self.settings["rms_threshold"]
            self.voiced += self.settings["frame_ms"] * int(loud)
            if not self.speech:
                self.lead.append(frame)
                if loud:
                    self.speech, self.quiet = list(self.lead), 0
                    self.lead.clear()
            else:
                self.speech.append(frame)
                self.quiet = 0 if loud else self.quiet + self.settings["frame_ms"]
                if (self.quiet >= self.settings["silence_ms"] or
                    len(self.speech) * self.settings["frame_ms"] >= self.settings["utterance_seconds"] * 1000):
                    if self.voiced >= self.settings["min_speech_ms"]:
                        yield b"".join(self.speech)
                    self.speech, self.quiet, self.voiced = [], 0, 0

    def finish(self):
        result = b"".join(self.speech) + self.pending if self.voiced >= self.settings["min_speech_ms"] else b""
        self.pending, self.speech, self.quiet, self.voiced = b"", [], 0, 0
        self.lead.clear()
        return result


class Telegram(QueuedDevice):
    def __init__(self):
        super().__init__("telegram")
        self.owner = self.cfg["telegram"]["owner"]
        self.state, self.peer, self.engine, self.serial = CALL_DOWN, None, None, 0
        self.signals, self.linked, self.since, self.audio_sequence = [], False, 0, 0
        self.audio = SpeechBuffer(self.cfg["audio"])
        self.call_lock = asyncio.Lock()
        self.native_lock = asyncio.Lock()
        self.call_task, self.outgoing = None, False

    async def start(self):
        self.loop = asyncio.get_running_loop()
        desktop = TDesktop(str(self.cfg.path(self.cfg["telegram"]["tdata"])))
        if len(accounts := [account for account in desktop.accounts if account.UserId != self.owner]) != 1:
            raise RuntimeError("Telegram requires exactly one user session distinct from the owner")
        self.client = await TelegramClient.FromTDesktop(accounts[0], session=str(self.file("session")),
            flag=UseCurrentSession, api=API.TelegramDesktop, request_retries=0, connection_retries=0,
            auto_reconnect=False, flood_sleep_threshold=0, raise_last_call_error=True, catch_up=False)
        await self.client.connect()
        identity = await self.client.get_me()
        if identity is None or identity.bot or identity.id == self.owner:
            raise RuntimeError("An authorized Telegram user session is required")
        await self.client.get_dialogs()
        self.owner_entity = await self.client.get_input_entity(self.owner)
        self.client.add_event_handler(self.enqueue_message, events.NewMessage(incoming=True, from_users=[self.owner]))
        self.client.add_event_handler(self.enqueue_update, events.Raw())

    async def enqueue_message(self, event):
        if event.is_private and event.raw_text.strip():
            self.tasks.create_task(self.send("mind", event.raw_text, "01"))

    async def enqueue_update(self, event):
        self.tasks.create_task(self.update(event))

    async def tick(self):
        self.tasks.create_task(super().tick())
        try:
            await self.client.disconnected
            raise ConnectionError("Telegram disconnected")
        finally:
            self.client.remove_event_handler(self.enqueue_message)
            self.client.remove_event_handler(self.enqueue_update)
            self.serial += 1
            self.audio.finish()

    async def receive(self, source, frame):
        if frame.data[0] == 0 and frame.reading:
            return f"{self.state:02d}".encode()
        if frame.data[0] == 2:
            self.tasks.create_task(self.hang(missed=False, serial=self.serial))
            return b""
        if (writer := {17: "tools", 32: "voice", 33: "voice"}.get(frame.data[0])) and source != self.cfg["address"][writer]:
            raise Nack(Refusal.UNKNOWN_DATA)
        if frame.data[0] == 16 and source == self.cfg["telegram"]["bridge"]:
            self.file("bridge").touch()
        if frame.data[0] == 1:
            if self.state:
                raise Nack(Refusal.NOT_READY)
            self.state, self.outgoing = CALL_DIALING, True
            self.call_task = self.tasks.create_task(self.dial())
            return b""
        return await super().receive(source, frame)

    async def work(self, source, frame):
        match frame.data[0]:
            case 16:
                text = frame.data[1:].decode()
                if self.state == CALL_UP or self.cfg["telegram"]["voice_notes"]:
                    await self.send("voice", text, "01" if self.state == CALL_UP else "02")
                else:
                    size = self.cfg["telegram"]["chat_chars"]
                    for offset in range(0, len(text), size):
                        await self.client.send_message(self.owner_entity, text[offset:offset + size], parse_mode=None)
            case 17:
                path, _, text = frame.data[1:].decode().partition("\n")
                await (self.client.send_file(self.owner_entity, path, caption=text[:self.cfg["telegram"]["caption_chars"]], parse_mode=None, force_document=True) if path else self.client.send_message(self.owner_entity, text[:self.cfg["telegram"]["chat_chars"]], parse_mode=None))
            case 32:
                await self.play(frame.data[1:].decode())
            case 33:
                await self.client.send_file(self.owner_entity, path := frame.data[1:].decode(), attributes=[DocumentAttributeAudio(round(soundfile.info(path).duration), voice=True)])

    def protocol(self):
        native = NTgCalls.get_protocol()
        return PhoneCallProtocol(udp_p2p=native.udp_p2p, udp_reflector=native.udp_reflector,
            min_layer=self.cfg["telegram"]["min_layer"], max_layer=native.max_layer,
            library_versions=list(reversed(native.library_versions)))

    async def prepare(self):
        self.serial += 1
        serial = self.serial
        self.accepted, self.confirmed, self.connected = (self.loop.create_future() for _ in range(3))
        self.engine, self.linked = NTgCalls(), False
        self.engine.on_frames(lambda uid, mode, device, frames: self.loop.call_soon_threadsafe(
            self.frames, serial, mode, device, frames))
        self.engine.on_connection_change(lambda uid, info: self.loop.call_soon_threadsafe(
            self.connection, serial, info.state))
        self.engine.on_signaling_data(lambda uid, data: self.loop.call_soon_threadsafe(
            self.signaling, serial, bytes(data)))
        await self.native("create_p2p_call", self.owner)
        for mode, rate in ((StreamMode.CAPTURE, self.cfg["audio"]["send_rate"]),
                           (StreamMode.PLAYBACK, self.cfg["audio"]["receive_rate"])):
            media = MediaDescription(AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True), None, None, None)
            await self.native("set_stream_sources", self.owner, mode, media)
        config = await self.client(GetDhConfigRequest(0, self.cfg["telegram"]["dh_bytes"]))
        self.dh = DhConfig(config.g, bytes(config.p), bytes(config.random))

    async def native(self, method, *arguments):
        async with self.native_lock:
            pending = getattr(self.engine, method)(*arguments)
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                await pending
                raise

    def frames(self, serial, mode, device, frames):
        if serial == self.serial and mode == StreamMode.PLAYBACK and device == StreamDevice.MICROPHONE:
            for audio in self.audio.push(b"".join(bytes(frame.data) for frame in frames)):
                self.heard(audio)

    def heard(self, audio):
        self.audio_sequence += 1
        path = self.file(f"{self.audio_sequence}.pcm")
        path.write_bytes(audio)
        self.tasks.create_task(self.send("ears", str(path), "01"))

    def connection(self, serial, state):
        if serial != self.serial:
            return
        if state == ConnectionState.CONNECTED:
            if not self.connected.done():
                self.connected.set_result(None)
        elif state == ConnectionState.CLOSED:
            self.tasks.create_task(self.hang(missed=self.outgoing and self.state == CALL_DIALING, serial=serial, discard=False))
        elif state in (ConnectionState.FAILED, ConnectionState.TIMEOUT):
            self.tasks.create_task(self.transport_fault(state))

    async def transport_fault(self, state):
        raise RuntimeError(f"Telegram transport {state}")

    def signaling(self, serial, data):
        if serial == self.serial:
            self.tasks.create_task(self.client(SendSignalingDataRequest(peer=self.peer, data=data)))

    async def connect(self, call):
        endpoints = []
        for endpoint in call.connections:
            if isinstance(endpoint, PhoneConnectionWebrtc):
                endpoints.append(RTCServer(endpoint.id, endpoint.ip, endpoint.ipv6, endpoint.port,
                    endpoint.username, endpoint.password, endpoint.turn, endpoint.stun, False, None))
            elif isinstance(endpoint, PhoneConnection):
                endpoints.append(RTCServer(endpoint.id, endpoint.ip, endpoint.ipv6, endpoint.port,
                    None, None, False, True, endpoint.tcp, bytes(endpoint.peer_tag)))
            else:
                raise RuntimeError("Unsupported Telegram connection")
        custom = call.custom_parameters.data if call.custom_parameters is not None else None
        await self.native("connect_p2p", self.owner, endpoints, list(call.protocol.library_versions), call.p2p_allowed, custom)
        self.linked = True
        for data in self.signals:
            await self.native("send_signaling_data", self.owner, data)
        self.signals.clear()
        await asyncio.wait_for(self.connected, self.cfg["telegram"]["connect_seconds"])
        self.state, self.since = CALL_UP, time.monotonic()
        await self.send("timer", "", "03")

    async def dial(self):
        await self.prepare()
        exchange = bytes(await self.native("init_exchange", self.owner, self.dh, None))
        response = await self.client(RequestCallRequest(user_id=self.owner_entity,
            random_id=secrets.randbelow(self.cfg["telegram"]["random_id_max"]), g_a_hash=exchange,
            protocol=self.protocol(), video=False))
        if isinstance(response.phone_call, PhoneCallDiscarded):
            await self.hang(missed=True, serial=self.serial, discard=False)
            return
        self.peer = InputPhoneCall(response.phone_call.id, response.phone_call.access_hash)
        try:
            accepted = await asyncio.wait_for(self.accepted, self.cfg["telegram"]["ring_seconds"])
        except TimeoutError:
            await self.hang(missed=True, serial=self.serial)
            return
        if accepted.id != self.peer.id:
            raise RuntimeError("Telegram accepted a different call")
        keys = await self.native("exchange_keys", self.owner, bytes(accepted.g_b), 0)
        response = await self.client(ConfirmCallRequest(peer=self.peer, g_a=bytes(keys.g_a_or_b),
            key_fingerprint=keys.key_fingerprint, protocol=self.protocol()))
        await self.connect(response.phone_call)

    async def answer(self, request):
        await self.prepare()
        exchange = bytes(await self.native("init_exchange", self.owner, self.dh, bytes(request.g_a_hash)))
        response = await self.client(AcceptCallRequest(peer=self.peer, g_b=exchange, protocol=self.protocol()))
        if not isinstance(call := response.phone_call, PhoneCall):
            call = await asyncio.wait_for(self.confirmed, self.cfg["telegram"]["connect_seconds"])
        await self.native("exchange_keys", self.owner, bytes(call.g_a_or_b), call.key_fingerprint)
        await self.connect(call)

    async def update(self, event):
        if isinstance(event, UpdatePhoneCallSignalingData):
            if self.peer is not None and event.phone_call_id == self.peer.id:
                if self.linked:
                    await self.native("send_signaling_data", self.owner, bytes(event.data))
                else:
                    self.signals.append(bytes(event.data))
        elif isinstance(event, UpdatePhoneCall):
            call = event.phone_call
            if isinstance(call, PhoneCallRequested) and call.admin_id == self.owner:
                if self.state:
                    await self.hang(missed=False, serial=self.serial)
                self.state, self.peer = CALL_DIALING, InputPhoneCall(call.id, call.access_hash)
                self.outgoing = False
                self.call_task = self.tasks.create_task(self.answer(call))
                await self.send("timer", "", "03")
            elif (isinstance(call, PhoneCallAccepted) and self.outgoing and self.state == CALL_DIALING
                  and self.engine is not None and (self.peer is None or call.id == self.peer.id)
                  and call.participant_id == self.owner and not self.accepted.done()):
                self.accepted.set_result(call)
            elif self.peer is not None and call.id == self.peer.id:
                if isinstance(call, PhoneCall) and not self.confirmed.done():
                    self.confirmed.set_result(call)
                elif isinstance(call, PhoneCallDiscarded):
                    await self.hang(missed=isinstance(call.reason, PhoneCallDiscardReasonMissed), serial=self.serial, discard=False)

    async def release(self):
        if audio := self.audio.finish():
            self.heard(audio)
        if self.engine is not None:
            await self.native("stop", self.owner)
        self.engine, self.peer, self.state, self.linked, self.since = None, None, CALL_DOWN, False, 0
        self.signals.clear()

    async def hang(self, missed, serial, discard=True):
        async with self.call_lock:
            if serial != self.serial:
                return
            self.serial += 1
            if self.call_task is not None and self.call_task != asyncio.current_task() and not self.call_task.done():
                self.call_task.cancel()
                await asyncio.wait([self.call_task])
            if discard and self.peer is not None:
                await self.client(DiscardCallRequest(peer=self.peer,
                    duration=int(time.monotonic() - self.since) if self.since else 0,
                    reason=PhoneCallDiscardReasonHangup(), connection_id=0, video=False))
            await self.release()
            await self.send("timer", "", "02" if missed else "03")

    async def play(self, path):
        if self.state != CALL_UP:
            return
        with wave.open(path, "rb") as audio:
            if audio.getsampwidth() != self.cfg["audio"]["sample_bytes"]:
                raise ValueError("Voice must produce PCM16 WAV")
            rate, channels = audio.getframerate(), audio.getnchannels()
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").reshape(-1, channels).mean(axis=1)
        target = self.cfg["audio"]["send_rate"]
        divisor = gcd(rate, target)
        samples = resample_poly(samples, target // divisor, rate // divisor)
        pcm = np.clip(np.rint(samples), np.iinfo(np.int16).min, np.iinfo(np.int16).max).astype("<i2").tobytes()
        size, serial, started = self.cfg["audio"]["send_frame_bytes"], self.serial, self.loop.time()
        for offset in range(0, len(pcm), size):
            async with self.call_lock:
                if serial != self.serial:
                    return
                await self.native("send_external_frame", self.owner, StreamDevice.MICROPHONE,
                    pcm[offset:offset + size].ljust(size, b"\0"), FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, 0, 0))
            await asyncio.sleep(max(0, started + (offset + size) / (target * self.cfg["audio"]["sample_bytes"]) - self.loop.time()))

    async def close(self):
        if self.peer is not None:
            await self.client(DiscardCallRequest(peer=self.peer, duration=0, reason=PhoneCallDiscardReasonHangup(),
                connection_id=0, video=False))
        await self.release()
        await self.client.disconnect()


if __name__ == "__main__":
    Telegram().launch()
