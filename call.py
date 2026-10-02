import asyncio
import ctypes
import os
import random
import shutil
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
    VideoDescription,
    RTCServer,
    StreamDevice,
    StreamMode,
    VIDEO_ROTATION_0,
)
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from telethon.errors import RPCError
from telethon.sessions import MemorySession
from telethon.tl.functions.messages import GetDhConfigRequest
from telethon.tl.functions.phone import AcceptCallRequest, ConfirmCallRequest, DiscardCallRequest, RequestCallRequest, SendSignalingDataRequest
from telethon.tl.types import (
    InputPhoneCall,
    PhoneCall,
    PhoneCallAccepted,
    PhoneCallDiscarded,
    PhoneCallDiscardReasonBusy,
    PhoneCallDiscardReasonDisconnect,
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
OWNER = 5884279027
RATE_TX = 48000
RATE_RX = 16000
FRAME_TX = RATE_TX // 100 * 2
WINDOW = 512
PAD = RATE_RX * 120 // 1000
MIN_UTTER = RATE_RX
MAX_UTTER = RATE_RX * 30
IDLE_EAR = 20
DESK_W = 960
DESK_H = 540
DESK_FPS = 12

LIVE = None
VAD_PROC = None
_VAD = threading.Lock()


def die(message):
    print(message, file=sys.stderr, flush=True)
    err = SystemExit(2)
    err.message = message
    raise err


def telegram_home():
    roots = []
    for key in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(key, "")
        if value:
            roots.append(Path(value) / "Telegram Desktop")
    program = os.environ.get("PROGRAMFILES", "")
    if program:
        roots.append(Path(program) / "Telegram Desktop")
    for home in roots:
        if (home / "Telegram.exe").is_file() and (home / "tdata").is_dir():
            return home
    for name in ("Telegram.exe", "Telegram"):
        found = shutil.which(name)
        if not found:
            continue
        home = Path(found).resolve().parent
        if (home / "Telegram.exe").is_file() and (home / "tdata").is_dir():
            return home
    die("telegram desktop missing")


def call_fault(phone):
    reason = getattr(phone, "reason", None)
    if isinstance(reason, PhoneCallDiscardReasonBusy):
        return "call busy"
    if isinstance(reason, PhoneCallDiscardReasonMissed):
        return "call missed"
    if isinstance(reason, PhoneCallDiscardReasonDisconnect):
        return "call disconnect"
    if isinstance(reason, PhoneCallDiscardReasonHangup):
        return "call hangup"
    return "call discarded"


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
    home = telegram_home()
    subprocess.Popen([str(home / "Telegram.exe")], cwd=str(home), shell=False)
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
    LIVE.cancel = threading.Event()
    LIVE.ring = threading.Event()
    LIVE.mark_lock = threading.Lock()
    LIVE.thread_line = None
    LIVE.desk_on = False

    def runner():
        asyncio.set_event_loop(LIVE.loop)
        LIVE.loop.run_forever()

    LIVE.thread = threading.Thread(target=runner, name="trident-call", daemon=True)
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


def external(rate, camera=False):
    shot = None
    if camera:
        shot = VideoDescription(MediaSource.EXTERNAL, DESK_W, DESK_H, DESK_FPS, "", True)
    return MediaDescription(
        microphone=AudioDescription(MediaSource.EXTERNAL, rate, 1, "", True),
        speaker=None,
        camera=shot,
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


async def end_media():
    calls = LIVE.calls
    peer = LIVE.peer_id
    phone = LIVE.phone
    client = LIVE.client
    began = LIVE.began
    LIVE.calls = None
    LIVE.phone = None
    LIVE.media_up = False
    LIVE.linked = False
    LIVE.up = False
    LIVE.drop_rx = False
    if calls is not None and peer:
        try:
            await calls.stop(peer)
        except Exception:
            pass
    if client is not None and client.is_connected() and phone is not None:
        try:
            await asyncio.wait_for(
                client(
                    DiscardCallRequest(
                        peer=phone,
                        duration=max(0, int(time.time() - began)) if began else 0,
                        reason=PhoneCallDiscardReasonHangup(),
                        connection_id=0,
                        video=True,
                    )
                ),
                8,
            )
        except Exception:
            pass


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
        submit(end_media(), 12)
    except BaseException:
        LIVE.up = False
    mark("idle")


def drop_runtime(name):
    path = ROOT / name
    if path.is_file():
        path.unlink()


def vad_pid():
    path = ROOT / "vad.pid"
    if not path.is_file():
        return 0
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) < 2 or lines[1].strip() != "ready":
        return 0
    return int(lines[0].strip())


def vad_alive():
    pid = vad_pid()
    if pid <= 0:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32))
    kernel.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x1000, 0, pid)
    if not handle:
        return False
    size = ctypes.c_uint32(32768)
    buf = ctypes.create_unicode_buffer(size.value)
    ok = kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
    kernel.CloseHandle(handle)
    return bool(ok) and Path(buf.value).name.lower() == "vad.exe"


