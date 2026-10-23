"""Pilot: Fara 1.5 is the brain and drives the desktop with Trident's eye and hand.

The loop follows Microsoft's harness, github.com/microsoft/fara src/fara/agents/fara/fara15_agent.py
(Fara15Agent.run / _generate_model_call / maybe_remove_old_screenshots / _dispatch_action, commit a675d6d):
- every call is [system] + history; the system prompt is FARA_SYSTEM (Microsoft's, 1000x1000 coordinates);
- the first user turn is [screenshot, task]; each later user turn is [screenshot, NEXT]
  (or [screenshot, observation + NEXT] after an action that produced text, or [screenshot, reply] after a user answer);
- the assistant turn is Fara's raw reply (thought + <tool_call> text), fed back verbatim;
- only the newest MAX_IMAGES screenshots are sent: older screenshot turns are dropped whole, except the
  original task turn, which keeps its text;
- one action per step, coordinates 0-1000 of the capture; left_click_drag drags from the current cursor;
- terminate ends the run, ask_user_question stops it (or continues with Microsoft's canned eval reply when
  TRIDENT_PILOT_AUTOREPLY=1), pause_and_memorize_fact only stores the fact, wait sleeps.
Desktop differences: there is no URL prefix; visit_url and web_search open the default browser;
history_back is alt+left; read_page_answer_question has no page text here, so Fara is told so.
Screenshots carry no cursor, like the harness's Playwright screenshots, so none is drawn.
"""
import json, os, re, time
from pathlib import Path
from urllib.parse import quote_plus

NEXT = 'Here is the next screenshot. Think about what to do next.'
AUTO_REPLY = "keep going so far as you don't make up information, you have my approval"
MAX_IMAGES, STEPS, WAIT_CAP = 3, 30, 30.0
POINTER = ('left_click', 'right_click', 'double_click', 'triple_click', 'mouse_move', 'left_click_drag')
NO_PAGE = 'This is a Windows desktop, not a web page: {action} cannot read page text here. Look at the screenshot and use the mouse and keyboard.'


def user_turn(url, text, original=False, keep=None):
    msg = dict(role='user', content=[dict(type='image_url', image_url=dict(url=url)), dict(type='text', text=text)])
    if original: msg['_keep'] = text
    elif keep is not None: msg['_keep'] = keep
    return msg


def has_image(msg):
    return isinstance(msg.get('content'), list) and any(isinstance(c, dict) and c.get('type') == 'image_url' for c in msg['content'])


def prune(history, keep=MAX_IMAGES):
    """Microsoft's maybe_remove_old_screenshots: keep the newest `keep` screenshot turns; an older one is
    dropped whole unless it is the task (or a user reply), which keeps only its text."""
    out, seen = [], 0
    for msg in reversed(history):
        if has_image(msg):
            if seen < keep: out.append(msg)
            elif '_keep' in msg: out.append(dict(role=msg['role'], content=[dict(type='text', text=msg['_keep'])], _keep=msg['_keep']))
            seen += 1
        else: out.append(msg)
    return out[::-1]


def messages(system, history, keep=MAX_IMAGES):
    return [dict(role='system', content=system)] + [{k: v for k, v in m.items() if not k.startswith('_')} for m in prune(history, keep)]


def key_combo(main, keys):
    if isinstance(keys, str): keys = [keys]
    names = [part for k in keys or [] for part in re.split(r'\s*\+\s*', str(k).strip()) if part]
    return main.fara_keys(names)[len('key '):]


def scroll_amount(pixels, span):
    """Fara scrolls by 0-1000 display units (positive up); Windows wheels by 120 per notch, positive up."""
    pixels = float(pixels or 0)
    if not pixels or not span: return 0
    notches = int(round(pixels * span / 1000 / 120.0)) or (1 if pixels > 0 else -1)
    return notches * 120


def type_text(desktop, text):
    if not text.isascii() or any(ord(c) < 32 and c not in '\n\t' for c in text): desktop.paste(text); return
    for i, line in enumerate(text.split('\n')):
        if i: desktop.key('enter')
        if line: desktop.type_text(line)


