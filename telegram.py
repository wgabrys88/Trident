import asyncio, gc, io, json, os, random, re, threading, time
import numpy as np
from PIL import Image
from ntgcalls import (AudioDescription, ConnectionState, DhConfig, FrameData, MediaDescription, MediaSource,
                     NTgCalls, RTCServer, StreamDevice, StreamMode, VideoDescription, VIDEO_ROTATION_0)
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from telethon.sessions import MemorySession
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import (AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest,
                                       RequestCallRequest, SendSignalingDataRequest)
from telethon.tl.types import (InputPhoneCall, PhoneCall, PhoneCallAccepted, PhoneCallDiscarded,
    PhoneCallDiscardReasonHangup, PhoneCallDiscardReasonMissed, PhoneCallProtocol, PhoneCallRequested,
    PhoneConnection, PhoneConnectionWebrtc, UpdatePhoneCall, UpdatePhoneCallSignalingData)
from audio import Segmenter
from core import CONFIG, timestamp
from desktop import image_size
from usage import Usage

OWNER, CFG = CONFIG["owner"]["telegram_id"], CONFIG["telegram"]

def protocol():
    p = NTgCalls.get_protocol()
    return PhoneCallProtocol(udp_p2p=p.udp_p2p, udp_reflector=p.udp_reflector, min_layer=65, max_layer=92,
                             library_versions=list(reversed(p.library_versions)))

def media(rate, size=None):
    return MediaDescription(microphone=AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True), speaker=None,
        camera=VideoDescription(MediaSource.EXTERNAL, *size, CFG["desk_fps"], "", True) if size else None, screen=None)

