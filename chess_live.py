"""Fast chess.com computer player. This docstring is the operating record.

A move is local. Find the green board, match square colors to the tracked
game in chess_live.json, ask Stockfish, click the two squares. That click was
about 0.31 seconds, including a queen-side castle made by clicking the king
onto its castling square. Stockfish movetime is 0.08 seconds. The whole move
has to stay under 5 seconds. An animation frame can match a different legal square. After a click,
the loop keeps the pre-click position until that move appears, or 1.5
seconds pass. It does not click during the wait.

Stockfish is a subprocess of play. play itself is meant to be a subprocess of
the wake, so the wake can do something else at the same time. Retina is not
in this process. `watch` is a separate process: it asks only whether a board
is visible and writes chess_watch.json. Its screenshots go to sense/ so they
do not replace the wake's screen.json. The watch is not on the click path.

The mouse is shared through hand.lock, one locked byte. play holds it only
across the two clicks. Another program holds it across one stroke and then
lets go. A delivered click is not proof the move landed. If the board stays
unreadable, play stops. A result dialog covers the center. Do not click it.

Piece types are not in the color grid. chess_atlas.json stores inner-square
luma templates. They are trusted only when every occupied square clears the
correlation bar in chess_eye.classify. A trusted grid that disagrees with the
tracked move blocks the click. Retina, asked for every piece, returned square
names rather than types. Do not parse that list into a FEN. Retina's job is
the slow presence check: is a board visible, is it covered, is the browser
minimized. Jev is the yes/no check when the colors no longer fit the game.
Neither number is permission to click.

Chrome is launched, not attached:
  chrome --user-data-dir=chrome-profile --start-maximized --no-first-run
  --no-default-browser-check --force-renderer-accessibility
  https://www.chess.com/play/computer
Port 9222 does not open. chess_player.boot() refuses it. The mouse and
Stockfish live in chess_player.py. The binary is
engines\\stockfish\\stockfish-windows-x86-64-universal.exe (Stockfish 19).
Buttons come from chess_ui.py. The left-rail Play control is a hyperlink, not
a game button. Click a ButtonControl below y=400. fresh clicks New Game, then
Play, and does not click Continue. Continue resumes a tracked game. Do not
accept a human challenge and do not open a human game: an engine clicking
against a person cheats that person. The computer game is the speed test.

The board rectangle is recomputed every read. On a 1920x1080 maximized
window it was physical [284, 192, 1023, 931]. A resize moves it. Hands in
main.py are whole-desktop 0-1000, with pixel = coord * (axis - 1) / 1000.
GetWindowRect matches physical pixels only after DPI awareness context -4.
The first read after navigation often finds no board because the page is
still painting. Retry. Saved coordinates are not a board.

  .venv\\Scripts\\python.exe chess_live.py fresh
  .venv\\Scripts\\python.exe chess_live.py see
  .venv\\Scripts\\python.exe chess_live.py play 60
  .venv\\Scripts\\python.exe chess_live.py watch
"""
import ctypes
import json
import msvcrt
import os
import subprocess
import sys
import time
from pathlib import Path

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import chess
import chess.engine

from chess_eye import read_colors
from chess_player import click_px, start_engine

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'chess_live.json'
LOCK = ROOT / 'hand.lock'
SENSE = ROOT / 'sense'
WATCH_STOP = ROOT / 'chess_watch.stop'
CHROME = Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
PROFILE = ROOT / 'chrome-profile'
URL = 'https://www.chess.com/play/computer'
BODY = Path(r'C:\Users\eb-wjt\AppData\Local\Python\pythoncore-3.11-64\python.exe')
WATCH = (
    'Is a chessboard visible on this desktop? Is another window, dialog, or menu covering it? '
    'Is the browser minimized? Where does the board sit? Do not list chess pieces.'
)
LABELED = [
    'bbbbbbbb',
    'bb.bbbbb',
    '........',
    '..b.....',
    '....w...',
    '.....w..',
    'wwww.www',
    'wwwwww.w',
]


def symbols(board, white_bottom):
    rows = [['.'] * 8 for _ in range(8)]
    for sq, piece in board.piece_map().items():
        file = chess.square_file(sq)
        rank = chess.square_rank(sq)
        if white_bottom:
            row, col = 7 - rank, file
        else:
            row, col = rank, 7 - file
        rows[row][col] = piece.symbol()
    return [''.join(row) for row in rows]


