import ctypes
import io
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageGrab

from organs import CONFIG, run_dir

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


def crop(png: bytes, box: list, upscale: bool = True) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    y0, x0, y1, x1 = (float(v) for v in box)
    left, top = round(min(x0, x1) / 1000 * width), round(min(y0, y1) / 1000 * height)
    right, bottom = round(max(x0, x1) / 1000 * width), round(max(y0, y1) / 1000 * height)
    piece = image.crop((left, top, max(right, left + 1), max(bottom, top + 1)))
    if upscale and max(piece.size) < 512:
        piece = piece.resize((piece.width * 4, piece.height * 4), Image.NEAREST)
    buffer = io.BytesIO()
    piece.save(buffer, format="PNG", compress_level=1)
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


def _panel(labels: list[str], width: int) -> Image.Image | None:
    if not labels:
        return None
    font = ImageFont.load_default(size=32)
    line_h, rows, col_w = 36, 30, 520
    cols = (len(labels) + rows - 1) // rows
    panel = Image.new("RGB", (max(width, cols * col_w), rows * line_h), (0, 0, 0))
    draw = ImageDraw.Draw(panel)
    for i, label in enumerate(labels):
        draw.text(((i // rows) * col_w + 4, (i % rows) * line_h), label, fill=(0, 255, 255), font=font)
    return panel


def overlay(png: bytes, box: list | None = None, notes: list | None = None) -> bytes:
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
        image, left, top, span_w, span_h, scale = src, 0, 0, src_w, src_h, 1
    sw, sh = screen_size()
    draw = ImageDraw.Draw(image)
    lines = []
    from organs.hands import interactive_controls
    for name, x, y, w, h in interactive_controls():
        ix = (x * src_w / sw - left) * scale
        iy = (y * src_h / sh - top) * scale
        iw = max(1, w * src_w / sw * scale)
        ih = max(1, h * src_h / sh * scale)
        if ix + iw < 0 or iy + ih < 0 or ix >= image.width or iy >= image.height:
            continue
        draw.rectangle((ix, iy, ix + iw, iy + ih), outline=(0, 255, 255), width=2)
        lines.append((min(1000, max(0, round((iy + ih / 2) / image.height * 1000))), min(1000, max(0, round((ix + iw / 2) / image.width * 1000))), name))
    for name, spot in notes or []:
        y0, x0, y1, x1 = (float(v) for v in spot)
        ix0 = round((min(x0, x1) / 1000 * src_w - left) * scale)
        iy0 = round((min(y0, y1) / 1000 * src_h - top) * scale)
        ix1 = round((max(x0, x1) / 1000 * src_w - left) * scale)
        iy1 = round((max(y0, y1) / 1000 * src_h - top) * scale)
        if ix1 < 0 or iy1 < 0 or ix0 >= image.width or iy0 >= image.height:
            continue
        draw.rectangle((ix0, iy0, max(ix1, ix0 + 1), max(iy1, iy0 + 1)), outline=(0, 255, 255), width=3)
        lines.append((min(1000, max(0, round((iy0 + iy1) / 2 / image.height * 1000))), min(1000, max(0, round((ix0 + ix1) / 2 / image.width * 1000))), name))
    point = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        if box:
            cx, cy = point.x * src_w / sw, point.y * src_h / sh
            if left <= cx < left + span_w and top <= cy < top + span_h:
                _pointer(image, round((cx - left) * scale), round((cy - top) * scale))
        else:
            _pointer(image, round(point.x * (image.width - 1) / max(1, sw - 1)), round(point.y * (image.height - 1) / max(1, sh - 1)))
    panel = _panel([f"y {gy} x {gx} {name}" for gy, gx, name in sorted(lines)], image.width)
    if panel is not None:
        canvas = Image.new("RGB", (max(image.width, panel.width), image.height + panel.height), (0, 0, 0))
        canvas.paste(image, (0, 0))
        ImageDraw.Draw(canvas).line((0, image.height, canvas.width - 1, image.height), fill=(0, 255, 255), width=2)
        canvas.paste(panel, (0, image.height))
        image = canvas
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
    path = run_dir() / "screen.png"
    path.write_bytes(screenshot())
    print(path, screen_size())
