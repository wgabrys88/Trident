"""Measured events are evidence; narrative lessons remain claims."""
import json, re
from pathlib import Path

HEAD = re.compile(r'(?m)^## (\d\d:\d\d:\d\d) (\S+)(?: (\S+))?$')
COMPONENTS = {'eye', 'decide', 'hands', 'capture', 'windows', 'region', 'place', 'story', 'ledger', 'stop', 'measure'}


def objects(text):
    """Decode concatenated JSON objects without merging their causal identity."""
    decoder, out, pos = json.JSONDecoder(), [], 0
    text = text.strip()
    while pos < len(text):
        try:
            value, end = decoder.raw_decode(text, pos)
        except ValueError:
            break
        out.append(value)
        pos = end
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


def measured(path):
    path = Path(path)
    structured = path.with_suffix('.jsonl')
    components, harness, captures, actions, errors = {}, 0, [], [], 0
    first = last = None
    first_action = None
    if structured.exists():
        from state import locked
        with locked(path.with_suffix('.lock')):
            rows = [json.loads(line) for line in structured.read_text(encoding='utf-8').splitlines() if line.strip()]
        for row in rows:
            errors += row['kind'] in ('component_error', 'wake_error', 'stream_error', 'action_failed')
            stamp = row['at']
            first = stamp if first is None else min(first, stamp)
            last = stamp if last is None else max(last, stamp)
            if row['kind'] == 'capture_saved' and isinstance(row.get('payload'), dict) and row['payload'].get('id'):
                captures.append(str(row['payload']['id']))
            if row['kind'] == 'component':
                name, payload = row['component'], row.get('payload') or {}
                components[name] = components.get(name, 0) + 1
                if first_action is None and name in COMPONENTS:
                    import time
                    first_action = time.strftime('%H:%M:%S', time.localtime(stamp))
                if name in ('capture', 'hands') and payload.get('id'):
                    captures.append(str(payload['id']))
                if payload.get('action_id'):
                    actions.append(payload['action_id'])
            if row['kind'] == 'agent' and isinstance(row.get('payload'), dict):
                e = row['payload']
                harness += e.get('type') == 'tool_call' and e.get('subtype') == 'completed'
        source = str(structured)
    else:
        text = path.read_text(encoding='utf-8', errors='replace') if path.exists() else ''
        heads = list(HEAD.finditer(text))
        first, last = (heads[0][1], heads[-1][1]) if heads else (None, None)
        for i, head in enumerate(heads):
            stamp, kind, _ = head.groups()
            if kind == 'request':
                harness += 1
            if kind in COMPONENTS:
                components[kind] = components.get(kind, 0) + 1
                first_action = first_action or stamp
                body = text[head.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
                for obj in objects(body):
                    if isinstance(obj, dict) and kind in ('hands', 'capture') and obj.get('id'):
                        captures.append(str(obj['id']))
        source = str(path)
    return dict(run=path.stem.removeprefix('run_'), source=source, first_event=first, last_event=last,
                first_component=first_action, component_actions=components, capture_ids=list(dict.fromkeys(captures)),
                action_ids=actions, harness_calls=harness, recorded_errors=errors if structured.exists() else None,
                harness_count_basis='completed' if structured.exists() else 'requests; completions unavailable')


def context(work, run, ledger):
    work = Path(work)
    paths = sorted(set(work.glob('run_*.md')) | {path.with_suffix('.md') for path in work.glob('run_*.jsonl')})
    current = work / f'run_{run}.md'
    if current not in paths:
        paths.append(current)
    rows = [measured(path) for path in paths]
    contradictions = []
    for section in re.split(r'(?m)^## wake ', ledger)[1:]:
        lesson_run = section.split(None, 1)[0]
        row = next((row for row in rows if row['run'] == lesson_run), None)
        if not row:
            continue
        if re.search(r'no (?:capture|screenshot) (?:was )?taken', section, re.I) and row['capture_ids']:
            contradictions.append(dict(claim=section.strip(), evidence=row['source'], capture_ids=row['capture_ids'], contradiction='capture records exist'))
        match = re.search(r'first action at (\d\d:\d\d:\d\d)', section, re.I)
        if match and row['first_component'] and match[1] > row['first_component']:
            contradictions.append(dict(claim=match[0], evidence=row['source'], first_component=row['first_component'], contradiction='earlier component recorded'))
    return dict(current=next(row for row in rows if row['run'] == run), historical=rows[-6:],
                contradictions=contradictions, lesson_status='Narrative claims; unverified except the explicit measured comparisons above.')


def cost(usage, rates=None):
    """Only estimate an explicit token schema with explicit compatible rates."""
    out = dict(usage=usage, estimated_usd=None, money='not metered')
    if not isinstance(usage, dict) or not rates:
        return out
    required = ('input_tokens', 'cached_input_tokens', 'output_tokens')
    if rates.get('usage_schema') != 'input_includes_cached' or not all(key in usage and key in rates for key in required):
        return out
    values = [usage[key] for key in required]
    if not all(type(value) is int and value >= 0 for value in values) or values[1] > values[0]:
        return out
    if not all(type(rates[key]) in (int, float) and 0 <= rates[key] < float('inf') for key in required):
        return out
    out.update(estimated_usd=((values[0] - values[1]) * rates['input_tokens'] + values[1] * rates['cached_input_tokens'] + values[2] * rates['output_tokens']) / 1e6,
               money='estimate', rates=rates)
    return out


def usage_summary(path):
    """Group metered call records without guessing whether agent totals overlap."""
    path = Path(path).with_suffix('.jsonl')
    if not path.exists():
        return dict(calls=[], tokens=None, money='not metered', estimated_usd=None)
    from state import locked
    with locked(path.with_suffix('.lock')):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    calls, chats = [], {}
    for index, row in enumerate(rows, 1):
        payload = row.get('payload')
        if not isinstance(payload, dict):
            continue
        if row['kind'] in ('eye_response', 'decision_response') or (row['kind'] == 'agent' and 'usage' in payload):
            call = dict(event_line=index, chat=row.get('chat'), kind=row['kind'], model=payload.get('model'), **cost(payload.get('usage')))
            calls.append(call)
            chats.setdefault(str(row.get('chat')), []).append(call)
    return dict(calls=calls, chats=chats, tokens=None, estimated_usd=None, money='not metered',
                aggregation='Raw per-call usage retained; mixed/possibly cumulative agent schemas are not summed.')
