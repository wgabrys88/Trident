import json, urllib.request
from pathlib import Path

from base import NORM, SET, Fail, read_file
from desk import png

URL = 'https://openrouter.ai/api/alpha/decisions'


def split_card(text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == '---':
            narrative, questions = '\n'.join(lines[:index]).strip(), [item.strip() for item in lines[index + 1:] if item.strip()]
            if not narrative or not questions:
                raise Fail('the decide file needs a narrative, a line of three dashes, and at least one question')
            return narrative, questions
    raise Fail('the decide file needs a line of three dashes before the questions')


def parse_questions(lines):
    parsed = {}
    for index, line in enumerate(lines):
        kind_at = next((i for i, char in enumerate(line) if char in '?#@'), -1)
        if kind_at < 0:
            raise Fail(f'a question needs ? # or @: {line}')
        name, kind, body = line[:kind_at].strip() or f'q{index + 1}', line[kind_at], line[kind_at + 1:].strip()
        ask, *options = [part.strip() for part in body.split('|')]
        if not ask or (kind == '#' and len(options) < 2) or (kind == '?' and len(options) == 1) or (kind == '@' and options):
            raise Fail(f'this question does not match its form: {line}')
        if kind == '@':
            parsed[name] = dict(type='box', instructions=ask)
        elif kind == '#':
            parsed[name] = dict(type='score', instructions=ask, criteria=options)
        elif options:
            criteria = dict(item.split('=', 1) if '=' in item else (f'opt{n + 1}', item) for n, item in enumerate(options))
            if kind == '?' and set(map(str.lower, criteria)) == {'true', 'false'}:
                parsed[name] = dict(type='noul', instructions=ask, criteria={key.lower(): value for key, value in criteria.items()})
            else:
                parsed[name] = dict(type='choice', instructions=ask, criteria=criteria)
        else:
            parsed[name] = dict(type='noul', instructions=ask)
    return parsed


def format_noul(name, probability):
    probability = float(probability)
    line = f'{name}: P(yes)={probability:.2f}'
    if probability < 0.5:
        line += ' no'
    return line


def summarize(name, answer):
    kind = answer.get('type')
    if kind == 'noul':
        return format_noul(name, answer.get('p', answer.get('noul', 0)))
    if kind == 'choice':
        probs = ', '.join(f'{key} {float(value):.2f}' for key, value in (answer.get('probabilities') or {}).items())
        return f"{name}: {answer.get('choice')} ({probs})"
    if kind == 'box':
        left, top, right, bottom = answer['norm']
        padded = ' padded' if answer.get('padded') else ''
        return f'{name}: box {left} {top} {right} {bottom}{padded}'
    return f"{name}: score {float(answer.get('score', 0)):.2f}"


def pad(image):
    pixels, width, height = image
    wide, tall = max(width, math_ceil(height / 2)), max(height, math_ceil(width / 2))
    pad_row = bytes((wide - width) * 4)
    stride = width * 4
    canvas = b''.join(pixels[y * stride:(y + 1) * stride] + pad_row for y in range(height)) + bytes(wide * 4 * (tall - height))
    return (canvas, wide, tall), wide / width, tall / height


def math_ceil(value):
    import math
    return math.ceil(value)


def norm_box(box, scale_x, scale_y):
    def axis(value, scale):
        return max(0, min(NORM, round(min(1, float(value) * scale) * NORM)))
    return [axis(box['left'], scale_x), axis(box['top'], scale_y), axis(box['right'], scale_x), axis(box['bottom'], scale_y)]


class Decide:
    name, flag = 'decide', 'decide_file'
    doc = '''$CALL --decide-file PATH
Reads a narrative, a line of three dashes, then one question on each line.
name?claim is a probability. name?text|a=One|b=Two is a choice. name#text|low|high is a score. name@target is a box.
A probability is printed as P(yes)= and then yes or no. P(yes) is the probability the claim is true, not a yes by itself.
Only a box sends the capture. For desktop decisions, use Fara's written observations in the narrative and do not request a box or attach a screenshot to this model. Desktop perception belongs only to Trident's Fara eye. A grade should be text, with the evidence written in the narrative.
A choice contains only the options in the file.'''

    def run(self, app, path, dry=False):
        narrative, lines = split_card(read_file(path))
        questions = parse_questions(lines)
        if dry:
            answers = {name: dict(type='noul', p=0.04) if item['type'] == 'noul' else dict(item) for name, item in questions.items()}
            return dict(dry=True, questions=questions, summary='\n'.join(summarize(name, answer) for name, answer in answers.items()))
        return self.ask(app, narrative, questions)

    def ask(self, app, narrative, questions):
        boxed = any(item['type'] == 'box' for item in questions.values())
        state = ['STORY: ' + (app.work / 'story.txt').read_text(encoding='utf-8') if (app.work / 'story.txt').exists() else '', 'LEDGER: ' + (app.work / 'ledger.md').read_text(encoding='utf-8') if (app.work / 'ledger.md').exists() else '', 'WHY: ' + narrative, 'GOAL: ' + (app.work / 'goal.txt').read_text(encoding='utf-8') if (app.work / 'goal.txt').exists() else '']
        scale_x = scale_y = 1
        if boxed:
            screen = json.loads((app.work / 'screen.json').read_text(encoding='utf-8-sig'))
            if screen.get('blank'):
                return dict(blank=True, capture=screen.get('id'))
            image, scale_x, scale_y = pad(unpng_screen(screen))
            state.append(dict(type='image_url', image_url=dict(url='data:image/png;base64,' + __import__('base64').b64encode(png(image)).decode())))
        token = (Path(__file__).resolve().parents[1] / '.env').read_text(encoding='utf-8-sig').strip().strip('"\'')
        request = urllib.request.Request(URL, json.dumps(dict(model=SET['decide_model'], state=state, questions=questions)).encode(), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.load(response)
        answers = {}
        for name, answer in (body.get('answers') or {}).items():
            if answer.get('type') == 'noul':
                answers[name] = dict(type='noul', p=answer.get('noul'))
            elif answer.get('type') == 'box':
                answers[name] = dict(type='box', norm=norm_box(answer['box'], scale_x, scale_y), padded=scale_x != 1 or scale_y != 1, confidence=answer.get('confidence'))
            else:
                answers[name] = answer
        return dict(answers=answers, summary='\n'.join(summarize(name, answer) for name, answer in answers.items()), usage=body.get('usage') or {})


def unpng_screen(screen):
    from desk import unpng
    return unpng(Path(screen['image']).read_bytes())


decide = Decide()
