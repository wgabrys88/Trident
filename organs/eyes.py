import ctypes
import io
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageGrab

from organs import CONFIG, state_dir

CFG = CONFIG["eyes"]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def screenshot() -> bytes:
    image = ImageGrab.grab()
    image.thumbnail((CFG["max_side"], CFG["max_side"]), Image.LANCZOS)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def shrink(png: bytes, side: int = 0) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    side = side or CFG["cloud_side"]
    image.thumbnail((side, side), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def crop(png: bytes, box: list) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    y0, x0, y1, x1 = (float(v) for v in box)
    left, top = round(min(x0, x1) / 1000 * width), round(min(y0, y1) / 1000 * height)
    right, bottom = round(max(x0, x1) / 1000 * width), round(max(y0, y1) / 1000 * height)
    piece = image.crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    if max(piece.size) < 512:
        piece = piece.resize((piece.width * 4, piece.height * 4), Image.NEAREST)
    buffer = io.BytesIO()
    piece.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def mark(png: bytes, areas: list) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    for label, box in areas:
        y0, x0, y1, x1 = (float(v) for v in box)
        left, top = round(min(x0, x1) / 1000 * (width - 1)), round(min(y0, y1) / 1000 * (height - 1))
        right, bottom = round(max(x0, x1) / 1000 * (width - 1)), round(max(y0, y1) / 1000 * (height - 1))
        draw.rectangle((left, top, max(right, left + 1), max(bottom, top + 1)), outline=(255, 0, 0), width=3)
        draw.text((left + 4, top + 4), str(label), fill=(255, 255, 0), font=ImageFont.load_default(size=16))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def _pointer(image: Image.Image, px: int, py: int) -> None:
    width, height = image.size
    px = min(max(px, 0), width - 1)
    py = min(max(py, 0), height - 1)
    gy = min(1000, max(0, round(py / height * 1000)))
    gx = min(1000, max(0, round(px / width * 1000)))
    draw = ImageDraw.Draw(image)
    draw.line([(0, py), (width - 1, py)], fill=(255, 0, 0), width=1)
    draw.line([(px, 0), (px, height - 1)], fill=(255, 0, 0), width=1)
    draw.polygon([(px, py), (px, py + 16), (px + 4, py + 13), (px + 7, py + 19), (px + 10, py + 17), (px + 6, py + 12), (px + 12, py + 12)], fill=(255, 255, 255), outline=(0, 0, 0))
    font = ImageFont.load_default(size=32)
    text = f"y {gy} x {gx}"
    tw = int(draw.textlength(text, font=font))
    th = 32
    tx, ty = px + 18, py + 24
    if tx + tw >= width:
        tx = max(1, px - tw - 6)
    if ty + th >= height:
        ty = max(1, py - th - 6)
    draw.rectangle((tx - 2, ty - 2, tx + tw + 2, ty + th), fill=(0, 0, 0))
    draw.text((tx, ty), text, fill=(255, 255, 0), font=font)


def _controls(image: Image.Image, src_w: int, src_h: int, left: int, top: int, scale: int) -> None:
    from organs.hands import interactive_controls
    sw, sh = screen_size()
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    placed = []
    for name, x, y, w, h in sorted(interactive_controls(), key=lambda row: (row[2], row[1])):
        ix = (x * src_w / sw - left) * scale
        iy = (y * src_h / sh - top) * scale
        iw = max(1, w * src_w / sw * scale)
        ih = max(1, h * src_h / sh * scale)
        if ix + iw < 0 or iy + ih < 0 or ix >= image.width or iy >= image.height:
            continue
        draw.rectangle((ix, iy, ix + iw, iy + ih), outline=(0, 255, 255), width=2)
        gy = min(1000, max(0, round((iy + ih / 2) / image.height * 1000)))
        gx = min(1000, max(0, round((ix + iw / 2) / image.width * 1000)))
        label = f"y {gy} x {gx} {name}"
        tw = int(draw.textlength(label, font=font))
        placed.append((ix + iw / 2, iy + ih / 2, label, tw))
    if not placed:
        return
    line_h, col_w = 30, 420
    per = max(1, image.height // line_h)
    cols = (len(placed) + per - 1) // per
    x0 = max(0, image.width - cols * col_w)
    for i, (cx, cy, label, tw) in enumerate(placed):
        lx = x0 + (i // per) * col_w
        ly = (i % per) * line_h
        draw.line((cx, cy, lx, ly + 11), fill=(0, 255, 255), width=1)
        draw.rectangle((lx, ly, min(image.width - 1, lx + tw + 2), ly + line_h - 2), fill=(0, 0, 0))
        draw.text((lx + 1, ly), label, fill=(0, 255, 255), font=font)


def overlay(png: bytes, box: list | None = None) -> bytes:
    src = Image.open(io.BytesIO(png)).convert("RGB")
    src_w, src_h = src.size
    if box:
        image = Image.open(io.BytesIO(crop(png, box))).convert("RGB")
        y0, x0, y1, x1 = (float(v) for v in box)
        left, top = round(min(x0, x1) / 1000 * src_w), round(min(y0, y1) / 1000 * src_h)
        right, bottom = round(max(x0, x1) / 1000 * src_w), round(max(y0, y1) / 1000 * src_h)
        span_w, span_h = max(right, left + 1) - left, max(bottom, top + 1) - top
        scale = 4 if max(span_w, span_h) < 512 else 1
    else:
        image = src
        left = top = 0
        span_w, span_h, scale = src_w, src_h, 1
    _controls(image, src_w, src_h, left, top, scale)
    point = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        sw, sh = screen_size()
        if box:
            cx, cy = point.x * src_w / sw, point.y * src_h / sh
            if left <= cx < left + span_w and top <= cy < top + span_h:
                _pointer(image, round((cx - left) * scale), round((cy - top) * scale))
        else:
            _pointer(image, round(point.x * (image.width - 1) / (sw - 1)), round(point.y * (image.height - 1) / (sh - 1)))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def embed(box: list, inner: list) -> list:
    y0, x0, y1, x1 = (float(v) for v in box)
    top, left, bottom, right = min(y0, y1), min(x0, x1), max(y0, y1), max(x0, x1)
    iy0, ix0, iy1, ix1 = (float(v) for v in inner)
    return [top + (bottom - top) * iy0 / 1000, left + (right - left) * ix0 / 1000, top + (bottom - top) * iy1 / 1000, left + (right - left) * ix1 / 1000]


def center_px(box_2d: list) -> tuple[int, int]:
    width, height = screen_size()
    y0, x0, y1, x1 = (float(v) for v in box_2d)
    return round((x0 + x1) / 2000 * (width - 1)), round((y0 + y1) / 2000 * (height - 1))


def point_px(box: list, y: float, x: float) -> tuple[int, int]:
    y0, x0, y1, x1 = (float(v) for v in box)
    top, left = min(y0, y1), min(x0, x1)
    gy = top + (max(y0, y1) - top) * float(y) / 1000
    gx = left + (max(x0, x1) - left) * float(x) / 1000
    return center_px([gy, gx, gy, gx])


if __name__ == "__main__":
    path = state_dir() / "screen.png"
    path.write_bytes(screenshot())
    print(path, screen_size())
