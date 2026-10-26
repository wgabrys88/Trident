import json, os, socket, subprocess, sys, time
from pathlib import Path

CODE = Path(__file__).resolve().parent
PYTHON = Path(sys.executable)
NORM = 1000
PORT = 8765
SET = {}
RUN = None


class Fail(Exception):
    pass


def configure(work, run, minutes='', wake_start='', wake_end='', shot_delay='1.5', vision_url='http://192.168.16.31:1234', vision_model='retina', decide_model='typesafe/jev-1.14-20261014', mind_model=''):
    global RUN
    SET.clear()
    SET.update(work=str(Path(work).resolve()), run=run or time.strftime('%Y%m%d_%H%M%S'), minutes=str(minutes or ''), wake_start=str(wake_start or ''), wake_end=str(wake_end or ''), shot_delay=str(shot_delay or '1.5'), vision_url=vision_url.rstrip('/'), vision_model=vision_model, decide_model=decide_model, mind_model=mind_model or '')
    RUN = Path(SET['work']) / f"run_{SET['run']}.md"


def clock():
    if not SET.get('wake_start'):
        return {}
    now = time.time()
    start = float(SET['wake_start'])
    body = dict(now=time.strftime('%H:%M:%S'), used=f'{(now - start) / 60:.1f} min')
    if SET.get('wake_end'):
        body['left'] = f'{(float(SET["wake_end"]) - now) / 60:.1f} min'
    return dict(clock=body)


def read_file(path):
    file = Path(path)
    if not file.is_file():
        raise Fail(f'not a file: {path}')
    return file.read_text(encoding='utf-8-sig')


def ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def command():
    bits = [ps(PYTHON), ps(CODE / 'main.py'), '--work', ps(SET['work']), '--run', ps(SET['run'])]
    for flag, key in (('--minutes', 'minutes'), ('--wake-start', 'wake_start'), ('--wake-end', 'wake_end')):
        if SET.get(key):
            bits += [flag, ps(SET[key])]
    return '& ' + ' '.join(bits)


def _port_open():
    try:
        with socket.create_connection(('127.0.0.1', PORT), 0.2):
            return True
    except OSError:
        return False


def ensure_journal():
    if _port_open() or os.environ.get('TRIDENT_JOURNAL'):
        return
    subprocess.Popen([str(PYTHON), '-B', str(CODE / 'main.py'), '--journal', '--work', SET['work'], '--run', SET['run']], cwd=SET['work'], env=os.environ | {'TRIDENT_JOURNAL': '1'}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)


def log(kind, text=''):
    if RUN is None:
        return
    if os.environ.get('TRIDENT_JOURNAL') != '1':
        try:
            (CODE / 'journal.path').write_text(str(RUN), encoding='utf-8')
            ensure_journal()
        except OSError:
            pass
    body = str(text or '').rstrip()
    block = f"## {time.strftime('%H:%M:%S')} {kind}\n\n{body}\n\n" if body else f"## {time.strftime('%H:%M:%S')} {kind}\n\n"
    with open(RUN, 'a', encoding='utf-8') as handle:
        handle.write(block)


def emit(kind, payload):
    body = payload | clock()
    log(kind, json.dumps(body, ensure_ascii=False)[:4000])
    print(json.dumps(body, ensure_ascii=False), flush=True)
    return body


def serve_journal():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    page = b'<!DOCTYPE html><meta charset=utf-8><title>trident</title><body style="background:#000;color:#fff;margin:0"><pre id=t style="white-space:pre-wrap;margin:0;font:14px monospace"></pre><script>var t=document.getElementById("t"),es=new EventSource("/events");function z(){scrollTo(0,1e9)}es.addEventListener("reset",function(e){t.textContent=e.data;z()});es.addEventListener("more",function(e){t.textContent+=e.data;z()})</script>'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, code, ctype, body=b'', stream=False):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if body and not stream:
                self.wfile.write(body)

        def _event(self, name, payload):
            lines = payload.decode('utf-8', 'replace').split('\n')
            body = ''.join(f'data: {line}\n' for line in lines)
            self.wfile.write(f'event: {name}\n{body}\n'.encode())
            self.wfile.flush()

        def _target(self):
            pointer = CODE / 'journal.path'
            if pointer.exists():
                name = pointer.read_text(encoding='utf-8').strip()
                if name:
                    return Path(name)
            return RUN

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            target = self._target()
            if path == '/md':
                data = target.read_bytes() if target and target.exists() else b''
                self._send(200, 'text/plain; charset=utf-8', data)
                return
            if path == '/events':
                self._send(200, 'text/event-stream; charset=utf-8', stream=True)
                seen = current = None
                try:
                    while True:
                        target = self._target()
                        data = target.read_bytes() if target and target.exists() else b''
                        key = str(target)
                        if key != current or seen is None or len(data) < seen:
                            current, seen = key, len(data)
                            self._event('reset', data)
                        elif len(data) > seen:
                            self._event('more', data[seen:])
                            seen = len(data)
                        time.sleep(0.4)
                except OSError:
                    return
            if path in ('/', '/index.html'):
                self._send(200, 'text/html; charset=utf-8', page)
                return
            self.send_error(404)

    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
