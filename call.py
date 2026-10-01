import asyncio
import ctypes
import os
import random
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort
from ntgcalls import (
    NTgCalls,
    AudioDescription,
    ConnectionState,
    DhConfig,
    FrameData,
    MediaDescription,
    MediaSource,
    RTCServer,
    StreamDevice,
    StreamMode,
    VIDEO_ROTATION_0,
)
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from telethon.sessions import MemorySession
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest, RequestCallRequest, SendSignalingDataRequest
from telethon.tl.types import (
    InputPhoneCall,
    PhoneCall,
    PhoneCallAccepted,
    PhoneCallDiscarded,
    PhoneCallDiscardReasonHangup,
    PhoneCallDiscardReasonMissed,
    PhoneCallProtocol,
    PhoneCallRequested,
    PhoneCallWaiting,
    PhoneConnection,
    PhoneConnectionWebrtc,
    UpdatePhoneCall,
    UpdatePhoneCallSignalingData,
)
from telethon.tl.types.messages import DhConfig as MessagesDhConfig

ROOT = Path(__file__).resolve().parent
DESKTOP = Path(os.environ["APPDATA"]) / "Telegram Desktop"
EXE = DESKTOP / "Telegram.exe"
TDATA = DESKTOP / "tdata"
IRIS = 8893165754
PEER = 5884279027
RATE_TX = 48000
RATE_RX = 16000
FRAME_TX = RATE_TX // 100 * 2
WINDOW = 512
PAD = RATE_RX * 120 // 1000
MIN_UTTER = RATE_RX
MAX_UTTER = RATE_RX * 30

LIVE = None


def die(message):
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


def telegram_pids():
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Telegram.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        shell=False,
    )
    found = []
    for line in (completed.stdout or "").splitlines():
        if "Telegram.exe" not in line:
            continue
        parts = [part.strip().strip('"') for part in line.split(",")]
        if len(parts) > 1 and parts[1].isdigit():
            found.append(int(parts[1]))
    return found


def quit_telegram():
    pids = set(telegram_pids())
    if not pids:
        return
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    user32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
    user32.PostThreadMessageW.restype = ctypes.c_int
    threads = set()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _lparam):
        proc = ctypes.c_ulong()
        thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value in pids and thread:
            threads.add(int(thread))
        return True

    user32.EnumWindows(visit, 0)
    if not threads:
        die("telegram window missing")
    for thread in threads:
        user32.PostThreadMessageW(thread, 0x0012, 0, 0)
    deadline = time.time() + 25
    while time.time() < deadline and telegram_pids():
        time.sleep(0.2)
    if telegram_pids():
        die("telegram did not quit")
    print("call: quit", file=sys.stderr, flush=True)


def start_telegram():
    if telegram_pids():
        return
    subprocess.Popen([str(EXE)], cwd=str(DESKTOP), shell=False)
    deadline = time.time() + 30
    while time.time() < deadline and not telegram_pids():
        time.sleep(0.2)
    if not telegram_pids():
        die("telegram did not start")
    time.sleep(4)
    if not telegram_pids():
        die("telegram exited")
    print("call: desktop", file=sys.stderr, flush=True)


def write_blocker(message):
    (ROOT / "call.blocker.txt").write_text(message.strip() + "\n", encoding="utf-8")


def submit(coro, timeout):
    fut = asyncio.run_coroutine_threadsafe(coro, LIVE.loop)
    return fut.result(timeout)


