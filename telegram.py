import asyncio
import array
import io
import os
import sys
import time
import wave
from pathlib import Path

import i2c


def rms(frame):
    count = len(frame) // 2
    if not count:
        return 0.0
    total = 0
    for index in range(0, count * 2, 2):
        sample = int.from_bytes(frame[index:index + 2], "little", signed=True)
        total += sample * sample
    return (total / count) ** 0.5 / 32768


def pcm_play(payload, rate):
    with wave.open(io.BytesIO(payload), "rb") as source:
        if source.getsampwidth() != 2:
            raise RuntimeError("Voice output must be PCM16 WAV")
        source_rate, channels = source.getframerate(), source.getnchannels()
        frames = source.readframes(source.getnframes())
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if channels > 1:
        mixed = array.array("h")
        for index in range(0, len(samples), channels):
            mixed.append(int(sum(samples[index:index + channels]) / channels))
        samples = mixed
    if source_rate != rate and samples:
        count = int(round(len(samples) * rate / source_rate))
        last = len(samples) - 1
        output = array.array("h")
        for index in range(count):
            position = index * source_rate / rate
            left = int(position)
            if left >= last:
                output.append(samples[last])
                continue
            mix = samples[left] + (samples[left + 1] - samples[left]) * (position - left)
            output.append(max(-32768, min(32767, int(round(mix)))))
        samples = output
    if not samples:
        raise RuntimeError("Voice output is empty")
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def plan(state, data):
    if not data:
        raise i2c.Nack()
    reg, body = data[0], data[1:]
    if reg == 1:
        if state != "down":
            raise i2c.Nack()
        return "dial", body
    if reg == 2:
        return "hang", body
    if reg == 0x10:
        return "chat", body
    if reg == 0x20:
        return "play", body
    raise i2c.Nack()


def bind():
    global AudioDescription, ConnectionState, DhConfig, FrameData, MediaDescription, MediaSource
    global NTgCalls, RTCServer, StreamDevice, StreamMode, VIDEO_ROTATION_0
    global API, UseCurrentSession, TDesktop, TelegramClient, events
    global GetDhConfigRequest, AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest
    global RequestCallRequest, SendSignalingDataRequest
    global InputPhoneCall, PhoneCall, PhoneCallAccepted, PhoneCallDiscarded
    global PhoneCallDiscardReasonHangup, PhoneCallProtocol, PhoneCallRequested
    global PhoneConnection, PhoneConnectionWebrtc, UpdatePhoneCall, UpdatePhoneCallSignalingData
    import logging
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
        InputPhoneCall, PhoneCall, PhoneCallAccepted, PhoneCallDiscarded,
        PhoneCallDiscardReasonHangup, PhoneCallProtocol, PhoneCallRequested,
        PhoneConnection, PhoneConnectionWebrtc, UpdatePhoneCall, UpdatePhoneCallSignalingData,
    )
    logging.getLogger("telethon").setLevel(logging.CRITICAL)


def endpoints(call):
    found = []
    for item in call.connections:
        if isinstance(item, PhoneConnectionWebrtc):
            found.append(RTCServer(
                item.id, item.ip, item.ipv6, item.port,
                item.username, item.password, item.turn, item.stun, False, None,
            ))
        elif isinstance(item, PhoneConnection):
            found.append(RTCServer(
                item.id, item.ip, item.ipv6, item.port,
                None, None, False, True, item.tcp, bytes(item.peer_tag),
            ))
        else:
            raise RuntimeError(f"Unsupported Telegram connection: {type(item).__name__}")
    return found


