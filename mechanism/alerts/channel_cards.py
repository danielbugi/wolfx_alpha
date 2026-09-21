# mechanism/alerts/channel_cards.py
"""
Extra PNG cards for the channel (CHANNEL_CONTENT_REPORT_2026-09-21.md P1-P3), drawn with the market card's canvas, palette and fonts
("Midnight Dawn", Pillow only, no database, no network -> unit-testable):

  render_health_card    market health: four stat tiles + a two-line chart (share of stocks above their 50-day / 200-day average)
  render_sector_card    sector rotation: median 20-session change per sector, diverging bars on a zero baseline
  render_macro_card     gold / crude / dollar index / Bitcoin tiles with 60-session sparklines

Colour rules (dataviz skill): up / down keep the card's aqua / coral and always come with a triangle and a number. The two chart lines use a
categorical pair validated on this surface with validate_palette.js (dark mode, surface #0a1a3c: lightness band, chroma, colour-blind
separation dE 27.8, normal-vision dE 29.3, contrast >= 3:1): blue #4f86e8 = 50-day, amber #bf8514 = 200-day. Text stays in ink tokens; both
lines are named twice (legend row above, label at the line end), so identity never rests on colour alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Sequence

from alerts.market_card import HAIRLINE, INK, INK2, MARGIN, MUTED, NEUTRAL, SURFACE, W, SectorBar, Tile, _Canvas, _tile

SERIES_50, SERIES_200 = "#4f86e8", "#bf8514"
DISCLAIMER_LINE = "Educational information from public price data. Not investment advice."
TILE_W, TILE_H, TILE_GAP = (W - 2 * MARGIN - 24) // 2, 140, 16


@dataclass
class HealthCard:
    session: date
    universe_n: int                                  # liquid stocks in the daily scan (the same number as the market card)
    n50: int                                         # stocks counted in the 50-day / 200-day percentages / 52-week range
    n200: int
    n_range: int
    dates: Sequence[date]                            # one per point of the two series
    above50: Sequence[float]
    above200: Sequence[float]
    ref_sessions: int
    new_highs: int
    new_lows: int
    breakouts: int
    near_breakouts: int
    caption_extra: str = ""


def _pct_tile(label: str, series: Sequence[float], ref_sessions: int) -> Tile:
    now = series[-1] if len(series) else None
    if now is None or now != now:
        return Tile(label, "n/a")
    ago = series[-1 - ref_sessions] if len(series) > ref_sessions else None
    if ago is None or ago != ago:
        return Tile(label, f"{now:.0f}%", "", 0, list(series))
    diff = now - ago
    direction = 1 if diff > 0.5 else -1 if diff < -0.5 else 0
    return Tile(label, f"{now:.0f}%", f"{abs(diff):.0f} pts vs {ref_sessions} sessions ago", direction, list(series))


def health_tiles(d: HealthCard) -> List[Tile]:
    return [
        _pct_tile("Above 50-day avg.", d.above50, d.ref_sessions),
        _pct_tile("Above 200-day avg.", d.above200, d.ref_sessions),
        Tile("New 52-week highs · lows", f"{d.new_highs:,} · {d.new_lows:,}"),
        Tile("Breakouts · near breakouts", f"{d.breakouts:,} · {d.near_breakouts:,}"),
    ]


def _dashed(c: _Canvas, x0: float, x1: float, y: float, color: str, dash: int = 10, gap: int = 8):
    x = x0
    while x < x1:
        c.line([(x, y), (min(x + dash, x1), y)], color, 1)
        x += dash + gap


def _line_chart(c: _Canvas, top: float, d: HealthCard):
    c.text(MARGIN, top, "Share of stocks above their moving averages", 30, INK, bold=True)
    c.text(MARGIN, top + 32, f"last {len(d.above50)} sessions · percent of stocks", 22, MUTED)
    # legend row (always present for two series)
    lx = MARGIN
    for name, col in (("50-day average", SERIES_50), ("200-day average", SERIES_200)):
        c.rrect((lx, top + 56, lx + 26, top + 66), 3, fill=col)
        c.text(lx + 36, top + 68, name, 22, INK2)
        lx += 36 + c.text_width(name, 22) + 34
    x0, x1, y0, y1 = MARGIN + 64, W - MARGIN - 170, top + 110, top + 470
    for v in (0, 25, 50, 75, 100):
        y = y1 - v / 100 * (y1 - y0)
        if v == 50:
            _dashed(c, x0, x1, y, NEUTRAL)
        else:
            c.line([(x0, y), (x1, y)], HAIRLINE, 1)
        c.text(x0 - 12, y + 8, f"{v}%", 20, MUTED, anchor="rs")
    ends = []
    for values, col, name in ((d.above50, SERIES_50, "50-day"), (d.above200, SERIES_200, "200-day")):
        n = len(values)
        if n < 2:
            continue
        pts = [(x0 + (x1 - x0) * i / (n - 1), y1 - (v / 100) * (y1 - y0)) for i, v in enumerate(values) if v == v]
        if len(pts) < 2:
            continue
        c.line(pts, col, 3)
        c.dot(pts[-1][0], pts[-1][1], 6, col, ring=SURFACE, ring_w=2)
        ends.append([pts[-1][1], name, values[-1]])
    ends.sort()
    for i in range(1, len(ends)):                                    # two labels must not sit on top of each other
        if ends[i][0] - ends[i - 1][0] < 30:
            ends[i][0] = ends[i - 1][0] + 30
    for y, name, v in ends:
        c.text(x1 + 18, y + 8, f"{name} {v:.0f}%", 22, INK)
    if len(d.dates) >= 2:                                            # first and last date under the plot, nothing else
        c.text(x0, y1 + 34, f"{d.dates[0]:%d %b}", 20, MUTED)
        c.text(x1, y1 + 34, f"{d.dates[-1]:%d %b}", 20, MUTED, anchor="rs")
    return y1 + 34


def render_health_card(d: HealthCard) -> bytes:
    height = 1290
    c = _Canvas(height)
    c.text(MARGIN, 82, "FIRST LIGHT", 26, INK2, bold=True)
    c.text(MARGIN, 148, f"Market health · {d.session:%a %d %b}", 56, INK, bold=True)
    c.text(MARGIN, 192, f"How broad the move is · {d.universe_n:,} liquid US stocks · educational data", 24, MUTED)
    for i, t in enumerate(health_tiles(d)):
        _tile(c, MARGIN + (i % 2) * (TILE_W + 24), 232 + (i // 2) * (TILE_H + TILE_GAP), TILE_W, TILE_H, t)
    bottom = _line_chart(c, 232 + 2 * (TILE_H + TILE_GAP) + 60, d)
    c.text(MARGIN, bottom + 60, f"Averages counted for {d.n50:,} stocks (50-day) and {d.n200:,} (200-day); 52-week range for {d.n_range:,}.", 20, MUTED)
    c.text(MARGIN, bottom + 88, "Stocks with too short a history for a measure are left out of that measure.", 20, MUTED)
    c.text(MARGIN, height - 30, DISCLAIMER_LINE, 22, MUTED)
    return c.png()


def render_sector_card(session: date, sessions: int, bars: Sequence[SectorBar], unclassified_n: int, universe_n: int) -> bytes:
    from alerts.market_card import _sectors
    height = 480 + 32 * max(len(bars), 3)
    c = _Canvas(height)
    c.text(MARGIN, 82, "FIRST LIGHT", 26, INK2, bold=True)
    c.text(MARGIN, 148, f"Sector rotation · {session:%a %d %b}", 56, INK, bold=True)
    c.text(MARGIN, 192, f"Last {sessions} sessions · {universe_n:,} liquid US stocks · educational data", 24, MUTED)
    _sectors(c, 262, list(bars), unclassified_n, title=f"Median {sessions}-session change by sector",
             subtitle="the middle stock of each sector, so one outlier cannot move the bar")
    c.text(MARGIN, height - 56, "Sector tags are today's; earlier sector membership is not reconstructed.", 20, MUTED)
    c.text(MARGIN, height - 30, DISCLAIMER_LINE, 22, MUTED)
    return c.png()


def render_macro_card(session: date, tiles: Sequence[Tile]) -> bytes:
    height = 232 + 2 * (TILE_H + TILE_GAP) + 110
    c = _Canvas(height)
    c.text(MARGIN, 82, "FIRST LIGHT", 26, INK2, bold=True)
    c.text(MARGIN, 148, f"Beyond stocks · {session:%a %d %b}", 56, INK, bold=True)
    c.text(MARGIN, 192, "Gold, crude oil, dollar index and Bitcoin · daily close · educational data", 24, MUTED)
    for i, t in enumerate(list(tiles)[:4]):
        _tile(c, MARGIN + (i % 2) * (TILE_W + 24), 232 + (i // 2) * (TILE_H + TILE_GAP), TILE_W, TILE_H, t)
    c.text(MARGIN, height - 56, "Gold and crude are front-month futures. Bitcoin trades every day.", 20, MUTED)
    c.text(MARGIN, height - 30, DISCLAIMER_LINE, 22, MUTED)
    return c.png()
