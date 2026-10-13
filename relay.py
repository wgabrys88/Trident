import asyncio, audioop, json, os, secrets, time, uuid
from collections import deque
import ntgcalls as n
import soundfile as sf
import numpy as np
from opentele.api import API, UseCurrentSession
from opentele.td import TDesktop
from opentele.tl.telethon import TelegramClient
from telethon import events
from telethon.tl import types as t
from telethon.tl.functions import messages, phone
from fabric import CFG, ROOT, RUN, NotAttempted, log, publish_owner, request, serve, token, write

async def main():
    owner = CFG['owner']; loop=asyncio.get_running_loop()
    accounts=[a for a in TDesktop(os.path.expandvars(CFG['tdata'])).accounts if a.UserId!=owner]
    if len(accounts)!=1:
        raise RuntimeError('Exactly one assistant Telegram user account required')
    client=await TelegramClient.FromTDesktop(accounts[0],session=str(ROOT/'private'/'telegram'),flag=UseCurrentSession,
        api=API.TelegramDesktop,request_retries=0,connection_retries=0,flood_sleep_threshold=0,catch_up=True)
    await asyncio.wait_for(client.connect(),15); await asyncio.wait_for(client.get_dialogs(),15)
    peer=await asyncio.wait_for(client.get_input_entity(owner),15)
    cursor=ROOT/'private'/'incoming.json'
    recent=await asyncio.wait_for(client.get_messages(peer,limit=1),15) if not cursor.exists() else []
    latest=json.loads(cursor.read_text('utf-8'))['id'] if cursor.exists() else recent[0].id if recent else 0
    write(cursor,dict(id=latest))
    consumed_path=ROOT/'private'/'call-intent.json'
    consumed=json.loads(consumed_path.read_text('utf-8'))['owner_id'] if consumed_path.exists() else None
    grant=None; line=dict(engine=None,peer=None,serial=0,connected=False,outgoing=False,signals=[],rx=bytearray(),silent=0,tx=deque())

    def protocol():
        value=n.NTgCalls.get_protocol()
        return t.PhoneCallProtocol(udp_p2p=value.udp_p2p,udp_reflector=value.udp_reflector,min_layer=65,
            max_layer=value.max_layer,library_versions=list(reversed(value.library_versions)))
    async def api(method,*args):
        return await getattr(line['engine'],method)(*args)
    async def transcription(pcm):
        path=RUN/(token()+'-call.wav'); sf.write(path,np.frombuffer(pcm,dtype='<i2'),16000,subtype='PCM_16')
        result=await request('cochlea','transcribe',dict(path=str(path)),30)
        log('call_audio_received',path=str(path),bytes=len(pcm),transcription=result)
        if result.get('text','').strip():
            publish_owner(result['text'],'telegram-call')
    def receive(serial,mode,device,frames):
        if serial!=line['serial'] or mode!=n.StreamMode.PLAYBACK or device!=n.StreamDevice.SPEAKER:
            return
        for data in frames:
            line['recording'].write(np.frombuffer(data,dtype='<i2'))
            line['rx_bytes']+=len(data); line['peak_rms']=max(line['peak_rms'],audioop.rms(data,2))
            loud=audioop.rms(data,2)>300
            if loud or line['rx']:
                line['rx'].extend(data); line['silent']=0 if loud else line['silent']+len(data)
                if line['silent']>=25600 or len(line['rx'])>=480000:
                    pcm=bytes(line['rx']); line['rx'].clear(); line['silent']=0; loop.create_task(transcription(pcm))
    async def transmit(serial):
        due=time.monotonic()
        try:
            while serial==line['serial']:
                data,completion=line['tx'].popleft() if line['tx'] else (bytes(960),None)
                await api('send_external_frame',owner,n.StreamDevice.MICROPHONE,data,n.FrameData())
                line['tx_frames']+=1
                if completion and not completion.done():
                    completion.set_result(None)
                due+=.01; await asyncio.sleep(max(0,due-time.monotonic()))
        except Exception as error:
            log('call_audio_failed',error=f'{type(error).__name__}: {error}')
            publish_owner('The call audio stream stopped; hearing and speech are unavailable.','call-observation')
            line['media_error']=str(error)
    async def hang(discard=True):
        current,engine=line['peer'],line['engine']; line['serial']+=1
        if line['rx']:
            loop.create_task(transcription(bytes(line['rx'])))
        if line.get('recording'):
            line['recording'].close(); line['recording']=None
        line.update(engine=None,peer=None,connected=False,rx=bytearray(),tx=deque())
        for name in ('accepted','confirmed','connection'):
            future=line.get(name)
            if future and not future.done():
                future.set_exception(RuntimeError('Telegram ended the call')); future.add_done_callback(lambda f:f.exception())
        if current and discard:
            await asyncio.wait_for(client(phone.DiscardCallRequest(peer=current,duration=0,reason=t.PhoneCallDiscardReasonHangup(),connection_id=0,video=False)),10)
        if engine:
            await engine.stop(owner)
        log('call_ended',media={k:line.get(k) for k in ('rx_bytes','tx_frames','peak_rms','audio_path')})
    def connection(serial,state):
        if serial==line['serial'] and state in (n.ConnectionState.CONNECTED,n.ConnectionState.FAILED,n.ConnectionState.TIMEOUT,n.ConnectionState.CLOSED):
            if state==n.ConnectionState.CONNECTED:
                line['connected']=True
                if not line['connection'].done():
                    line['connection'].set_result(None)
            elif not line['connection'].done():
                line['connection'].set_exception(RuntimeError(f'Call transport {state}'))
            else:
                loop.create_task(hang(False))
    async def signal(serial,data):
        if serial==line['serial'] and line['peer']:
            await client(phone.SendSignalingDataRequest(peer=line['peer'],data=data))
    async def prepare(outgoing):
        line.update(serial=line['serial']+1,outgoing=outgoing,connected=False,linked=False,signals=[],rx=bytearray(),silent=0,tx=deque(),media_error=None)
        serial=line['serial']; line['engine']=n.NTgCalls()
        line.update(rx_bytes=0,tx_frames=0,peak_rms=0,audio_path=str(RUN/(token()+'-full-call.wav')))
        line['recording']=sf.SoundFile(line['audio_path'],'w',samplerate=16000,channels=1,subtype='PCM_16')
        for name in ('accepted','confirmed','connection'):
            line[name]=loop.create_future()
        engine=line['engine']
        engine.on_connection_change(lambda uid,info:loop.call_soon_threadsafe(connection,serial,info.state))
        engine.on_signaling_data(lambda uid,data:loop.call_soon_threadsafe(lambda:loop.create_task(signal(serial,bytes(data)))))
        engine.on_frames(lambda uid,mode,device,frames:loop.call_soon_threadsafe(receive,serial,mode,device,[bytes(f.data) for f in frames]))
        await api('create_p2p_call',owner)
        for mode,rate in ((n.StreamMode.CAPTURE,48000),(n.StreamMode.PLAYBACK,16000)):
            audio=n.AudioDescription(n.MediaSource.EXTERNAL,rate,1,'',True)
            await api('set_stream_sources',owner,mode,n.MediaDescription(audio if mode==n.StreamMode.CAPTURE else None,audio if mode==n.StreamMode.PLAYBACK else None,None,None))
        loop.create_task(transmit(serial))
        dh=await client(messages.GetDhConfigRequest(0,256))
        line['dh']=n.DhConfig(dh.g,bytes(dh.p),bytes(dh.random))
    async def connect(call):
        endpoints=[]
        for item in call.connections:
            if isinstance(item,t.PhoneConnectionWebrtc):
                endpoints.append(n.RTCServer(item.id,item.ip,item.ipv6,item.port,item.username,item.password,item.turn,item.stun,False,None))
            else:
                endpoints.append(n.RTCServer(item.id,item.ip,item.ipv6,item.port,None,None,False,True,item.tcp,bytes(item.peer_tag)))
        await api('connect_p2p',owner,endpoints,list(call.protocol.library_versions),call.p2p_allowed,None if call.custom_parameters is None else call.custom_parameters.data)
        line['linked']=True
        for data in line['signals']:
            await api('send_signaling_data',owner,data)
        line['signals'].clear(); await asyncio.wait_for(line['connection'],30)
        log('call_connected',outgoing=line['outgoing'])
        return dict(connected=True,media_error=line['media_error'],hearing_verified=False,outcome='completed')
    async def dial():
        await prepare(True)
        exchange=bytes(await api('init_exchange',owner,line['dh'],None))
        call=(await client(phone.RequestCallRequest(user_id=peer,random_id=secrets.randbelow(2147483648),g_a_hash=exchange,protocol=protocol(),video=False))).phone_call
        if isinstance(call,t.PhoneCallDiscarded):
            await hang(False); return dict(connected=False,reason='Telegram discarded the call',outcome='failed')
        line['peer']=t.InputPhoneCall(call.id,call.access_hash)
        accepted=await asyncio.wait_for(line['accepted'],60)
        keys=await api('exchange_keys',owner,bytes(accepted.g_b),0)
        confirmed=(await client(phone.ConfirmCallRequest(peer=line['peer'],g_a=bytes(keys.g_a_or_b),key_fingerprint=keys.key_fingerprint,protocol=protocol()))).phone_call
        return await connect(confirmed)
    @client.on(events.Raw)
    async def raw(event):
        if isinstance(event,t.UpdatePhoneCallSignalingData) and line['peer'] and event.phone_call_id==line['peer'].id:
            if line.get('linked'):
                await api('send_signaling_data',owner,bytes(event.data))
            else:
                line['signals'].append(bytes(event.data))
        if isinstance(event,t.UpdatePhoneCall):
            call=event.phone_call
            if isinstance(call,t.PhoneCallDiscarded) and line.get('incoming') and call.id==line['incoming'].id:
                line.pop('incoming')
            if isinstance(call,t.PhoneCallRequested) and call.admin_id==owner and line['peer'] is None:
                line['incoming']=call; publish_owner('The owner is calling on Telegram.','call-observation')
            elif line['engine'] and isinstance(call,t.PhoneCallAccepted) and call.participant_id==owner:
                if not line['accepted'].done():
                    line['accepted'].set_result(call)
            elif line['peer'] and call.id==line['peer'].id:
                if isinstance(call,t.PhoneCall) and not line['confirmed'].done():
                    line['confirmed'].set_result(call)
                elif isinstance(call,t.PhoneCallDiscarded):
                    await hang(False)
    @client.on(events.NewMessage(incoming=True,from_users=[owner]))
    async def incoming(event):
        nonlocal latest,grant
        if not event.is_private or event.id<=latest:
            return
        latest=event.id; write(cursor,dict(id=latest)); grant=None
        if event.voice:
            path=await asyncio.wait_for(event.download_media(file=str(RUN)),15)
            result=await request('cochlea','transcribe',dict(path=path),30); text=result.get('text','')
            if not text:
                publish_owner('Incoming voice note could not be transcribed; intent is unknown.','input-failure')
        else:
            text=event.raw_text
        if text.strip() and event.id==latest:
            publish_owner(text,'telegram-owner',at=event.date.timestamp(),ident=uuid.uuid5(uuid.NAMESPACE_URL,f'telegram:{owner}:{event.id}').hex)
    async def handle(op,args):
        nonlocal grant,consumed
        if op=='deliver':
            message=await asyncio.wait_for(client.send_file(peer,args['path'],voice_note=True),15)
            result=dict(delivered=True,message_id=message.id,hearing_verified=False,outcome='completed')
            if line['connected'] and args.get('wav'):
                samples,rate=sf.read(args['wav'],dtype='int16'); pcm=audioop.ratecv(samples.tobytes(),2,1,rate,48000,None)[0]
                completion=loop.create_future()
                for index in range(0,len(pcm),960):
                    chunk=pcm[index:index+960]; line['tx'].append((chunk.ljust(960,b'\0'),completion if index+960>=len(pcm) else None))
                try:
                    await asyncio.wait_for(completion,len(pcm)/96000+5)
                    result['call_audio_submitted']=True
                except Exception as error:
                    result['call_audio_error']=f'{type(error).__name__}: {error}'
            return result
        if op=='authorize':
            current=json.loads((ROOT/'private'/'owner.json').read_text('utf-8'))
            if (args['owner_id']!=current['id'] or args['quote']!=current['text'] or time.time()-current['at']>=120
                    or current['source'] not in ('telegram-owner','telegram-call','local-owner') or consumed==current['id']):
                raise NotAttempted('Call permission requires the exact current owner request')
            audit=await request('discern','decide',dict(state=current,questions=dict(intent=dict(type='choice',instructions='Does this owner directly request an outgoing call NOW, rather than quoting history or describing a capability?',criteria=dict(explicit='Direct current request to call.',absent='No current calling request.',ambiguous='Unclear.')))),15)
            if audit.get('answers',{}).get('intent',{}).get('choice')!='explicit':
                raise NotAttempted('Current explicit outgoing-call intent is absent')
            grant=(current['id'],time.time()+120); return dict(authorized=True,once=True,outcome='completed')
        if op in ('dial','answer'):
            if op=='dial':
                current=json.loads((ROOT/'private'/'owner.json').read_text('utf-8')); permission,grant=grant,None
                if not permission or permission[0]!=current['id'] or time.time()>=permission[1]:
                    raise NotAttempted('Separate current consumed call authorization required')
                consumed=current['id']; write(consumed_path,dict(owner_id=consumed,at=time.time()))
            try:
                if op=='dial':
                    return await dial()
                call=line.pop('incoming'); await prepare(False); line['peer']=t.InputPhoneCall(call.id,call.access_hash)
                exchange=bytes(await api('init_exchange',owner,line['dh'],bytes(call.g_a_hash)))
                await client(phone.AcceptCallRequest(peer=line['peer'],g_b=exchange,protocol=protocol()))
                confirmed=await asyncio.wait_for(line['confirmed'],60)
                await api('exchange_keys',owner,bytes(confirmed.g_a_or_b),confirmed.key_fingerprint)
                return await connect(confirmed)
            except Exception as error:
                try:
                    await hang()
                except Exception as cleanup:
                    log('call_cleanup_failed',error=str(cleanup))
                return dict(connected=False,reason=f'{type(error).__name__}: {error}',outcome='failed')
        if op=='hangup':
            await hang(); return dict(connected=False,outcome='completed')
        if op=='status':
            return dict(connected=line['connected'],incoming=bool(line.get('incoming')),media_error=line.get('media_error'),queued_frames=len(line['tx']),media={k:line.get(k) for k in ('rx_bytes','tx_frames','peak_rms','audio_path')},outcome='completed')
        raise ValueError(op)
    try:
        await serve(handle,'relay')
    finally:
        try:
            await hang()
        finally:
            await client.disconnect()

if __name__=='__main__':
    asyncio.run(main())
