import asyncio, secrets, time
from collections import deque
from math import gcd
import ntgcalls as n
import numpy as np
import soundfile
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from scipy.signal import resample_poly
from telethon import events
from telethon.tl import types as t
from telethon.tl.functions import messages, phone
from bus import Device, Refused, journal

class Speech:
    def __init__(self, cfg):
        self.cfg, self.pending, self.parts, self.voiced, self.quiet = cfg, b'', [], 0, 0
        self.size = cfg['receive_rate'] * cfg['sample_bytes'] * cfg['frame_ms'] // 1000
        self.padding = deque(maxlen=cfg['padding_ms'] // cfg['frame_ms'])
    def feed(self, data):
        self.pending += data
        while len(self.pending) >= self.size:
            frame, self.pending = self.pending[:self.size], self.pending[self.size:]
            samples = np.frombuffer(frame, dtype='<i2').astype(np.float32) / 32768
            loud = np.sqrt(np.mean(samples ** 2)) >= self.cfg['rms_threshold']
            self.voiced += self.cfg['frame_ms'] * loud
            self.quiet = (self.quiet + self.cfg['frame_ms']) * (not loud)
            if not self.parts:
                self.padding.append(frame)
                if loud: self.parts = list(self.padding); self.padding.clear()
            else: self.parts.append(frame)
            if self.parts and (self.quiet >= self.cfg['silence_ms'] or len(self.parts) * self.cfg['frame_ms'] >= self.cfg['utterance_seconds'] * 1000): yield self.finish()
    def finish(self):
        result = b''.join(self.parts) if self.voiced >= self.cfg['min_speech_ms'] else b''
        self.parts, self.voiced, self.quiet = [], 0, 0
        self.padding.clear()
        return result

class Telegram(Device):
    async def start(self):
        self.settings, self.loop = self.cfg['telegram'], asyncio.get_running_loop()
        self.owner, self.state, self.serial, self.peer, self.engine, self.call = self.settings['owner'], 0, 0, None, None, None
        self.talked, self.since, self.linked, self.outgoing, self.signals = 0, 0, False, False, []
        self.speech, self.native_lock, self.call_lock = Speech(self.cfg['audio']), asyncio.Lock(), asyncio.Lock()
        self.call_grant = 0
        accounts = [account for account in TDesktop(str(self.cfg.path(self.settings['tdata']))).accounts if account.UserId != self.owner]
        if len(accounts) != 1: raise RuntimeError('Exactly one non-owner Telegram account is required')
        self.client = await TelegramClient.FromTDesktop(accounts[0], session=str(self.file('session')), flag=UseCurrentSession, api=API.TelegramDesktop, request_retries=0, connection_retries=0, auto_reconnect=False, flood_sleep_threshold=0, raise_last_call_error=True, catch_up=False)
        self.cleanup.push_async_callback(self.client.disconnect)
        await self.client.connect()
        identity = await self.client.get_me()
        if identity is None or identity.bot or identity.id == self.owner: raise RuntimeError('Telegram user login required')
        await self.client.get_dialogs()
        self.contact = await self.client.get_input_entity(self.owner)
        self.client.add_event_handler(self.message, events.NewMessage(incoming=True, from_users=[self.owner]))
        self.client.add_event_handler(self.raw, events.Raw())
        self.tasks.create_task(self.disconnected())
    async def disconnected(self):
        await self.client.disconnected
        raise ConnectionError('Telegram disconnected')
    async def message(self, event):
        if not event.is_private: return
        text = event.raw_text.strip()
        if text == '/revoke-call':
            self.call_grant = 0
            journal(self.run, 'call_permission', state='revoked')
            return
        if text.startswith('/permit-call '):
            if self.state: return
            self.call_grant = self.loop.time() + self.settings['call_grant_seconds']
            journal(self.run, 'call_permission', state='granted_once')
            text = text[len('/permit-call '):].strip()
        if text: self.tasks.create_task(self.send('mind', text, '01'))
    async def raw(self, event):
        self.tasks.create_task(self.update(event))
    def bridged(self):
        marker = self.file('bridge')
        return marker.exists() and time.time() - marker.stat().st_mtime < self.settings['bridge_seconds']
    def barred(self, source):
        return source != self.settings['bridge'] and self.state == 2 and self.loop.time() - self.talked < self.cfg['audio']['barge_hold_seconds']
    async def receive(self, source, frame):
        if frame.register == '00': return f'{self.state:02d}'.encode()
        writer = {'11': 'tools', '20': 'voice', '21': 'voice'}.get(frame.register)
        if writer is not None and source != self.cfg['address'][writer]: raise Refused('UNKNOWN_DATA')
        if frame.register == '10' and source == self.settings['bridge']: self.file('bridge').touch()
        if frame.register == '10' and self.barred(source): raise Refused('FULL')
        if frame.register == '01':
            if self.state or (source != self.settings['bridge'] and self.bridged()): raise Refused('NOT_READY')
            if self.loop.time() >= self.call_grant:
                journal(self.run, 'call_denied', src=source, reason='owner_permission_required')
                raise Refused('FULL')
            self.call_grant = 0
            self.state, self.outgoing = 1, True
            self.call = self.tasks.create_task(self.dial())
            return b''
        if frame.register == '02': self.tasks.create_task(self.hang(self.serial, False)); return b''
        return await super().receive(source, frame)
    async def work(self, source, frame):
        if frame.register == '10':
            if self.barred(source): print('Speech cancelled by owner interruption', flush=True); return
            if self.state == 2 or self.settings['voice_notes']:
                await self.send('voice', frame.text, ('02', '01')[self.state == 2])
            else:
                for offset in range(0, len(frame.text), self.settings['chat_chars']): await self.client.send_message(self.contact, frame.text[offset:offset + self.settings['chat_chars']], parse_mode=None)
        if frame.register == '11':
            items = [item.partition('\n') for item in frame.text.split('\x1e')[:self.settings['album_files']]]
            await self.client.send_file(self.contact, [item[0] for item in items], caption=[item[2][:self.settings['caption_chars']] for item in items], force_document=True, parse_mode=None)
        if frame.register == '20': await self.play(frame.text)
        if frame.register == '21': await self.client.send_file(self.contact, frame.text, attributes=[t.DocumentAttributeAudio(round(soundfile.info(frame.text).duration), voice=True)])
    async def native(self, method, *args):
        async with self.native_lock:
            task = asyncio.ensure_future(getattr(self.engine, method)(*args))
            try: return await asyncio.shield(task)
            except asyncio.CancelledError: await task; raise
    def protocol(self):
        value = n.NTgCalls.get_protocol()
        return t.PhoneCallProtocol(udp_p2p=value.udp_p2p, udp_reflector=value.udp_reflector, min_layer=self.settings['min_layer'], max_layer=value.max_layer, library_versions=list(reversed(value.library_versions)))
    async def prepare(self):
        self.serial += 1
        serial = self.serial
        self.accepted, self.confirmed, self.connected = [self.loop.create_future() for _ in range(3)]
        self.engine, self.linked = n.NTgCalls(), False
        self.engine.on_frames(lambda uid, mode, device, frames: self.loop.call_soon_threadsafe(self.frames, serial, mode, device, frames))
        self.engine.on_connection_change(lambda uid, info: self.loop.call_soon_threadsafe(self.connection, serial, info.state))
        self.engine.on_signaling_data(lambda uid, data: self.loop.call_soon_threadsafe(self.signaling, serial, bytes(data)))
        await self.native('create_p2p_call', self.owner)
        for mode, rate in ((n.StreamMode.CAPTURE, self.cfg['audio']['send_rate']), (n.StreamMode.PLAYBACK, self.cfg['audio']['receive_rate'])):
            await self.native('set_stream_sources', self.owner, mode, n.MediaDescription(n.AudioDescription(n.MediaSource.EXTERNAL, rate, 1, '', True), None, None, None))
        dh = await self.client(messages.GetDhConfigRequest(0, self.settings['dh_bytes']))
        self.dh = n.DhConfig(dh.g, bytes(dh.p), bytes(dh.random))
    def frames(self, serial, mode, device, frames):
        if serial == self.serial and mode == n.StreamMode.PLAYBACK and device == n.StreamDevice.MICROPHONE:
            for pcm in self.speech.feed(b''.join(bytes(frame.data) for frame in frames)): self.heard(pcm)
            if self.speech.voiced >= self.cfg['audio']['barge_in_ms']: self.talked = self.loop.time()
    def heard(self, pcm):
        if pcm:
            path = self.file(f'{time.time_ns()}.pcm')
            path.write_bytes(pcm)
            self.tasks.create_task(self.send('ears', str(path), '01'))
    def signaling(self, serial, data):
        if serial == self.serial: self.tasks.create_task(self.client(phone.SendSignalingDataRequest(peer=self.peer, data=data)))
    def connection(self, serial, state):
        if serial != self.serial: return
        if state == n.ConnectionState.CONNECTED and not self.connected.done(): self.connected.set_result(None)
        if state == n.ConnectionState.CLOSED: self.tasks.create_task(self.hang(serial, self.outgoing and self.state == 1, False))
        if state in (n.ConnectionState.FAILED, n.ConnectionState.TIMEOUT): self.tasks.create_task(self.failed(state))
    async def failed(self, state):
        raise RuntimeError(f'Call transport failed: {state}')
    async def connect(self, call):
        endpoints = []
        for item in call.connections:
            if isinstance(item, t.PhoneConnectionWebrtc): endpoints.append(n.RTCServer(item.id, item.ip, item.ipv6, item.port, item.username, item.password, item.turn, item.stun, False, None))
            elif isinstance(item, t.PhoneConnection): endpoints.append(n.RTCServer(item.id, item.ip, item.ipv6, item.port, None, None, False, True, item.tcp, bytes(item.peer_tag)))
            else: raise ValueError('Unknown call connection type')
        await self.native('connect_p2p', self.owner, endpoints, list(call.protocol.library_versions), call.p2p_allowed, None if call.custom_parameters is None else call.custom_parameters.data)
        self.linked = True
        for data in self.signals: await self.native('send_signaling_data', self.owner, data)
        self.signals.clear()
        await asyncio.wait_for(self.connected, self.settings['connect_seconds'])
        self.state, self.since = 2, self.loop.time()
        journal(self.run, 'call_connected', outgoing=self.outgoing)
        await self.send('timer', '', '03')
    async def dial(self):
        journal(self.run, 'call_dial')
        await self.prepare()
        exchange = bytes(await self.native('init_exchange', self.owner, self.dh, None))
        call = (await self.client(phone.RequestCallRequest(user_id=self.contact, random_id=secrets.randbelow(self.settings['random_id_max']), g_a_hash=exchange, protocol=self.protocol(), video=False))).phone_call
        if isinstance(call, t.PhoneCallDiscarded): await self.hang(self.serial, call.reason is None or isinstance(call.reason, t.PhoneCallDiscardReasonMissed), False); return
        self.peer = t.InputPhoneCall(call.id, call.access_hash)
        try: accepted = await asyncio.wait_for(self.accepted, self.settings['ring_seconds'])
        except TimeoutError: await self.hang(self.serial, True); return
        if accepted.id != self.peer.id: raise RuntimeError('Wrong call accepted')
        keys = await self.native('exchange_keys', self.owner, bytes(accepted.g_b), 0)
        await self.connect((await self.client(phone.ConfirmCallRequest(peer=self.peer, g_a=bytes(keys.g_a_or_b), key_fingerprint=keys.key_fingerprint, protocol=self.protocol()))).phone_call)
    async def answer(self, call):
        await self.prepare()
        exchange = bytes(await self.native('init_exchange', self.owner, self.dh, bytes(call.g_a_hash)))
        response = (await self.client(phone.AcceptCallRequest(peer=self.peer, g_b=exchange, protocol=self.protocol()))).phone_call
        confirmed = response if isinstance(response, t.PhoneCall) else await asyncio.wait_for(self.confirmed, self.settings['connect_seconds'])
        await self.native('exchange_keys', self.owner, bytes(confirmed.g_a_or_b), confirmed.key_fingerprint)
        await self.connect(confirmed)
    async def update(self, event):
        if isinstance(event, t.UpdatePhoneCallSignalingData) and self.peer is not None and event.phone_call_id == self.peer.id:
            if self.linked: await self.native('send_signaling_data', self.owner, bytes(event.data))
            else: self.signals.append(bytes(event.data))
        if not isinstance(event, t.UpdatePhoneCall): return
        call = event.phone_call
        if isinstance(call, t.PhoneCallRequested) and call.admin_id == self.owner:
            self.call_grant = 0
            if self.state: await self.hang(self.serial, False)
            self.state, self.peer, self.outgoing = 1, t.InputPhoneCall(call.id, call.access_hash), False
            journal(self.run, 'call_incoming')
            self.call = self.tasks.create_task(self.answer(call))
            await self.send('timer', '', '03')
        elif isinstance(call, t.PhoneCallAccepted) and self.outgoing and self.state == 1 and self.engine is not None and call.participant_id == self.owner and (self.peer is None or call.id == self.peer.id) and not self.accepted.done(): self.accepted.set_result(call)
        elif self.peer is not None and call.id == self.peer.id:
            if isinstance(call, t.PhoneCall) and not self.confirmed.done(): self.confirmed.set_result(call)
            if isinstance(call, t.PhoneCallDiscarded): await self.hang(self.serial, isinstance(call.reason, t.PhoneCallDiscardReasonMissed) or call.reason is None and self.outgoing and self.state == 1, False)
    async def hang(self, serial, missed, discard=True):
        async with self.call_lock:
            if serial != self.serial: return
            self.serial += 1
            if self.call is not None and self.call != asyncio.current_task() and not self.call.done(): self.call.cancel(); await asyncio.wait([self.call])
            if discard and self.peer is not None: await self.client(phone.DiscardCallRequest(peer=self.peer, duration=int(self.loop.time() - self.since) * int(bool(self.since)), reason=t.PhoneCallDiscardReasonHangup(), connection_id=0, video=False))
            self.heard(self.speech.finish())
            self.speech.pending = b''
            if self.engine is not None: await self.native('stop', self.owner)
            self.peer, self.engine, self.state, self.linked, self.since = None, None, 0, False, 0
            self.signals.clear()
            journal(self.run, 'call_end', missed=missed)
            await self.send('timer', '', ('03', '02')[missed])
    async def play(self, filename):
        samples, rate = soundfile.read(filename, dtype='int16', always_2d=True)
        target, width = self.cfg['audio']['send_rate'], self.cfg['audio']['send_frame_bytes']
        factor = gcd(rate, target)
        data = np.clip(np.rint(resample_poly(samples.mean(axis=1), target // factor, rate // factor)), -32768, 32767).astype('<i2').tobytes()
        serial, started = self.serial, self.loop.time()
        for offset in range(0, len(data), width):
            async with self.call_lock:
                if self.state != 2 or serial != self.serial or self.talked > started:
                    journal(self.run, 'speech_interrupted', media=filename.rsplit('\\', 1)[-1].rsplit('/', 1)[-1])
                    return
                await self.native('send_external_frame', self.owner, n.StreamDevice.MICROPHONE, data[offset:offset + width].ljust(width, b'\0'), n.FrameData(int(time.time() * 1000), n.VIDEO_ROTATION_0, 0, 0))
            await asyncio.sleep(max(0, started + (offset + width) / (target * self.cfg['audio']['sample_bytes']) - self.loop.time()))
        journal(self.run, 'speech_frames_submitted', media=filename.rsplit('\\', 1)[-1].rsplit('/', 1)[-1])
    async def close(self):
        self.client.remove_event_handler(self.message)
        self.client.remove_event_handler(self.raw)
        self.serial += 1
        if self.peer is not None: await self.client(phone.DiscardCallRequest(peer=self.peer, duration=0, reason=t.PhoneCallDiscardReasonHangup(), connection_id=0, video=False))
        if self.engine is not None: await self.native('stop', self.owner)
Telegram('telegram').launch()
