# mechanism/alerts/market_card.py
"""
"First Light" market card: ONE portrait PNG (1080x1440, phone-friendly) that shows the day's market performance at a glance.
Pure drawing (Pillow only, bundled DejaVu font, no database, no network) so it is unit-testable and looks the same on any
machine. Data assembly lives in market_context.py.

Design (dataviz skill, deep-navy "Midnight Dawn" surface):
  * 6 headline numbers  -> stat tiles: value, direction triangle + delta, 60-session sparkline (gray line, one accent end dot)
  * stocks up vs down   -> a single stacked bar, direct labels, 2px surface gaps
  * sector performance  -> diverging bars on a zero baseline, sorted, every bar labelled
  * colour = polarity only (up aqua / down coral, validated on the navy surface: colour-blind separation dE 14.5, normal-vision
    dE 27.4), ALWAYS paired with a triangle and a number, so colour never carries meaning alone. Text stays in ink tokens.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

W, H, SS = 1080, 1440, 2                    # output size and supersampling factor (drawn at 2x, downsampled for smooth edges)
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# "Midnight Dawn" palette: deep-navy surface, cool aqua = up, warm coral = down (opposite poles, so polarity survives on a blue
# background). Up/down validated with the dataviz skill's validate_palette.js on this surface: lightness band, chroma, colour-blind
# separation dE 14.5 (target 8), normal-vision dE 27.4 (floor 15), contrast 5.9:1 / 5.1:1. Text contrast on the surface: ink 16:1,
# secondary 10:1, muted 6.5:1. test_market_card.py re-checks the contrast so a later tweak cannot silently break it.
SURFACE, TILE = "#0a1a3c", "#0f2452"
INK, INK2, MUTED = "#f4f8ff", "#b9c8e8", "#8aa0cc"
HAIRLINE, NEUTRAL = "#1d3a72", "#3b5691"
UP, DOWN = "#1ea5c4", "#e5626b"

MARGIN = 48


@dataclass
class Tile:
    label: str
    value: str                              # "7,650.50" or "n/a"
    delta: str = ""                         # "0.17%" / "5.1 bps" (no sign: the triangle carries direction)
    direction: int = 0                      # +1 up, -1 down, 0 flat / unknown
    spark: Sequence[float] = field(default_factory=list)


@dataclass
class SectorBar:
    name: str
    change_pct: float


@dataclass
class CardData:
    session: date
    universe_n: int
    up_n: int
    down_n: int
    tiles: List[Tile]
    sectors: List[SectorBar]
    unclassified_n: int = 0


@lru_cache(maxsize=None)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size * SS)


class _Canvas:
    """Thin wrapper: every coordinate is in output pixels; the wrapper scales by SS."""

    def __init__(self):
        self.img = Image.new("RGB", (W * SS, H * SS), SURFACE)
        self.d = ImageDraw.Draw(self.img)

    def text(self, x, y, s, size, fill=INK, bold=False, anchor="ls"):
        self.d.text((x * SS, y * SS), s, font=_font(size, bold), fill=fill, anchor=anchor)

    def text_width(self, s, size, bold=False) -> float:
        return self.d.textlength(s, font=_font(size, bold)) / SS

    def rrect(self, box, radius, fill=None, outline=None, width=1, corners=None):
        x0, y0, x1, y1 = (v * SS for v in box)
        # A radius larger than half the shape (a 0.06% sector bar, a 1-stock breadth segment) raises in Pillow < 11: clamp it,
        # and draw a plain rectangle when there is no room for a corner at all.
        r = min(radius * SS, (x1 - x0) / 2 - 1, (y1 - y0) / 2 - 1)
        if r < 1:
            self.d.rectangle((x0, y0, x1, y1), fill=fill, outline=outline, width=width * SS)
        else:
            self.d.rounded_rectangle((x0, y0, x1, y1), r, fill=fill, outline=outline, width=width * SS, corners=corners)

    def line(self, pts: Sequence[Tuple[float, float]], fill, width=2):
        self.d.line([(x * SS, y * SS) for x, y in pts], fill=fill, width=int(width * SS), joint="curve")

    def dot(self, cx, cy, r, fill, ring=None, ring_w=2):
        if ring:
            self.d.ellipse(((cx - r - ring_w) * SS, (cy - r - ring_w) * SS, (cx + r + ring_w) * SS, (cy + r + ring_w) * SS), fill=ring)
        self.d.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=fill)

    def triangle(self, cx, cy, size, direction: int):
        """Direction mark drawn as a shape (font independent): up / down triangle, or a short dash for flat."""
        if direction == 0:
            self.line([(cx - size / 2, cy), (cx + size / 2, cy)], MUTED, 3)
            return
        h = size * 0.9
        tip, base = (cy - h / 2, cy + h / 2) if direction > 0 else (cy + h / 2, cy - h / 2)
        pts = [(cx, tip), (cx - size / 2, base), (cx + size / 2, base)]
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=UP if direction > 0 else DOWN)

    def png(self) -> bytes:
        out = self.img.resize((W, H), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _direction_delta(c: _Canvas, x, y, direction, text, size=26, fill=INK):
    """▲/▼ shape + number, left aligned at x (baseline y)."""
    c.triangle(x + 8, y - size * 0.34, 15, direction)
    c.text(x + 24, y, text, size, fill)


def _spark(c: _Canvas, box, values: Sequence[float], direction: int):
    """Gray 2px line with one accent end dot. Nothing is drawn for fewer than 2 points (no fabricated trend)."""
    if len(values) < 2:
        return
    x0, y0, x1, y1 = box
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = (y1 - y0) * 0.10
    pts = [(x0 + (x1 - x0) * i / (len(values) - 1), y1 - pad - (v - lo) / span * (y1 - y0 - 2 * pad)) for i, v in enumerate(values)]
    c.line(pts, MUTED, 2)
    c.dot(pts[-1][0], pts[-1][1], 5, UP if direction > 0 else DOWN if direction < 0 else MUTED, ring=TILE, ring_w=2)


def _tile(c: _Canvas, x, y, w, h, t: Tile):
    c.rrect((x, y, x + w, y + h), 14, fill=TILE, outline=HAIRLINE, width=2)
    c.text(x + 22, y + 36, t.label, 24, INK2)
    c.text(x + 22, y + 84, t.value, 42, INK if t.value != "n/a" else MUTED, bold=True)
    if t.delta:
        _direction_delta(c, x + 22, y + 122, t.direction, t.delta)
    _spark(c, (x + w - 22 - 176, y + 28, x + w - 22, y + h - 28), t.spark, t.direction)


def _breadth(c: _Canvas, y, up_n: int, down_n: int, universe_n: int):
    total = max(universe_n, up_n + down_n, 1)
    flat_n = max(total - up_n - down_n, 0)
    c.text(MARGIN, y, "Stocks up vs down today", 30, INK, bold=True)
    c.text(W - MARGIN, y, f"of {_fmt_int(universe_n)} liquid US stocks", 22, MUTED, anchor="rs")
    ly = y + 46
    c.triangle(MARGIN + 8, ly - 9, 15, +1)
    c.text(MARGIN + 24, ly, f"{_fmt_int(up_n)} up · {up_n / total * 100:.0f}%", 26)
    c.text(W - MARGIN - 24, ly, f"{down_n / total * 100:.0f}% · {_fmt_int(down_n)} down", 26, anchor="rs")
    c.triangle(W - MARGIN - 8, ly - 9, 15, -1)
    by, bh, x0, x1, gap = ly + 20, 40, MARGIN, W - MARGIN, 2
    usable = (x1 - x0) - 2 * gap
    segs = [(up_n, UP), (flat_n, NEUTRAL), (down_n, DOWN)]
    widths = [max(usable * n / total, 6) if n else 0 for n, _ in segs]
    scale = usable / (sum(widths) or 1)
    x = x0
    for i, ((n, col), wd) in enumerate(zip(segs, widths)):
        if not n:
            continue
        wd *= scale
        corners = (i == 0 or x == x0, x + wd >= x1 - 1, x + wd >= x1 - 1, i == 0 or x == x0)
        c.rrect((x, by, x + wd, by + bh), 4, fill=col, corners=corners)
        x += wd + gap
    if flat_n:
        c.text(W // 2, by + bh + 32, f"{_fmt_int(flat_n)} unchanged", 22, MUTED, anchor="ms")


def _sectors(c: _Canvas, y, sectors: List[SectorBar], unclassified_n: int):
    c.text(MARGIN, y, "Average 1-day change by sector", 30, INK, bold=True)
    c.text(MARGIN, y + 32, "equal-weighted across the stocks in the daily scan", 22, MUTED)
    if not sectors:
        c.text(MARGIN, y + 90, "Sector data unavailable today.", 24, MUTED)
        return
    top, row_h, bar_h = y + 66, 32, 16
    label_x, zero_x, half, val_right = MARGIN, 640, 250, W - MARGIN
    biggest = max(abs(s.change_pct) for s in sectors) or 1.0
    k = half / biggest
    c.line([(zero_x, top - 6), (zero_x, top + row_h * len(sectors) + 2)], NEUTRAL, 2)
    for i, s in enumerate(sectors):
        cy = top + i * row_h + row_h / 2
        c.line([(MARGIN, cy + row_h / 2), (W - MARGIN, cy + row_h / 2)], HAIRLINE, 1)       # recessive row guide
        c.text(label_x, cy + 8, s.name, 24, INK2)
        length = abs(s.change_pct) * k
        direction = 1 if s.change_pct > 0 else -1 if s.change_pct < 0 else 0
        if direction:
            if direction > 0:
                box, corners = (zero_x, cy - bar_h / 2, zero_x + max(length, 4), cy + bar_h / 2), (False, True, True, False)
            else:
                box, corners = (zero_x - max(length, 4), cy - bar_h / 2, zero_x, cy + bar_h / 2), (True, False, False, True)
            c.rrect(box, 4, fill=UP if direction > 0 else DOWN, corners=corners)
        vx = val_right - c.text_width(f"{abs(s.change_pct):.2f}%", 24)
        c.triangle(vx - 22, cy + 0, 15, direction)
        c.text(val_right, cy + 8, f"{abs(s.change_pct):.2f}%", 24, INK, anchor="rs")
    if unclassified_n:
        c.text(MARGIN, top + row_h * len(sectors) + 34, f"{_fmt_int(unclassified_n)} stocks without a sector are not shown.", 20, MUTED)


def render_market_card(data: CardData) -> bytes:
    c = _Canvas()
    c.text(MARGIN, 82, "FIRST LIGHT", 26, INK2, bold=True)
    c.text(MARGIN, 148, f"US close · {data.session:%a %d %b}", 60, INK, bold=True)
    c.text(MARGIN, 192, "Market performance · daily long-side scan · educational data", 24, MUTED)

    tile_w, tile_h, gap = (W - 2 * MARGIN - 24) // 2, 140, 16
    ty = 232
    for i, t in enumerate(data.tiles[:6]):
        col, row = i % 2, i // 2
        _tile(c, MARGIN + col * (tile_w + 24), ty + row * (tile_h + gap), tile_w, tile_h, t)

    _breadth(c, 742, data.up_n, data.down_n, data.universe_n)
    _sectors(c, 936, data.sectors, data.unclassified_n)
    c.text(MARGIN, H - 30, "Educational information from public price data. Not investment advice.", 22, MUTED)
    return c.png()
