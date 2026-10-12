import asyncio, base64, json, time, urllib.error, urllib.request
from pathlib import Path
from PIL import Image
from fabric import CFG, RUN, box_valid, log, object_in, serve, token

def get(endpoint):
    with urllib.request.urlopen(CFG['vision_url']+endpoint, timeout=1) as response:
        return json.load(response)

async def ready():
    deadline = time.monotonic()+CFG['seconds']
    while time.monotonic() < deadline:
        try:
            health = await asyncio.to_thread(get, '/health')
            models = await asyncio.to_thread(get, '/v1/models')
            model = next((item for item in models['data'] if item.get('id') == CFG['vision_alias']), {})
            if health.get('status') != 'ok' or 'image' not in model.get('architecture', {}).get('input_modalities', []):
                raise ValueError('Vision server is up without an image model')
            log('vision_ready', model=CFG['vision_alias'])
            return
        except (urllib.error.URLError, TimeoutError, ValueError):
            await asyncio.sleep(.2)
    raise TimeoutError('Vision server did not become ready')

def inspect(args):
    started = time.monotonic()
    source = Path(args['image'])
    region = args.get('region', [0, 0, 1000, 1000])
    if not box_valid(region):
        raise ValueError('Invalid crop')
    x0, y0, x1, y1 = region
    with Image.open(source) as pixels:
        width, height = pixels.size
        rectangle = [round(x0*width/1000), round(y0*height/1000), round(x1*width/1000), round(y1*height/1000)]
        crop = pixels.crop(rectangle)
        if not crop.width or not crop.height:
            raise ValueError('Crop contains no pixels')
        submitted = RUN / (token()+'-look.png')
        crop.save(submitted)
    content = [dict(type='image_url', image_url=dict(url='data:image/png;base64,'+base64.b64encode(submitted.read_bytes()).decode())),
               dict(type='text', text=args['user'])]
    body = dict(model=CFG['vision_alias'], temperature=0.1, min_p=0.15, repeat_penalty=1.05, max_tokens=16384,
                messages=[dict(role='system', content=args['system']), dict(role='user', content=content)])
    req = urllib.request.Request(CFG['vision_url']+'/v1/chat/completions', json.dumps(body).encode(),
                                 {'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=CFG['seconds']) as response:
        response = json.load(response)
    choice = response['choices'][0]
    raw = choice['message'].get('content') or ''
    log('vision_answer', raw=raw, finish=choice.get('finish_reason'), usage=response.get('usage'),
        source_size=[width, height], crop=rectangle)
    parsed = object_in(raw)
    observed = parsed.get('observed') if isinstance(parsed.get('observed'), str) and parsed['observed'].strip() else raw
    box = parsed.get('box')
    if box is not None and box_valid(box):
        a, b, c, d = box
        box = [x0+a*(x1-x0)/1000, y0+b*(y1-y0)/1000, x0+c*(x1-x0)/1000, y0+d*(y1-y0)/1000]
    else:
        box = None
    return dict(observed=observed, box=box, reply=parsed, raw=raw, image=str(source), submitted=str(submitted),
                finish=choice.get('finish_reason'), seconds=round(time.monotonic()-started, 3))

async def main():
    await ready()
    async def handle(op, args):
        return await asyncio.to_thread(inspect, args)
    await serve(handle, 'retina')

if __name__ == '__main__':
    asyncio.run(main())
