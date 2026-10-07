import asyncio
import os
import secrets
import time
import wave

from ntgcalls import (
    AudioDescription, ConnectionState, DhConfig, FrameData, MediaDescription,
    MediaSource, NTgCalls, RTCServer, StreamDevice, StreamMode, VIDEO_ROTATION_0,
)
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import GetDhConfigRequest, SendMessageRequest, SendMediaRequest
from telethon.tl.functions.phone import (
    AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest,
    RequestCallRequest, SendSignalingDataRequest,
)
from telethon.tl.types import (
    DocumentAttributeAudio, InputPhoneCall, PhoneCall, PhoneCallAccepted, PhoneCallDiscarded,
    PhoneCallDiscardReasonHangup, PhoneCallProtocol, PhoneCallRequested,
    PhoneConnection, PhoneConnectionWebrtc, UpdatePhoneCall, UpdatePhoneCallSignalingData, UpdateShortSentMessage,
)

from audio import Hearing, Utterances
from store import CONFIG, ROOT, activate, cancel, save, write


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


async def native(engine, method, *arguments):
    pending = getattr(engine, method)(*arguments)
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        await pending
        raise


class Line:
    def __init__(self, inbox, record, life, calls=True):
        self.inbox, self.record = inbox, record
        self.life, self.calls = life, calls
        self.owner_id = CONFIG["owner"]["telegram_id"]
        self.client = None
        self.owner = None
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
        self.native_serial = 0
        self.hearing = Hearing(record, self.emit) if calls else None

    async def open(self):
        self.loop = asyncio.get_running_loop()
        desktop = TDesktop(os.path.expandvars(CONFIG["telegram"]["tdata"]))
        accounts = [a for a in desktop.accounts if int(a.UserId) != self.owner_id]
        if len(accounts) != 1:
            raise RuntimeError(f"Telegram tdata needs exactly one Trident user session distinct from Wojciech; found {len(accounts)}")
        [account] = accounts
        if self.client is None:
            self.client = await TelegramClient.FromTDesktop(
                account, session=str(self.record.folder / "telegram"), flag=UseCurrentSession, api=API.TelegramDesktop,
                request_retries=0, connection_retries=0, auto_reconnect=True, flood_sleep_threshold=0,
                raise_last_call_error=True, catch_up=True,
            )
            self.client.add_event_handler(self.message, events.NewMessage(incoming=True, from_users=[self.owner_id]))
            if self.calls:
                self.client.add_event_handler(self.update, events.Raw())
        await self.client.connect()
        identity = await self.client.get_me()
        if identity is None or identity.bot or identity.id == self.owner_id or identity.id != int(account.UserId):
            raise RuntimeError("Telegram needs an authorized user session distinct from Wojciech")
        await self.client.get_dialogs()
        self.owner = await self.client.get_input_entity(self.owner_id)
        if self.life["owner_cursor"] is None:
            latest = await self.client.get_messages(self.owner, limit=1)
            # A live callback may already have installed a newer owner input.
            self.life["owner_cursor"] = max(self.life["owner_cursor"] or 0, latest[0].id if latest else 0)
            save(self.record.folder, self.life)
            write(ROOT / "runs" / "owner.json", {"cursor": self.life["owner_cursor"]})
        async for message in self.client.iter_messages(self.owner, min_id=self.life["owner_cursor"], reverse=True):
            if not message.out and message.sender_id == self.owner_id:
                self.owner_message(message.id, message.raw_text)
        if self.record.task is None or self.record.task.done():
            self.record.task = self.dispatch(self.record.send(self))
        self.record.append("telegram_connected", {"identity": identity.id, "calls": self.calls})

    def owner_message(self, identifier, text):
        cursor = self.life["owner_cursor"]
        if text and text.strip() and (cursor is None or identifier > cursor):
            self.emit("owner", {"id": identifier, "text": text})

    def emit(self, kind, value):
        if not self.calls and kind not in ("owner", "audio", "audio_pending"):
            observation = {"type": type(value).__name__, "error": str(value)} if isinstance(value, BaseException) else value
            receipt = self.record.append(kind, observation)
            self.life["history"].append({"observation": observation, "receipt": receipt})
        if kind == "audio_pending":
            self.life["audio_pending"][value["id"]] = value
        if kind in ("owner", "audio"):
            if kind == "audio":
                self.life["audio_pending"].pop(value["id"], None)
            text = value["text"] if kind == "owner" else value["recognition"]["text"]
            if text.strip():
                sequence = value.get("source_receipt", self.record.offset())
                if sequence < self.life.get("owner_sequence", -1):
                    value = {**value, "superseded": True}
                value = {**value, "receipt": self.record.append("owner_input", value, "OWNER", "LUNA")}
                if not value.get("superseded"):
                    activate(self.life, text, kind, value["receipt"], sequence)
                else:
                    self.life["owner_applied"] = value["receipt"]
            if kind == "owner":
                self.life["owner_cursor"] = max(self.life["owner_cursor"] or 0, value["id"])
        if kind in ("owner", "audio", "audio_pending"):
            save(self.record.folder, self.life)
            if kind == "owner":
                write(ROOT / "runs" / "owner.json", {"cursor": self.life["owner_cursor"]})
        self.generation += 1
        self.inbox.put_nowait((kind, value))

    async def message(self, event):
        try:
            if event.is_private:
                self.owner_message(event.id, event.raw_text)
                if event.media and not event.raw_text:
                    self.emit("observation", {"message": event.id, "media": "Owner sent media; text or live-call speech is available"})
        except Exception as error:
            self.emit("error", error)

    async def update(self, event):
        try:
            if isinstance(event, UpdatePhoneCallSignalingData):
                if self.peer is not None and event.phone_call_id == self.peer.id:
                    if self.linked:
                        await native(self.engine, "send_signaling_data", self.owner_id, bytes(event.data))
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
        return task

    def job_finished(self, task):
        self.jobs.remove(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self.emit("error", error)

    async def signal(self, serial, data):
        if serial != self.native_serial:
            self.record.append("expired_signal", {"serial": serial})
            return
        await self.client(SendSignalingDataRequest(peer=self.peer, data=data))

    def connection(self, serial, status):
        if serial != self.native_serial:
            return
        if status == ConnectionState.CONNECTED:
            if not self.connected.done():
                self.connected.set_result(True)
        elif status in (ConnectionState.FAILED, ConnectionState.TIMEOUT):
            self.emit("error", RuntimeError(f"Telegram transport: {status}"))
        elif status == ConnectionState.CLOSED:
            self.dispatch(self.release())
            self.emit("line", "Telegram audio transport closed.")

    def frames(self, serial, mode, device, frames):
        if serial == self.native_serial and mode == StreamMode.PLAYBACK and device == StreamDevice.MICROPHONE:
            pcm = b"".join(bytes(frame.data) for frame in frames)
            self.loop.call_soon_threadsafe(self.receive, serial, pcm)

    def receive(self, serial, pcm):
        if serial == self.native_serial and self.state == "up":
            try:
                for utterance in self.utterances.feed(pcm):
                    self.hearing.submit(utterance)
            except Exception as error:
                self.emit("error", error)

    async def prepare(self):
        self.connected = self.loop.create_future()
        self.accepted = self.loop.create_future()
        self.confirmed = self.loop.create_future()
        self.native_serial += 1
        serial = self.native_serial
        self.engine = engine = NTgCalls()
        engine.on_frames(lambda uid, mode, device, frames: self.frames(serial, mode, device, frames))
        engine.on_connection_change(lambda uid, info: self.loop.call_soon_threadsafe(self.connection, serial, info.state))
        engine.on_signaling_data(lambda uid, data: self.loop.call_soon_threadsafe(self.dispatch, self.signal(serial, bytes(data))))
        await native(engine, "create_p2p_call", self.owner_id)
        await native(engine, "set_stream_sources", self.owner_id, StreamMode.CAPTURE, media(48000))
        await native(engine, "set_stream_sources", self.owner_id, StreamMode.PLAYBACK, media(16000))
        dh = await self.client(GetDhConfigRequest(0, 256))
        return DhConfig(dh.g, bytes(dh.p), bytes(dh.random))

    async def dial(self):
        if self.state != "down":
            raise RuntimeError(f"Cannot dial: call is {self.state}")
        self.state = "dialing"
        dh = await self.prepare()
        exchange = bytes(await native(self.engine, "init_exchange", self.owner_id, dh, None))
        response = await self.client(RequestCallRequest(
            user_id=self.owner, random_id=secrets.randbelow(2**31), g_a_hash=exchange,
            protocol=protocol(), video=False,
        ))
        if isinstance(response.phone_call, PhoneCallDiscarded):
            await self.release()
            self.record.append("call_not_answered", {"reason": "discarded"})
            return {"call": self.state, "answered": False}
        self.peer = InputPhoneCall(response.phone_call.id, response.phone_call.access_hash)
        try:
            accepted = await asyncio.wait_for(self.accepted, 90)
        except asyncio.TimeoutError:
            await self.hang()
            self.record.append("call_not_answered", {"reason": "timeout"})
            return {"call": self.state, "answered": False}
        keys = await native(self.engine, "exchange_keys", self.owner_id, bytes(accepted.g_b), 0)
        response = await self.client(ConfirmCallRequest(
            peer=self.peer, g_a=bytes(keys.g_a_or_b), key_fingerprint=keys.key_fingerprint, protocol=protocol(),
        ))
        await self.connect(response.phone_call)
        return {"call": self.state, "direction": "outgoing", "answered": True}

    async def answer(self):
        if self.state != "ringing":
            raise RuntimeError(f"Cannot answer: call is {self.state}")
        if self.requested is None or self.peer is None:
            raise RuntimeError("Cannot answer: ringing call metadata is unavailable")
        dh = await self.prepare()
        exchange = bytes(await native(self.engine, "init_exchange", self.owner_id, dh, bytes(self.requested.g_a_hash)))
        response = await self.client(AcceptCallRequest(peer=self.peer, g_b=exchange, protocol=protocol()))
        call = response.phone_call
        if not isinstance(call, PhoneCall):
            call = await asyncio.wait_for(self.confirmed, 30)
        await native(self.engine, "exchange_keys", self.owner_id, bytes(call.g_a_or_b), call.key_fingerprint)
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
        await native(self.engine, "connect_p2p",
            self.owner_id, servers, list(call.protocol.library_versions), call.p2p_allowed,
            call.custom_parameters.data if call.custom_parameters is not None else None,
        )
        self.linked = True
        for signal in self.signals:
            await native(self.engine, "send_signaling_data", self.owner_id, signal)
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
            await native(self.engine, "send_external_frame",
                self.owner_id, StreamDevice.MICROPHONE, pcm[offset:offset + 960].ljust(960, b"\0"),
                FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, 0, 0),
            )
            await asyncio.sleep(max(0, started + (offset + 960) / 96000 - self.loop.time()))
        return {"transmitted_seconds": len(pcm) / 96000}

    async def send_audio(self, path, words):
        with wave.open(str(path), "rb") as source:
            duration = round(source.getnframes() / source.getframerate())
        if duration < 1:
            raise RuntimeError("Voice file is empty")
        message = await self.client.send_file(
            self.owner, str(path), caption=words, voice_note=False, force_document=False,
            attributes=[DocumentAttributeAudio(duration=duration, voice=False)],
        )
        return {"channel": "telegram", "message": message.id, "audio": str(path), "words": words}

    async def chat(self, text):
        if self.client is None or self.owner is None:
            raise RuntimeError("Telegram client is not connected")
        message = await self.client.send_message(
            self.owner, text, parse_mode=None,
        )
        self.record.append(
            "chat", {"text": text, "message": message.id}, "LUNA", "OWNER",
        )
        return {"sent": text, "message": message.id}

    async def deliver_text(self, text, random_id):
        request = SendMessageRequest(self.owner, text, random_id=random_id, no_webpage=True)
        try:
            result = await self.client(request)
        except FloodWaitError:
            raise
        except Exception:
            return await self.delivery_lookup(text, random_id)
        if isinstance(result, UpdateShortSentMessage):
            return {"message": result.id, "random_id": random_id}
        message = self.client._get_response_message(request, result, self.owner)
        if message is None:
            return await self.delivery_lookup(text, random_id)
        return {"message": message.id, "random_id": random_id}

    async def deliver_file(self, file, caption, random_id):
        _, uploaded, _ = await self.client._file_to_media(file, force_document=True)
        request = SendMediaRequest(self.owner, uploaded, caption, random_id=random_id)
        try:
            result = await self.client(request)
        except FloodWaitError:
            raise
        except Exception:
            return await self.delivery_lookup(caption, random_id)
        message = self.client._get_response_message(request, result, self.owner)
        if message is None:
            return await self.delivery_lookup(caption, random_id)
        return {"message": message.id, "random_id": random_id}

    async def delivery_lookup(self, text, random_id):
        marker = next(part for part in text.splitlines() if part.startswith("#trident_"))
        async for message in self.client.iter_messages(self.owner, search=marker, limit=10):
            if message.out and message.raw_text == text:
                return {"message": message.id, "random_id": random_id, "recovered_from_history": True}
        raise RuntimeError("Telegram returned no recoverable delivery receipt; piece remains pending")

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
        self.native_serial += 1
        self.peer = self.requested = None
        self.state, self.since, self.linked = "down", 0, False
        self.signals.clear()
        tail = self.utterances.finish()
        if tail:
            self.hearing.submit(tail)
        if engine is not None:
            await native(engine, "stop", self.owner_id)
            del engine
        self.record.append("call_closed", {})

    async def stop_calls(self):
        if self.client is not None and self.calls:
            # Keep owner text live until disconnect, but admit no new call
            # callbacks while hearing and voice are being dismantled.
            self.client.remove_event_handler(self.update)
        failures = []
        if self.peer is not None:
            try:
                await self.hang()
            except Exception as error:
                failures.append(str(error))
        if self.engine is not None:
            try:
                await self.release()
            except Exception as error:
                failures.append(str(error))
        await cancel(*(task for task in self.jobs if task != self.record.task))
        self.record.append("call_callbacks_closed", {"failures": failures})
        if failures:
            raise RuntimeError("; ".join(failures))

    async def close(self):
        failures = []
        for operation in (self.stop_calls, self.record.close,
                          self.client.disconnect if self.client is not None else None):
            if operation is None:
                continue
            try:
                await operation()
            except Exception as error:
                failures.append(str(error))
        self.record.append("telegram_closed", {"failures": failures})
        if failures:
            raise RuntimeError("; ".join(failures))
