#!/usr/bin/env python3
# mechanism/alerts/brand_assets.py
"""
Brand artwork for "First Light ⚡ Market Daily" (owner decision 2026-10-22: cool, young, sharp, bold; a worldwide channel), drawn with
Pillow -- no image service, nothing sent anywhere.

    python mechanism/alerts/brand_assets.py [--out reports/first_light/brand]

Writes
  channel_avatar.png           640 x 640   the channel's photo: the dawn sun split by a lightning bolt
  bot_avatar.png               640 x 640   the assistant's photo (set in BotFather): the bolt in a chat bubble
  assistant_post_<n>.png      1080 x 1080  the picture for each of the 4 rotating assistant posts (channel_content.ASSISTANT_POSTS)

Public copy rules: nothing about how First Light works (no scan, lists, rankings), no advice words, every number on a mock screen is
labelled as an example, no place or time zone (worldwide channel). The wording-guard test scans BRAND_COPY.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))               # `alerts` package when run as a script

from alerts.promo_assets import Canvas, _mix  # noqa: E402

# palette: deep night navy, electric cyan, hot coral, a yellow bolt
BG0, BG1 = "#05081a", "#10204f"
PANEL, LINE = "#0f1a44", "#2a3a78"
INK, INK2, MUTED = "#f5f7ff", "#b9c3e6", "#7d89b5"
CYAN, CORAL, BOLT, LIME = "#22d3ee", "#ff5e6c", "#ffd23f", "#5ee59a"

S = 1080
M = 72

BRAND_COPY = {
    "brand": "FIRST LIGHT", "pill": "ASSISTANT",
    "cta": "Request access  ·  seats limited", "foot": "Example screens. Not advice.",
    "posts": [
        {"h1": "YOUR STOCKS.", "h2": "YOUR ASSISTANT.", "sub": "Your watchlist and portfolio, followed from the day you add them."},
        {"h1": "YOUR RULES.", "h2": "EVERY MATCH.", "sub": "Set your rules once. See every US stock that fits, in seconds."},
        {"h1": "ONE MESSAGE.", "h2": "AFTER THE CLOSE.", "sub": "The stocks you follow, what changed, nothing else."},
        {"h1": "TYPE A TICKER.", "h2": "GET THE CARD.", "sub": "Facts, headlines and a chart, in one tap."},
    ],
    "mock": ["Your stocks", "since added", "Portfolio value", "Tech · above $10B · up 5 days", "12 stocks fit", "After the close",
             "3 of your stocks moved", "News", "Chart", "Facts", "example"],
}


# ================================================================== drawing pieces
def bolt_points(cx: float, cy: float, h: float) -> List[Tuple[float, float]]:
    w = h * 0.62
    shape = [(0.30, 0.0), (0.78, 0.0), (0.52, 0.40), (0.92, 0.40), (0.22, 1.0), (0.40, 0.56), (0.06, 0.56)]
    return [(cx - w / 2 + x * w, cy - h / 2 + y * h) for x, y in shape]


def draw_bolt(c: Canvas, cx: float, cy: float, h: float, fill: str = BOLT, outline: str = None, ow: float = 0.0) -> None:
    """A lightning bolt; `outline` draws a true stroke of `ow` x height around it (so it cuts cleanly through the sun)."""
    pts = [(x * 2, y * 2) for x, y in bolt_points(cx, cy, h)]
    if outline:
        c.d.polygon(pts, fill=fill, outline=outline, width=max(2, int(h * ow * 2)))
        return
    c.d.polygon(pts, fill=fill)


def draw_sun_bolt(c: Canvas, cx: float, cy: float, size: float, bg: str = BG0) -> None:
    """The mark: a dawn sun (coral rim fading to warm yellow at the horizon) split by a yellow lightning bolt."""
    r = size * 0.40
    base_y = cy + size * 0.12
    c.glow(cx, base_y - r * 0.45, size * 0.95, CORAL, 0.28)
    for k in range(9):                                                     # rays
        ang = math.radians(200 + k * 17.5)
        r0, r1 = r + size * 0.06, r + size * (0.13 if k % 2 == 0 else 0.10)
        c.line([(cx + r0 * math.cos(ang), base_y + r0 * math.sin(ang)), (cx + r1 * math.cos(ang), base_y + r1 * math.sin(ang))],
               _mix(CORAL, bg, 0.25), max(3, size * 0.018))
    for i in range(int(r * 2), 0, -2):                                     # the half sun
        col = _mix("#ffb35c", CORAL, i / (r * 2))
        c.d.pieslice(((cx - i / 2) * 2, (base_y - i / 2) * 2, (cx + i / 2) * 2, (base_y + i / 2) * 2), 180, 360, fill=col)
    c.line([(cx - size * 0.5, base_y), (cx + size * 0.5, base_y)], CYAN, max(3, size * 0.014))
    draw_bolt(c, cx + size * 0.02, cy - size * 0.02, size * 0.78, BOLT, outline=bg, ow=0.05)


def _bg(c: Canvas, w: int, h: int) -> None:
    c.vgradient((0, 0, w, h), BG1, BG0)
    c.glow(w * 0.92, h * 0.06, w * 0.62, "#1b4fb0", 0.55)
    c.glow(w * 0.02, h * 1.0, w * 0.55, "#5a1f4a", 0.40)


def _fit(c: Canvas, s: str, size: int, max_w: float, bold=True) -> int:
    while size > 30 and c.text_width(s, size, bold) > max_w:
        size -= 2
    return size


def _wrap(c: Canvas, s: str, size: int, max_w: float) -> List[str]:
    out, cur = [], ""
    for word in s.split():
        t = (cur + " " + word).strip()
        if c.text_width(t, size) <= max_w:
            cur = t
        else:
            out.append(cur)
            cur = word
    return out + ([cur] if cur else [])


def _panel(c: Canvas, box, r=34):
    c.rrect(box, r, fill=PANEL, outline=LINE, width=2)


def _tag(c: Canvas, x, y, s, fg=MUTED):
    c.text(x, y, s, 20, fg)


def _pct(c: Canvas, x, y, v: float, size=30, anchor="rs"):
    col = LIME if v >= 0 else CORAL
    c.text(x, y, f"{'+' if v >= 0 else '−'}{abs(v):.1f}%", size, col, True, anchor=anchor)


def _bubble(c: Canvas, box, fill, text, size=28, fg=INK, bold=False):
    c.rrect(box, 26, fill=fill)
    c.text(box[0] + 26, (box[1] + box[3]) / 2 + size * 0.36, text, size, fg, bold)


# ================================================================== the four mock screens (illustrative, labelled "example")
def _mock_portfolio(c: Canvas, x0, y0, x1, y1):
    _panel(c, (x0, y0, x1, y1))
    c.text(x0 + 36, y0 + 62, BRAND_COPY["mock"][0], 34, INK, True)
    c.text(x1 - 36, y0 + 58, "example", 20, MUTED, anchor="rs")
    rows = (("NVDA", 12.4), ("MSFT", 5.8), ("TSLA", -2.0))
    for i, (sym, v) in enumerate(rows):
        y = y0 + 132 + i * 64
        c.line([(x0 + 36, y - 40), (x1 - 36, y - 40)], LINE, 1)
        c.text(x0 + 36, y, sym, 32, INK, True)
        c.text(x0 + 180, y, BRAND_COPY["mock"][1], 22, MUTED)
        _pct(c, x1 - 36, y, v)
    yb = y1 - 40
    c.line([(x0 + 36, yb - 46), (x1 - 36, yb - 46)], LINE, 2)
    c.text(x0 + 36, yb, BRAND_COPY["mock"][2], 24, INK2)
    c.text(x1 - 36, yb, "$48,210", 34, INK, True, anchor="rs")


def _mock_screener(c: Canvas, x0, y0, x1, y1):
    _panel(c, (x0, y0, x1, y1))
    c.text(x1 - 36, y0 + 52, "example", 20, MUTED, anchor="rs")
    _bubble(c, (x1 - 36 - 560, y0 + 80, x1 - 36, y0 + 156), CYAN, BRAND_COPY["mock"][3], 26, BG0, True)
    _bubble(c, (x0 + 36, y0 + 190, x0 + 36 + 360, y0 + 266), "#1d2b63", BRAND_COPY["mock"][4], 28, INK, True)
    chips = ("NVDA", "AVGO", "AMD", "ANET", "CRWD", "+7")
    x = x0 + 36
    for s in chips:
        w = c.text_width(s, 26, True) + 44
        c.rrect((x, y0 + 296, x + w, y0 + 352), 28, fill="#18265a", outline=LINE, width=2)
        c.text(x + w / 2, y0 + 333, s, 26, INK, True, anchor="ms")
        x += w + 14
    _bubble(c, (x0 + 36, y1 - 108, x1 - 36, y1 - 36), "#152357", "Every US stock checked in seconds", 24, INK2)


def _mock_alert(c: Canvas, x0, y0, x1, y1):
    _panel(c, (x0, y0, x1, y1))
    c.text(x1 - 36, y0 + 52, "example", 20, MUTED, anchor="rs")
    nb = (x0 + 36, y0 + 82, x1 - 36, y0 + 200)                              # a notification banner
    c.rrect(nb, 28, fill="#1a2a66", outline=CYAN, width=2)
    c.dot(nb[0] + 58, nb[1] + 59, 30, BOLT)
    draw_bolt(c, nb[0] + 58, nb[1] + 59, 38, BG0)
    c.text(nb[0] + 112, nb[1] + 50, BRAND_COPY["mock"][5], 28, INK, True)
    c.text(nb[0] + 112, nb[1] + 88, BRAND_COPY["mock"][6], 24, INK2)
    for i, (sym, v) in enumerate((("NVDA", 4.2), ("AMD", 3.1), ("TSLA", -2.4))):
        y = y0 + 258 + i * 52
        c.text(x0 + 60, y, sym, 32, INK, True)
        _pct(c, x1 - 60, y, v, 32)


def _candles(c: Canvas, x, y, w, h):
    series = ((1.0, 1.4), (1.4, 1.2), (1.2, 1.8), (1.8, 2.3), (2.3, 2.0), (2.0, 2.7), (2.7, 3.2), (3.2, 2.9), (2.9, 3.6), (3.6, 4.1),
              (4.1, 3.9), (3.9, 4.6))
    lo, hi = 0.6, 5.0
    ty = lambda v: y + h - (v - lo) / (hi - lo) * h                             # noqa: E731
    step = w / len(series)
    for i, (o, cl) in enumerate(series):
        px, col = x + step * i + step / 2, LIME if cl >= o else CORAL
        c.line([(px, ty(min(o, cl) - 0.25)), (px, ty(max(o, cl) + 0.25))], col, 3)
        c.rrect((px - step * 0.27, ty(max(o, cl)), px + step * 0.27, ty(min(o, cl))), 3, fill=col)


def _mock_card(c: Canvas, x0, y0, x1, y1):
    _panel(c, (x0, y0, x1, y1))
    c.text(x1 - 36, y0 + 52, "example", 20, MUTED, anchor="rs")
    _bubble(c, (x1 - 36 - 150, y0 + 70, x1 - 36, y0 + 140), CYAN, "AAPL", 30, BG0, True)
    c.text(x0 + 36, y0 + 128, "AAPL", 44, INK, True)
    c.text(x0 + 36 + c.text_width("AAPL", 44, True) + 22, y0 + 128, "$253.40", 34, INK2)
    _pct(c, x1 - 36, y0 + 200, 1.2, 34)
    _candles(c, x0 + 36, y0 + 168, x1 - x0 - 260, 120)
    x = x0 + 36
    for s in BRAND_COPY["mock"][7:10]:
        w = c.text_width(s, 26, True) + 52
        c.rrect((x, y1 - 84, x + w, y1 - 30), 27, fill="#18265a", outline=CYAN, width=2)
        c.text(x + w / 2, y1 - 48, s, 26, INK, True, anchor="ms")
        x += w + 16


MOCKS = (_mock_portfolio, _mock_screener, _mock_alert, _mock_card)


# ================================================================== the outputs
def render_channel_avatar(size: int = 640) -> bytes:
    """Telegram shows it in a circle: everything sits inside the central ~70%."""
    c = Canvas(size, size, BG0)
    _bg(c, size, size)
    draw_sun_bolt(c, size * 0.5, size * 0.5, size * 0.56)
    return c.png()


def render_bot_avatar(size: int = 640) -> bytes:
    """The bolt in a chat bubble: reads as "the assistant" and is told apart from the channel photo at a glance."""
    c = Canvas(size, size, BG0)
    _bg(c, size, size)
    x0, y0, x1, y1 = size * 0.18, size * 0.17, size * 0.82, size * 0.68
    tail = [(x0 + size * 0.10, y1 - 4), (x0 + size * 0.24, y1 - 4), (x0 + size * 0.06, y1 + size * 0.14)]
    c.polygon(tail, CYAN)
    c.rrect((x0, y0, x1, y1), size * 0.14, fill=PANEL, outline=CYAN, width=max(4, size * 0.012))
    c.polygon([(tail[0][0] + 6, tail[0][1] - 6), (tail[1][0] - 6, tail[1][1] - 6), (tail[2][0] + 6, tail[2][1] - 12)], PANEL)
    draw_bolt(c, size * 0.5, size * 0.42, size * 0.34, BOLT)
    for k in range(3):                                                         # typing dots
        c.dot(size * 0.5 + (k - 1) * size * 0.07, size * 0.62, size * 0.018, INK2 if k < 2 else MUTED)
    return c.png()


def render_assistant_post(index: int) -> bytes:
    p = BRAND_COPY["posts"][index % len(BRAND_COPY["posts"])]
    c = Canvas(S, S, BG0)
    _bg(c, S, S)
    # brand row: mini mark, wordmark, ASSISTANT pill
    draw_sun_bolt(c, M + 34, 92, 74, BG1)
    c.text(M + 92, 104, BRAND_COPY["brand"], 34, INK, True, tracking=6)
    px = M + 92 + c.text_width(BRAND_COPY["brand"], 34, True) + 6 * len(BRAND_COPY["brand"]) + 18
    pw = c.text_width(BRAND_COPY["pill"], 22, True) + 64
    c.rrect((px, 74, px + pw, 116), 21, fill=CYAN)
    draw_bolt(c, px + 24, 95, 24, BG0)
    c.text(px + 40, 103, BRAND_COPY["pill"], 22, BG0, True)
    # headline: two bold lines, the second in bolt yellow
    size = min(_fit(c, p["h1"], 96, S - 2 * M), _fit(c, p["h2"], 96, S - 2 * M))
    c.text(M, 236, p["h1"], size, INK, True)
    c.text(M, 236 + size + 6, p["h2"], size, BOLT, True)
    sub = _wrap(c, p["sub"], 30, S - 2 * M)
    for j, line in enumerate(sub):
        c.text(M, 236 + 2 * size + 58 + j * 40, line, 30, INK2)
    top = max(520, 236 + 2 * size + 58 + (len(sub) - 1) * 40 + 44)
    MOCKS[index % len(MOCKS)](c, M, top, S - M, 924)
    # call to action
    c.rrect((M, 958, S - M, 1036), 39, fill=BOLT)
    c.text(S / 2, 1008, BRAND_COPY["cta"], 32, BG0, True, anchor="ms")
    c.text(S / 2, 1066, BRAND_COPY["foot"], 18, MUTED, anchor="ms")
    return c.png()


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the First Light ⚡ Market Daily brand artwork (nothing is sent)")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "reports" / "first_light" / "brand"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = {"channel_avatar.png": render_channel_avatar(), "bot_avatar.png": render_bot_avatar()}
    files.update({f"assistant_post_{i + 1}.png": render_assistant_post(i) for i in range(len(BRAND_COPY["posts"]))})
    for name, png in files.items():
        (out / name).write_bytes(png)
        print(f"{out / name}  ({len(png) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