class Gate:
    def __init__(self):
        self.session = ort.InferenceSession(str(ROOT / "silero_vad.onnx"), providers=["CPUExecutionProvider"])
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.ctx = np.zeros(WINDOW // 8, dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.float32)
        self.lead = []
        self.speech = []
        self.triggered = False
        self.temp_end = 0
        self.current = 0
        self.min_silence = RATE_RX * 700 // 1000

    def reset(self):
        self.state.fill(0)
        self.ctx.fill(0)
        self.pending = np.zeros(0, dtype=np.float32)
        self.lead.clear()
        self.speech.clear()
        self.triggered = False
        self.temp_end = 0
        self.current = 0

    def feed(self, hop):
        audio = np.concatenate([self.ctx, hop]).astype(np.float32)
        self.ctx = audio[-self.ctx.size :].copy()
        rate = np.array([RATE_RX], dtype=np.int64)
        prob, nxt = self.session.run(
            None,
            {"input": audio.reshape(1, -1), "state": self.state, "sr": rate},
        )
        self.state = nxt
        speech = float(prob.reshape(-1)[0])
        self.current += hop.size
        if speech >= 0.65 and self.temp_end:
            self.temp_end = 0
        if speech >= 0.65 and not self.triggered:
            self.triggered = True
            return "start"
        if speech < 0.50 and self.triggered:
            if not self.temp_end:
                self.temp_end = self.current
            if self.current - self.temp_end >= self.min_silence:
                self.temp_end = 0
                self.triggered = False
                return "end"
        return ""

    def push(self, pcm):
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self.pending = np.concatenate([self.pending, samples])
        finished = None
        while self.pending.size >= WINDOW:
            hop = self.pending[:WINDOW].copy()
            self.pending = self.pending[WINDOW:]
            event = self.feed(hop)
            if event == "start":
                take = self.lead[-PAD:] if PAD < len(self.lead) else list(self.lead)
                self.speech = take
                self.speech.extend(hop.tolist())
                self.lead.clear()
            elif event == "end":
                finished = np.asarray(self.speech, dtype=np.float32)
                self.speech = []
            elif self.triggered:
                self.speech.extend(hop.tolist())
                if len(self.speech) >= MAX_UTTER:
                    finished = np.asarray(self.speech, dtype=np.float32)
                    self.speech = []
                    self.triggered = False
                    self.state.fill(0)
                    self.ctx.fill(0)
            else:
                self.lead.extend(hop.tolist())
                if len(self.lead) > PAD:
                    self.lead = self.lead[-PAD:]
        return finished


def boot():
    global LIVE
    LIVE = type("Line", (), {})()
    LIVE.loop = asyncio.new_event_loop()
    LIVE.rx = bytearray()
    LIVE.rx_lock = threading.Lock()
    LIVE.rx_event = threading.Event()
    LIVE.drop_rx = False
    LIVE.gate = Gate()
    LIVE.tx_seconds = 0.0
    LIVE.rx_seconds = 0.0
    LIVE.transcript = ""
    LIVE.caller = 0
    LIVE.caller_name = ""
    LIVE.peer_id = 0
    LIVE.peer_name = ""
    LIVE.phone = None
    LIVE.client = None
    LIVE.calls = None
    LIVE.began = 0.0
    LIVE.up = False
    LIVE.in_sig = []
    LIVE.media_up = False
    LIVE.linked = False
    LIVE.accepted = None
    LIVE.discarded = None
    LIVE.final = None
    LIVE.got_accept = None
    LIVE.got_final = None
    LIVE.closed = False
    LIVE.fault = ""
    LIVE.opening = ""
    LIVE.ring = threading.Event()
    LIVE.mark_lock = threading.Lock()
    LIVE.thread_line = None

    def runner():
        asyncio.set_event_loop(LIVE.loop)
        LIVE.loop.run_forever()

    LIVE.thread = threading.Thread(target=runner, name="iris-call", daemon=True)
    LIVE.thread.start()


def stop_loop():
    global LIVE
    if LIVE is None:
        return
    LIVE.loop.call_soon_threadsafe(LIVE.loop.stop)
    LIVE.thread.join(5)
    LIVE = None


def pcm_48k(path):
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        die("mouth wav is not s16")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != RATE_TX:
        dest = int(len(samples) * RATE_TX / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, dest), np.arange(len(samples)), samples)
    return samples.clip(-32768, 32767).astype(np.int16).tobytes()


def write_wav(path, samples):
    pcm = (samples.clip(-1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE_RX)
        handle.writeframes(pcm.tobytes())


def servers_of(connections):
    found = []
    for item in connections:
        if isinstance(item, PhoneConnectionWebrtc):
            found.append(
                RTCServer(
                    int(item.id),
                    item.ip,
                    item.ipv6 or "",
                    int(item.port),
                    item.username,
                    item.password,
                    bool(item.turn),
                    bool(item.stun),
                    False,
                    None,
                )
            )
        elif isinstance(item, PhoneConnection):
            found.append(
                RTCServer(
                    int(item.id),
                    item.ip,
                    item.ipv6 or "",
                    int(item.port),
                    None,
                    None,
                    False,
                    True,
                    bool(item.tcp),
                    bytes(item.peer_tag),
                )
            )
        else:
            die("call server")
    if not found:
        die("call servers")
    return found


def external(rate):
    return MediaDescription(
        microphone=AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True),
        speaker=None,
        camera=None,
        screen=None,
    )