def stop_vad():
    global VAD_PROC
    with _VAD:
        proc = VAD_PROC
        running = vad_alive() or (proc is not None and proc.poll() is None)
        if not running:
            drop_runtime("vad.stop")
            drop_runtime("vad.hold")
            drop_runtime("vad.utterance.txt")
            VAD_PROC = None
            return
        (ROOT / "vad.stop").write_text("stop\n", encoding="ascii")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not vad_alive() and (proc is None or proc.poll() is not None):
            break
        time.sleep(0.05)
    else:
        die("vad did not stop")
    with _VAD:
        if VAD_PROC is proc:
            VAD_PROC = None
        drop_runtime("vad.stop")
        drop_runtime("vad.hold")
        drop_runtime("vad.utterance.txt")


def start_vad():
    global VAD_PROC
    with _VAD:
        if vad_alive():
            return
        drop_runtime("vad.utterance.txt")
        drop_runtime("vad.hold")
        drop_runtime("vad.stop")
        VAD_PROC = subprocess.Popen(
            [str(ROOT / "vad.exe"), "--resident", "vad.txt"],
            cwd=str(ROOT),
            shell=False,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        proc = VAD_PROC
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if vad_alive():
            print("idle: ear", file=sys.stderr, flush=True)
            return
        if proc.poll() is not None:
            if proc.returncode == 0:
                return
            die("vad exited")
        if LIVE is None or LIVE.closed or LIVE.ring.is_set() or LIVE.up:
            stop_vad()
            return
        time.sleep(0.05)
    die("vad did not become ready")


def take_inject():
    path = ROOT / "ear.inject.txt"
    if not path.is_file():
        return ""
    text = " ".join(path.read_text(encoding="utf-8").split())
    path.unlink()
    return text


def take_utterance():
    card = ROOT / "vad.utterance.txt"
    if not card.is_file():
        return None
    name = card.read_text(encoding="utf-8").strip()
    card.unlink()
    wav = ROOT / name
    if name != Path(name).name or not wav.is_file():
        die("bad vad utterance")
    return wav


def idle_world(node):
    if node.quiet_set():
        print("idle: quiet", file=sys.stderr, flush=True)
        return
    print("idle: world", file=sys.stderr, flush=True)
    report, _image = node.tool_look("What is on screen?", False)
    seen = " ".join(report.split())
    print("idle: see " + seen, file=sys.stderr, flush=True)
    reply = node.agent_turn("voice", "Idle check. Not his voice. Nothing is in progress. " + seen, "", hands=False)
    print(reply, flush=True)


def idle_heard(node):
    start_vad()
    wav = take_utterance()
    if wav is None:
        return False
    import hear

    print("idle: asr", file=sys.stderr, flush=True)
    text, lang = hear.transcribe(wav)
    wav.with_suffix(".txt").unlink()
    wav.unlink()
    print("idle: rx " + lang + " " + text, file=sys.stderr, flush=True)
    if LIVE is None or LIVE.closed or LIVE.up or LIVE.ring.is_set():
        return True
    reply = node.agent_turn("voice", text, "", hands=True)
    print(reply, flush=True)
    return True


def close_session():
    stop_vad()
    if LIVE is None:
        start_telegram()
        clear_ready()
        return
    me = threading.current_thread()
    line = LIVE.thread_line
    LIVE.cancel.set()
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
    await LIVE.calls.set_stream_sources(peer_id, StreamMode.CAPTURE, external(RATE_TX, True))
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
    start_desk()


async def discard_missed(phone):
    await LIVE.client(
        DiscardCallRequest(
            peer=InputPhoneCall(int(phone.id), int(phone.access_hash)),
            duration=0,
            reason=PhoneCallDiscardReasonMissed(),
            connection_id=0,
            video=True,
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
        if LIVE.up or LIVE.calls is not None or int(phone.admin_id) != OWNER:
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
    tdata = telegram_home() / "tdata"
    if not tdata.is_dir():
        die("tdata missing")
    tdesk = TDesktop(str(tdata))
    if not tdesk.isLoaded() or not tdesk.accounts:
        die("tdata empty")
    chosen = None
    for account in tdesk.accounts:
        if int(account.UserId) != OWNER:
            chosen = account
            break
    if chosen is None:
        die("telegram account missing")
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
    if int(me.id) == OWNER:
        die("telegram account missing")
    LIVE.caller = int(me.id)
    LIVE.caller_name = person_name(me)
    LIVE.peer_id = 0
    LIVE.peer_name = ""
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not dialog.is_user or getattr(entity, "bot", False) or int(entity.id) != OWNER:
            continue
        LIVE.peer_id = OWNER
        LIVE.peer_name = person_name(entity)
        break
    if not LIVE.peer_id:
        die("owner missing")
    print("call: owner " + (LIVE.peer_name or "ready"), file=sys.stderr, flush=True)


async def accept_requested(requested):
    await begin_media(OWNER)
    g_b = bytes(await LIVE.calls.init_exchange(OWNER, await dh_config(), bytes(requested.g_a_hash)))
    LIVE.phone = InputPhoneCall(int(requested.id), int(requested.access_hash))
    wire = wire_protocol()
    answered = await LIVE.client(AcceptCallRequest(peer=LIVE.phone, g_b=g_b, protocol=wire))
    call = answered.phone_call
    if isinstance(call, PhoneCallDiscarded):
        die(call_fault(call))
    if LIVE.discarded is not None:
        die(call_fault(LIVE.discarded))
    if not isinstance(call, PhoneCall):
        await asyncio.wait_for(LIVE.got_final.wait(), 30)
        if LIVE.discarded is not None:
            die("call discarded")
        call = LIVE.final
    if not isinstance(call, PhoneCall):
        die("call confirm")
    await LIVE.calls.exchange_keys(OWNER, bytes(call.g_a_or_b), int(call.key_fingerprint))
    await finish_link(call)
    print("call: answered", file=sys.stderr, flush=True)
    LIVE.ring.set()


async def place():
    if LIVE.up:
        die("call already up")
    await begin_media(LIVE.peer_id)
    g_a_hash = bytes(await LIVE.calls.init_exchange(LIVE.peer_id, await dh_config(), None))
    wire = wire_protocol()
    try:
        invited = await LIVE.client(
            RequestCallRequest(
                user_id=await LIVE.client.get_input_entity(LIVE.peer_id),
                random_id=random.randint(0, 2**31 - 1),
                g_a_hash=g_a_hash,
                protocol=wire,
                video=True,
            )
        )
    except RPCError as exc:
        name = type(exc).__name__
        if name in {"CallOccupyFailedError", "UserAlreadyParticipantError", "CallAlreadyAcceptedError"}:
            die("call busy")
        die("call " + name)
    waiting = invited.phone_call
    if isinstance(waiting, PhoneCallDiscarded):
        die(call_fault(waiting))
    LIVE.phone = InputPhoneCall(int(waiting.id), int(waiting.access_hash))
    LIVE.began = time.time()
    print("call: ringing", file=sys.stderr, flush=True)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if LIVE.cancel.is_set():
            die("call hung")
        if LIVE.discarded is not None:
            die(call_fault(LIVE.discarded))
        if LIVE.got_accept.is_set() and LIVE.accepted is not None:
            break
        await asyncio.sleep(0.1)
    else:
        if LIVE.phone is not None:
            try:
                await discard_missed(LIVE.phone)
            except Exception:
                pass
        die("call missed")
    if LIVE.discarded is not None:
        die(call_fault(LIVE.discarded))
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
    return "up " + (LIVE.peer_name or "owner")


async def send_frame(chunk):
    await LIVE.calls.send_external_frame(
        LIVE.peer_id,
        StreamDevice.MICROPHONE,
        chunk,
        FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, 0, 0),
    )


def send_pcm(pcm):
    start = time.perf_counter()
    sent = 0
    for offset in range(0, len(pcm), FRAME_TX):
        chunk = pcm[offset : offset + FRAME_TX]
        if len(chunk) < FRAME_TX:
            chunk = chunk + bytes(FRAME_TX - len(chunk))
        submit(send_frame(chunk), 5)
        sent += 1
        delay = start + sent * 0.01 - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
    LIVE.tx_seconds += len(pcm) / 2 / RATE_TX


def reset_rx():
    LIVE.drop_rx = False
    with LIVE.rx_lock:
        LIVE.rx.clear()
    LIVE.rx_event.clear()
    LIVE.gate.reset()


class _BMI(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


def desk_i420():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SetStretchBltMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.SetStretchBltMode.restype = ctypes.c_int
    gdi32.StretchBlt.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint32,
    ]
    gdi32.StretchBlt.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = ctypes.c_int
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = ctypes.c_int
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    src = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, DESK_W, DESK_H)
    old = gdi32.SelectObject(mem, bmp)
    gdi32.SetStretchBltMode(mem, 4)
    gdi32.StretchBlt(mem, 0, 0, DESK_W, DESK_H, src, 0, 0, sw, sh, 0x00CC0020)
    gdi32.SelectObject(mem, old)
    hdr = _BMI()
    hdr.biSize = ctypes.sizeof(_BMI)
    hdr.biWidth = DESK_W
    hdr.biHeight = DESK_H
    hdr.biPlanes = 1
    hdr.biBitCount = 32
    buf = (ctypes.c_ubyte * (DESK_W * DESK_H * 4))()
    gdi32.GetDIBits(mem, bmp, 0, DESK_H, buf, ctypes.byref(hdr), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, src)
    bgr = np.flipud(np.frombuffer(buf, dtype=np.uint8).reshape(DESK_H, DESK_W, 4)[:, :, :3]).astype(np.int32)
    blue, green, red = bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2]
    y = np.clip(((66 * red + 129 * green + 25 * blue + 128) >> 8) + 16, 16, 235).astype(np.uint8)
    red2 = (red[0::2, 0::2] + red[1::2, 0::2] + red[0::2, 1::2] + red[1::2, 1::2] + 2) >> 2
    green2 = (green[0::2, 0::2] + green[1::2, 0::2] + green[0::2, 1::2] + green[1::2, 1::2] + 2) >> 2
    blue2 = (blue[0::2, 0::2] + blue[1::2, 0::2] + blue[0::2, 1::2] + blue[1::2, 1::2] + 2) >> 2
    u = np.clip(((-38 * red2 - 74 * green2 + 112 * blue2 + 128) >> 8) + 128, 16, 240).astype(np.uint8)
    v = np.clip(((112 * red2 - 94 * green2 - 18 * blue2 + 128) >> 8) + 128, 16, 240).astype(np.uint8)
    return np.concatenate((y.reshape(-1), u.reshape(-1), v.reshape(-1))).tobytes()