def pieces_ok(board, white_bottom, pieces):
    got = symbols(board, white_bottom)
    for want, seen in zip(got, pieces):
        for left, right in zip(want, seen):
            if right == '?':
                continue
            if left != right:
                return False
    return True


def sig(board, white_bottom):
    rows = [['.'] * 8 for _ in range(8)]
    for sq, piece in board.piece_map().items():
        file = chess.square_file(sq)
        rank = chess.square_rank(sq)
        if white_bottom:
            row, col = 7 - rank, file
        else:
            row, col = rank, 7 - file
        rows[row][col] = 'w' if piece.color == chess.WHITE else 'b'
    return [''.join(row) for row in rows]


def acquire_hand(timeout=12):
    """Lock one byte of hand.lock. The holder must release it."""
    if not LOCK.exists() or LOCK.stat().st_size < 1:
        LOCK.write_bytes(b'\0')
    handle = open(LOCK, 'r+b')
    deadline = time.time() + timeout
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return handle
        except OSError:
            if time.time() >= deadline:
                handle.close()
                raise TimeoutError('hand busy')
            time.sleep(0.05)


def release_hand(handle):
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def hand_click(px, py):
    handle = acquire_hand()
    try:
        return click_px(px, py)
    finally:
        release_hand(handle)


def square_px(box, square, white_bottom):
    file = chess.square_file(square)
    rank = chess.square_rank(square)
    if white_bottom:
        col, row = file, 7 - rank
    else:
        col, row = 7 - file, rank
    x0, y0, x1, y1 = box
    side = x1 - x0
    return x0 + (col + 0.5) * side / 8, y0 + (row + 0.5) * side / 8


def pick(moves):
    if len(moves) == 1:
        return moves[0]
    queens = [mv for mv in moves if mv.promotion in (None, chess.QUEEN)]
    if len(queens) == 1:
        return queens[0]
    return None


def fitting(board, colors, white_bottom):
    hits = []
    for mv in board.legal_moves:
        nxt = board.copy(stack=False)
        nxt.push(mv)
        if sig(nxt, white_bottom) == colors:
            hits.append(mv)
    return hits


def fitting_two(board, colors, white_bottom):
    hits = []
    for mv in board.legal_moves:
        mid = board.copy(stack=False)
        mid.push(mv)
        for mv2 in mid.legal_moves:
            nxt = mid.copy(stack=False)
            nxt.push(mv2)
            if sig(nxt, white_bottom) == colors:
                hits.append((mv, mv2))
                if len(hits) > 1:
                    return hits
    return hits


def load():
    if not STATE.exists():
        return None
    return json.loads(STATE.read_text(encoding='utf-8'))


def save(board, white_bottom, note):
    payload = dict(fen=board.fen(), white_bottom=white_bottom, note=note, at=time.strftime('%H:%M:%S'))
    STATE.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return payload


def interpret(colors, state, pieces=None, trusted=False):
    def accept(board, bottom, status):
        if trusted and pieces and not pieces_ok(board, bottom, pieces):
            return None, bottom, 'pieces'
        return board, bottom, status

    start = chess.Board()
    for bottom in (True, False):
        if sig(start, bottom) == colors:
            if state and state.get('fen') == start.fen() and state.get('white_bottom') is bottom:
                return accept(start, bottom, 'same')
            return accept(start, bottom, 'start')
    if not state or 'fen' not in state or 'white_bottom' not in state:
        return None, None, 'cold'
    bottom = state['white_bottom']
    board = chess.Board(state['fen'])
    if sig(board, bottom) == colors:
        return accept(board, bottom, 'same')
    one = pick(fitting(board, colors, bottom))
    if one is not None:
        board.push(one)
        return accept(board, bottom, one.uci())
    two = fitting_two(board, colors, bottom)
    if len(two) == 1:
        board.push(two[0][0])
        board.push(two[0][1])
        return accept(board, bottom, two[0][0].uci() + ' ' + two[0][1].uci())
    return None, bottom, 'lost'


def look(path=None):
    seen = read_colors(path)
    if not seen.get('found'):
        return seen
    return seen


def check():
    seen = look(ROOT / 'shots' / '194013461.png')
    got = seen.get('colors')
    print(json.dumps(dict(found=seen.get('found'), box=seen.get('box'), colors=got)))
    if got != LABELED:
        raise SystemExit('labeled shot did not match the position after 1. e4 c5 2. Nf3')
    print('labeled shot matches')


