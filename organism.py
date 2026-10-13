import asyncio, ctypes, json, msvcrt, os, shutil, sys, threading, time
from pathlib import Path
from urllib.parse import urlparse
import psutil
from fabric import CFG, ROOT, RUN, Job, execute, log, openrouter_key, publish_owner, request, token, write

DEVICES = ['manipulator','retina','phonation','relay','cochlea','discern']

class Body:
    def __init__(self):
        self.jobs, self.logs = {}, []
    def spawn(self, role, command=None):
        stream = (RUN/f'{role}.log').open('ab'); self.logs.append(stream)
        self.jobs[role] = Job(command or [sys.executable,'-B',str(ROOT/f'{role}.py')],cwd=ROOT,stdout=stream,stderr=stream)
        log('started',role=role,pid=self.jobs[role].process.pid)
    def stop(self,role):
        job = self.jobs[role]
        if job.handle:
            try:
                job.close(); job.process.wait(timeout=3)
                log('stopped',role=role,code=job.process.returncode)
            except Exception as error:
                log('cleanup_failed',role=role,error=str(error))
            if role=='manipulator':
                ctypes.windll.user32.mouse_event(4,0,0,0,0)
                for key in (0x10,0x11,0x12,0x5B):
                    ctypes.windll.user32.keybd_event(key,0,2,0)
    async def call(self,role,op,args,seconds):
        job = self.jobs.get(role)
        if not job or job.process.poll() is not None:
            return dict(outcome='not_attempted',error=f'{role} unavailable')
        result = await request(role,op,args,seconds)
        if result.get('outcome')=='unknown':
            self.stop(role)
        return result
    def close(self):
        for role in self.jobs:
            self.stop(role)
        for stream in self.logs:
            stream.close()