async def send_desk(frame):
    live = LIVE
    if live is None or live.calls is None or not live.peer_id or not frame:
        return
    await live.calls.send_external_frame(
        live.peer_id,
        StreamDevice.CAMERA,
        frame,
        FrameData(int(time.time() * 1000), VIDEO_ROTATION_0, DESK_W, DESK_H),
    )


def desk_video():
    sent = 0
    try:
        while LIVE is not None and LIVE.up and not LIVE.closed and LIVE.calls is not None:
            try:
                submit(send_desk(desk_i420()), 3)
            except BaseException:
                break
            sent += 1
            if sent == 1:
                print("desk video " + str(DESK_W) + "x" + str(DESK_H) + " " + str(DESK_FPS), file=sys.stderr, flush=True)
            time.sleep(1.0 / DESK_FPS)
    finally:
        print("desk video sent " + str(sent), file=sys.stderr, flush=True)
        if LIVE is not None:
            LIVE.desk_on = False


def start_desk():
    if LIVE is None or LIVE.desk_on:
        return
    LIVE.desk_on = True
    threading.Thread(target=desk_video, name="trident-desk", daemon=True).start()


def speak(text):
    if LIVE is None or not LIVE.up:
        die("call down")
    spoken = " ".join((text or "").split())
    if not spoken:
        die("empty say")
    import mouth

    lang = mouth.language_of(spoken)
    LIVE.drop_rx = True
    try:
        import gemma
        import node

        node.park_gemma()
        model = "v3" if lang == "pl" else "nano"
        wav = mouth.synthesize(model, lang, spoken)
        print("vram: mouth unloaded", file=sys.stderr, flush=True)
        gemma.begin_preload()
        send_pcm(pcm_48k(wav))
    finally:
        reset_rx()
    print("call: tx " + format(LIVE.tx_seconds, ".2f"), file=sys.stderr, flush=True)