def mark(state):
    if LIVE is None:
        return
    with LIVE.mark_lock:
        if state == "idle" and LIVE.up:
            state = "up"
        (ROOT / "call.ready").write_text(state + "\n", encoding="utf-8")


def clear_ready():
    try:
        (ROOT / "call.ready").unlink()
    except OSError:
        pass


def armed():
    return LIVE is not None and LIVE.client is not None and not LIVE.closed


def live():
    return LIVE is not None and LIVE.up and not LIVE.closed


async def end_media():
    try:
        if LIVE.client is not None and LIVE.client.is_connected() and LIVE.phone is not None:
            await LIVE.client(
                DiscardCallRequest(
                    peer=LIVE.phone,
                    duration=max(0, int(time.time() - LIVE.began)) if LIVE.began else 0,
                    reason=PhoneCallDiscardReasonHangup(),
                    connection_id=0,
                    video=False,
                )
            )
    finally:
        if LIVE.calls is not None and LIVE.peer_id:
            try:
                await LIVE.calls.stop(LIVE.peer_id)
            except Exception:
                pass
        LIVE.calls = None
        LIVE.phone = None
        LIVE.media_up = False
        LIVE.up = False
        LIVE.drop_rx = False


async def stop_call():
    try:
        await end_media()
    finally:
        if LIVE.client is not None and LIVE.client.is_connected():
            await LIVE.client.disconnect()


def drop_call():
    if LIVE is None:
        return
    LIVE.up = False
    LIVE.rx_event.set()
    try:
        submit(end_media(), 45)
    except BaseException:
        LIVE.up = False
    mark("idle")


def close_session():
    if LIVE is None:
        start_telegram()
        clear_ready()
        return
    me = threading.current_thread()
    line = LIVE.thread_line
    LIVE.closed = True
    LIVE.up = False
    LIVE.ring.set()
    LIVE.rx_event.set()
    try:
        if LIVE.client is not None:
            submit(stop_call(), 20)
    except BaseException:
        pass
    if line is not None and line is not me and line.is_alive():
        line.join(3)
    stop_loop()
    start_telegram()
    clear_ready()


def pull_rx():
    with LIVE.rx_lock:
        data = bytes(LIVE.rx)
        LIVE.rx.clear()
    return data


def on_frames(_uid, mode, _device, frames):
    if mode != StreamMode.PLAYBACK or LIVE.drop_rx:
        return
    with LIVE.rx_lock:
        for frame in frames:
            LIVE.rx.extend(frame.data)
    LIVE.rx_event.set()


def on_connection(_uid, info):
    print("call: link " + str(info.state).rsplit(".", 1)[-1], file=sys.stderr, flush=True)
    if info.state == ConnectionState.CONNECTED:
        LIVE.linked = True
        LIVE.loop.call_soon_threadsafe(LIVE.connected.set)


async def push_signal(data):
    if LIVE.phone is None:
        return
    await LIVE.client(SendSignalingDataRequest(peer=LIVE.phone, data=data))


def on_signal(_uid, data):
    asyncio.run_coroutine_threadsafe(push_signal(bytes(data)), LIVE.loop)


def person_name(entity):
    return " ".join(part for part in (getattr(entity, "first_name", "") or "", getattr(entity, "last_name", "") or "") if part)


def wire_protocol():
    protocol = NTgCalls.get_protocol()
    versions = list(protocol.library_versions)
    versions.reverse()
    return PhoneCallProtocol(
        udp_p2p=bool(protocol.udp_p2p),
        udp_reflector=bool(protocol.udp_reflector),
        min_layer=65,
        max_layer=92,
        library_versions=versions,
    )


async def dh_config():
    cfg = await LIVE.client(GetDhConfigRequest(0, 256))
    if not isinstance(cfg, MessagesDhConfig):
        die("dh config")
    return DhConfig(int(cfg.g), bytes(cfg.p), bytes(cfg.random))