def act(engine, main, args, size, facts):
    """Run one Fara action with Trident's hand. Returns (stop, description, observation for the next turn)."""
    kind, d = args.get('action'), engine.desktop
    point = main.fara_point(args)
    if kind in POINTER and point is None: raise ValueError(f'{kind} needs a coordinate')
    if kind == 'left_click': d.click(*point); return False, f'I clicked at coordinates {point}.', ''
    if kind == 'right_click': d.rightClick(*point); return False, f'I right-clicked at coordinates {point}.', ''
    if kind == 'double_click': d.doubleClick(*point); return False, f'I double-clicked at coordinates {point}.', ''
    if kind == 'triple_click': d.click(*point, clicks=3); return False, f'I triple-clicked at coordinates {point}.', ''
    if kind == 'mouse_move': d.moveTo(*point); return False, f'I moved the cursor to {point}.', ''
    if kind == 'left_click_drag':
        start = d.position(); d.drag([start, point]); return False, f'I dragged from {tuple(start)} to {point}.', ''
    if kind == 'key':
        combo = key_combo(main, args.get('keys')); d.key(combo); return False, f'I pressed the following keys: {combo}', ''
    if kind == 'type':
        value = str(args.get('text', '')); type_text(d, value); return False, f"I typed '{value}'.", ''
    if kind in ('scroll', 'hscroll'):
        amount = scroll_amount(args.get('pixels'), (size[0] if kind == 'hscroll' else size[1]) if size else 0)
        at = point or d.position()
        if amount: d.wheel(amount, *at, horizontal=kind == 'hscroll')
        return False, f'I scrolled {"horizontally " if kind == "hscroll" else ""}by {amount} wheel units at {tuple(at)}.', ''
    if kind == 'wait':
        t = min(WAIT_CAP, max(0.0, float(args.get('time', args.get('duration', 3.0)) or 0))); time.sleep(t); return False, f'I waited {t:g}s.', ''
    if kind in ('visit_url', 'web_search'):
        raw = str(args.get('url' if kind == 'visit_url' else 'query') or '')
        url = raw if kind == 'visit_url' and raw.startswith(('https://', 'http://')) else ('https://' + raw if kind == 'visit_url' and ' ' not in raw else 'https://www.bing.com/search?q=' + quote_plus(raw) + '&FORM=QBLH')
        os.startfile(url); return False, f'I opened {url} in the default browser.', ''
    if kind == 'history_back': d.key('alt+left'); return False, 'I pressed alt+left (back).', ''
    if kind == 'read_page_answer_question':
        note = NO_PAGE.format(action=kind); return False, note, note
    if kind == 'pause_and_memorize_fact':
        fact = str(args.get('fact', '')); facts.append(fact); return False, f'I memorized the following fact: {fact}', ''
    if kind == 'ask_user_question': return True, f"I asked the user: {args.get('question', '')}", ''
    if kind == 'terminate': return True, str(args.get('answer', '')), ''
    raise ValueError(f'unknown action {kind!r}')


def step_md(n, obs, sent, thoughts, args, point, px, result, info):
    shown = {k: v for k, v in args.items() if k not in ('action', 'thoughts')}
    rows = [f'### step {n}', f"- screenshot: {obs['image']} (sent {sent[0]}x{sent[1]})", f'- thought: {thoughts or "-"}',
            f"- action: {args.get('action')} {json.dumps(shown, ensure_ascii=False)}"]
    if point: rows.append(f'- coordinate: {list(point)} of 0-1000 -> screen px {list(px)}')
    rows += [f'- result: {result}', f"- fara: {info}"]
    return '\n'.join(rows)