def picture(png):
    if LIVE is None or LIVE.closed or LIVE.client is None or not LIVE.peer_id:
        die("call down")
    if not png:
        die("empty shot")

    async def send():
        import io

        buf = io.BytesIO(png)
        buf.name = "desk.png"
        sent = await LIVE.client.send_file(LIVE.peer_id, buf, force_document=False)
        if isinstance(sent, list):
            sent = sent[0] if sent else None
        return int(getattr(sent, "id", 0) or 0)

    ident = submit(send(), 60)
    print("call: shot " + str(ident), file=sys.stderr, flush=True)
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
        injected = take_inject()
        if injected:
            print("inject: " + injected, file=sys.stderr, flush=True)
            return injected
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
        print("vram: asr start", file=sys.stderr, flush=True)
        text, lang = hear.transcribe(path)
        print("vram: asr ready", file=sys.stderr, flush=True)
        LIVE.transcript = text
        LIVE.rx_seconds = clip.size / RATE_RX
        (ROOT / "call.hear.txt").write_text(text + "\n", encoding="utf-8")
        print("call: rx " + format(LIVE.rx_seconds, ".2f") + " " + lang + " " + text, file=sys.stderr, flush=True)
        return text
    return ""


def vision_fault(message):
    text = message or ""
    return text.startswith("desk") or text.startswith("vision") or text.startswith("desktop")