async def hardware():
    processes = {}
    while True:
        sample = dict(at=time.time(),cpu_percent=psutil.cpu_percent(),ram=psutil.virtual_memory()._asdict(),processes={})
        for child in [psutil.Process(os.getpid())]+psutil.Process(os.getpid()).children(recursive=True):
            try:
                proc = processes.setdefault(child.pid,child)
                sample['processes'][str(proc.pid)] = dict(name=proc.name(),cpu_percent=proc.cpu_percent(),rss=proc.memory_info().rss)
            except psutil.NoSuchProcess:
                pass
        try:
            gpu = await asyncio.to_thread(execute,['nvidia-smi','--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw','--format=csv,noheader,nounits'],3)
            sample['gpu'] = gpu.decode().strip()
        except Exception as error:
            sample['gpu_error'] = str(error)
        with (RUN/'hardware.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(sample)+'\n')
        await asyncio.sleep(15)

def infer(prompt,image):
    folder = Path(os.environ['LOCALAPPDATA'])/'cursor-agent'/'versions'/CFG['cursor_version']
    image = Path(image).resolve()
    prompt = 'FIRST native Read '+str(image)+'. Inspect actual pixels.\n'+prompt
    prompt_file = RUN/(token()+'.prompt'); prompt_file.write_text(prompt,'utf-8')
    transcript = prompt_file.with_suffix('.stream.jsonl')
    bootstrap = "process.argv.push(require('node:fs').readFileSync(process.argv.pop(),'utf8'));require(process.argv[1]);"
    started = time.monotonic()
    raw = execute([str(folder/'node.exe'),'-e',bootstrap,str(folder/'index.js'),'--trust','-p',
        '--approve-mcps','--model',CFG['mind_model'],'--workspace',str(ROOT),'--output-format','stream-json',str(prompt_file)],
        CFG['mind_seconds'],transcript=transcript,cwd=ROOT).decode('utf-8',errors='replace')
    texts,inspected = [],False
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get('type')=='assistant':
            texts.append(''.join(p['text'] for p in event['message']['content'] if p.get('type')=='text'))
        if event.get('type')=='tool_call' and event.get('subtype')=='completed':
            read = event.get('tool_call',{}).get('readToolCall',{})
            path = Path(read.get('args',{}).get('path','')); receipt = read.get('result',{}).get('success',{})
            if (receipt.get('dataBlobId') and receipt.get('fileSize')==image.stat().st_size and not receipt.get('exceededLimit')
                    and (path if path.is_absolute() else ROOT/path).resolve()==image):
                inspected = True
    log('thought',prompt=str(prompt_file),transcript=str(transcript),chars=len(prompt),seconds=round(time.monotonic()-started,3),pixels=inspected)
    if not texts or not inspected:
        raise ValueError('No final answer or successful native PNG Read receipt')
    return texts[-1]

async def main():
    profile=ROOT/'runtime'/'cursor'; profile.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'.cursor'/'cli-config.json',profile/'cli-config.json')
    os.environ.update(CURSOR_CONFIG_DIR=str(profile),HF_HOME=str(ROOT/'runtime'/'hf-cache'),TEMP=str(ROOT/'runtime'/'temp'),TMP=str(ROOT/'runtime'/'temp'),OPENROUTER_API_KEY=openrouter_key())
    private = ROOT/'private'; private.mkdir(exist_ok=True)
    lock = (private/'living.lock').open('a+b')
    if lock.tell()==0:
        lock.write(b'0'); lock.flush()
    lock.seek(0); msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    run = private/token(); run.mkdir(); os.environ['TRIDENT_RUN'] = str(run)
    import fabric
    global RUN
    RUN = fabric.RUN = run
    for role in DEVICES:
        (run/role).mkdir()
    (ROOT/'runtime'/'output').mkdir(parents=True,exist_ok=True)
    state_path = private/'continuity.json'
    state = json.loads(state_path.read_text('utf-8-sig')) if state_path.exists() else dict(
        memory=dict(goal='',narrative=''),owner=dict(id=token(),text='',source='no-owner-goal',at=time.time()),previous=[],unknown={},pixels={})
    state.update(active=True,process_state='running',run=str(run))
    write(state_path,state); body = Body()
    def console():
        for text in sys.stdin:
            if text.strip():
                publish_owner(text.strip(),'local-owner')
    threading.Thread(target=console,daemon=True).start()
    telemetry = None
    try:
        models = ROOT/'runtime'/'models'
        body.spawn('vision-server',[str(ROOT/'runtime'/'llama'/'llama-server.exe'),'-m',str(models/CFG['vision_model']),
            '--mmproj',str(models/CFG['vision_projector']),'--device','CUDA0','--mmproj-device','CUDA0','-ngl','999','--fit','off',
            '--ctx-size',str(CFG['vision_context']),'--parallel','1','--reasoning','off','--host','127.0.0.1',
            '--port',str(urlparse(CFG['vision_url']).port),'--alias',CFG['vision_alias']])
        for role in DEVICES:
            body.spawn(role)
        telemetry = asyncio.create_task(hardware())
        print(f'Trident alive. Run: {run}\nOwner input: stdin, Telegram text, voice note or call.',flush=True)
        if len(sys.argv)>1:
            publish_owner(' '.join(sys.argv[1:]),'local-owner')
        policy = (ROOT/'instinct.txt').read_text('utf-8-sig'); announced = set()
        while not (run/'stop').exists():
            state = json.loads(state_path.read_text('utf-8-sig')); updates = []
            for role,job in body.jobs.items():
                if job.process.poll() is not None and role not in announced:
                    announced.add(role); body.stop(role); updates.append(dict(device=role,stopped=job.process.returncode))
            for role in state['memory'].pop('restart_devices',[]):
                if role in DEVICES:
                    body.stop(role); body.spawn(role); announced.discard(role)
            for path in sorted((private/'inbox').glob('*.json')):
                message = json.loads(path.read_text('utf-8')); path.unlink(); updates.append(message)
                if message['kind']=='owner':
                    state['owner']=message; state['memory']['goal']=message['text']; write(private/'owner.json',message)
            screen = await body.call('manipulator','capture',{},10)
            if not screen.get('image'):
                updates.append(dict(capture_failure=screen,stale_pixels=True)); screen=state.get('screen',{})
                if not screen.get('image'):
                    log('blocked',reason=screen); await asyncio.sleep(15); continue
            state['screen']=screen; write(state_path,state)
            orientation = run/(token()+'.orientation.json'); write(orientation,dict(status='pending'))
            async def describe():
                result = await body.call('retina','inspect',dict(image=screen['image'],question='Describe visible applications, controls, text and precise spatial relationships without assuming an intended task.'),CFG['look_seconds'])
                write(orientation,result)
            vision = asyncio.create_task(describe())
            context = dict(goal=state['memory']['goal'],narrative=state['memory']['narrative'],owner=state['owner'],updates=updates,
                receivers=[p.name for p in run.iterdir() if p.is_dir()],previous=state.get('previous',[]),previous_proposal=state.get('proposal'),unknown=state['unknown'],screen=screen,orientation=str(orientation),
                tools=str(private/'tools.py'),output_directory=str(ROOT/'runtime'/'output'))
            try:
                answer = await asyncio.to_thread(infer,policy+'\nCONTEXT\n'+json.dumps(context,ensure_ascii=False)+
                    '\nAfter inspecting the PNG, Read the orientation file if useful; pending means the independent description is still running.',screen['image'])
                await vision
                state=json.loads(state_path.read_text('utf-8'))
                state['pixels'][screen['image']]=screen['at']
                proposal=run/(token()+'.answer'); proposal.write_text(answer,'utf-8')
                state['proposal']=dict(text=answer,artifact=str(proposal)); write(state_path,state)
                log('cycle',proposal=str(proposal),summary_chars=len(state['memory']['narrative']),operations=len(state.get('previous',[])))
            except Exception as error:
                log('cycle_failed',error=f'{type(error).__name__}: {error}')
                state = json.loads(state_path.read_text('utf-8-sig')); state['previous'].append(dict(error=str(error),outcome='processing_failed'))
                write(state_path,state)
            finally:
                await vision
    finally:
        if telemetry:
            telemetry.cancel()
        body.close(); state=json.loads(state_path.read_text('utf-8-sig')); state.update(active=False,process_state='stopped')
        write(state_path,state); lock.close()

if __name__=='__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