def await_board(tries=8):
    seen = None
    for _ in range(tries):
        seen = look()
        if seen.get('found'):
            return seen
        time.sleep(0.35)
    return seen


def see():
    seen = await_board()
    if not seen.get('found'):
        print(json.dumps(seen))
        return
    board, bottom, status = interpret(seen['colors'], load(), seen.get('pieces'), seen.get('pieces_trusted'))
    print(json.dumps(dict(box=seen['box'], colors=seen['colors'], pieces=seen.get('pieces'),
                          trusted=seen.get('pieces_trusted'), status=status,
                          fen=None if board is None else board.fen(), white_bottom=bottom)))


def chrome_running():
    out = subprocess.check_output(['tasklist', '/FI', 'IMAGENAME eq chrome.exe'], text=True, errors='replace')
    return 'chrome.exe' in out.lower()


def launch():
    if chrome_running():
        return
    prefs = PROFILE / 'Default' / 'Preferences'
    if prefs.exists():
        data = json.loads(prefs.read_text(encoding='utf-8'))
        data.setdefault('profile', {})['exit_type'] = 'Normal'
        data['profile']['exited_cleanly'] = True
        prefs.write_text(json.dumps(data), encoding='utf-8')
    subprocess.Popen([str(CHROME), f'--user-data-dir={PROFILE}', '--start-maximized',
                       '--no-first-run', '--no-default-browser-check',
                       '--force-renderer-accessibility', URL])


def interesting():
    from chess_ui import chrome_hwnd, walk
    user32 = ctypes.windll.user32
    hwnd = chrome_hwnd()
    user32.ShowWindow(hwnd, 3)
    user32.SetForegroundWindow(hwnd)
    rows = []
    for row in walk():
        name = row['name'].lower()
        if any(word in name for word in ('start', 'play', 'accept', 'reject', 'resign', 'hint', 'restore', 'continue', 'new game', 'new bot', 'rematch', 'nelson', 'choose')):
            rows.append(row)
    return rows


def open_game(depth=0):
    if depth > 8:
        raise SystemExit('open did not reach a game')
    rows = []
    for _ in range(20):
        try:
            rows = interesting()
        except RuntimeError:
            launch()
            time.sleep(0.5)
            continue
        names = {r['name'].strip().lower() for r in rows if r['type'] == 'ButtonControl' and r['top'] > 400}
        if names & {'resign', 'show hint', 'continue', 'start', 'play', 'new game'}:
            break
        time.sleep(0.4)
    else:
        print('controls', [(r['name'], r['left'], r['top'], r['width'], r['height']) for r in rows])
        print('no board button; not clicking the nav')
        return
    print('controls', [(r['name'], r['left'], r['top'], r['width'], r['height']) for r in rows])
    names = {r['name'].strip().lower(): r for r in rows}

    def click_named(label):
        row = names[label]
        hand_click(row['left'] + row['width'] / 2, row['top'] + row['height'] / 2)
        print('clicked', label)

    def button(label):
        hits = [r for r in rows if r['name'].strip().lower() == label and r['type'] == 'ButtonControl' and r['top'] > 400]
        return max(hits, key=lambda r: r['width'] * r['height']) if hits else None

    # The left rail has a Play hyperlink. It is navigation, not a game button.
    # Continue keeps the tracked FEN. Accept would be a human challenge; it is not clicked.
    if button('resign') or button('show hint'):
        print('game already open')
        return
    cont = button('continue')
    if cont:
        hand_click(cont['left'] + cont['width'] / 2, cont['top'] + cont['height'] / 2)
        print('clicked Continue')
        time.sleep(1.2)
        return open_game(depth + 1)
    if 'reject all' in names:
        click_named('reject all')
        time.sleep(0.8)
        return open_game(depth + 1)
    if button('accept'):
        print('human challenge ignored')
        return
    start = button('start')
    if start:
        hand_click(start['left'] + start['width'] / 2, start['top'] + start['height'] / 2)
        print('clicked Start')
        time.sleep(1.2)
        return open_game(depth + 1)
    play = button('play')
    if play:
        hand_click(play['left'] + play['width'] / 2, play['top'] + play['height'] / 2)
        print('clicked Play', play['left'], play['top'])
        time.sleep(1.5)
        return
    fresh = button('new game')
    if fresh:
        hand_click(fresh['left'] + fresh['width'] / 2, fresh['top'] + fresh['height'] / 2)
        print('clicked New Game')
        time.sleep(1.2)
        return open_game(depth + 1)
    print('no board button; not clicking the nav')


