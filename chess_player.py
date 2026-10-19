"""Local chess.com player.

Reads the board through Chrome's debugging port, asks Stockfish for a move,
and plays it with a real mouse click. Retina and Jev are not on this path.
Coordinates are physical screen pixels, converted the same way main.py maps
the whole virtual desktop.

Wake 1 found that Chrome stable 155.0.8059.85 does not open port 9222 and
does not answer --remote-debugging-pipe or --remote-debugging-io-pipes.
Wake 2 did not retry the port. Page buttons came through UI Automation when
Chrome was launched with --force-renderer-accessibility (see chess_ui.py).
The board squares had no accessible names; chess_eye.py read occupancy from
pixels. This CDP reader is still unproven. Do not boot() expecting a port.

  .venv\\Scripts\\python.exe chess_player.py probe
  .venv\\Scripts\\python.exe chess_player.py click Play
  .venv\\Scripts\\python.exe chess_player.py play
"""
import asyncio, ctypes, json, os, re, sys, time, urllib.request
from ctypes import wintypes
from pathlib import Path

import chess
import chess.engine

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / 'engines' / 'stockfish' / 'stockfish-windows-x86-64-universal.exe'
CHROME = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
PROFILE = ROOT / 'chrome-profile'
URL = 'https://www.chess.com/play/computer'
LOG = ROOT / 'chess_log.txt'
STATE = ROOT / 'chess_state.json'
HALT = ROOT / 'chess_halt.txt'
PORT = 'http://127.0.0.1:9222/json'

ULONG_PTR = ctypes.c_size_t
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]
class INPUTUNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT)]
class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]

USER32 = ctypes.WinDLL('user32', use_last_error=True)
USER32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
USER32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
USER32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
USER32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))

PIECE = dict(wp='P', wn='N', wb='B', wr='R', wq='Q', wk='K', bp='p', bn='n', bb='b', br='r', bq='q', bk='k')
METRICS_JS = """(() => {
  const dpr = window.devicePixelRatio || 1;
  const borderX = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
  const chromeTop = Math.max(0, window.outerHeight - window.innerHeight - borderX);
  return {dpr, borderX, chromeTop, screenX: window.screenX, screenY: window.screenY,
          outerWidth: window.outerWidth, outerHeight: window.outerHeight,
          innerWidth: window.innerWidth, innerHeight: window.innerHeight};
})()"""
STATE_JS = """(() => {
  const metrics = () => {
    const dpr = window.devicePixelRatio || 1;
    const borderX = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
    const chromeTop = Math.max(0, window.outerHeight - window.innerHeight - borderX);
    return {dpr, borderX, chromeTop, screenX: window.screenX, screenY: window.screenY};
  };
  const m = metrics();
  const toScreen = (x, y, w, h) => ({
    x: (m.screenX + m.borderX + x) * m.dpr, y: (m.screenY + m.chromeTop + y) * m.dpr,
    w: w * m.dpr, h: h * m.dpr, dpr: m.dpr
  });
  const board = document.querySelector('wc-chess-board, chess-board');
  const clocks = [...document.querySelectorAll('[class*=clock]')].slice(0, 12).map(el => String(el.className || '').slice(0, 160));
  if (!board) return {href: location.href, title: document.title, board: false, clocks, metrics: m};
  const r = board.getBoundingClientRect();
  const cls = String(board.className || '');
  const flipped = /\\bflipped\\b/.test(cls) || board.getAttribute('orientation') === 'black';
  let fen = board.getAttribute('position') || board.getAttribute('fen') || null;
  try { if (!fen && board.game && board.game.getFEN) fen = board.game.getFEN(); } catch (e) {}
  const pieces = [];
  const grab = (root) => {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('[class*="piece"]').forEach(el => pieces.push(String(el.className || '')));
    root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) grab(el.shadowRoot); });
  };
  grab(board);
  return {
    href: location.href, title: document.title, board: true, fen, pieces, clocks, flipped,
    cls: cls.slice(0, 240),
    attrs: [...board.attributes].slice(0, 24).map(a => a.name + '=' + String(a.value).slice(0, 120)),
    rect: toScreen(r.left, r.top, r.width, r.height),
    metrics: m
  };
})()"""
PROBE_JS = """(() => {
  const texts = [];
  const visit = (root, d) => {
    if (!root || d > 6 || texts.length > 80) return;
    if (root.querySelectorAll) {
      root.querySelectorAll('button, a, [role="button"]').forEach(el => {
        const t = String(el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
        if (t && t.length < 70) texts.push(t);
      });
      root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) visit(el.shadowRoot, d + 1); });
    }
  };
  visit(document, 0);
  const body = String((document.body && document.body.innerText) || '').replace(/\\s+/g, ' ').slice(0, 1200);
  return {texts: [...new Set(texts)].slice(0, 50), body};
})()"""