def serve_call(node):
    opening = LIVE.opening
    LIVE.opening = ""
    try:
        stop_vad()
        if LIVE.fault:
            message = LIVE.fault
            LIVE.fault = ""
            die(message)
        purpose = " ".join((getattr(LIVE, "after", "") or "").split())
        LIVE.after = ""
        if opening and LIVE.up:
            speak(opening)
        if purpose and purpose != opening and LIVE is not None and LIVE.up:
            speak(purpose)
        while LIVE is not None and LIVE.up and not LIVE.closed:
            try:
                heard = listen("")
            except SystemExit as exc:
                message = getattr(exc, "message", "") or ""
                if message.startswith("hear"):
                    print("call: stay " + message, file=sys.stderr, flush=True)
                    continue
                raise
            if LIVE is None or not LIVE.up or LIVE.closed:
                break
            if not heard:
                continue
            try:
                reply = node.agent_turn("voice", heard, "", hands=True)
            except SystemExit as exc:
                message = getattr(exc, "message", "") or ""
                if vision_fault(message):
                    print("call: stay " + message, file=sys.stderr, flush=True)
                    continue
                raise
            print(reply, flush=True)
            if node.is_stop(reply):
                drop_call()
                break
            spoken = node.answer_text(reply)
            if spoken and LIVE.up:
                speak(spoken)
    except SystemExit as exc:
        message = getattr(exc, "message", "") or ""
        if message == "call down" and LIVE is not None and not LIVE.closed:
            drop_call()
            return
        if LIVE is not None and not LIVE.closed:
            close_session()
        return
    if LIVE is not None and not LIVE.closed:
        if LIVE.up:
            mark("idle")
        else:
            drop_call()


