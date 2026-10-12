import asyncio, json, os, time, urllib.request
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from fabric import CFG, ROOT, RUN, log, publish_owner, request, serve

async def main():
    owner = CFG['owner']
    accounts = [a for a in TDesktop(os.path.expandvars(CFG['tdata'])).accounts if a.UserId != owner]
    if len(accounts) != 1:
        raise RuntimeError('Exactly one assistant Telegram user account required')
    client = await TelegramClient.FromTDesktop(accounts[0], session=str(ROOT / 'private' / 'telegram'),
                    flag=UseCurrentSession, api=API.TelegramDesktop, request_retries=0,
                    connection_retries=0, flood_sleep_threshold=0, catch_up=True)
    await asyncio.wait_for(client.connect(), 15)
    await asyncio.wait_for(client.get_dialogs(), 15)
    peer = await asyncio.wait_for(client.get_input_entity(owner), 15)
    grant = None

    @client.on(events.NewMessage(incoming=True, from_users=[owner]))
    async def incoming(event):
        nonlocal grant
        if not event.is_private:
            return
        grant = None
        if event.voice:
            try:
                path = await asyncio.wait_for(event.download_media(file=str(RUN)), 15)
                result = await request('cochlea', 'transcribe', dict(path=path), 30)
            except Exception as error:
                result = dict(error=f'{type(error).__name__}: {error}')
            if 'text' not in result:
                log('input_failed', result=result)
                publish_owner('My incoming voice note could not be transcribed: '+str(result), 'input-failure')
                return
            text = result['text']
        else:
            text = event.raw_text
        if text.strip():
            publish_owner(text, 'telegram-owner')

    async def handle(op, args):
        nonlocal grant
        if op == 'deliver':
            message = await asyncio.wait_for(client.send_file(peer, args['path'], voice_note=True), 15)
            return dict(delivered=True, message_id=message.id, hearing_verified=False)
        if op == 'authorize':
            current = json.loads((ROOT / 'private' / 'owner.json').read_text('utf-8'))
            if (args['owner_id'] != current['id'] or args['quote'] != current['text'] or
                    current['source'] not in ('telegram-owner', 'local-owner') or
                    time.time()-current['at'] >= 120 or args.get('intent_confirmed') is not True):
                raise ValueError('Permission must cite the current owner message')
            grant = (current['id'], time.time()+120)
            log('permission', owner_id=current['id'])
            return dict(authorized=True, once=True)
        if op == 'dial':
            current = json.loads((ROOT / 'private' / 'owner.json').read_text('utf-8'))
            permission, grant = grant, None
            if not permission or permission[0] != current['id'] or time.time() >= permission[1]:
                return dict(refused=True, reason='Explicit current owner permission required', connected=False)
            endpoint = os.environ.get('TRIDENT_CALL_URL')
            log('dial_attempt', provider=bool(endpoint))
            if not endpoint:
                return dict(refused=True, reason='No outgoing call provider configured', connected=False)
            req = urllib.request.Request(endpoint, json.dumps(dict(owner=owner)).encode(), {'Content-Type':'application/json'})
            def dial():
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.load(response)
            result = await asyncio.to_thread(dial)
            if not isinstance(result, dict) or not isinstance(result.get('connected'), bool):
                return dict(error='Call provider returned no connection outcome', outcome='unknown', verified=False)
            return result
        raise ValueError(op)

    try:
        await serve(handle, 'relay')
    finally:
        await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