def log(msg):
    line = time.strftime('%H:%M:%S') + ' ' + str(msg)
    print(line, flush=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def bounds():
    g = USER32.GetSystemMetrics
    return tuple(map(g, (76, 77, 78, 79)))


def send(*events):
    arr = (INPUT * len(events))(*events)
    if USER32.SendInput(len(events), arr, ctypes.sizeof(INPUT)) != len(events):
        raise ctypes.WinError()


def mouse(flags, x=0, y=0):
    return INPUT(type=0, u=INPUTUNION(mi=MOUSEINPUT(dx=x, dy=y, mouseData=0, dwFlags=flags)))


def move_px(px, py):
    left, top, width, height = bounds()
    nx = min(65535, round((px - left + 0.5) * 65536 / width))
    ny = min(65535, round((py - top + 0.5) * 65536 / height))
    send(mouse(0xE001, nx, ny))
    time.sleep(0.02)


def click_px(px, py):
    move_px(px, py)
    send(mouse(0x0002))
    time.sleep(0.03)
    send(mouse(0x0004))
    time.sleep(0.04)
    return dict(px=round(px, 1), py=round(py, 1))


def port_open():
    try:
        with urllib.request.urlopen(PORT + '/version', timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def targets():
    with urllib.request.urlopen(PORT, timeout=3) as r:
        return json.load(r)


def chess_target():
    pages = [p for p in targets() if p.get('type') == 'page']
    hit = [p for p in pages if 'chess.com' in p.get('url', '')]
    if hit:
        return hit[0]
    return None


def boot():
    raise SystemExit('Chrome on this machine does not open port 9222. Use chess_live.py.')


def connect(url):
    import websockets
    headers = {'Origin': 'http://127.0.0.1:9222'}
    try:
        return websockets.connect(url, max_size=8_000_000, additional_headers=headers)
    except TypeError:
        return websockets.connect(url, max_size=8_000_000, extra_headers=headers)


async def cdp(ws, ident, method, params=None):
    await ws.send(json.dumps({'id': ident, 'method': method, 'params': params or {}}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if msg.get('id') != ident:
            continue
        if msg.get('error'):
            raise RuntimeError(str(msg['error'])[:400])
        result = msg.get('result') or {}
        details = result.get('exceptionDetails')
        if details:
            raise RuntimeError(str(details.get('text') or details)[:400])
        value = result.get('result', {}).get('value') if method == 'Runtime.evaluate' else result
        return value


async def evaluate(ws, ident, expression):
    return await cdp(ws, ident, 'Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})


def save(payload):
    STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def fen_from_pieces(classes, turn):
    board = chess.Board.empty()
    for cls in classes:
        kind = re.search(r'\b([wb][pnbrqk])\b', cls)
        sq = re.search(r'square-([1-8])([1-8])', cls)
        if not kind or not sq:
            continue
        board.set_piece_at(chess.square(int(sq.group(1)) - 1, int(sq.group(2)) - 1), chess.Piece.from_symbol(PIECE[kind.group(1)]))
    def home(color, king, rook_a, rook_h):
        if board.piece_at(king) == chess.Piece(chess.KING, color) and board.piece_at(rook_h) == chess.Piece(chess.ROOK, color):
            board.castling_rights |= chess.BB_SQUARES[rook_h]
        if board.piece_at(king) == chess.Piece(chess.KING, color) and board.piece_at(rook_a) == chess.Piece(chess.ROOK, color):
            board.castling_rights |= chess.BB_SQUARES[rook_a]
    home(chess.WHITE, chess.E1, chess.A1, chess.H1)
    home(chess.BLACK, chess.E8, chess.A8, chess.H8)
    board.turn = turn
    return board


def position_of(state):
    """Return a chess.Board and whether the FEN came from the page itself."""
    fen = state.get('fen')
    if isinstance(fen, str) and ' ' in fen:
        return chess.Board(fen), True
    clocks = state.get('clocks') or []
    bottom = any('bottom' in c and 'player-turn' in c for c in clocks)
    top = any(('top' in c or 'opponent' in c) and 'player-turn' in c for c in clocks)
    if bottom or top:
        we_white = not state.get('flipped')
        turn = chess.WHITE if (bottom == we_white) else chess.BLACK
    else:
        turn = chess.WHITE
    board = fen_from_pieces(state.get('pieces') or [], turn)
    if isinstance(fen, str) and '/' in fen and ' ' not in fen:
        # placement only; keep the turn we inferred
        placed = chess.Board(fen + ' w - - 0 1')
        placed.turn = turn
        placed.castling_rights = board.castling_rights
        return placed, False
    return board, False


def our_turn(board, state):
    clocks = state.get('clocks') or []
    bottom = any('bottom' in c and 'player-turn' in c for c in clocks)
    top = any('player-turn' in c and 'bottom' not in c for c in clocks)
    if bottom or top:
        return bottom
    we_white = not state.get('flipped')
    return board.turn == (chess.WHITE if we_white else chess.BLACK)


def square_pixel(rect, square, flipped):
    file = chess.square_file(square)
    rank = chess.square_rank(square)
    if flipped:
        col, row = 7 - file, rank
    else:
        col, row = file, 7 - rank
    return rect['x'] + (col + 0.5) * rect['w'] / 8, rect['y'] + (row + 0.5) * rect['h'] / 8


def pixel(px, py):
    try:
        from PIL import ImageGrab
        return ImageGrab.grab(all_screens=True).getpixel((int(px), int(py)))
    except Exception as e:
        return str(e)


async def session():
    deadline = time.time() + 25
    page = None
    while time.time() < deadline:
        page = chess_target()
        if page and page.get('webSocketDebuggerUrl'):
            break
        await asyncio.sleep(0.4)
    if not page:
        raise RuntimeError('no chess.com page on the debugging port')
    return page


def find_button_js(label):
    want = json.dumps(label.lower())
    return """(() => {
      const want = %s;
      const m = (() => {
        const dpr = window.devicePixelRatio || 1;
        const borderX = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
        const chromeTop = Math.max(0, window.outerHeight - window.innerHeight - borderX);
        return {dpr, borderX, chromeTop, screenX: window.screenX, screenY: window.screenY};
      })();
      const hits = [];
      const visit = (root, d) => {
        if (!root || d > 7) return;
        if (!root.querySelectorAll) return;
        root.querySelectorAll('button, a, [role="button"]').forEach(el => {
          const t = String(el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
          if (!t.toLowerCase().includes(want)) return;
          const r = el.getBoundingClientRect();
          if (r.width < 4 || r.height < 4) return;
          hits.push({t, x: (m.screenX + m.borderX + r.left + r.width / 2) * m.dpr,
                     y: (m.screenY + m.chromeTop + r.top + r.height / 2) * m.dpr,
                     w: r.width, h: r.height, exact: t.toLowerCase() === want});
        });
        root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) visit(el.shadowRoot, d + 1); });
      };
      visit(document, 0);
      hits.sort((a, b) => (b.exact - a.exact) || (a.t.length - b.t.length) || (b.w * b.h - a.w * a.h));
      return hits.slice(0, 8);
    })()""" % want


async def read_state(ws, ident):
    return await evaluate(ws, ident, STATE_JS)


async def probe():
    boot()
    page = await session()
    async with connect(page['webSocketDebuggerUrl']) as ws:
        state = await read_state(ws, 1)
        extra = await evaluate(ws, 2, PROBE_JS)
    state = state or {}
    state['buttons'] = (extra or {}).get('texts')
    state['body'] = (extra or {}).get('body')
    rect = state.get('rect') or {}
    if rect.get('w'):
        cx, cy = rect['x'] + rect['w'] / 2, rect['y'] + rect['h'] / 2
        state['center_pixel'] = pixel(cx, cy)
        state['center'] = [round(cx, 1), round(cy, 1)]
    save(state)
    log('probe board=%s fen=%s pieces=%s buttons=%s' % (state.get('board'), state.get('fen'), len(state.get('pieces') or []), len(state.get('buttons') or [])))
    return state


async def click_label(label):
    page = await session()
    async with connect(page['webSocketDebuggerUrl']) as ws:
        hits = await evaluate(ws, 1, find_button_js(label))
    save(dict(click=label, hits=hits))
    if not hits:
        log('no control labeled %r' % label)
        return None
    hit = hits[0]
    at = click_px(hit['x'], hit['y'])
    log('clicked %r at %s' % (hit.get('t'), at))
    return hit


def start_engine():
    if not ENGINE.exists():
        raise FileNotFoundError(ENGINE)
    engine = chess.engine.SimpleEngine.popen_uci(str(ENGINE))
    threads = max(1, min(4, (os.cpu_count() or 2) // 2))
    options = {'Threads': threads, 'Hash': 32}
    skill = os.environ.get('CHESS_SKILL')
    if skill:
        options['Skill Level'] = int(skill)
    engine.configure(options)
    log('engine %s threads=%s' % (engine.id.get('name'), threads))
    return engine


async def play(seconds=None):
    if HALT.exists():
        HALT.unlink()
    engine = start_engine()
    deadline = time.time() + float(seconds) if seconds else None
    ident = 10
    last = None
    try:
        page = await session()
        async with connect(page['webSocketDebuggerUrl']) as ws:
            while not HALT.exists():
                if deadline and time.time() > deadline:
                    log('play window elapsed')
                    break
                ident += 1
                try:
                    state = await read_state(ws, ident)
                except Exception as e:
                    log('read failed: %s' % e)
                    await asyncio.sleep(0.4)
                    continue
                if not state or not state.get('board') or not state.get('rect'):
                    await asyncio.sleep(0.25)
                    continue
                try:
                    board, official = position_of(state)
                except Exception as e:
                    log('fen rejected: %s' % e)
                    await asyncio.sleep(0.4)
                    continue
                fen = board.fen()
                if fen == last:
                    await asyncio.sleep(0.12)
                    continue
                if board.is_game_over() or not our_turn(board, state):
                    if fen != last:
                        log('waiting fen=%s official=%s' % (fen, official))
                    last = fen
                    await asyncio.sleep(0.15)
                    continue
                limit = float(os.environ.get('CHESS_MOVETIME', '0.12'))
                chosen = engine.play(board, chess.engine.Limit(time=limit))
                move = chosen.move
                if move not in board.legal_moves:
                    log('illegal suggestion %s' % move)
                    last = fen
                    await asyncio.sleep(0.3)
                    continue
                start = square_pixel(state['rect'], move.from_square, state.get('flipped'))
                dest = square_pixel(state['rect'], move.to_square, state.get('flipped'))
                log('play %s from %s to %s fen=%s' % (move.uci(), tuple(round(v, 1) for v in start), tuple(round(v, 1) for v in dest), fen))
                click_px(*start)
                time.sleep(0.05)
                click_px(*dest)
                if move.promotion:
                    await asyncio.sleep(0.15)
                    ident += 1
                    promo = await evaluate(ws, ident, find_button_js('queen'))
                    if promo:
                        click_px(promo[0]['x'], promo[0]['y'])
                changed = False
                for _ in range(25):
                    await asyncio.sleep(0.1)
                    ident += 1
                    nxt = await read_state(ws, ident)
                    try:
                        nboard, _ = position_of(nxt)
                    except Exception:
                        continue
                    if nboard.fen() != fen:
                        last = nboard.fen()
                        changed = True
                        log('moved now=%s' % last)
                        break
                if not changed:
                    log('board did not change after %s' % move.uci())
                    last = fen
                    await asyncio.sleep(0.4)
    finally:
        engine.quit()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'probe'
    if mode == 'boot':
        boot()
        return
    if mode == 'probe':
        boot()
        asyncio.run(probe())
        return
    if mode == 'click':
        label = sys.argv[2] if len(sys.argv) > 2 else 'Play'
        asyncio.run(click_label(label))
        return
    if mode == 'play':
        asyncio.run(play(sys.argv[2] if len(sys.argv) > 2 else None))
        return
    raise SystemExit('usage: chess_player.py boot|probe|click LABEL|play [seconds]')


if __name__ == '__main__':
    main()