def line_loop():
    import node

    idle_since = time.monotonic()
    looked = False
    while LIVE is not None and not LIVE.closed:
        if LIVE.ring.wait(0.2):
            if LIVE is None or LIVE.closed:
                return
            LIVE.ring.clear()
            serve_call(node)
            idle_since = time.monotonic()
            looked = False
            continue
        if LIVE is None or LIVE.closed or LIVE.up:
            continue
        if not armed() or not LIVE.peer_id:
            idle_since = time.monotonic()
            continue
        try:
            injected = take_inject()
            if injected:
                print("inject: " + injected, file=sys.stderr, flush=True)
                reply = node.agent_turn("voice", injected, "", written=True, hands=True)
                print(reply, flush=True)
                continue
            if time.monotonic() - idle_since < IDLE_EAR:
                continue
            if idle_heard(node):
                continue
            if LIVE is None or LIVE.closed or LIVE.up or LIVE.ring.is_set():
                continue
            if not looked:
                looked = True
                idle_world(node)
        except SystemExit as exc:
            message = getattr(exc, "message", "") or ""
            if message.startswith("hear") or vision_fault(message) or message == "call down" or message.startswith("call "):
                print("call: stay " + message, file=sys.stderr, flush=True)
                continue
            if LIVE is not None and not LIVE.closed:
                close_session()
            return


def arm():
    if armed():
        state = "up" if LIVE.up else "idle"
        mark(state)
        return state
    telegram_home()
    quit_telegram()
    boot()
    LIVE.thread_line = threading.Thread(target=line_loop, name="trident-line", daemon=True)
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
    stop_vad()
    if not armed():
        die("call down")
    if LIVE.up and LIVE.linked and LIVE.media_up and not LIVE.cancel.is_set():
        die("call already up")
    if LIVE.calls is not None or LIVE.phone is not None or LIVE.up:
        drop_call()
    LIVE.cancel.clear()
    try:
        text = submit(place(), 150)
    except TimeoutError:
        LIVE.cancel.set()
        write_blocker("call missed")
        drop_call()
        die("call missed")
    except BaseException as exc:
        write_blocker(getattr(exc, "message", "") or str(exc))
        drop_call()
        raise
    if not LIVE.up or LIVE.cancel.is_set():
        drop_call()
        die("call hung")
    LIVE.opening = " ".join((reason or "").split())
    if not LIVE.up or LIVE.cancel.is_set():
        drop_call()
        die("call hung")
    if LIVE.opening:
        print("call: open " + LIVE.opening, file=sys.stderr, flush=True)
    else:
        print("call: listening", file=sys.stderr, flush=True)
    LIVE.ring.set()
    return text


def hang(kind=""):
    if kind == "close":
        close_session()
        return "closed"
    if not armed():
        return "hung"
    LIVE.cancel.set()
    LIVE.up = False
    LIVE.rx_event.set()
    if LIVE.calls is not None or LIVE.phone is not None:
        drop_call()
    else:
        mark("idle")
    return "hung"

