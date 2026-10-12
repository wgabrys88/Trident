import asyncio, base64, json, time, urllib.error, urllib.request
from pathlib import Path
from PIL import Image
from fabric import CFG, RUN, box_valid, log, object_in, serve, token

def get(endpoint):
    with urllib.request.urlopen(CFG['vision_url']+endpoint, timeout=1) as response:
        return json.load(response)

async def ready():
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        try:
            health = await asyncio.to_thread(get, '/health')
            models = await asyncio.to_thread(get, '/v1/models')
            model = next((m for m in models['data'] if m.get('id') == 'retina'), {})
            if health.get('status') != 'ok' or 'image' not in model.get('architecture', {}).get('input_modalities', []):
                raise ValueError('Expected healthy retina model with image input')
            log('vision_ready', model='retina', image_capable=True)
            return
        except (urllib.error.URLError, TimeoutError):
            await asyncio.sleep(.2)
    raise TimeoutError('Quantized vision server did not become ready within 30 seconds')

def inspect(args):
    started, mode = time.monotonic(), args.get('mode', 'inspect')
    source = Path(args['image'])
    region = args.get('region', [0, 0, 1000, 1000])
    if mode not in ('inspect', 'locate') or not box_valid(region):
        raise ValueError('Invalid visual mode or crop')
    x0, y0, x1, y1 = region
    with Image.open(source) as pixels:
        width, height = pixels.size
        rectangle = [round(x0*width/1000), round(y0*height/1000), round(x1*width/1000), round(y1*height/1000)]
        crop = pixels.crop(rectangle)
        if not crop.width or not crop.height:
            raise ValueError('Crop contains no readable pixels')
        # Only orientation/locating may use a thumbnail. Judgments receive native pixels.
        if mode == 'locate' and region == [0, 0, 1000, 1000]:
            crop.thumbnail((CFG['image_side'], CFG['image_side']))
        submitted = RUN / (token()+'-look.png')
        crop.save(submitted)
        dimensions = list(crop.size)
    system = ('Read actual pixels literally. Answer the single question with JSON only: '
              '{"observed":"specific visible shapes or exact text, never just Yes", '
              '"contradiction":"visible conflicting evidence, or empty", '
              '"verdict":"supported|refuted|uncertain", "box":[xmin,ymin,xmax,ymax] or null}. '
              'Do not infer hidden results, saved contents, submission, or delivery. '
              'Unreadable details require uncertain. Missing requested effects require refuted. '
              + ('Locate the requested target with a normalized X,Y 0..1000 box; Y increases downward.'
                 if mode == 'locate' else 'Inspect the exact expected change. Return box=null.'))
    data = submitted.read_bytes()
    content = [dict(type='text', text=args['question']), dict(type='image_url',
               image_url=dict(url='data:image/png;base64,'+base64.b64encode(data).decode()))]
    body = dict(model='retina', temperature=0, max_tokens=350,
                messages=[dict(role='system', content=system), dict(role='user', content=content)])
    log('pixels_submitted', action_id=args.get('action_id'), image=str(source), submitted=str(submitted),
        question=args['question'], mode=mode, source_size=[width, height], crop=rectangle,
        submitted_size=dimensions, bytes=len(data))
    req = urllib.request.Request(CFG['vision_url']+'/v1/chat/completions', json.dumps(body).encode(), {'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=max(.1, CFG['look_seconds']-(time.monotonic()-started))) as response:
        response = json.load(response)
    choice = response['choices'][0]
    raw = choice['message']['content']
    log('vision_answer', action_id=args.get('action_id'), image=str(source), submitted=str(submitted),
        raw=raw, finish=choice['finish_reason'], usage=response.get('usage'))
    if choice['finish_reason'] != 'stop':
        raise ValueError('Incomplete visual answer; no proof')
    answer = object_in(raw)
    if (set(answer) != {'observed', 'contradiction', 'verdict', 'box'} or
            not isinstance(answer['observed'], str) or answer['contradiction'] is not None and not isinstance(answer['contradiction'], str) or
            answer['verdict'] not in ('supported', 'refuted', 'uncertain')):
        raise ValueError('Malformed visual judgment')
    answer['contradiction'] = answer['contradiction'] or ''
    box = answer['box']
    if mode == 'inspect' and box is not None or box is not None and not box_valid(box):
        raise ValueError('Invalid located box')
    if box is not None:
        a, b, c, d = box
        answer['box'] = [x0+a*(x1-x0)/1000, y0+b*(y1-y0)/1000, x0+c*(x1-x0)/1000, y0+d*(y1-y0)/1000]
    answer.update(image=str(source), submitted=str(submitted), question=args['question'], raw=raw,
                  mode=mode, action_id=args.get('action_id'), pixels_delivered=True, at=time.time(),
                  seconds=round(time.monotonic()-started, 3), verified=False)
    log('look', **answer)
    return answer

async def main():
    await ready()
    async def handle(op, args):
        return await asyncio.to_thread(inspect, args)
    await serve(handle, 'retina')

if __name__ == '__main__':
    asyncio.run(main())