def run(engine, main, task, steps=STEPS):
    """Fara drives until terminate, ask_user_question, an error, the stop file or `steps` actions."""
    task, steps = str(task).strip(), int(steps or STEPS)
    if not task: raise ValueError('pilot needs a task')
    auto = os.environ.get('TRIDENT_PILOT_AUTOREPLY') == '1'
    region = (engine.work / 'region.txt').read_text('utf-8').strip()
    trace_path = engine.work / 'shots' / f'pilot_{main.WAKE_ID}.json'
    trace = dict(task=task, steps=steps, region=region, model=main.VISION_MODEL, url=engine.vision.url, max_images=MAX_IMAGES, records=[])
    main.log('pilot', dict(text=f'PILOT\n\nTASK: {task}\n\nFara drives (Microsoft Fara15Agent loop), at most {steps} actions, region {region}, '
                                f'{MAX_IMAGES} newest screenshots per call, pause {main.SCREENSHOT_DELAY}s after each action. Trace: {trace_path}'))
    history, facts, pending, status, answer = [], [], '', 'max steps', ''
    obs = engine.capture()
    for n in range(1, steps + 1):
        if engine.sentinel.exists(): status = 'stop file'; break
        with main.Image.open(obs['image']) as im: image = main.fara_fit(im.convert('RGB'))
        url = main.b64(image)
        if n == 1: history.append(user_turn(url, task, original=True))
        elif pending.startswith('USER:'): history.append(user_turn(url, pending[5:], keep=pending[5:]))
        else: history.append(user_turn(url, f'{pending}\n{NEXT}' if pending else NEXT))
        pending = ''
        payload = messages(main.FARA_SYSTEM, history)
        t0 = time.time()
        try: raw, body = engine.vision.chat(payload, dict(step=n, image=obs['image'], sent_size=list(image.size), images=sum(map(has_image, payload)), text=payload[-1]['content'][-1]['text']))
        except Exception as e: status = f'fara error: {e!r}'; main.log('pilot', dict(text=f'### step {n}\n- fara error: {e!r}')); break
        history.append(dict(role='assistant', content=raw))
        tm = (body or {}).get('timings') or {}
        info = f"{time.time() - t0:.1f}s, prompt {tm.get('prompt_n', '?')} tok (cached {tm.get('cache_n', '?')}), reply {tm.get('predicted_n', '?')} tok"
        record = dict(step=n, screenshot=obs['image'], sent_size=list(image.size), reply=raw, seconds=round(time.time() - t0, 1), timings=tm)
        try:
            thoughts, call = main.fara_call(raw); args = dict(call['arguments'])
            if args.get('action') not in main.FARA_ACTIONS: raise ValueError(f"unknown action {args.get('action')!r}")
            point = main.fara_point(args)
            px = main.HAND()._resolve_screen_pos(point[0], point[1], region) if point else None
            stop, result, pending = act(engine, main, args, obs.get('size'), facts)
        except Exception as e:
            record.update(error=repr(e)); trace['records'].append(record)
            main.log('pilot', dict(text=f'### step {n}\n- reply: {raw}\n- invalid: {e!r}')); status = f'invalid action: {e}'; break
        record.update(thoughts=thoughts, action=args.get('action'), arguments=args, coordinate=list(point) if point else None, screen_px=list(px) if px else None, result=result)
        trace['records'].append(record)
        main.log('pilot', dict(text=step_md(n, obs, image.size, thoughts, args, point, px, result, info)))
        Path(trace_path).parent.mkdir(exist_ok=True); trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=1), 'utf-8')
        if args.get('action') == 'ask_user_question' and auto:
            pending, stop = 'USER:' + AUTO_REPLY, False
        if stop:
            status, answer = args.get('action'), result; break
        time.sleep(main.SCREENSHOT_DELAY)
        obs = engine.capture()
    final = engine.capture()
    trace.update(status=status, answer=answer, facts=facts, final=final['image'],
                 last_messages=[dict(role=m['role'], content=[c if c.get('type') != 'image_url' else dict(type='image_url', image_url='<screenshot>') for c in m['content']] if isinstance(m['content'], list) else m['content']) for m in messages(main.FARA_SYSTEM, history)[1:]])
    Path(trace_path).parent.mkdir(exist_ok=True); trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=1), 'utf-8')
    main.log('pilot', dict(text=f'PILOT END\n\n- status: {status}\n- actions: {len(trace["records"])}\n- answer: {answer or "-"}\n- facts: {facts}\n- final screenshot: {final["image"]}'))
    return dict(task=task, status=status, actions=len(trace['records']), answer=answer, facts=facts, final=final['image'], trace=str(trace_path), journal=str(main.RUN))
