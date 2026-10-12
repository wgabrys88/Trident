import asyncio, json, os, re, time, urllib.request
from fabric import CFG, ROOT, log, serve

def decide(args):
    key = os.environ.get('OPENROUTER_API_KEY') or re.search(
        r'\bsk-or-v1-[A-Za-z0-9_-]+', (ROOT.parent/'.env').read_text('utf-8-sig')).group()
    started = time.monotonic()
    payload = dict(model=CFG['decision_model'], state=args['state'], questions=args['questions'])
    req = urllib.request.Request('https://openrouter.ai/api/alpha/decisions', json.dumps(payload).encode(),
                                 {'Authorization':'Bearer '+key, 'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.load(response)
    if set(result.get('answers',{})) != set(args['questions']):
        raise ValueError('Jev did not answer the requested questions')
    result['seconds'] = round(time.monotonic()-started,3)
    log('decisions', questions=args['questions'], state=args['state'], result=result)
    return result

async def handle(op,args):
    return await asyncio.to_thread(decide,args)

if __name__=='__main__':
    asyncio.run(serve(handle,'discern'))
