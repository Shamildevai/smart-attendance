"""Generate the Windows application icon without external image libraries."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build_assets" / "attendance.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)
MASTER = 1024

GREEN_TOP = (22, 160, 133)
GREEN_BOTTOM = (9, 92, 73)
WHITE = (255, 255, 255, 255)


def _rounded_rect(x: float, y: float, left: float, top: float, right: float, bottom: float, radius: float) -> bool:
    if x < left or x > right or y < top or y > bottom:
        return False
    nearest_x = min(max(x, left + radius), right - radius)
    nearest_y = min(max(y, top + radius), bottom - radius)
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= radius * radius


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _master_pixels() -> list[tuple[int, int, int, int]]:
    bars = [26, 12, 18, 34, 12, 26, 12, 34, 18, 12, 26]
    gap = 18
    total = sum(bars) + gap * (len(bars) - 1)
    area_left, area_right = 236, 788
    area_top, area_bottom = 246, 560
    scale = (area_right - area_left) / total
    rects: list[tuple[float, float, float, float]] = []
    cursor = area_left
    for width in bars:
        bar_width = width * scale
        rects.append((cursor, area_top, cursor + bar_width, area_bottom))
        cursor += bar_width + gap * scale

    circle_x, circle_y, circle_r = 700.0, 724.0, 176.0
    check = ((628.0, 728.0), (684.0, 784.0), (790.0, 668.0))
    stroke = 58.0

    pixels: list[tuple[int, int, int, int]] = []
    margin = 30.0
    radius = 218.0
    for y in range(MASTER):
        py = y + 0.5
        ratio = y / (MASTER - 1)
        background = (
            round(GREEN_TOP[0] + (GREEN_BOTTOM[0] - GREEN_TOP[0]) * ratio),
            round(GREEN_TOP[1] + (GREEN_BOTTOM[1] - GREEN_TOP[1]) * ratio),
            round(GREEN_TOP[2] + (GREEN_BOTTOM[2] - GREEN_TOP[2]) * ratio),
            255,
        )
        for x in range(MASTER):
            px = x + 0.5
            if not _rounded_rect(px, py, margin, margin, MASTER - margin, MASTER - margin, radius):
                pixels.append((0, 0, 0, 0))
                continue
            px_out = px - circle_x
            py_out = py - circle_y
            on_check = any(
                _segment_distance(px, py, check[i][0], check[i][1], check[i + 1][0], check[i + 1][1]) <= stroke / 2
                for i in range(len(check) - 1)
            )
            if px_out * px_out + py_out * py_out <= circle_r * circle_r:
                pixels.append(background[:3] + (255,) if on_check else WHITE)
                continue
            if any(left <= px <= right and top <= py <= bottom for left, top, right, bottom in rects):
                pixels.append(WHITE)
                continue
            pixels.append(background)
    return pixels


def _downsample(master: list[tuple[int, int, int, int]], size: int) -> list[tuple[int, int, int, int]]:
    factor = MASTER // size
    pixels: list[tuple[int, int, int, int]] = []
    for y in range(size):
        row_base = y * factor * MASTER
        for x in range(size):
            red = green = blue = alpha = 0
            for dy in range(factor):
                index = row_base + dy * MASTER + x * factor
                for dx in range(factor):
                    r, g, b, a = master[index + dx]
                    red += r * a
                    green += g * a
                    blue += b * a
                    alpha += a
            if alpha:
                pixels.append((red // alpha, green // alpha, blue // alpha, alpha // (factor * factor)))
            else:
                pixels.append((0, 0, 0, 0))
    return pixels


def _png(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            raw.extend((r, g, b, a))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(
            ">I", zlib.crc32(tag + payload) & 0xFFFFFFFF
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _dib(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0
    )
    body = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            body.extend((b, g, r, a))
    mask_stride = ((size + 31) // 32) * 4
    mask = bytearray(mask_stride * size)
    for y in range(size):
        row = (size - 1 - y) * mask_stride
        for x in range(size):
            if pixels[y * size + x][3] == 0:
                mask[row + x // 8] |= 0x80 >> (x % 8)
    return header + bytes(body) + bytes(mask)


def build_ico(path: Path) -> None:
    master = _master_pixels()
    entries: list[tuple[int, bytes]] = []
    for size in SIZES:
        pixels = _downsample(master, size)
        entries.append((size, _png(size, pixels) if size >= 128 else _dib(size, pixels)))

    offset = 6 + 16 * len(entries)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    payload = bytearray()
    for size, data in entries:
        directory.extend(
            struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        )
        payload.extend(data)
        offset += len(data)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(directory) + bytes(payload))


if __name__ == "__main__":
    build_ico(OUTPUT)
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
