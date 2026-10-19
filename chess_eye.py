"""Find the chess.com board in a screenshot and report occupied squares.

The computer-play board is a green square. The vertical scan covers the full
frame, so a board that is not in the old 150–1200 x window can still be found.
On shot 194013461, after 1. e4 c5 2. Nf3, empty squares were distance <= 18
and occupied squares >= 43. Threshold 30 and luminance split 115 fit that
green theme, not every theme.

Piece types come from chess_atlas.json when it exists: the inner 40% of a
square, 24x24 luma, normalized correlation against same-color templates.
A Retina request that asked for every piece returned square names, not types.
Do not treat that kind of list as a position. Retina is the presence monitor.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageGrab


def boardish(pixel):
    r, g, b = pixel
    return g > 120 and g > r + 15 and g > b + 8 and r > 70 and b < 180


def locate(pix, width, height):
    xs = [sum(1 for y in range(100, min(height, 980), 2) if boardish(pix[x, y])) for x in range(width)]
    ys = [sum(1 for x in range(0, width, 2) if boardish(pix[x, y])) for y in range(height)]

    def runs(values, thresh):
        found = []
        i = 0
        n = len(values)
        while i < n:
            if values[i] < thresh:
                i += 1
                continue
            j = i
            while j < n and values[j] >= thresh:
                j += 1
            found.append((i, j - 1))
            i = j
        return found

    # Green controls beside the board, and a green strip above it, are
    # separate runs once the threshold clears the dimmer pixels. The board
    # is the largest pair of runs whose sides match.
    best = None
    for tx in (80, 120, 160):
        for ty in (80, 120, 160, 200):
            for x0, x1 in runs(xs, tx):
                for y0, y1 in runs(ys, ty):
                    side_x, side_y = x1 - x0, y1 - y0
                    if abs(side_x - side_y) <= 12 and min(side_x, side_y) >= 480:
                        if best is None or side_x > best[0]:
                            best = (side_x, x0, y0)
    if not best:
        return None
    return best[1], best[2], best[0]


def distances(pix, box, frac=0.18):
    x0, y0, side = box
    square = side / 8
    grid = []
    for row in range(8):
        for col in range(8):
            cx = x0 + (col + 0.5) * square
            cy = y0 + (row + 0.5) * square
            rad = max(4, int(square * 0.16))
            r = g = b = n = 0
            for yy in range(int(cy - rad), int(cy + rad)):
                for xx in range(int(cx - rad), int(cx + rad)):
                    pr, pg, pb = pix[xx, yy]
                    r += pr
                    g += pg
                    b += pb
                    n += 1
            ox = int(x0 + col * square + square * frac)
            oy = int(y0 + row * square + square * frac)
            rad2 = max(3, int(square * 0.08))
            cr = cg = cb = m = 0
            for yy in range(oy, oy + rad2):
                for xx in range(ox, ox + rad2):
                    pr, pg, pb = pix[xx, yy]
                    cr += pr
                    cg += pg
                    cb += pb
                    m += 1
            center = (r / n, g / n, b / n)
            corner = (cr / m, cg / m, cb / m)
            grid.append(sum((a - c) ** 2 for a, c in zip(center, corner)) ** 0.5)
    return grid


def grab(path=None):
    if path:
        image = Image.open(path).convert('RGB')
    else:
        import ctypes
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        image = ImageGrab.grab(all_screens=True).convert('RGB')
    return image


def kinds(pix, box, occ_thresh=30, lum_split=115):
    """Occupied squares as 'w' or 'b', empty as '.'.

    Occupancy is the corner-versus-center distance from distances().
    On shot 194013461 the gap was empty <= 18 and occupied >= 43.
    Piece color is the median luminance of center pixels that differ from
    the corner. On that shot black pieces were about 78-99 and white pieces
    about 129-197, so 115 sits in the gap. Piece type is not identified.
    """
    x0, y0, side = box
    square = side / 8
    dist = distances(pix, box)
    rows = []
    for row in range(8):
        chars = []
        for col in range(8):
            if dist[row * 8 + col] < occ_thresh:
                chars.append('.')
                continue
            cx = int(x0 + (col + 0.5) * square)
            cy = int(y0 + (row + 0.5) * square)
            rad = max(4, int(square * 0.16))
            ox = int(x0 + col * square + square * 0.18)
            oy = int(y0 + row * square + square * 0.18)
            span = max(3, int(square * 0.08))
            cr = cg = cb = n = 0
            for yy in range(oy, oy + span):
                for xx in range(ox, ox + span):
                    pr, pg, pb = pix[xx, yy]
                    cr += pr
                    cg += pg
                    cb += pb
                    n += 1
            bg = (cr / n, cg / n, cb / n)
            vals = []
            for yy in range(cy - rad, cy + rad):
                for xx in range(cx - rad, cx + rad):
                    pr, pg, pb = pix[xx, yy]
                    if (pr - bg[0]) ** 2 + (pg - bg[1]) ** 2 + (pb - bg[2]) ** 2 > 40 * 40:
                        vals.append(0.2126 * pr + 0.7152 * pg + 0.0722 * pb)
            if not vals:
                pr, pg, pb = pix[cx, cy]
                med = 0.2126 * pr + 0.7152 * pg + 0.0722 * pb
            else:
                vals.sort()
                med = vals[len(vals) // 2]
            chars.append('w' if med >= lum_split else 'b')
        rows.append(''.join(chars))
    return rows


def read_board(path=None, thresh=30):
    image = grab(path)
    pix = image.load()
    box = locate(pix, image.size[0], image.size[1])
    if not box:
        return dict(found=False, size=list(image.size))
    grid = distances(pix, box)
    rows = [''.join('x' if grid[row * 8 + col] >= thresh else '.' for col in range(8)) for row in range(8)]
    return dict(found=True, box=[box[0], box[1], box[0] + box[2], box[1] + box[2]], side=box[2],
                threshold=thresh, distances=[[round(v) for v in grid[row * 8:(row + 1) * 8]] for row in range(8)],
                occupied=rows, size=list(image.size))


def cell_luma(pix, box, row, col, n=24, half=0.20):
    """Grayscale samples of the inner part of one square, row-major."""
    x0, y0, side = box
    square = side / 8
    cx = x0 + (col + 0.5) * square
    cy = y0 + (row + 0.5) * square
    span = square * half
    left, top = int(x0), int(y0)
    right, bottom = int(x0 + side - 1), int(y0 + side - 1)
    vals = []
    for iy in range(n):
        for ix in range(n):
            x = int(cx - span + (2 * span) * (ix + 0.5) / n)
            y = int(cy - span + (2 * span) * (iy + 0.5) / n)
            x = min(right, max(left, x))
            y = min(bottom, max(top, y))
            r, g, b = pix[x, y]
            vals.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
    return vals


def ncc(a, b):
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    va = vb = dot = 0.0
    for x, y in zip(a, b):
        dx, dy = x - ma, y - mb
        va += dx * dx
        vb += dy * dy
        dot += dx * dy
    if va < 1e-6 or vb < 1e-6:
        return 0.0
    return dot / (va * vb) ** 0.5


def mean_patch(patches):
    acc = [0.0] * len(patches[0])
    for patch in patches:
        for i, value in enumerate(patch):
            acc[i] += value
    scale = float(len(patches))
    return [value / scale for value in acc]


def atlas_path():
    return Path(__file__).resolve().parent / 'chess_atlas.json'


def load_atlas(path=None):
    path = Path(path) if path else atlas_path()
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    templates = data.get('templates') or {}
    return templates or None


def save_atlas(templates, counts, path=None):
    path = Path(path) if path else atlas_path()
    payload = dict(n=24, half=0.20, source='inner square luma, normalized correlation',
                   counts=counts, templates=templates)
    path.write_text(json.dumps(payload), encoding='utf-8')
    return str(path)


def merge_atlas(previous, counts, patches):
    templates = dict(previous or {})
    counts = dict(counts or {})
    for sym, samples in patches.items():
        fresh = mean_patch(samples)
        old_n = int(counts.get(sym, 0))
        if sym in templates and old_n > 0:
            old = templates[sym]
            total = old_n + len(samples)
            templates[sym] = [(old[i] * old_n + fresh[i] * len(samples)) / total for i in range(len(fresh))]
            counts[sym] = total
        else:
            templates[sym] = fresh
            counts[sym] = len(samples)
    return templates, counts


def classify(pix, box, colors, templates, trust=0.80):
    """Piece symbols for occupied squares. '?' is an untrusted match.

    Color already says white or black, so each square is compared only with
    that color's six templates. trust is the minimum correlation and margin.
    """
    rows, scores, trusted = [], [], True
    for row in range(8):
        chars, row_scores = [], []
        for col in range(8):
            kind = colors[row][col]
            if kind == '.':
                chars.append('.')
                row_scores.append(None)
                continue
            patch = cell_luma(pix, box, row, col)
            ranked = sorted(((ncc(patch, tmpl), sym) for sym, tmpl in templates.items()
                             if sym.isupper() == (kind == 'w')), reverse=True)
            if not ranked:
                chars.append('?')
                row_scores.append(None)
                trusted = False
                continue
            best, nxt = ranked[0][0], ranked[1][0] if len(ranked) > 1 else -1.0
            row_scores.append(round(best, 3))
            if best < trust or best - nxt < 0.05:
                chars.append('?')
                trusted = False
            else:
                chars.append(ranked[0][1])
        rows.append(''.join(chars))
        scores.append(row_scores)
    return rows, scores, trusted


def read_colors(path=None, occ_thresh=30, lum_split=115):
    image = grab(path)
    pix = image.load()
    box = locate(pix, image.size[0], image.size[1])
    if not box or box[2] < 480:
        return dict(found=False, size=list(image.size), side=None if not box else box[2])
    x0, y0, side = box
    colors = kinds(pix, box, occ_thresh, lum_split)
    result = dict(found=True, box=[x0, y0, x0 + side, y0 + side], side=side,
                  colors=colors, size=list(image.size))
    templates = load_atlas()
    if templates:
        pieces, scores, trusted = classify(pix, (x0, y0, side), colors, templates)
        result['pieces'] = pieces
        result['piece_scores'] = scores
        result['pieces_trusted'] = trusted
    return result


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(read_board(target)))