def i420(png):
    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.int32)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    y = np.clip(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16, 16, 235).astype(np.uint8)
    r, g, b = [(c[::2, ::2] + c[1::2, ::2] + c[::2, 1::2] + c[1::2, 1::2] + 2) >> 2 for c in (r, g, b)]
    u = np.clip(((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128, 16, 240).astype(np.uint8)
    v = np.clip(((112 * r - 94 * g - 18 * b + 128) >> 8) + 128, 16, 240).astype(np.uint8)
    return y.tobytes() + u.tobytes() + v.tobytes(), (rgb.shape[1], rgb.shape[0])

def call_text(name, arguments):
    if isinstance(arguments, str):
        arguments = json.loads(arguments) if arguments else {}
    if not arguments:
        return f"{name}()"
    return name + "(" + ", ".join(f"{key}={value!r}" for key, value in arguments.items()) + ")"

def plain(content):
    if isinstance(content, str):
        return content
    lines = []
    for part in content:
        if part.get("type") == "text" and part.get("text"):
            lines.append(part["text"])
        elif part.get("type") == "image_url":
            lines.append("[screenshot]")
    return "\n".join(lines)

def assistant_lines(message):
    parts = []
    if message.get("reasoning_content"):
        parts.append("Thinking:\n" + message["reasoning_content"])
    if message.get("content"):
        parts.append("Message:\n" + message["content"])
    for call in message.get("tool_calls") or []:
        function = call["function"]
        parts.append("Tool call:\n" + call_text(function["name"], function.get("arguments") or {}))
    return "\n\n".join(parts)

def context_limit():
    args = CONFIG["brain"]["server_args"]
    return args[args.index("--ctx-size") + 1]

def brain_request(messages):
    blocks = ["TRIDENT -> GEMMA", ""]
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "assistant":
            blocks.append(assistant_lines(message))
        elif role == "tool":
            blocks.append("Tool response:\n" + str(message.get("content") or ""))
        else:
            text = plain(message.get("content"))
            if text:
                blocks.append("User:\n" + text)
        blocks.append("")
    return "\n".join(blocks).strip() + "\n"

def brain_response(raw):
    data = json.loads(raw)
    usage = data["usage"]
    body = assistant_lines(data["choices"][0]["message"])
    return f"GEMMA -> TRIDENT\n\n{body}\n\nContext size: {context_limit()}\nTokens used: {usage['prompt_tokens']}\n"

def tool_record(name, arguments, words):
    return f"GEMMA -> TRIDENT\n\nTool call:\n{call_text(name, arguments)}\n\nTool response:\n{words}\n"

class Line:
    def __init__(self, events_queue, folder):
        self.events, self.folder, self.usage = events_queue, folder, Usage(folder)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.client = self.owner = self.calls = self.phone = self.image = None
        self.connected = self.accepted = self.confirmed = None
        self.state, self.began, self.linked, self.signals = "down", 0, False, []
        self.segmenter, self.checkpoint, self.video_task = Segmenter(), lambda: None, None

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

    def send(self, text, files=(), model="Trident", direction="event"):
        self.wait(self.emit(text, files, model, direction), 600)

    async def emit(self, text, files=(), model="Trident", direction="event"):
        stamp = timestamp()
        filename_model = re.sub(r'[<>:\"/\\|?*\x00-\x1f]', '_', model)
        stem = f"{stamp}_{filename_model}_{direction}"
        self.usage.record(stamp, model, direction)
        path = self.folder / (stem + ".txt")
        path.write_bytes(text.encode("utf-8"))
        if files:
            suffix, data = files[0]
            image_path = self.folder / f"{stem}.{suffix}"
            image_path.write_bytes(data)
            if len(text) > 1024:
                raise RuntimeError("Record with an image exceeds the Telegram caption limit")
            await self.client.send_file(self.owner, str(image_path), caption=text, force_document=True)
        elif len(text) <= 4096:
            await self.client.send_message(self.owner, text, parse_mode=None)
        else:
            await self.client.send_file(self.owner, str(path), force_document=True)

    def start(self):
        self.thread.start()
        self.wait(self.connect(), 180)

    async def connect(self):
        [account] = [a for a in TDesktop(os.path.expandvars(CFG["tdata"])).accounts if int(a.UserId) != OWNER]
        self.client = await TelegramClient.FromTDesktop(account, session=MemorySession(),
                                                       flag=UseCurrentSession, api=API.TelegramDesktop)
        self.client.add_event_handler(lambda event: self.guard(self.message(event)), events.NewMessage(incoming=True, from_users=[OWNER]))
        self.client.add_event_handler(lambda event: self.guard(self.raw(event)), events.Raw())
        await self.client.connect()
        me = await self.client.get_me()
        if me is None or me.bot:
            raise RuntimeError("Telegram requires an authorized user account")
        await self.client.get_dialogs()
        self.owner = await self.client.get_input_entity(OWNER)

    async def message(self, event):
        if event.is_private:
            text = event.raw_text
            if event.document:
                content = (await event.download_media(bytes)).decode("utf-8-sig")
                text = text + "\n" + content if text else content
            if text:
                self.events.put(("text", text))

    async def guard(self, coroutine):
        try:
            return await coroutine
        except Exception as error:
            self.events.put(("fatal", error))

    async def raw(self, update):
        if isinstance(update, UpdatePhoneCallSignalingData):
            if self.phone and update.phone_call_id == self.phone.id:
                if self.linked:
                    await self.calls.send_signaling_data(OWNER, bytes(update.data))
                else:
                    self.signals.append(bytes(update.data))
        elif isinstance(update, UpdatePhoneCall):
            call = update.phone_call
            if isinstance(call, PhoneCallRequested):
                if call.admin_id == OWNER and self.state == "down":
                    self.state = "ringing"
                    asyncio.create_task(self.guard(self.place(call)))
                else:
                    await self.client(DiscardCallRequest(peer=InputPhoneCall(call.id, call.access_hash), duration=0,
                        reason=PhoneCallDiscardReasonMissed(), connection_id=0, video=call.video))
            elif (isinstance(call, PhoneCallAccepted) and call.participant_id == OWNER and self.accepted
                  and not self.accepted.done()):
                self.phone = InputPhoneCall(call.id, call.access_hash)
                self.accepted.set_result(call)
            elif self.phone and call.id == self.phone.id:
                if isinstance(call, PhoneCall) and self.confirmed and not self.confirmed.done():
                    self.confirmed.set_result(call)
                elif isinstance(call, PhoneCallDiscarded):
                    await self.close()

    async def begin(self):
        self.calls = engine = NTgCalls()
        engine.on_frames(lambda uid, mode, device, frames: self.frames(engine, mode, device, frames))
        engine.on_connection_change(lambda uid, info: self.loop.call_soon_threadsafe(self.connection, engine, info.state))
        engine.on_signaling_data(lambda uid, data: asyncio.run_coroutine_threadsafe(
            self.guard(self.client(SendSignalingDataRequest(peer=self.phone, data=bytes(data)))), self.loop)
            if self.calls is engine and self.phone else None)
        self.connected, self.accepted, self.confirmed = self.loop.create_future(), None, None
        self.linked, self.signals = False, []
        self.segmenter.reset()
        await engine.create_p2p_call(OWNER)
        await engine.set_stream_sources(OWNER, StreamMode.CAPTURE, media(48000, image_size()))
        await engine.set_stream_sources(OWNER, StreamMode.PLAYBACK, media(16000))

    def connection(self, engine, state):
        if self.calls is engine:
            if state == ConnectionState.CONNECTED and not self.connected.done():
                self.connected.set_result(True)
            elif state in (ConnectionState.CLOSED, ConnectionState.FAILED, ConnectionState.TIMEOUT):
                asyncio.create_task(self.guard(self.close()))
                if state != ConnectionState.CLOSED:
                    self.events.put(("fatal", RuntimeError(f"Call: {state}")))

    async def dh(self):
        cfg = await self.client(GetDhConfigRequest(0, 256))
        return DhConfig(cfg.g, bytes(cfg.p), bytes(cfg.random))

    async def link(self, call):
        servers = []
        for c in call.connections:
            if isinstance(c, PhoneConnectionWebrtc):
                servers.append(RTCServer(c.id, c.ip, c.ipv6 or "", c.port, c.username, c.password, c.turn, c.stun, False, None))
            elif isinstance(c, PhoneConnection):
                servers.append(RTCServer(c.id, c.ip, c.ipv6 or "", c.port, None, None, False, True, c.tcp, bytes(c.peer_tag)))
        self.phone = InputPhoneCall(call.id, call.access_hash)
        await self.calls.connect_p2p(OWNER, servers, list(call.protocol.library_versions), call.p2p_allowed,
                                     call.custom_parameters.data if call.custom_parameters else None)
        self.linked = True
        for data in self.signals:
            await self.calls.send_signaling_data(OWNER, data)
        self.signals = []
        await asyncio.wait_for(self.connected, 30)
        self.state, self.began = "up", time.monotonic()
        self.video_task = asyncio.create_task(self.guard(self.video(self.calls)))

    async def place(self, requested=None):
        if requested is None and self.state != "down":
            raise RuntimeError(f"Call: {self.state}")
        self.state = "ringing"
        try:
            if requested:
                self.phone = InputPhoneCall(requested.id, requested.access_hash)
            await self.begin()
            exchange = bytes(await self.calls.init_exchange(OWNER, await self.dh(), bytes(requested.g_a_hash) if requested else None))
            self.confirmed, self.accepted = self.loop.create_future(), self.loop.create_future()
            if requested:
                result = await self.client(AcceptCallRequest(peer=self.phone, g_b=exchange, protocol=protocol()))
                call = result.phone_call if isinstance(result.phone_call, PhoneCall) else await asyncio.wait_for(self.confirmed, 30)
                await self.calls.exchange_keys(OWNER, bytes(call.g_a_or_b), call.key_fingerprint)
            else:
                result = await self.client(RequestCallRequest(user_id=self.owner, random_id=random.randrange(2**31),
                                          g_a_hash=exchange, protocol=protocol(), video=True))
                if isinstance(result.phone_call, PhoneCallDiscarded):
                    raise RuntimeError("The call was discarded")
                self.phone = InputPhoneCall(result.phone_call.id, result.phone_call.access_hash)
                accepted = await asyncio.wait_for(self.accepted, 90)
                auth = await self.calls.exchange_keys(OWNER, bytes(accepted.g_b), 0)
                result = await self.client(ConfirmCallRequest(peer=self.phone, g_a=bytes(auth.g_a_or_b),
                                          key_fingerprint=auth.key_fingerprint, protocol=protocol()))
                call = result.phone_call
            await self.link(call)
        except BaseException:
            await self.hang()
            raise

    async def hang(self):
        try:
            if self.phone:
                await self.client(DiscardCallRequest(peer=self.phone, duration=int(time.monotonic() - self.began) if self.began else 0,
                                  reason=PhoneCallDiscardReasonHangup(), connection_id=0, video=True))
        finally:
            await self.close()

    async def close(self):
        self.state = "down"
        task, self.video_task = self.video_task, None
        if task:
            await task
        self.linked = False
        calls, self.calls, self.phone, self.began = self.calls, None, None, 0
        clip = self.segmenter.finish()
        if clip is not None:
            self.events.put(("audio", clip))
        for pending in (self.connected, self.accepted, self.confirmed):
            if pending and not pending.done():
                pending.cancel()
        if calls:
            calls.on_frames(lambda uid, mode, device, frames: None)
            calls.on_connection_change(lambda uid, info: None)
            calls.on_signaling_data(lambda uid, data: None)
            await calls.stop(OWNER)
            del calls
            gc.collect()

    async def reserve_restart(self):
        if self.state != "down":
            return False
        self.state = "restarting"
        return True

    def frames(self, engine, mode, device, frames):
        if self.calls is engine and mode == StreamMode.PLAYBACK and device == StreamDevice.MICROPHONE:
            self.loop.call_soon_threadsafe(self.audio, engine, b"".join(bytes(frame.data) for frame in frames))

    def audio(self, engine, pcm):
        if self.calls is engine and self.up:
            try:
                for clip in self.segmenter.push(pcm):
                    self.events.put(("audio", clip))
            except Exception as error:
                self.events.put(("fatal", error))

    async def frame(self, device, data, size=(0, 0)):
        if not self.up:
            raise RuntimeError("Call: down")
        await self.calls.send_external_frame(OWNER, device, data, FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, *size))

    async def show(self, png):
        self.image = i420(png)
        if self.up:
            await self.frame(StreamDevice.CAMERA, *self.image)

    async def video(self, calls):
        while self.up and self.calls is calls:
            if self.image:
                await self.frame(StreamDevice.CAMERA, *self.image)
            await asyncio.sleep(1 / CFG["desk_fps"])

    async def speak(self, pcm):
        started = time.monotonic()
        for n, offset in enumerate(range(0, len(pcm), 960), 1):
            self.checkpoint()
            await self.frame(StreamDevice.MICROPHONE, pcm[offset:offset + 960].ljust(960, b"\0"))
            await asyncio.sleep(max(0, started + n * 0.01 - time.monotonic()))

    async def disconnect(self):
        try:
            await self.hang()
        finally:
            if self.client:
                await self.client.disconnect()

    def stop(self):
        try:
            if self.thread.is_alive():
                self.wait(self.disconnect(), 30)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            self.usage.close()