async def begin_media(peer_id):
    LIVE.peer_id = peer_id
    LIVE.calls = NTgCalls()
    LIVE.connected = asyncio.Event()
    LIVE.got_accept = asyncio.Event()
    LIVE.got_final = asyncio.Event()
    LIVE.linked = False
    LIVE.media_up = False
    LIVE.in_sig = []
    LIVE.discarded = None
    LIVE.accepted = None
    LIVE.final = None
    LIVE.gate.reset()
    LIVE.calls.on_frames(on_frames)
    LIVE.calls.on_connection_change(on_connection)
    LIVE.calls.on_signaling_data(on_signal)
    await LIVE.calls.create_p2p_call(peer_id)
    await LIVE.calls.set_stream_sources(peer_id, StreamMode.CAPTURE, external(RATE_TX))
    await LIVE.calls.set_stream_sources(peer_id, StreamMode.PLAYBACK, external(RATE_RX))


async def finish_link(call):
    LIVE.phone = InputPhoneCall(int(call.id), int(call.access_hash))
    versions = list(call.protocol.library_versions)
    custom = call.custom_parameters.data if call.custom_parameters else None
    await LIVE.calls.connect_p2p(LIVE.peer_id, servers_of(call.connections), versions, bool(call.p2p_allowed), custom)
    LIVE.media_up = True
    queued = list(LIVE.in_sig)
    LIVE.in_sig.clear()
    for blob in queued:
        await LIVE.calls.send_signaling_data(LIVE.peer_id, blob)
    await asyncio.wait_for(LIVE.connected.wait(), 30)
    if not LIVE.linked:
        die("call link")
    LIVE.up = True
    LIVE.began = time.time()
    mark("up")
    print("call: connected", file=sys.stderr, flush=True)


async def discard_missed(phone):
    await LIVE.client(
        DiscardCallRequest(
            peer=InputPhoneCall(int(phone.id), int(phone.access_hash)),
            duration=0,
            reason=PhoneCallDiscardReasonMissed(),
            connection_id=0,
            video=False,
        )
    )


async def on_raw(update):
    try:
        await on_update(update)
    except SystemExit as exc:
        LIVE.fault = getattr(exc, "message", "") or "call failed"
        LIVE.up = False
        LIVE.ring.set()


async def on_update(update):
    if isinstance(update, UpdatePhoneCallSignalingData):
        blob = bytes(update.data)
        if LIVE.media_up and LIVE.calls is not None:
            await LIVE.calls.send_signaling_data(LIVE.peer_id, blob)
        else:
            LIVE.in_sig.append(blob)
        return
    if not isinstance(update, UpdatePhoneCall):
        return
    phone = update.phone_call
    if isinstance(phone, PhoneCallRequested):
        if LIVE.closed:
            return
        if LIVE.up or LIVE.calls is not None or int(phone.admin_id) != PEER:
            await discard_missed(phone)
            return
        await accept_requested(phone)
        return
    if isinstance(phone, (PhoneCallWaiting, PhoneCallAccepted, PhoneCall)):
        LIVE.phone = InputPhoneCall(int(phone.id), int(phone.access_hash))
    if isinstance(phone, PhoneCallAccepted) and LIVE.got_accept is not None:
        LIVE.accepted = phone
        LIVE.got_accept.set()
    if isinstance(phone, PhoneCall) and LIVE.got_final is not None:
        LIVE.final = phone
        LIVE.got_final.set()
    if isinstance(phone, PhoneCallDiscarded):
        LIVE.discarded = phone
        LIVE.up = False
        LIVE.rx_event.set()
        if LIVE.got_accept is not None:
            LIVE.got_accept.set()
        if LIVE.got_final is not None:
            LIVE.got_final.set()


async def connect_session():
    if not TDATA.is_dir():
        die("tdata missing")
    tdesk = TDesktop(str(TDATA))
    if not tdesk.isLoaded() or not tdesk.accounts:
        die("tdata empty")
    chosen = None
    seen = []
    for account in tdesk.accounts:
        ident = int(account.UserId)
        seen.append(ident)
        if ident == IRIS:
            chosen = account
    if chosen is None:
        die("iris account missing " + " ".join(str(item) for item in seen))
    client = await TelegramClient.FromTDesktop(
        chosen,
        session=MemorySession(),
        flag=UseCurrentSession,
        api=API.TelegramDesktop,
    )
    LIVE.client = client
    LIVE.got_accept = None
    LIVE.got_final = None
    client.add_event_handler(on_raw, events.Raw())
    await client.connect()
    me = await client.get_me()
    if int(me.id) != IRIS:
        die("iris session " + str(int(me.id)))
    LIVE.caller = int(me.id)
    LIVE.caller_name = person_name(me)
    humans = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not dialog.is_user or getattr(entity, "bot", False) or int(entity.id) == IRIS:
            continue
        humans.append((int(entity.id), person_name(entity)))
    match = [item for item in humans if item[0] == PEER]
    if not match:
        die("peer missing " + " ".join(str(item[0]) for item in humans))
    LIVE.peer_id = match[0][0]
    LIVE.peer_name = match[0][1]
    print("call: peer " + str(LIVE.peer_id) + " " + LIVE.peer_name, file=sys.stderr, flush=True)


