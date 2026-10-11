import base64
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from PIL import ImageGrab

HERE = Path(__file__).resolve().parent
PATHS = {
    'here': str(HERE),
    'trident': str(HERE.parent),
    'models': os.path.expandvars(r'%LOCALAPPDATA%\Trident\models'),
}


def fill(parts):
    return [part.format_map(PATHS) for part in parts]


def load(world):
    world['wiring'] = json.loads((HERE / 'wiring.json').read_text(encoding='utf-8'))


def post(url, payload, headers, seconds):
    request = urllib.request.Request(
        url,
        json.dumps(payload).encode(),
        headers | {'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=seconds) as response:
        return json.loads(response.read().decode())


def append(world, line):
    with (HERE / world['wiring']['story']).open('a', encoding='utf-8') as handle:
        handle.write(line.replace('\n', ' ') + '\n')


def read_story(world):
    return (HERE / world['wiring']['story']).read_text(encoding='utf-8')


def grab_screen(world):
    path = HERE / 'frames' / f"{world['n']}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab().save(path)
    return path


def grab_url(world):
    path = HERE / 'frames' / f"{world['n']}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(urllib.request.urlopen(
        world['wiring']['look']['url'],
        timeout=world['wiring']['seconds'],
    ).read())
    return path


CAPTURES = {'screen': grab_screen, 'url': grab_url}


def transcribe(world, path):
    eyes = world['wiring']['eyes']
    body = post(eyes['url'], {
        'model': eyes['alias'],
        'temperature': eyes['temperature'],
        'max_tokens': eyes['max_tokens'],
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()}},
                {'type': 'text', 'text': eyes['prompt']},
            ],
        }],
    }, {}, eyes['seconds'])
    return body['choices'][0]['message']['content']


def ask_mind(world):
    mind = world['wiring']['mind']
    body = post(mind['url'], {
        'model': mind['model'],
        'temperature': mind['temperature'],
        'max_tokens': mind['max_tokens'],
        'messages': [
            {'role': 'system', 'content': world['wiring']['prompts']['mind']},
            {'role': 'user', 'content': read_story(world)},
        ],
    }, {'Authorization': 'Bearer ' + world['env']['OPENROUTER_API_KEY']}, mind['seconds'])
    text = body['choices'][0]['message']['content'].strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[-1].rsplit('```', 1)[0]
    world['beat'] = json.loads(text.strip())


def judge(world, questions):
    jev = world['wiring']['jev']
    body = post(jev['url'], {
        'state': read_story(world),
        'model': jev['model'],
        'questions': questions,
    }, {'Authorization': 'Bearer ' + world['env']['OPENROUTER_API_KEY']}, jev['seconds'])
    for name, answer in body['answers'].items():
        append(world, 'Judged: ' + name + ' ' + json.dumps(answer, ensure_ascii=False))


def look(world):
    world['n'] += 1
    path = CAPTURES[world['wiring']['look']['source']](world)
    append(world, 'Happening: ' + transcribe(world, path))
    return world['wiring']['returns']['look']


def tell(world):
    ask_mind(world)
    append(world, 'Expected: ' + world['beat'].get('expect', ''))
    questions = world['beat'].get('questions') or {}
    if questions:
        judge(world, questions)
        return 'again'
    return world['beat']['signal']


def act(world):
    done = subprocess.run(
        world['beat']['command'],
        shell=True,
        check=True,
        capture_output=True,
        text=True,
    )
    append(world, 'Did: ' + world['beat']['command'])
    append(world, 'Out: ' + done.stdout + done.stderr)
    return world['wiring']['returns']['act']


def say(world):
    line = 'Called: ' + world['beat']['say']
    append(world, line)
    (HERE / 'call.txt').write_text(line + '\n', encoding='utf-8')
    print(line, flush=True)
    return world['wiring']['returns']['say']


def rewire(world):
    for key, value in world['beat']['wiring'].items():
        world['wiring'][key] = value
    (HERE / 'wiring.json').write_text(json.dumps(world['wiring'], indent=2) + '\n', encoding='utf-8')
    append(world, 'Rewired: ' + ' '.join(world['beat']['wiring']))
    return world['wiring']['returns']['rewire']


def rest(world):
    raise SystemExit(world['beat'].get('expect', ''))


NODES = {
    'look': look,
    'tell': tell,
    'act': act,
    'say': say,
    'rewire': rewire,
    'rest': rest,
}


def boot(world):
    world['server'] = subprocess.Popen(fill(world['wiring']['eyes']['command']))


def wheel(world):
    name = world['wiring']['start']
    while True:
        signal = NODES[name](world)
        name = world['wiring']['edges'][name][signal]


def read_env():
    env = {}
    for line in (HERE / '.env').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        env[name.strip()] = value.strip().strip('"').strip("'")
    return env


def main():
    world = {'n': 0, 'beat': {}, 'env': read_env()}
    if not world['env'].get('OPENROUTER_API_KEY'):
        raise SystemExit('Put your OpenRouter key on the OPENROUTER_API_KEY line in version-two/.env')
    load(world)
    boot(world)
    time.sleep(world['wiring']['eyes']['boot_seconds'])
    try:
        wheel(world)
    finally:
        world['server'].kill()


main()
