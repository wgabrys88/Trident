import asyncio, json, os, time, urllib.request, uuid
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from fabric import CFG, ROOT, RUN, NotAttempted, log, publish_owner, request, serve, write

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
    cursor_path = ROOT/'private'/'incoming.json'
    latest = json.loads(cursor_path.read_text('utf-8'))['id'] if cursor_path.exists() else 0

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
                    time.time()-current['at'] >= 120):
                raise NotAttempted('Permission must cite the current owner message')
            grant = (current['id'], time.time()+120)
            log('permission', owner_id=current['id'])
            return dict(authorized=True, once=True)
        if op == 'dial':
            current = json.loads((ROOT / 'private' / 'owner.json').read_text('utf-8'))
            permission, grant = grant, None
            if not permission or permission[0] != current['id'] or time.time() >= permission[1]:
                return dict(refused=True, reason='Explicit current owner permission required', connected=False, outcome='not_attempted')
            endpoint = os.environ.get('TRIDENT_CALL_URL')
            log('dial_attempt', provider=bool(endpoint))
            if not endpoint:
                return dict(refused=True, reason='No outgoing call provider configured; no network call was placed', connected=False, outcome='failed')
            req = urllib.request.Request(endpoint, json.dumps(dict(owner=owner)).encode(), {'Content-Type':'application/json'})
            def dial():
                with urllib.request.urlopen(req, timeout=CFG['seconds']) as response:
                    return json.load(response)
            result = await asyncio.to_thread(dial)
            if not isinstance(result, dict) or not isinstance(result.get('connected'), bool):
                return dict(error='Call provider returned no connection outcome', outcome='unknown', verified=False)
            result.setdefault('outcome', 'completed' if result['connected'] else 'failed')
            if not result['connected']:
                result.setdefault('refused', True)
                result.setdefault('reason', 'The provider reported that the call was not connected')
            return result
        raise ValueError(op)

    try:
        await serve(handle, 'relay')
    finally:
        await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