class Telegram:
    def __init__(self, bus, cfg, run, root):
        self.bus = bus
        self.cfg = cfg
        self.run = Path(run)
        self.root = Path(root)
        self.owner = int(cfg["owner"]["telegram_id"])
        self.ears = i2c.addr(cfg, "ears")
        self.voice = i2c.addr(cfg, "voice")
        self.luna = i2c.addr(cfg, "luna")
        self.timer = i2c.addr(cfg, "timer")
        self.state = "down"
        self.client = None
        self.entity = None
        self.engine = None
        self.peer = None
        self.serial = 0
        self.cursor = 0
        self.since = 0
        self.signals = []
        self.linked = False
        self.jobs = set()
        self.out = []
        self.mic = []
        self.pending = b""
        self.lead = []
        self.parts = []
        self.quiet = 0
        self.pcm_n = 0
        self.pending_play = []
        ears = cfg["ears"]
        limits = cfg["limits"]
        self.rms_limit = float(ears["rms_threshold"])
        self.silence = int(ears["silence_ms"])
        self.frame_ms = int(limits["frame_ms"])
        self.frame_bytes = int(limits["pcm_rate"]) * 2 * self.frame_ms // 1000
        self.lead_limit = max(1, int(ears["padding_ms"]) // self.frame_ms)
        self.utterance = float(ears["utterance_seconds"])
        self.ring = float(limits["ring_seconds"])
        self.connect = float(limits["connect_seconds"])
        self.chat_slice = int(limits["chat_slice"])
        self.play_frame = int(limits["play_frame"])
        self.play_rate = int(limits["play_rate"])
        self.loop = None
        self.accepted = None
        self.confirmed = None
        self.connected = None
        self.dh = None
        self.requested = None
        self.releasing = False

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.jobs.add(task)
        return task

    def fail(self):
        for task in list(self.jobs):
            if not task.done():
                continue
            self.jobs.discard(task)
            if not task.cancelled() and task.exception():
                raise task.exception()

    def cut(self, pcm):
        ready = []
        self.pending += pcm
        while len(self.pending) >= self.frame_bytes:
            frame, self.pending = self.pending[:self.frame_bytes], self.pending[self.frame_bytes:]
            loud = rms(frame) >= self.rms_limit
            if not self.parts:
                self.lead.append(frame)
                if len(self.lead) > self.lead_limit:
                    self.lead = self.lead[-self.lead_limit:]
                if loud:
                    self.parts = self.lead
                    self.lead = []
                    self.quiet = 0
            else:
                self.parts.append(frame)
                self.quiet = 0 if loud else self.quiet + self.frame_ms
                long = len(self.parts) * (self.frame_ms / 1000) >= self.utterance
                if self.quiet >= self.silence or long:
                    ready.append(b"".join(self.parts))
                    self.parts = []
                    self.quiet = 0
        return ready

    def flush_audio(self):
        audio = b"".join(self.parts) + self.pending if self.parts else b""
        self.pending = b""
        self.lead = []
        self.parts = []
        self.quiet = 0
        return audio

    def queue_pcm(self, pcm):
        self.mic.append(pcm)

    def status(self):
        return {"down": 0, "dialing": 1, "up": 2}[self.state]

    async def on_frame(self, _src, line):
        if line.split()[2] == "R" and not i2c.write_payload(line):
            return i2c.with_payload(line, bytes([self.status()]))
        action, body = plan(self.state, i2c.write_payload(line))
        if action == "dial":
            self.state = "dialing"
            self.spawn(self.dial())
        elif action == "hang":
            self.spawn(self.hang())
        elif action == "chat":
            self.spawn(self.chat(body.decode()))
        else:
            self.pending_play.append(body.decode())
        return i2c.pack_write(self.bus.addr, i2c.write_payload(line))

    async def chat(self, text):
        await self.send(text)
        if self.state == "up":
            self.out.append((self.voice, bytes([1]) + text.encode()))

    async def send(self, text):
        if not text:
            return
        async with self.lock:
            for offset in range(0, len(text), self.chat_slice):
                await self.client.send_message(self.entity, text[offset:offset + self.chat_slice], parse_mode=None)

    def miss(self):
        self.state = "down"
        self.out.append((self.timer, b"\x02"))

    def heard(self, pcm):
        pieces = self.cut(b"" if pcm is None else pcm)
        if pcm is None:
            audio = self.flush_audio()
            if audio:
                pieces.append(audio)
        for piece in pieces:
            if not piece:
                continue
            self.pcm_n += 1
            path = self.run / "pcm" / f"{self.pcm_n}.pcm"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(piece)
            self.out.append((self.ears, bytes([1]) + str(path).encode()))

    async def flush_out(self):
        while self.out:
            target, payload = self.out.pop(0)
            await self.bus.request(target, i2c.pack_write(target, payload))

    async def pump(self):
        self.fail()
        while self.mic:
            self.heard(self.mic.pop(0))
        if self.pending_play and self.state == "up" and self.engine is not None:
            await self.play(pcm_play(Path(self.pending_play.pop(0)).read_bytes(), self.play_rate))
        await self.flush_out()
        self.fail()

    def protocol(self):
        native = NTgCalls.get_protocol()
        return PhoneCallProtocol(
            udp_p2p=native.udp_p2p, udp_reflector=native.udp_reflector,
            min_layer=65, max_layer=native.max_layer,
            library_versions=list(reversed(native.library_versions)),
        )

    def media(self, rate):
        return MediaDescription(AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True), None, None, None)

    async def call(self, engine, method, *arguments):
        pending = getattr(engine, method)(*arguments)
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            await pending
            raise

    def frames(self, serial, mode, device, frames):
        if serial != self.serial or mode != StreamMode.PLAYBACK or device != StreamDevice.MICROPHONE:
            return
        pcm = b"".join(bytes(frame.data) for frame in frames)
        if pcm:
            self.loop.call_soon_threadsafe(self.queue_pcm, pcm)

    def connection(self, serial, status):
        if serial != self.serial:
            return
        if status == ConnectionState.CONNECTED and self.connected is not None and not self.connected.done():
            self.connected.set_result(True)
        elif status in (ConnectionState.FAILED, ConnectionState.TIMEOUT):
            self.spawn(self._boom(RuntimeError(f"Telegram transport: {status}")))
        elif status == ConnectionState.CLOSED:
            self.spawn(self.release())

    async def _boom(self, error):
        raise error

    def outgoing(self, serial, data):
        if serial == self.serial:
            self.spawn(self.signal(serial, data))

    async def signal(self, serial, data):
        if serial == self.serial and self.peer is not None:
            await self.client(SendSignalingDataRequest(peer=self.peer, data=data))

    async def prepare(self):
        self.serial += 1
        serial = self.serial
        self.accepted = self.loop.create_future()
        self.confirmed = self.loop.create_future()
        self.connected = self.loop.create_future()
        self.linked = False
        self.engine = NTgCalls()
        self.engine.on_frames(lambda uid, mode, device, frames: self.frames(serial, mode, device, frames))
        self.engine.on_connection_change(
            lambda uid, info: self.loop.call_soon_threadsafe(self.connection, serial, info.state))
        self.engine.on_signaling_data(
            lambda uid, data: self.loop.call_soon_threadsafe(self.outgoing, serial, bytes(data)))
        await self.call(self.engine, "create_p2p_call", self.owner)
        await self.call(self.engine, "set_stream_sources", self.owner, StreamMode.CAPTURE, self.media(48000))
        await self.call(self.engine, "set_stream_sources", self.owner, StreamMode.PLAYBACK, self.media(16000))
        dh = await self.client(GetDhConfigRequest(0, 256))
        self.dh = DhConfig(dh.g, bytes(dh.p), bytes(dh.random))

    async def connect(self, call):
        custom = call.custom_parameters.data if call.custom_parameters is not None else None
        await self.call(
            self.engine, "connect_p2p", self.owner, endpoints(call),
            list(call.protocol.library_versions), call.p2p_allowed, custom,
        )
        self.linked = True
        for signal in self.signals:
            await self.call(self.engine, "send_signaling_data", self.owner, signal)
        self.signals = []
        await asyncio.wait_for(self.connected, self.connect)
        self.state = "up"
        self.since = time.monotonic()

    async def release(self):
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
            if engine is not None:
                await self.call(engine, "stop", self.owner)
        finally:
            self.releasing = False

    async def dial(self):
        import secrets
        await self.prepare()
        exchange = bytes(await self.call(self.engine, "init_exchange", self.owner, self.dh, None))
        response = await self.client(RequestCallRequest(
            user_id=self.entity, random_id=secrets.randbelow(2**31), g_a_hash=exchange,
            protocol=self.protocol(), video=False,
        ))
        if isinstance(response.phone_call, PhoneCallDiscarded):
            await self.release()
            self.miss()
            return
        ringing = response.phone_call
        self.peer = InputPhoneCall(ringing.id, ringing.access_hash)
        try:
            accepted = await asyncio.wait_for(self.accepted, self.ring)
        except TimeoutError:
            await self.hang()
            self.miss()
            return
        keys = await self.call(self.engine, "exchange_keys", self.owner, bytes(accepted.g_b), 0)
        response = await self.client(ConfirmCallRequest(
            peer=self.peer, g_a=bytes(keys.g_a_or_b),
            key_fingerprint=keys.key_fingerprint, protocol=self.protocol(),
        ))
        await self.connect(response.phone_call)

    async def answer(self):
        await self.prepare()
        exchange = bytes(await self.call(
            self.engine, "init_exchange", self.owner, self.dh, bytes(self.requested.g_a_hash),
        ))
        response = await self.client(AcceptCallRequest(peer=self.peer, g_b=exchange, protocol=self.protocol()))
        call = response.phone_call
        if not isinstance(call, PhoneCall):
            call = await asyncio.wait_for(self.confirmed, self.connect)
        await self.call(self.engine, "exchange_keys", self.owner, bytes(call.g_a_or_b), call.key_fingerprint)
        await self.connect(call)

    async def hang(self):
        peer = self.peer
        if peer is not None and self.state != "down":
            await self.client(DiscardCallRequest(
                peer=peer, duration=int(time.monotonic() - self.since) if self.since else 0,
                reason=PhoneCallDiscardReasonHangup(), connection_id=0, video=False,
            ))
        await self.release()

    async def play(self, pcm):
        if self.state != "up" or self.engine is None:
            return
        engine, serial, started = self.engine, self.serial, self.loop.time()
        step = self.play_frame
        pace = self.play_rate * 2
        for offset in range(0, len(pcm), step):
            if serial != self.serial or self.state != "up":
                return
            chunk = pcm[offset:offset + step].ljust(step, b"\0")
            await self.call(
                engine, "send_external_frame", self.owner, StreamDevice.MICROPHONE, chunk,
                FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, 0, 0),
            )
            await asyncio.sleep(max(0, started + (offset + step) / pace - self.loop.time()))

    async def update(self, event):
        if isinstance(event, UpdatePhoneCallSignalingData):
            if self.peer is not None and event.phone_call_id == self.peer.id:
                if self.linked:
                    await self.call(self.engine, "send_signaling_data", self.owner, bytes(event.data))
                else:
                    self.signals.append(bytes(event.data))
            return
        if not isinstance(event, UpdatePhoneCall):
            return
        call = event.phone_call
        if isinstance(call, PhoneCallRequested):
            if call.admin_id != self.owner or self.state != "down":
                raise RuntimeError("Unexpected Telegram caller or overlapping call")
            self.requested = call
            self.peer = InputPhoneCall(call.id, call.access_hash)
            self.state = "dialing"
            self.spawn(self.answer())
        elif isinstance(call, PhoneCallAccepted) and call.participant_id == self.owner and self.state == "dialing":
            self.peer = InputPhoneCall(call.id, call.access_hash)
            if self.accepted is not None and not self.accepted.done():
                self.accepted.set_result(call)
        elif self.peer is not None and call.id == self.peer.id:
            if isinstance(call, PhoneCall) and self.confirmed is not None and not self.confirmed.done():
                self.confirmed.set_result(call)
            elif isinstance(call, PhoneCallDiscarded):
                self.spawn(self.release())

    async def message(self, event):
        if not event.is_private or event.id <= self.cursor:
            return
        self.cursor = event.id
        text = (event.raw_text or "").strip()
        if text:
            self.out.append((self.luna, text.encode()))

    async def start(self):
        bind()
        self.loop = asyncio.get_running_loop()
        self.lock = asyncio.Lock()
        desktop = TDesktop(os.path.expandvars(self.cfg["telegram"]["tdata"]))
        accounts = [item for item in desktop.accounts if int(item.UserId) != self.owner]
        if len(accounts) != 1:
            raise RuntimeError(f"Telegram tdata needs one user session besides the owner; found {len(accounts)}")
        user = accounts[0]
        stamp = self.run.name.removeprefix("RUN_")
        session = self.root / "session" / stamp / "telegram"
        session.parent.mkdir(parents=True, exist_ok=True)
        self.client = await TelegramClient.FromTDesktop(
            user, session=str(session), flag=UseCurrentSession, api=API.TelegramDesktop,
            request_retries=0, connection_retries=0, auto_reconnect=True, flood_sleep_threshold=0,
            raise_last_call_error=True, catch_up=True,
        )
        self.client.add_event_handler(self.message, events.NewMessage(incoming=True, from_users=[self.owner]))
        self.client.add_event_handler(self.update, events.Raw())
        await self.client.connect()
        identity = await self.client.get_me()
        if identity is None or identity.bot or identity.id == self.owner or identity.id != int(user.UserId):
            raise RuntimeError("Telegram needs an authorized user session distinct from the owner")
        await self.client.get_dialogs()
        self.entity = await self.client.get_input_entity(self.owner)
        latest = await self.client.get_messages(self.entity, limit=1)
        self.cursor = latest[0].id if latest else 0

    async def stop(self):
        if self.state != "down" or self.engine is not None:
            await self.release()
        for task in list(self.jobs):
            task.cancel()
        if self.jobs:
            await asyncio.gather(*self.jobs, return_exceptions=True)
        if self.client is not None:
            await self.client.disconnect()


