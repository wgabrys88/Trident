import ast, asyncio, inspect, json, os, sys, time
from pathlib import Path
from fabric import CFG, ROOT, RUN, NotAttempted, request, token, write, file_boundary

PRIVATE = ROOT/'private'
state = json.loads((PRIVATE/'continuity.json').read_text('utf-8-sig'))
memory, owner, screen = state['memory'], state['owner'], state['screen']
previous = state.get('previous',[])
records, targets = [], {}

def save():
    state.update(memory=memory, screen=screen, previous=records)
    write(PRIVATE/'continuity.json',state)

def interrupted():
    return (RUN/'stop').exists() or any(json.loads(p.read_text('utf-8'))['kind']=='owner' for p in (PRIVATE/'inbox').glob('*.json'))

async def device(role, operation, arguments, seconds=30):
    if interrupted():
        raise InterruptedError('New owner input or stop; previous effects are retained')
    ident = token(); artifact = PRIVATE/'outcomes'/(ident+'.json')
    entry = dict(id=ident,at=time.time(),role=role,operation=operation,arguments=arguments,owner_id=owner['id'])
    if role=='manipulator' and operation!='capture' or role=='relay' and operation=='dial':
        if any(v['role']==role for v in state['unknown'].values()):
            raise NotAttempted('Resolve the prior unknown effect before repeating this device side effect')
        state['unknown'][ident] = dict(role=role,artifact=str(artifact),at=entry['at'])
        save()
    write(artifact,entry|dict(outcome='unknown',phase='attempted'))
    result = await request(role,operation,arguments,seconds)
    entry.update(result=result,artifact=str(artifact)); write(artifact,entry)
    if result.get('outcome')!='unknown':
        state['unknown'].pop(ident,None)
    records.append(entry)
    save()
    return result | dict(id=ident,artifact=str(artifact))

async def native(source, prediction, window=None, targets=None):
    global screen
    if not prediction.strip():
        raise ValueError('Predict the effect before native input')
    result = await device('manipulator','execute',dict(source=source,prediction=prediction,window=window,targets=targets or {}),30)
    if result.get('image'):
        screen = result
    globals()['targets'].clear(); save()
    return result

async def observe(question, region=None, mode='inspect'):
    result = await device('retina','inspect',dict(image=screen['image'],question=question,mode=mode,
                            **(dict(region=region) if region else {})),CFG['look_seconds'])
    return result | dict(desktop=screen['desktop'],window=screen['window'],at=screen['at'])

def accept(observation, box):
    image = observation['image']
    if image not in state['pixels'] or not (len(box)==4 and 0<=box[0]<box[2]<=1000 and 0<=box[1]<box[3]<=1000) or time.time()-observation['at']>CFG['target_seconds']:
        raise NotAttempted('Target requires an exact box and current native pixel Read')
    ident = observation['id']
    targets[ident] = {k:observation[k] for k in ('at','desktop','window','image')} | dict(box=box)
    return targets[ident]

async def ask(context, questions):
    return await device('discern','decide',dict(state=context,questions=questions),CFG['decision_seconds'])

async def speak(text, identity=None):
    identity = identity or token(); path = PRIVATE/'voice'/(identity+'.json')
    if path.exists():
        return json.loads(path.read_text('utf-8'))
    write(path,dict(text=text,outcome='unknown',delivered=False,hearing_verified=False))
    result = await device('phonation','speak',dict(text=text,narration_id=identity),55)
    write(path,result)
    return result

async def resolve(ident, evidence, interpretation):
    path = PRIVATE/'outcomes'/(ident+'.json'); entry = json.loads(path.read_text('utf-8'))
    if ident in state['unknown'] and entry['role']=='manipulator':
        if evidence not in state['pixels'] or state['pixels'][evidence]<entry['at']:
            raise NotAttempted('Resolve unknown input with freshly delivered pixels')
    if ident in state['unknown'] and entry['role']=='relay':
        receipt = json.loads((PRIVATE/'outcomes'/(evidence+'.json')).read_text('utf-8'))
        if receipt['at']<entry['at'] or receipt['operation']!='status' or receipt['result'].get('outcome')!='completed':
            raise NotAttempted('Unknown call needs an actual subsequent status receipt')
    entry['correction'] = dict(at=time.time(),evidence=evidence,interpretation=interpretation)
    write(path,entry); state['unknown'].pop(ident,None); save()
    return entry

async def recall(ident):
    return json.loads((PRIVATE/'outcomes'/(ident+'.json')).read_text('utf-8'))

async def rest(seconds=60):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline and not interrupted():
        await asyncio.sleep(.1)

async def main():
    sys.addaudithook(file_boundary)
    tools = PRIVATE/'tools.py'
    namespace = globals()
    if tools.exists():
        exec(compile(tools.read_text('utf-8'),str(tools),'exec'),namespace)
    source = Path(sys.argv[1]).read_text('utf-8-sig')
    tree = ast.parse(source)
    definitions = [node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom))]
    try:
        compiled = compile(source,str(sys.argv[1]),'exec',flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        result = eval(compiled,namespace)
        if inspect.isawaitable(result):
            await result
        if definitions:
            existing = tools.read_text('utf-8') if tools.exists() else ''
            old = ast.parse(existing).body
            replaced = {node.name for node in definitions if hasattr(node,'name')}
            merged = [node for node in old if getattr(node,'name',None) not in replaced]+definitions
            tools.write_text(ast.unparse(ast.Module(body=merged,type_ignores=[]))+'\n','utf-8')
    finally:
        save()

if __name__=='__main__':
    asyncio.run(main())
