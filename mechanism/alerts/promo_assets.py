#!/usr/bin/env python3
# mechanism/alerts/promo_assets.py
"""
Promotion images for the First Light assistant, drawn with Pillow in the SAME "Midnight Dawn" look as the market card (palette, bundled
DejaVu font), so the channel, the daily card and the bot share one brand.

    python mechanism/alerts/promo_assets.py [--out reports/first_light/promo]

Writes
  first_light_assistant_promo.png   1080 x 1350 (4:5)  a channel / story post: "Your private stock assistant" + 3 feature panels + CTA
  first_light_bot_avatar.png         640 x  640         the ASSISTANT's profile picture (BotFather Edit Botpic): the mark in a chat bubble
  first_light_channel_logo.png       640 x  640         the CHANNEL's photo: the First Light mark alone
Nothing is sent anywhere. Rules for the copy (a public asset): educational tone, no advice words, no performance claims, the disclaimer is
on the image, and every number is labelled as an example. The wording guard test scans PROMO_COPY.
"""
from __future__ import annotations

import argparse
import io
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))               # `alerts` package when run as a script

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from alerts.market_card import (DOWN, FONT_DIR, HAIRLINE, INK, INK2, MUTED, NEUTRAL, SURFACE, TILE, UP)

SS = 2                                             # drawn at 2x and downsampled for smooth edges
GLOW = "#1b4a86"                                   # a lighter navy used only for soft background light

PROMO_COPY = {                                     # every string on the promo image (scanned by the wording-guard test)
    "brand": "FIRST LIGHT", "brand_sub": "Stocks & Info",
    "headline_1": "Your private", "headline_2": "stock assistant",
    "sub": "Built on the daily First Light scan - in one chat.",
    "f1_title": "Today's lists", "f1_body": "The channel's lists, every stock once.",
    "f2_title": "Stock cards", "f2_body": "Numbers, news and a chart in one tap.",
    "f3_title": "Since you added it", "f3_body": "Follow a stock from the day you add it.",
    "cta": "Access on request  ·  seats limited", "cta_sub": "Tap \"Request access\" under today's post",
    "footer": "Example screens.",
}


@lru_cache(maxsize=None)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size * SS)