def main():
    root, cfg = i2c.load()
    run = Path(sys.argv[1])
    bus = i2c.Bus(root, i2c.addr(cfg, "telegram"), run, cfg)
    phone = Telegram(bus, cfg, run, root)

    async def live():
        await phone.start()
        try:
            await bus.run(phone.on_frame, phone.pump)
        finally:
            await phone.stop()

    i2c.entry(live)


def test():
    import tempfile

    assert plan("down", b"\x01") == ("dial", b"")
    try:
        plan("up", b"\x01")
        raise AssertionError("dial while up")
    except i2c.Nack:
        pass
    assert plan("up", b"\x02")[0] == "hang"
    assert plan("down", bytes([0x10]) + b"hi") == ("chat", b"hi")
    assert plan("up", bytes([0x20]) + b"C:/a.wav") == ("play", b"C:/a.wav")

    async def run():
        root = Path(tempfile.mkdtemp())
        cfg = i2c.test_cfg()
        cfg["owner"] = {"telegram_id": 1}
        cfg["telegram"] = {"tdata": "tdata"}
        cfg["ears"]["silence_ms"] = 40
        cfg["ears"]["padding_ms"] = int(cfg["limits"]["frame_ms"])
        run_dir = root / "RUN_test"
        run_dir.mkdir()
        got = []

        def listen(address):
            async def on_frame(_src, line):
                got.append((address, i2c.write_payload(line)))
                return i2c.pack_write(address, i2c.write_payload(line))
            return on_frame

        peers = []
        stop = [False]
        for address in (0x10, 0x12, 0x13, 0x16):
            bus = i2c.Bus(root, address, run_dir, cfg)
            bus.up()
            peers.append(asyncio.create_task(i2c._peer(bus, listen(address), stop)))
        phone_bus = i2c.Bus(root, 0x11, run_dir, cfg)
        phone = Telegram(phone_bus, cfg, run_dir, root)
        class Box:
            def __init__(self):
                self.sent = []

            async def send_message(self, _entity, text, parse_mode=None):
                self.sent.append(text)

        phone.client = Box()
        phone.entity = "owner"
        phone.lock = asyncio.Lock()
        loop = asyncio.create_task(phone_bus.run(phone.on_frame, phone.pump))
        for _ in range(40):
            if phone_bus.present(0x11):
                break
            await asyncio.sleep(0.02)
        master = i2c.Bus(root, 0x16, run_dir, cfg)
        master.seq = 50
        await master.request(0x11, i2c.pack_write(0x11, bytes([0x10]) + b"hello"))
        for _ in range(40):
            if phone.client.sent:
                break
            await asyncio.sleep(0.02)
        assert phone.client.sent == ["hello"]
        phone.state = "up"
        await master.request(0x11, i2c.pack_write(0x11, bytes([0x10]) + b"hi"))
        for _ in range(40):
            if any(item[0] == 0x13 and item[1].startswith(b"\x01hi") for item in got):
                break
            await asyncio.sleep(0.02)
        assert any(item[0] == 0x13 and item[1].startswith(b"\x01hi") for item in got)
        status = await master.request(0x11, "S 11 R A P")
        assert i2c.read_payload(status) == b"\x02"
        dial = await master.transfer(0x11, i2c.pack_write(0x11, b"\x01"))
        assert dial.split()[5] == "NA"
        phone.miss()
        for _ in range(40):
            if any(item[0] == 0x10 and item[1] == b"\x02" for item in got):
                break
            await asyncio.sleep(0.02)
        assert any(item[0] == 0x10 and item[1] == b"\x02" for item in got)
        samples = phone.frame_bytes // 2
        loud = (b"\xff\x0f" * samples) * 3 + (b"\x00\x00" * samples) * 4
        phone.heard(loud)
        for _ in range(40):
            if any(item[0] == 0x12 and item[1][:1] == b"\x01" for item in got):
                break
            await asyncio.sleep(0.02)
        assert any(item[0] == 0x12 and item[1][:1] == b"\x01" for item in got)
        await master.request(0x11, i2c.pack_write(0x11, bytes([0x20]) + b"C:/a.wav"))
        assert phone.pending_play == ["C:/a.wav"]
        loop.cancel()
        stop[0] = True
        await asyncio.gather(loop, *peers, return_exceptions=True)
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    test() if "--test" in sys.argv else main()
