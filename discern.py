import asyncio, json, os, time, urllib.request
from fabric import CFG, log, serve

def decide(args):
    key = os.environ['OPENROUTER_API_KEY']
    started = time.monotonic()
    payload = dict(model=CFG['decision_model'], state=args['state'], questions=args['questions'])
    req = urllib.request.Request('https://openrouter.ai/api/alpha/decisions', json.dumps(payload).encode(),
                                 {'Authorization':'Bearer '+key, 'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=CFG['seconds']) as response:
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
