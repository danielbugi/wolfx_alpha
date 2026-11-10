#!/usr/bin/env python3
"""Fundamental Updates card: one landscape PNG (1200x675, 16:9, shown full-width by Telegram) rendered from a PublicUpdate only.

Pure drawing (Pillow, the bundled DejaVu fonts of mechanism/alerts/assets, no database, no network), deterministic for the same input.
Its own visual identity, so it is never confused with the Market Daily card (deep navy, aqua/coral): a dark teal-graphite surface with ONE
amber accent (the left rule, the label and the header mark). Colour carries no meaning on its own: every figure is a number with words.
Text contrast on the surface and on the tiles is checked in tests (WCAG >= 4.5:1 for every text token).
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from inflection.update_model import PublicUpdate, fmt_date

W, H, SS = 1200, 675, 2
FONT_DIR = Path(__file__).resolve().parents[1] / "alerts" / "assets" / "fonts"

SURFACE, TILE, HAIRLINE = "#0e1c1f", "#15292d", "#24403f"
INK, INK2, MUTED = "#f2f6f5", "#c3d2cf", "#93a9a5"
ACCENT = "#f0b44c"
TEXT_TOKENS = {"ink": INK, "ink2": INK2, "muted": MUTED, "accent": ACCENT}
M = 56


@lru_cache(maxsize=None)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size * SS)


class _Canvas:
    def __init__(self):
        self.img = Image.new("RGB", (W * SS, H * SS), SURFACE)
        self.d = ImageDraw.Draw(self.img)

    def width(self, s: str, size: int, bold: bool = False) -> float:
        return self.d.textlength(s, font=_font(size, bold)) / SS

    def text(self, x, y, s, size, fill=INK, bold=False, anchor="ls"):
        self.d.text((x * SS, y * SS), s, font=_font(size, bold), fill=fill, anchor=anchor)

    def fit(self, s: str, size: int, max_w: float, bold: bool = False, min_size: int = 14) -> tuple:
        """Shrink to fit, then ellipsise: (text, size)."""
        while size > min_size and self.width(s, size, bold) > max_w:
            size -= 1
        if self.width(s, size, bold) <= max_w:
            return s, size
        while s and self.width(s + "…", size, bold) > max_w:
            s = s[:-1]
        return s.rstrip() + "…", size

    def wrap(self, s: str, size: int, max_w: float, lines: int = 2) -> list:
        """Greedy word wrap into at most `lines` lines; the last line is ellipsised if text remains."""
        words, out = s.split(), []
        while words and len(out) < lines:
            line = words.pop(0)
            while words and self.width(line + " " + words[0], size) <= max_w:
                line += " " + words.pop(0)
            out.append(line)
        if words:
            out[-1] = self.fit(out[-1] + " " + " ".join(words), size, max_w, min_size=size)[0]
        return [self.fit(x, size, max_w, min_size=size)[0] for x in out]

    def rect(self, box, fill, radius=0):
        x0, y0, x1, y1 = (v * SS for v in box)
        if radius:
            self.d.rounded_rectangle((x0, y0, x1, y1), radius * SS, fill=fill)
        else:
            self.d.rectangle((x0, y0, x1, y1), fill=fill)

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.img.resize((W, H), Image.LANCZOS).save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def _context_line(u: PublicUpdate) -> str:
    parts = [x for x in (u.sector, u.industry) if x]
    if u.market_cap:
        parts.append(f"Market cap {u.market_cap['value']} ({fmt_date(u.market_cap['as_of'])})")
    return " · ".join(parts)


def render_update_card(u: PublicUpdate, site: Optional[str] = "first-light.finance") -> bytes:
    c = _Canvas()
    c.rect((0, 0, 10, H), ACCENT)                                                     # the identity rule
    c.text(M, 64, "FIRST LIGHT", 18, MUTED, bold=True)
    c.text(M + c.width("FIRST LIGHT", 18, True) + 14, 64, "FUNDAMENTAL UPDATE", 18, ACCENT, bold=True)
    c.text(W - M, 64, u.fiscal_label, 18, MUTED, anchor="rs")

    sym_size = 64
    c.text(M, 150, u.symbol, sym_size, INK, bold=True)
    x_name = M + c.width(u.symbol, sym_size, True) + 22
    if u.company_name:
        name, size = c.fit(u.company_name, 28, W - M - x_name)
        c.text(x_name, 146, name, size, INK2)
    label, size = c.fit(u.label, 34, W - 2 * M, bold=True)
    c.text(M, 208, label, size, ACCENT, bold=True)
    ctx = _context_line(u)
    if ctx:
        ctx, size = c.fit(ctx, 20, W - 2 * M)
        c.text(M, 246, ctx, size, MUTED)

    n = len(u.figures)
    gap, top, bottom = 20, 290, 520
    tile_w = (W - 2 * M - gap * (n - 1)) / n
    for i, f in enumerate(u.figures):
        x0 = M + i * (tile_w + gap)
        c.rect((x0, top, x0 + tile_w, bottom), TILE, radius=14)
        pad = 24
        for j, lab in enumerate(c.wrap(f.label, 19, tile_w - 2 * pad)):
            c.text(x0 + pad, top + 46 + j * 25, lab, 19, INK2)
        val, size = c.fit(f.value, 54, tile_w - 2 * pad, bold=True, min_size=28)
        c.text(x0 + pad, top + 152, val, size, INK, bold=True)
        if f.compare:
            cmp_, size = c.fit(f.compare, 19, tile_w - 2 * pad)
            c.text(x0 + pad, top + 200, cmp_, size, MUTED)

    c.rect((M, 566, W - M, 567), HAIRLINE)
    foot = f"Public {fmt_date(u.public_date)} · {u.date_source}"
    foot, size = c.fit(foot, 19, W - 2 * M - 260)
    c.text(M, 612, foot, size, INK2)
    if site:
        c.text(W - M, 612, site, 19, MUTED, anchor="rs")
    return c.png()