def fresh():
    """Open a new computer game. Continue is a resume. Accept is a person."""
    launch()
    for _ in range(25):
        try:
            rows = interesting()
        except RuntimeError:
            time.sleep(0.4)
            continue

        def buttons(label):
            hits = [r for r in rows if r['name'].strip().lower() == label and r['type'] == 'ButtonControl' and r['top'] > 400]
            return max(hits, key=lambda r: r['width'] * r['height']) if hits else None

        if buttons('resign') or buttons('show hint'):
            print('game already open')
            return
        new = buttons('new game')
        if new:
            hand_click(new['left'] + new['width'] / 2, new['top'] + new['height'] / 2)
            print('clicked New Game')
            time.sleep(1.2)
            continue
        play_button = buttons('play')
        if play_button:
            hand_click(play_button['left'] + play_button['width'] / 2, play_button['top'] + play_button['height'] / 2)
            print('clicked Play')
            time.sleep(1.6)
            continue
        if buttons('accept'):
            print('human challenge ignored')
            return
        time.sleep(0.4)
    raise SystemExit('fresh did not open a computer game')


def watch():
    """Separate process. Asks Retina whether a board is visible. Does not click."""
    python = BODY if BODY.exists() else Path(sys.executable)
    script = ROOT / 'main.py'
    SENSE.mkdir(exist_ok=True)
    WATCH_STOP.unlink(missing_ok=True)
    while not WATCH_STOP.exists():
        try:
            quiet = dict(cwd=SENSE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([str(python), str(script), '--work', str(SENSE), '--capture'], timeout=40, **quiet)
            subprocess.run([str(python), str(script), '--work', str(SENSE), '--retina', WATCH], timeout=100, **quiet)
            look_path = SENSE / 'look.json'
            data = json.loads(look_path.read_text(encoding='utf-8')) if look_path.exists() else {}
            (ROOT / 'chess_watch.json').write_text(json.dumps(dict(
                at=time.strftime('%H:%M:%S'), question=WATCH, elements=data.get('elements')), indent=2), encoding='utf-8')
        except Exception as exc:
            (ROOT / 'chess_watch.json').write_text(json.dumps(dict(at=time.strftime('%H:%M:%S'), error=str(exc))), encoding='utf-8')
        for _ in range(20):
            if WATCH_STOP.exists():
                return
            time.sleep(1)


def start_watch():
    WATCH_STOP.unlink(missing_ok=True)
    log_path = ROOT / 'chess_watch_log.txt'
    handle = open(log_path, 'a', encoding='utf-8')
    return subprocess.Popen(
        [str(BODY if BODY.exists() else Path(sys.executable)), '-u', str(ROOT / 'chess_live.py'), 'watch'],
        cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def play(seconds):
    engine = start_engine()
    state = load()
    deadline = time.time() + float(seconds)
    pending = 0
    armed = None
    missed = 0
    watcher = start_watch()
    try:
        while time.time() < deadline:
            seen = look()
            if not seen.get('found'):
                missed += 1
                print('no board', seen.get('size'), seen.get('side'), flush=True)
                if missed > 25:
                    print('board stayed unreadable', flush=True)
                    break
                time.sleep(0.3)
                continue
            board, bottom, status = interpret(seen['colors'], state, seen.get('pieces'), seen.get('pieces_trusted'))
            if board is None:
                missed += 1
                print(status, seen.get('colors'), seen.get('pieces'), flush=True)
                if missed > 25:
                    print('position stayed unreadable', flush=True)
                    break
                time.sleep(0.4)
                continue
            missed = 0
            if status != 'same':
                # A sliding piece can match a different legal square for a frame.
                # Keep the pre-click position until the clicked move shows up.
                landed = armed is not None and (status == armed or status.startswith(armed + ' '))
                if armed is not None and not landed and time.time() - pending < 1.5:
                    print('hold', armed, status)
                    time.sleep(0.12)
                    continue
                armed = None
                state = save(board, bottom, status)
                pending = 0
                print(status, board.fen())
            we = chess.WHITE if bottom else chess.BLACK
            if board.is_game_over() or board.turn != we or time.time() - pending < 1.2:
                time.sleep(0.12)
                continue
            started = time.time()
            move = engine.play(board, chess.engine.Limit(time=0.08)).move
            try:
                lease = acquire_hand()
            except TimeoutError:
                print('hand busy')
                time.sleep(0.2)
                continue
            try:
                click_px(*square_px(seen['box'], move.from_square, bottom))
                time.sleep(0.04)
                click_px(*square_px(seen['box'], move.to_square, bottom))
            finally:
                release_hand(lease)
            pending = time.time()
            armed = move.uci()
            print('click', move.uci(), 'seconds', round(time.time() - started, 3), flush=True)
    finally:
        WATCH_STOP.write_text('stop', encoding='utf-8')
        watcher.terminate()
        engine.quit()


def learn_from(path=None):
    from chess_eye import atlas_path, cell_luma, grab, kinds, locate, merge_atlas, save_atlas
    image = grab(path)
    pix = image.load()
    box = locate(pix, image.size[0], image.size[1])
    if not box or box[2] < 480:
        raise SystemExit('no board to learn')
    colors = kinds(pix, box)
    board, bottom, status = interpret(colors, load())
    if board is None:
        raise SystemExit('learn needs a tracked position, got %s' % status)
    rows = symbols(board, bottom)
    patches = {}
    for row in range(8):
        for col in range(8):
            sym = rows[row][col]
            if sym != '.':
                patches.setdefault(sym, []).append(cell_luma(pix, box, row, col))
    path_atlas = atlas_path()
    raw, counts = {}, {}
    if path_atlas.exists():
        data = json.loads(path_atlas.read_text(encoding='utf-8'))
        raw, counts = data.get('templates') or {}, data.get('counts') or {}
    templates, counts = merge_atlas(raw, counts, patches)
    save_atlas(templates, counts)
    print(json.dumps(dict(status=status, fen=board.fen(), counts=counts, path=str(path_atlas))))


def score(path, fen, bottom=True):
    from chess_eye import classify, grab, kinds, load_atlas, locate
    image = grab(path)
    pix = image.load()
    box = locate(pix, image.size[0], image.size[1])
    if not box:
        raise SystemExit('no board')
    colors = kinds(pix, box)
    templates = load_atlas()
    if not templates:
        raise SystemExit('no atlas')
    pieces, scores, trusted = classify(pix, box, colors, templates, trust=0.0)
    want = symbols(chess.Board(fen), bottom)
    bad = []
    for row, (a, b) in enumerate(zip(want, pieces)):
        for col, (left, right) in enumerate(zip(a, b)):
            if left != right:
                bad.append(dict(square=row * 8 + col, want=left, got=right, score=scores[row][col]))
    occupied = [s for row in scores for s in row if s is not None]
    print(json.dumps(dict(want=want, got=pieces, trusted_bar=trusted, mismatches=bad,
                          min_score=None if not occupied else min(occupied))))


def wake(seconds):
    seen = await_board()
    if not seen or not seen.get('found'):
        open_game()
        seen = await_board(12)
    if not seen or not seen.get('found'):
        raise SystemExit('no board after open')
    board, bottom, status = interpret(seen['colors'], load(), seen.get('pieces'), seen.get('pieces_trusted'))
    print(json.dumps(dict(status=status, fen=None if board is None else board.fen(), box=seen.get('box'),
                          colors=seen.get('colors'), pieces=seen.get('pieces'))))
    if board is None:
        raise SystemExit('position not recognized')
    play(seconds)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'see'
    if mode == 'check':
        check()
    elif mode == 'see':
        see()
    elif mode == 'open':
        open_game()
    elif mode == 'play':
        play(sys.argv[2] if len(sys.argv) > 2 else '45')
    elif mode == 'learn':
        learn_from(sys.argv[2] if len(sys.argv) > 2 else None)
    elif mode == 'score':
        score(sys.argv[2], sys.argv[3], sys.argv[4] != 'black' if len(sys.argv) > 4 else True)
    elif mode == 'wake':
        wake(sys.argv[2] if len(sys.argv) > 2 else '90')
    elif mode == 'watch':
        watch()
    elif mode == 'fresh':
        fresh()
    else:
        raise SystemExit('usage: chess_live.py check|see|open|fresh|play [seconds]|learn [image]|score image fen [black]|wake [seconds]|watch')


if __name__ == '__main__':
    main()
