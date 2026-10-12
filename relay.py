import asyncio, json, os, secrets, time, uuid
import ntgcalls
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from telethon.tl import types as t
from telethon.tl.functions import messages, phone
from fabric import CFG, ROOT, RUN, NotAttempted, log, publish_owner, request, serve, write

class CallEnded(Exception):
    pass

async def main():
    owner = CFG['owner']
    accounts = [a for a in TDesktop(os.path.expandvars(CFG['tdata'])).accounts if a.UserId != owner]
    if len(accounts) != 1:
        raise RuntimeError('Exactly one assistant Telegram user account required')
    client = await TelegramClient.FromTDesktop(accounts[0], session=str(ROOT / 'private' / 'telegram'),
                    flag=UseCurrentSession, api=API.TelegramDesktop, request_retries=0,
                    connection_retries=0, flood_sleep_threshold=0, catch_up=True)
    await asyncio.wait_for(client.connect(), CFG['seconds'])
    await asyncio.wait_for(client.get_dialogs(), CFG['seconds'])
    peer = await asyncio.wait_for(client.get_input_entity(owner), CFG['seconds'])
    grant = None
    loop = asyncio.get_running_loop()
    call_lock = asyncio.Lock()
    line = dict(serial=0, peer=None, engine=None, linked=False, signals=[], outgoing=False, dh=None,
                accepted=None, confirmed=None, connected=None, since=0)
    cursor_path = ROOT/'private'/'incoming.json'
    latest = json.loads(cursor_path.read_text('utf-8'))['id'] if cursor_path.exists() else 0

    def protocol():
        value = ntgcalls.NTgCalls.get_protocol()
        return t.PhoneCallProtocol(udp_p2p=value.udp_p2p, udp_reflector=value.udp_reflector, min_layer=65,
                                    max_layer=value.max_layer, library_versions=list(reversed(value.library_versions)))

    async def native(method, *args):
        return await getattr(line['engine'], method)(*args)

    def finish(name, error=None):
        fut = line.get(name)
        if fut is not None and not fut.done():
            if error is None:
                fut.set_result(None)
                return
            fut.set_exception(error)
            fut.add_done_callback(lambda item: item.exception())

    async def hang(serial, missed, discard=True):
        async with call_lock:
            if serial != line['serial']:
                return
            line['serial'] += 1
            current, engine = line['peer'], line['engine']
            line.update(peer=None, engine=None, linked=False, signals=[])
            for name in ('accepted', 'confirmed', 'connected'):
                finish(name, CallEnded('The call ended'))
            if discard and current is not None:
                try:
                    await client(phone.DiscardCallRequest(peer=current, duration=int(max(0, time.time()-line['since'])),
                                                           reason=t.PhoneCallDiscardReasonHangup(), connection_id=0, video=False))
                except Exception as error:
                    log('call_discard_failed', error=str(error))
            if engine is not None:
                try:
                    await engine.stop(owner)
                except Exception as error:
                    log('call_stop_failed', error=str(error))
            line['since'] = 0
            log('call_end', missed=missed)

    def connection(serial, state):
        if serial != line['serial']:
            return
        if state == ntgcalls.ConnectionState.CONNECTED:
            finish('connected')
        if state == ntgcalls.ConnectionState.CLOSED:
            loop.create_task(hang(serial, line['outgoing'], False))
        if state in (ntgcalls.ConnectionState.FAILED, ntgcalls.ConnectionState.TIMEOUT):
            finish('connected', RuntimeError(f'Call transport failed: {state}'))

    def signaling(serial, data):
        if serial == line['serial'] and line['peer'] is not None:
            loop.create_task(client(phone.SendSignalingDataRequest(peer=line['peer'], data=data)))

    async def prepare():
        line['serial'] += 1
        serial = line['serial']
        line['accepted'], line['confirmed'], line['connected'] = (loop.create_future() for _ in range(3))
        line['engine'], line['linked'], line['signals'] = ntgcalls.NTgCalls(), False, []
        engine = line['engine']
        engine.on_connection_change(lambda uid, info: loop.call_soon_threadsafe(connection, serial, info.state))
        engine.on_signaling_data(lambda uid, data: loop.call_soon_threadsafe(signaling, serial, bytes(data)))
        await native('create_p2p_call', owner)
        for mode, rate in ((ntgcalls.StreamMode.CAPTURE, 48000), (ntgcalls.StreamMode.PLAYBACK, 16000)):
            await native('set_stream_sources', owner, mode, ntgcalls.MediaDescription(
                ntgcalls.AudioDescription(ntgcalls.MediaSource.EXTERNAL, rate, 1, '', True), None, None, None))
        dh = await client(messages.GetDhConfigRequest(0, 256))
        line['dh'] = ntgcalls.DhConfig(dh.g, bytes(dh.p), bytes(dh.random))

    async def connect_call(call):
        endpoints = []
        for item in call.connections:
            if isinstance(item, t.PhoneConnectionWebrtc):
                endpoints.append(ntgcalls.RTCServer(item.id, item.ip, item.ipv6, item.port, item.username, item.password, item.turn, item.stun, False, None))
            elif isinstance(item, t.PhoneConnection):
                endpoints.append(ntgcalls.RTCServer(item.id, item.ip, item.ipv6, item.port, None, None, False, True, item.tcp, bytes(item.peer_tag)))
            else:
                raise ValueError('Unknown call connection type')
        await native('connect_p2p', owner, endpoints, list(call.protocol.library_versions), call.p2p_allowed,
                     None if call.custom_parameters is None else call.custom_parameters.data)
        line['linked'] = True
        for data in line['signals']:
            await native('send_signaling_data', owner, data)
        line['signals'].clear()
        await asyncio.wait_for(line['connected'], 30)
        line['since'] = time.time()
        log('call_connected', outgoing=True)

    async def place():
        line['outgoing'] = True
        serial = None
        try:
            await prepare()
            serial = line['serial']
            exchange = bytes(await native('init_exchange', owner, line['dh'], None))
            invited = (await client(phone.RequestCallRequest(user_id=peer, random_id=secrets.randbelow(2147483648),
                                                              g_a_hash=exchange, protocol=protocol(), video=False))).phone_call
            if isinstance(invited, t.PhoneCallDiscarded):
                await hang(serial, True, False)
                return dict(connected=False, refused=True, reason='Telegram discarded the call before it rang', outcome='failed')
            line['peer'] = t.InputPhoneCall(invited.id, invited.access_hash)
            try:
                accepted = await asyncio.wait_for(line['accepted'], 90)
            except TimeoutError:
                await hang(serial, True)
                return dict(connected=False, refused=True, reason='The owner did not answer', outcome='failed')
            if accepted.id != line['peer'].id:
                raise RuntimeError('Wrong call accepted')
            keys = await native('exchange_keys', owner, bytes(accepted.g_b), 0)
            confirmed = (await client(phone.ConfirmCallRequest(peer=line['peer'], g_a=bytes(keys.g_a_or_b),
                                                                key_fingerprint=keys.key_fingerprint, protocol=protocol()))).phone_call
            await connect_call(confirmed)
            return dict(connected=True, outcome='completed')
        except CallEnded:
            return dict(connected=False, refused=True, reason='Telegram ended the call', outcome='failed')
        except Exception:
            if serial is not None:
                await hang(serial, False)
            raise

    async def phone_update(event):
        if isinstance(event, t.UpdatePhoneCallSignalingData) and line['peer'] is not None and event.phone_call_id == line['peer'].id:
            data = bytes(event.data)
            if line['linked'] and line['engine'] is not None:
                await native('send_signaling_data', owner, data)
            else:
                line['signals'].append(data)
            return
        if not isinstance(event, t.UpdatePhoneCall):
            return
        call = event.phone_call
        if (isinstance(call, t.PhoneCallAccepted) and line['outgoing'] and line['engine'] is not None and
                call.participant_id == owner and (line['peer'] is None or call.id == line['peer'].id) and
                line['accepted'] is not None and not line['accepted'].done()):
            line['accepted'].set_result(call)
        elif line['peer'] is not None and call.id == line['peer'].id:
            if isinstance(call, t.PhoneCall) and line['confirmed'] is not None and not line['confirmed'].done():
                line['confirmed'].set_result(call)
            if isinstance(call, t.PhoneCallDiscarded):
                missed = isinstance(call.reason, t.PhoneCallDiscardReasonMissed) or call.reason is None
                await hang(line['serial'], missed, False)

    @client.on(events.Raw)
    async def raw(event):
        loop.create_task(phone_update(event))

    @client.on(events.NewMessage(incoming=True, from_users=[owner]))
    async def incoming(event):
        nonlocal grant, latest
        if not event.is_private or event.id <= latest:
            return
        latest = event.id
        write(cursor_path, dict(id=latest))
        grant = None
        if event.voice:
            try:
                path = await asyncio.wait_for(event.download_media(file=str(RUN)), CFG['seconds'])
                result = await request('cochlea', 'transcribe', dict(path=path), CFG['seconds'])
            except Exception as error:
                result = dict(error=f'{type(error).__name__}: {error}')
            if 'text' not in result:
                log('input_failed', result=result)
                publish_owner('Your incoming voice note could not be transcribed. Its intent is unknown; please send it again as text.', 'input-failure')
                return
            text = result['text']
        else:
            text = event.raw_text
        if text.strip():
            if event.id != latest:
                log('superseded_voice', message_id=event.id)
                return
            ident = uuid.uuid5(uuid.NAMESPACE_URL, f'telegram:{owner}:{event.id}').hex
            publish_owner(text, 'telegram-owner', at=event.date.timestamp(), ident=ident)

    async def handle(op, args):
        nonlocal grant
        if op == 'deliver':
            message = await asyncio.wait_for(client.send_file(peer, args['path'], voice_note=True), CFG['seconds'])
            return dict(delivered=True, message_id=message.id, hearing_verified=False, outcome='completed')
        if op == 'authorize':
            current = json.loads((ROOT / 'private' / 'owner.json').read_text('utf-8'))
            if (args['owner_id'] != current['id'] or args['quote'] != current['text'] or
                    current['source'] not in ('telegram-owner', 'local-owner') or
                    time.time()-current['at'] >= CFG['seconds']):
                raise NotAttempted('Permission must cite the current owner message')
            grant = (current['id'], time.time()+CFG['seconds'])
            log('permission', owner_id=current['id'])
            return dict(authorized=True, once=True)
        if op == 'dial':
            current = json.loads((ROOT / 'private' / 'owner.json').read_text('utf-8'))
            permission, grant = grant, None
            if not permission or permission[0] != current['id'] or time.time() >= permission[1]:
                return dict(refused=True, reason='Explicit current owner permission required', connected=False, outcome='not_attempted')
            log('dial_attempt')
            return await place()
        raise ValueError(op)

    try:
        await serve(handle, 'relay')
    finally:
        if line['peer'] is not None or line['engine'] is not None:
            await hang(line['serial'], False)
        await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