class Canvas:
    """Every coordinate is in output pixels; the wrapper scales by SS."""

    def __init__(self, w: int, h: int, bg: str = SURFACE):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * SS, h * SS), bg)
        self.d = ImageDraw.Draw(self.img)

    def text(self, x, y, s, size, fill=INK, bold=False, anchor="ls", tracking: float = 0):
        if not tracking:
            self.d.text((x * SS, y * SS), s, font=_font(size, bold), fill=fill, anchor=anchor)
            return
        total = sum(self.text_width(ch, size, bold) + tracking for ch in s) - tracking
        cx = x - (total if anchor[0] == "r" else total / 2 if anchor[0] == "m" else 0)
        for ch in s:
            self.d.text((cx * SS, y * SS), ch, font=_font(size, bold), fill=fill, anchor="l" + anchor[1])
            cx += self.text_width(ch, size, bold) + tracking

    def text_width(self, s, size, bold=False) -> float:
        return self.d.textlength(s, font=_font(size, bold)) / SS

    def rrect(self, box, radius, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = (v * SS for v in box)
        r = max(1, min(radius * SS, (x1 - x0) / 2 - 1, (y1 - y0) / 2 - 1))
        self.d.rounded_rectangle((x0, y0, x1, y1), r, fill=fill, outline=outline, width=int(width * SS))

    def line(self, pts: Sequence[Tuple[float, float]], fill, width=2):
        self.d.line([(x * SS, y * SS) for x, y in pts], fill=fill, width=int(width * SS), joint="curve")

    def dot(self, cx, cy, r, fill):
        self.d.ellipse(((cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS), fill=fill)

    def polygon(self, pts, fill):
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill)

    def triangle(self, cx, cy, size, up=True, fill=None):
        h = size * 0.9
        tip, base = (cy - h / 2, cy + h / 2) if up else (cy + h / 2, cy - h / 2)
        self.polygon([(cx, tip), (cx - size / 2, base), (cx + size / 2, base)], fill or (UP if up else DOWN))

    def glow(self, cx, cy, radius, color, strength=0.5):
        """A soft radial light: full colour at the centre fading to nothing at `radius` (smooth-step falloff)."""
        yy, xx = np.mgrid[0:self.h * SS, 0:self.w * SS]
        dist = np.sqrt((xx - cx * SS) ** 2 + (yy - cy * SS) ** 2) / (radius * SS)
        a = np.clip(1 - dist, 0, 1)
        a = (a * a * (3 - 2 * a)) * strength
        mask = Image.fromarray((a * 255).astype("uint8"))
        self.img.paste(Image.new("RGB", self.img.size, color), (0, 0), mask)
        self.d = ImageDraw.Draw(self.img)

    def vgradient(self, box, top, bottom):
        x0, y0, x1, y1 = (int(v * SS) for v in box)
        t = np.linspace(0, 1, y1 - y0)[:, None, None]
        c0, c1 = np.array(_rgb(top))[None, None, :], np.array(_rgb(bottom))[None, None, :]
        band = (c0 * (1 - t) + c1 * t).astype("uint8")
        self.img.paste(Image.fromarray(np.repeat(band, x1 - x0, axis=1)), (x0, y0))
        self.d = ImageDraw.Draw(self.img)

    def png(self, size: Tuple[int, int] = None) -> bytes:
        out = self.img.resize(size or (self.w, self.h), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def _rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours."""
    def lum(c):
        r, g, b = (v / 255 for v in _rgb(c))
        f = lambda u: u / 12.92 if u <= 0.03928 else ((u + 0.055) / 1.055) ** 2.4          # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# ================================================================== the mark (sunrise over a rising bar chart)
def draw_mark(c: Canvas, cx: float, cy: float, size: float) -> None:
    """The First Light mark: a big sun rising behind five short rising bars, with dawn rays and a horizon hairline.
    `size` = width of the mark; (cx, cy) = the centre of the whole drawing (so it sits centred in a circular crop)."""
    r = size * 0.34
    base_y = cy + size * 0.13                                             # horizon
    c.glow(cx, base_y - r * 0.5, size * 0.85, "#e5626b", 0.34)             # warm dawn light behind the sun
    # dawn rays: short strokes fanned over the top half, fading toward the background
    for k in range(11):
        ang = math.radians(198 + k * (144 / 10))
        r0, r1 = r + size * 0.05, r + size * (0.11 if k % 2 == 0 else 0.085)
        col = _mix("#ffb08f", SURFACE, 0.42)
        c.line([(cx + r0 * math.cos(ang), base_y + r0 * math.sin(ang)), (cx + r1 * math.cos(ang), base_y + r1 * math.sin(ang))], col, max(3, size * 0.012))
    # sun: a half disc, coral at the rim fading lighter toward the horizon
    for i in range(int(r * 2), 0, -2):
        t = i / (r * 2)
        col = _mix("#ffb08f", "#e5626b", t)
        c.d.pieslice(((cx - i / 2) * SS, (base_y - i / 2) * SS, (cx + i / 2) * SS, (base_y + i / 2) * SS), 180, 360, fill=col)
    c.line([(cx - size * 0.46, base_y), (cx + size * 0.46, base_y)], NEUTRAL, max(2, size * 0.006))
    # five rising bars in the foreground (the chart): aqua, rounded tops, all lower than the sun so the sunrise reads first
    n, bw, gap = 5, size * 0.085, size * 0.04
    x0 = cx - (n * bw + (n - 1) * gap) / 2
    for k in range(n):
        h = size * (0.05 + 0.032 * k)
        x = x0 + k * (bw + gap)
        c.rrect((x, base_y - h, x + bw, base_y + size * 0.13), bw * 0.3, fill=UP)


def _mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(int(ra[i] * (1 - t) + rb[i] * t) for i in range(3))


def render_channel_logo(size: int = 640) -> bytes:
    """The channel's profile picture: the First Light mark alone. Telegram shows it in a circle, so everything important sits inside
    the central 70%."""
    c = Canvas(size, size)
    c.glow(size * 0.5, size * 0.55, size * 0.75, GLOW, 0.85)
    draw_mark(c, size * 0.5, size * 0.5, size * 0.66)
    return c.png()


def render_bot_avatar(size: int = 640) -> bytes:
    """The assistant's profile picture: the same mark inside a chat bubble with 'typing' dots, so it reads as "the assistant" and is
    told apart from the channel logo at a glance. Circle-safe (the bubble stays inside the central ~72%)."""
    c = Canvas(size, size)
    c.glow(size * 0.5, size * 0.55, size * 0.75, GLOW, 0.85)
    x0, y0, x1, y1 = size * 0.17, size * 0.15, size * 0.83, size * 0.70
    c.rrect((x0, y0, x1, y1), size * 0.13, fill=TILE, outline=NEUTRAL, width=max(3, size * 0.007))
    tail = [(x0 + size * 0.07, y1 - 2), (x0 + size * 0.20, y1 - 2), (x0 + size * 0.05, y1 + size * 0.13)]      # the bubble's tail
    c.polygon(tail, NEUTRAL)
    inner = [(x + (1.5 if k != 2 else 3.5) * (1 if k == 2 else 1), y - (1 if k != 2 else 3)) for k, (x, y) in enumerate(tail)]
    c.polygon([(tail[0][0] + 4, tail[0][1] - 2), (tail[1][0] - 3, tail[1][1] - 2), (tail[2][0] + 3, tail[2][1] - 6)], TILE)
    draw_mark(c, size * 0.5, size * 0.385, size * 0.44)
    for k, col in enumerate((INK2, INK2, MUTED)):                                                            # typing dots
        c.dot(size * 0.5 + (k - 1) * size * 0.065, size * 0.635, size * 0.018, col)
    return c.png()


# ================================================================== the promo post
W, H = 1080, 1350
M = 64                                                                  # side margin
PX = 520                                                                # left edge of the feature panels
ROW_Y = (474, 708, 942)                                                 # top of each feature row
ROW_H = 212


def _panel(c: Canvas, x, y, w, h):
    c.rrect((x, y, x + w, y + h), 22, fill=TILE, outline=HAIRLINE, width=2)


def _candles(c: Canvas, x, y, w, h):
    """A small ILLUSTRATIVE candle series (hand-shaped, not market data), scaled to its box; direction is shape as well as colour."""
    series = ((1.0, 1.5), (1.5, 1.2), (1.2, 1.9), (1.9, 2.4), (2.4, 2.1), (2.1, 2.8), (2.8, 3.3), (3.3, 3.0), (3.0, 3.7), (3.7, 4.2))   # (open, close)
    lo, hi = min(min(o, cl) for o, cl in series) - 0.35, max(max(o, cl) for o, cl in series) + 0.35
    ty = lambda v: y + h - (v - lo) / (hi - lo) * h                              # noqa: E731
    step = w / len(series)
    for i, (o, cl) in enumerate(series):
        px, col = x + step * i + step / 2, UP if cl >= o else DOWN
        c.line([(px, ty(min(o, cl) - 0.3)), (px, ty(max(o, cl) + 0.3))], col, 2)
        c.rrect((px - step * 0.26, ty(max(o, cl)), px + step * 0.26, ty(min(o, cl))), 2, fill=col)


def render_promo() -> bytes:
    c = Canvas(W, H)
    c.vgradient((0, 0, W, H), "#0d2150", SURFACE)
    c.glow(W * 0.86, 150, 560, GLOW, 0.9)
    c.glow(W * 0.10, H * 0.98, 520, "#3a2a55", 0.35)

    # brand row
    draw_mark(c, M + 46, 96, 104)
    c.text(M + 116, 90, PROMO_COPY["brand"], 34, INK, True, tracking=5)
    c.text(M + 116, 124, PROMO_COPY["brand_sub"], 24, MUTED)

    # headline
    c.text(M, 250, PROMO_COPY["headline_1"], 88, INK, True)
    c.text(M, 340, PROMO_COPY["headline_2"], 88, INK, True)
    c.rrect((M, 364, M + 150, 372), 4, fill=DOWN)
    c.rrect((M + 158, 364, M + 232, 372), 4, fill=UP)
    c.text(M, 424, PROMO_COPY["sub"], 28, INK2)

    pw = W - PX - M                                                     # feature panels (right column)
    rows = (("f1_title", "f1_body", _panel_lists), ("f2_title", "f2_body", _panel_card), ("f3_title", "f3_body", _panel_since))
    for i, (ty, (tk, bk, panel)) in enumerate(zip(ROW_Y, rows)):
        c.dot(M + 22, ty + 42, 22, HAIRLINE)
        c.text(M + 22, ty + 42, str(i + 1), 26, INK, True, anchor="mm")
        c.text(M + 62, ty + 54, PROMO_COPY[tk], 34, INK, True)
        for j, part in enumerate(_wrap(PROMO_COPY[bk], 24)):
            c.text(M + 62, ty + 98 + j * 32, part, 24, INK2)
        _panel(c, PX, ty, pw, ROW_H)
        panel(c, PX, ty, pw, ROW_H)

    # call to action
    cy = 1234
    c.rrect((M, cy - 50, W - M, cy + 50), 50, fill=INK)
    c.text(W / 2, cy - 13, PROMO_COPY["cta"], 34, SURFACE, True, anchor="mm")
    c.text(W / 2, cy + 25, PROMO_COPY["cta_sub"], 21, "#2b3f72", anchor="mm")
    c.text(W / 2, 1320, PROMO_COPY["footer"], 18, MUTED, anchor="mm")
    return c.png()


def _wrap(s: str, width: int):
    words, line, out = s.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    return out + [line]


def _panel_lists(c: Canvas, x, y, w, h):
    c.text(x + 24, y + 36, "Today's lists", 20, MUTED, True)
    c.text(x + w - 24, y + 36, "example", 18, MUTED, anchor="rs")
    rows = (("MSTR", "★", 16.4, "$153.92", "gainers #3 · ATR #3"), ("COIN", "★", 11.7, "$194.25", "gainers #3 · ATR #4"),
            ("NBN", "", 1.1, "$133.44", "ATR #5"))
    for k, (sym, star, pct, price, sub) in enumerate(rows):
        yy = y + 80 + k * 52
        c.text(x + 24, yy, sym, 27, INK, True)
        if star:
            c.text(x + 24 + c.text_width(sym, 27, True) + 10, yy, star, 24, INK2)
        c.triangle(x + 208, yy - 9, 14, True)
        c.text(x + 222, yy, f"{pct:.1f}%", 25, INK)
        c.text(x + w - 24, yy, price, 25, INK2, anchor="rs")
        c.text(x + 24, yy + 20, sub, 16, MUTED)


def _panel_card(c: Canvas, x, y, w, h):
    c.text(x + 24, y + 42, "MSTR", 29, INK, True)
    c.text(x + 24 + c.text_width("MSTR", 29, True) + 12, y + 42, "· Breakout", 20, INK2)
    line = "Close $153.92 · day"
    c.text(x + 24, y + 76, line, 20, INK2)
    c.triangle(x + 24 + c.text_width(line, 20) + 16, y + 69, 12, True)
    c.text(x + 24 + c.text_width(line, 20) + 28, y + 76, "16.4%", 20, INK2)
    _candles(c, x + 24, y + 90, w - 48, 58)
    bx = x + 24
    for label in ("News", "Chart", "ATR levels"):
        bw = c.text_width(label, 21, True) + 34
        c.rrect((bx, y + h - 58, bx + bw, y + h - 18), 20, fill=HAIRLINE, outline=NEUTRAL, width=2)
        c.text(bx + bw / 2, y + h - 38, label, 21, INK, True, anchor="mm")
        bx += bw + 12


def _panel_since(c: Canvas, x, y, w, h):
    c.text(x + 24, y + 36, "In your portfolio", 20, MUTED, True)
    c.text(x + w - 24, y + 36, "example", 18, MUTED, anchor="rs")
    c.triangle(x + 40, y + 78, 26, True)
    c.text(x + 62, y + 96, "9.9%", 54, INK, True)
    c.text(x + 24, y + 128, "since Mon 14 Sep", 21, INK2)
    c.text(x + 24, y + 156, "10 sh · your price $140.00 · now $153.92", 19, INK2)
    c.text(x + 24, y + 190, "Value $1,539.20 (+$139.20)", 23, INK, True)
    # a small line with the user's price as a dashed reference (illustrative), top right
    lx, ly, lw, lh = x + w - 190, y + 52, 166, 56
    pts = [(lx + lw * t, ly + lh * (1 - v)) for t, v in ((0, .15), (.2, .12), (.4, .30), (.55, .26), (.7, .55), (.85, .62), (1, .95))]
    ref = ly + lh * (1 - 0.15)
    for k in range(0, int(lw), 12):
        c.line([(lx + k, ref), (lx + min(k + 6, lw), ref)], MUTED, 2)
    c.line(pts, UP, 4)
    c.dot(pts[-1][0], pts[-1][1], 6, UP)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the First Light assistant promo images")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "reports" / "first_light" / "promo"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("first_light_assistant_promo.png", render_promo()), ("first_light_channel_logo.png", render_channel_logo()),
                       ("first_light_bot_avatar.png", render_bot_avatar())):
        (out / name).write_bytes(data)
        print(f"{out / name}  ({len(data) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
