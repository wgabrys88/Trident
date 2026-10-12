import asyncio, base64, io, json, subprocess, time, urllib.request
from PIL import Image
from fabric import CFG, ROOT, RUN, log, object_in, serve, token, write

def inspect(args):
    started = time.monotonic()
    source = Path(args['image'])
    with Image.open(source) as pixels:
        region = args.get('region', [0, 0, 1000, 1000])
        x0, y0, x1, y1 = region
        width, height = pixels.size
        crop = pixels.crop((round(x0*width/1000), round(y0*height/1000),
                           round(x1*width/1000), round(y1*height/1000)))
        submitted = RUN / (token() + '-look.png')
        crop.thumbnail((CFG['image_side'], CFG['image_side']))
        crop.save(submitted)
    system = ('Read pixels literally. Answer the single question with JSON only: '
              '{"observed":"specific visible shapes or exact text, never just Yes", '
              '"contradiction":"visible conflicting evidence, or empty", '
              '"verdict":"supported|refuted|uncertain", "box":[xmin,ymin,xmax,ymax] or null}. '
              'Box locates the requested target within this image, normalized X,Y 0..1000. '
              'Do not infer hidden results, file contents, submission or delivery. '
              'If unreadable or absent say uncertain or refuted. An empty dialog proves no attachment.')
    content = [dict(type='text', text=args['question']), dict(type='image_url',
               image_url=dict(url='data:image/png;base64,' + base64.b64encode(submitted.read_bytes()).decode()))]
    body = dict(model='retina', temperature=0, max_tokens=192,
                messages=[dict(role='system', content=system), dict(role='user', content=content)])
    req = urllib.request.Request(CFG['vision_url'] + '/v1/chat/completions', json.dumps(body).encode(), {'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=CFG['look_seconds']) as response:
        choice = json.load(response)['choices'][0]
    if choice['finish_reason'] != 'stop':
        raise ValueError('Incomplete look')
    answer = object_in(choice['message']['content'])
    box = answer.get('box')
    if box is not None:
        a, b, c, d = box
        if not 0 <= a < c <= 1000 or not 0 <= b < d <= 1000:
            raise ValueError('Invalid located box')
        answer['box'] = [x0+a*(x1-x0)/1000, y0+b*(y1-y0)/1000,
                         x0+c*(x1-x0)/1000, y0+d*(y1-y0)/1000]
    answer.update(image=str(source), submitted=str(submitted), question=args['question'],
                  at=time.time(), seconds=round(time.monotonic()-started, 3), verified=False)
    log('look', **answer)
    return answer

async def handle(op, args):
    return await asyncio.to_thread(inspect, args)

if __name__ == '__main__':
    from pathlib import Path
    asyncio.run(serve(handle, 'retina'))