async def accept_requested(requested):
    await begin_media(PEER)
    g_b = bytes(await LIVE.calls.init_exchange(PEER, await dh_config(), bytes(requested.g_a_hash)))
    LIVE.phone = InputPhoneCall(int(requested.id), int(requested.access_hash))
    wire = wire_protocol()
    answered = await LIVE.client(AcceptCallRequest(peer=LIVE.phone, g_b=g_b, protocol=wire))
    call = answered.phone_call
    if isinstance(call, PhoneCallDiscarded) or LIVE.discarded is not None:
        reason = type(getattr(LIVE.discarded, "reason", None)).__name__
        die("call " + reason)
    if not isinstance(call, PhoneCall):
        await asyncio.wait_for(LIVE.got_final.wait(), 30)
        if LIVE.discarded is not None:
            die("call discarded")
        call = LIVE.final
    if not isinstance(call, PhoneCall):
        die("call confirm")
    await LIVE.calls.exchange_keys(PEER, bytes(call.g_a_or_b), int(call.key_fingerprint))
    await finish_link(call)
    print("call: answered", file=sys.stderr, flush=True)
    LIVE.ring.set()


async def place():
    if LIVE.up:
        die("call already up")
    await begin_media(LIVE.peer_id)
    g_a_hash = bytes(await LIVE.calls.init_exchange(LIVE.peer_id, await dh_config(), None))
    wire = wire_protocol()
    invited = await LIVE.client(
        RequestCallRequest(
            user_id=await LIVE.client.get_input_entity(LIVE.peer_id),
            random_id=random.randint(0, 2**31 - 1),
            g_a_hash=g_a_hash,
            protocol=wire,
            video=False,
        )
    )
    waiting = invited.phone_call
    LIVE.phone = InputPhoneCall(int(waiting.id), int(waiting.access_hash))
    LIVE.began = time.time()
    print("call: ringing", file=sys.stderr, flush=True)
    await asyncio.wait_for(LIVE.got_accept.wait(), 150)
    if LIVE.discarded is not None:
        reason = type(LIVE.discarded.reason).__name__ if LIVE.discarded.reason else "discarded"
        die("call " + reason)
    if LIVE.accepted is None:
        die("call waiting")
    auth = await LIVE.calls.exchange_keys(LIVE.peer_id, bytes(LIVE.accepted.g_b), 0)
    confirmed = await LIVE.client(
        ConfirmCallRequest(
            peer=LIVE.phone,
            g_a=bytes(auth.g_a_or_b),
            key_fingerprint=int(auth.key_fingerprint),
            protocol=wire,
        )
    )
    call = confirmed.phone_call
    if not isinstance(call, PhoneCall):
        die("call confirm")
    await finish_link(call)
    return "up " + str(LIVE.caller) + " " + LIVE.caller_name + " " + str(LIVE.peer_id) + " " + LIVE.peer_name


async def send_frame(chunk):
    await LIVE.calls.send_external_frame(
        LIVE.peer_id,
        StreamDevice.MICROPHONE,
        chunk,
        FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, 0, 0),
    )


def speak(text):
    if LIVE is None or not LIVE.up:
        die("call down")
    spoken = " ".join((text or "").split())
    if not spoken:
        die("empty say")
    import mouth

    lang = mouth.language_of(spoken)
    model = "v3" if lang == "pl" else "nano"
    wav = mouth.synthesize(model, lang, spoken)
    pcm = pcm_48k(wav)
    LIVE.drop_rx = True
    start = time.perf_counter()
    sent = 0
    try:
        for offset in range(0, len(pcm), FRAME_TX):
            chunk = pcm[offset : offset + FRAME_TX]
            if len(chunk) < FRAME_TX:
                chunk = chunk + bytes(FRAME_TX - len(chunk))
            submit(send_frame(chunk), 5)
            sent += 1
            delay = start + sent * 0.01 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
    finally:
        LIVE.drop_rx = False
        with LIVE.rx_lock:
            LIVE.rx.clear()
        LIVE.rx_event.clear()
        LIVE.gate.reset()
    LIVE.tx_seconds += len(pcm) / 2 / RATE_TX
    print("call: tx " + format(LIVE.tx_seconds, ".2f"), file=sys.stderr, flush=True)


