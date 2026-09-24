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

from alerts.market_card import (DOWN, HAIRLINE, INK, INK2, MARGIN, MUTED, NEUTRAL, SURFACE, TILE, UP, W, SectorBar, Tile, _Canvas,
                                _direction_delta, _tile)
from alerts.message_format import fmt_price

SERIES_50, SERIES_200 = "#4f86e8", "#bf8514"
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
    height = 1240
    c = _Canvas(height)
    c.text(MARGIN, 82, "FIRST LIGHT", 26, INK2, bold=True)
    c.text(MARGIN, 148, f"Market health · {d.session:%a %d %b}", 56, INK, bold=True)
    c.text(MARGIN, 192, f"How broad the move is · {d.universe_n:,} liquid US stocks · educational data", 24, MUTED)
    for i, t in enumerate(health_tiles(d)):
        _tile(c, MARGIN + (i % 2) * (TILE_W + 24), 232 + (i // 2) * (TILE_H + TILE_GAP), TILE_W, TILE_H, t)
    bottom = _line_chart(c, 232 + 2 * (TILE_H + TILE_GAP) + 60, d)
    c.text(MARGIN, bottom + 60, f"Averages counted for {d.n50:,} stocks (50-day) and {d.n200:,} (200-day); 52-week range for {d.n_range:,}.", 20, MUTED)
    return c.png()


def render_sector_card(session: date, sessions: int, bars: Sequence[SectorBar], unclassified_n: int, universe_n: int) -> bytes:
    from alerts.market_card import _sectors
    height = 430 + 32 * max(len(bars), 3)
    c = _Canvas(height)
    c.text(MARGIN, 82, "FIRST LIGHT", 26, INK2, bold=True)
    c.text(MARGIN, 148, f"Sectors · {session:%a %d %b}", 56, INK, bold=True)          # "rotation" would be wrong on a day when every bar points the same way
    c.text(MARGIN, 192, f"Last {sessions} sessions · {universe_n:,} liquid US stocks · educational data", 24, MUTED)
    _sectors(c, 262, list(bars), unclassified_n, title=f"Median {sessions}-session change by sector",
             subtitle="the middle stock of each sector, so one outlier cannot move the bar")
    return c.png()


def render_macro_card(session: date, tiles: Sequence[Tile]) -> bytes:
    """Only tiles that have a value are drawn (a missing one is left out, not shown as "n/a"); one or two tiles make a one-row card."""
    tiles = [t for t in tiles if t.value != "n/a"][:4]
    rows = max(1, (len(tiles) + 1) // 2)
    height = 232 + rows * (TILE_H + TILE_GAP) + 50
    c = _Canvas(height)
    c.text(MARGIN, 82, "FIRST LIGHT", 26, INK2, bold=True)
    c.text(MARGIN, 148, f"Beyond stocks · {session:%a %d %b}", 56, INK, bold=True)
    c.text(MARGIN, 192, "Gold, crude oil, dollar index and Bitcoin · daily close", 24, MUTED)
    for i, t in enumerate(tiles):
        _tile(c, MARGIN + (i % 2) * (TILE_W + 24), 232 + (i // 2) * (TILE_H + TILE_GAP), TILE_W, TILE_H, t)
    return c.png()


# ------------------------------------------------------------------ momentum board (the leader's chart + the runners-up)
PLACE = {1: "1st", 2: "2nd", 3: "3rd"}


def _candles(c: _Canvas, box, ohlc, dates=None, marker=None, labels: bool = True, body_w: float = 0.56) -> None:
    """Candlesticks for the last sessions: a thin wick from low to high and a body from open to close, aqua when the close is at or above the open,
    coral when below (the two colours are the card's validated up / down pair; a taller body and the wick position carry the same message without
    colour). A bar that is missing or inconsistent (None) is left as a gap, never drawn from guessed values. `marker` = a date to mark with a dashed
    vertical line ("first listed"); `labels` adds the window's high and the first / last date."""
    x0, y0, x1, y1 = box
    bars = [b for b in ohlc if b]
    if len(bars) < 2:
        return
    lo, hi = min(b[2] for b in bars), max(b[1] for b in bars)
    span = (hi - lo) or 1.0
    pad = (y1 - y0) * 0.10
    n = len(ohlc)
    slot = (x1 - x0) / n

    def ypx(v):
        return y1 - pad - (v - lo) / span * (y1 - y0 - 2 * pad)
    c.line([(x0, y1), (x1, y1)], HAIRLINE, 1)
    if labels:
        c.line([(x0, y0), (x1, y0)], HAIRLINE, 1)
        c.text(x0, y0 - 10, f"{n}-session high {fmt_price(hi)}", 20, MUTED)
        if dates:
            c.text(x0, y1 + 26, f"{dates[0]:%d %b}", 20, MUTED)
            c.text(x1, y1 + 26, f"{dates[-1]:%d %b}", 20, MUTED, anchor="rs")
    if marker is not None and dates and marker in list(dates):
        mx = x0 + slot * (list(dates).index(marker) + 0.5)
        yy = y0
        while yy < y1:
            c.line([(mx, yy), (mx, min(yy + 8, y1))], NEUTRAL, 2)
            yy += 15
        if labels:
            c.text(mx, y0 - 10, "first listed", 20, INK2, anchor="rs" if mx > x1 - 90 else "ms")
    for i, b in enumerate(ohlc):
        if not b:
            continue
        o, h, l, cl = b
        cx = x0 + slot * (i + 0.5)
        col = UP if cl >= o else DOWN
        half = max(slot * body_w / 2, 2)
        c.line([(cx, ypx(h)), (cx, ypx(l))], col, 2 if labels else 1.5)
        top, bot = ypx(max(o, cl)), ypx(min(o, cl))
        if bot - top < 3:                                        # a doji (open = close) still shows as a thin bar
            top, bot = (top + bot) / 2 - 1.5, (top + bot) / 2 + 1.5
        c.rrect((cx - half, top, cx + half, bot), 2, fill=col)


def _leader_chart(c: _Canvas, box, r: dict) -> None:
    """The leader's last sessions as candlesticks (see _candles), with the day it was first listed marked when it is inside the window."""
    _candles(c, box, r["ohlc"], r["dates"], r["first_listed"])


def _facts(r: dict, n_sessions: int) -> list:
    out = []
    if r.get("at_day_high"):
        out.append("Closed at the top of its day's range")
    if r.get("above_20d_high"):
        out.append("Closed above its prior 20-day high")
    out.append(f"In the lists on {r['listed_sessions']} of the last {n_sessions} sessions")
    if r.get("rvol") is not None:
        out.append(f"Volume {r['rvol']:.1f}× its 50-day median")
    return out


def render_board_card(b: dict) -> bytes:
    """The momentum board picture: four count tiles (the whole pool, so the podium is never shown without its denominator), the leader with its
    last-sessions chart, and up to two runners-up with sparklines. `b` is board.compute()'s dict."""
    top = b["top"]
    runners = top[1:3]
    lead_h = 430
    height = 232 + 2 * (TILE_H + TILE_GAP) + 30 + lead_h + (200 + 24 if runners else 0) + 130
    c = _Canvas(height)
    c.text(MARGIN, 82, "FIRST LIGHT", 26, INK2, bold=True)
    c.text(MARGIN, 148, f"Momentum board · {b['session']:%a %d %b}", 56, INK, bold=True)
    c.text(MARGIN, 192, f"Stocks the lists carried in the previous {b['n_sessions']} sessions · educational data", 24, MUTED)
    tiles = [Tile("Stocks measured", f"{b['n_measured']:,}"),
             Tile("Closed higher · lower", f"{b['higher']:,} · {b['lower']:,}"),
             Tile("Top of the day's range", f"{b['at_day_high']:,}"),
             Tile("Above prior 20-day high", f"{b['above_20d_high']:,}")]
    for i, t in enumerate(tiles):
        _tile(c, MARGIN + (i % 2) * (TILE_W + 24), 232 + (i // 2) * (TILE_H + TILE_GAP), TILE_W, TILE_H, t)
    y = 232 + 2 * (TILE_H + TILE_GAP) + 30
    r = top[0]
    fw = W - 2 * MARGIN
    c.rrect((MARGIN, y, MARGIN + fw, y + lead_h), 16, fill=TILE, outline=HAIRLINE, width=2)
    c.text(MARGIN + 30, y + 52, "1st place", 26, INK2, bold=True)
    c.text(MARGIN + 30, y + 130, r["symbol"], 66, INK, bold=True)
    c.text(MARGIN + 30, y + 178, fmt_price(r["close"]), 34, INK2)
    _direction_delta(c, MARGIN + 30, y + 226, 1 if r["ret1_pct"] > 0 else -1, f"{abs(r['ret1_pct']):.1f}% today", size=32)
    for k, line in enumerate(_facts(r, b["n_sessions"])):
        c.text(MARGIN + 30, y + 290 + k * 30, line, 22, INK2)
    _leader_chart(c, (MARGIN + 470, y + 70, MARGIN + fw - 34, y + lead_h - 70), r)
    y += lead_h + 24
    for i, rr in enumerate(runners):
        x = MARGIN + i * (TILE_W + 24)
        c.rrect((x, y, x + TILE_W, y + 200), 16, fill=TILE, outline=HAIRLINE, width=2)
        c.text(x + 24, y + 44, f"{PLACE[i + 2]} place", 22, INK2, bold=True)
        c.text(x + 24, y + 104, rr["symbol"], 44, INK, bold=True)
        c.text(x + 24, y + 144, fmt_price(rr["close"]), 26, INK2)
        _direction_delta(c, x + 24, y + 182, 1 if rr["ret1_pct"] > 0 else -1, f"{abs(rr['ret1_pct']):.1f}%", size=26)
        _candles(c, (x + TILE_W - 24 - 190, y + 34, x + TILE_W - 24, y + 166), rr["ohlc"], labels=False, body_w=0.6)
    y = height - 76
    left_out = (f"{b['n_excluded']:,} of {b['n_pool']:,} stocks left out: a price adjustment or a missing bar."
                if b["n_excluded"] else f"All {b['n_pool']:,} stocks measured.")
    c.text(MARGIN, y, "Ranked by today's % change from the previous close, among stocks that closed higher.", 20, MUTED)
    c.text(MARGIN, y + 28, left_out + " The counts above cover the whole group.", 20, MUTED)
    return c.png()