def picture(png):
    if LIVE is None or not LIVE.up:
        die("call down")
    if not png:
        die("empty shot")

    async def send():
        import io

        buf = io.BytesIO(png)
        buf.name = "desk.png"
        await LIVE.client.send_file(LIVE.peer_id, buf, force_document=False)

    submit(send(), 60)
    return "sent"


def listen(limit=""):
    if LIVE is None or not LIVE.up:
        return ""
    import hear

    window = (limit or "").strip()
    seconds = float(window) if window else 0.0
    if window and seconds <= 0:
        return ""
    deadline = time.monotonic() + seconds if seconds else None
    while LIVE.up:
        if deadline is not None and time.monotonic() >= deadline:
            return ""
        data = pull_rx()
        if not data:
            LIVE.rx_event.wait(0.2)
            LIVE.rx_event.clear()
            continue
        clip = LIVE.gate.push(data)
        if clip is None or clip.size < MIN_UTTER:
            continue
        path = ROOT / "call.hear.wav"
        write_wav(path, clip)
        text, lang = hear.transcribe(path)
        LIVE.transcript = text
        LIVE.rx_seconds = clip.size / RATE_RX
        (ROOT / "call.hear.txt").write_text(text + "\n", encoding="utf-8")
        print("call: rx " + format(LIVE.rx_seconds, ".2f") + " " + lang + " " + text, file=sys.stderr, flush=True)
        return text
    return ""


def line_loop():
    while LIVE is not None and not LIVE.closed:
        LIVE.ring.wait()
        if LIVE is None or LIVE.closed:
            return
        LIVE.ring.clear()
        opening = LIVE.opening
        LIVE.opening = ""
        try:
            if LIVE.fault:
                message = LIVE.fault
                LIVE.fault = ""
                die(message)
            if opening and LIVE.up:
                speak(opening)
            while LIVE is not None and LIVE.up and not LIVE.closed:
                heard = listen("")
                if LIVE is None or not LIVE.up or LIVE.closed:
                    break
                if not heard:
                    continue
                mod = sys.modules["__main__"]
                reply = mod.agent_turn("voice", heard, "")
                print(reply, flush=True)
                if mod.is_stop(reply):
                    drop_call()
                    break
                spoken = mod.answer_text(reply)
                if spoken and LIVE.up:
                    speak(spoken)
        except SystemExit as exc:
            message = getattr(exc, "message", "") or ""
            if message == "call down" and LIVE is not None and not LIVE.closed:
                mark("idle")
                continue
            if LIVE is not None and not LIVE.closed:
                close_session()
            return
        if LIVE is not None and not LIVE.closed:
            mark("idle")


def arm():
    if armed():
        state = "up" if LIVE.up else "idle"
        mark(state)
        return state
    if not EXE.is_file():
        die("telegram desktop missing")
    quit_telegram()
    boot()
    LIVE.thread_line = threading.Thread(target=line_loop, name="iris-line", daemon=True)
    LIVE.thread_line.start()
    try:
        submit(connect_session(), 180)
    except BaseException as exc:
        write_blocker(getattr(exc, "message", "") or str(exc))
        close_session()
        raise
    if LIVE.up:
        mark("up")
        return "up"
    mark("idle")
    print("call: idle", file=sys.stderr, flush=True)
    return "idle"


def dial(reason=""):
    if not armed():
        die("call down")
    if LIVE.up:
        die("call already up")
    try:
        text = submit(place(), 240)
    except BaseException as exc:
        write_blocker(getattr(exc, "message", "") or str(exc))
        drop_call()
        raise
    LIVE.opening = " ".join((reason or "").split())
    LIVE.ring.set()
    return text


def hang(kind=""):
    if kind == "close":
        close_session()
        return "closed"
    if not armed():
        die("call down")
    if not LIVE.up:
        return "hung"
    drop_call()
    return "hung"

