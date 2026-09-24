"""Unit tests for the market-performance card and its data assembly (no database, no network).

Run:  python -m pytest mechanism/alerts/tests -q      (from the repo root)
"""
import io
import json
import os
import sys
import warnings
from datetime import date

import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from alerts import market_card as mc  # noqa: E402
from alerts import market_context as ctx  # noqa: E402
from alerts import texts  # noqa: E402
from alerts.telegram_client import CAPTION_MAX, TelegramClient, TelegramError  # noqa: E402

SESSION = date(2026, 9, 18)


def _card(**over):
    tiles = [mc.Tile("S&P 500", "7,650.50", "0.17%", +1, [1, 2, 3, 2, 4]), mc.Tile("Russell 2000", "2,860.40", "0.50%", -1, [4, 3, 2]),
             mc.Tile("VIX (volatility)", "n/a"), mc.Tile("Dow Jones", "51,682.64", "0.18%", 0, [1, 1, 1])]
    base = dict(session=SESSION, universe_n=2915, up_n=980, down_n=1894, tiles=tiles,
                sectors=[mc.SectorBar("Financial Services", 0.06), mc.SectorBar("Utilities", -1.05)], unclassified_n=8)
    return mc.CardData(**{**base, **over})


def _png(data) -> Image.Image:
    return Image.open(io.BytesIO(mc.render_market_card(data)))


def test_card_is_a_phone_sized_png():
    img = _png(_card())
    assert img.format == "PNG" and img.size == (mc.W, mc.H) == (1080, 1440)
    assert len(mc.render_market_card(_card())) < 1_000_000                             # light enough for a channel


def test_card_draws_something_in_every_section():
    img = _png(_card()).convert("RGB")
    def has(color, box):
        return any(img.getpixel((x, y)) == color for x in range(box[0], box[2], 4) for y in range(box[1], box[3], 2))
    hexrgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))                    # noqa: E731
    up, down = hexrgb(mc.UP), hexrgb(mc.DOWN)
    assert has(up, (48, 800, 1032, 900)) and has(down, (48, 800, 1032, 900))            # breadth bar: both polarities
    assert has(up, (600, 990, 700, 1050)) or has(up, (600, 1000, 700, 1040))            # a positive sector bar right of the baseline
    assert has(down, (300, 1040, 640, 1370))                                            # negative bars left of the baseline


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4          # noqa: E731
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_midnight_dawn_palette_keeps_contrast_on_both_backgrounds():
    for bg in (mc.SURFACE, mc.TILE):
        for ink in (mc.INK, mc.INK2):
            assert _contrast(ink, bg) >= 7, (ink, bg)                                    # text tokens: WCAG AAA
        assert _contrast(mc.MUTED, bg) >= 4.5, bg                                         # smallest text (captions, sparklines)
        for polarity in (mc.UP, mc.DOWN):
            assert _contrast(polarity, bg) >= 3, (polarity, bg)                           # marks: WCAG 3:1 for graphics
    up, down = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (mc.UP, mc.DOWN))
    assert up[2] > up[0] and down[0] > down[2]                                            # cool pole = up, warm pole = down
    assert _luminance(mc.SURFACE) < 0.03 and _luminance(mc.TILE) > _luminance(mc.SURFACE)  # deep, and tiles sit slightly above it


def test_card_survives_missing_data_without_inventing_any():
    empty = _card(tiles=[mc.Tile(l, "n/a") for l in ("a", "b", "c", "d", "e", "f")], sectors=[], up_n=0, down_n=0, universe_n=0,
                  unclassified_n=0)
    assert _png(empty).size == (1080, 1440)                                             # no crash on empty inputs
    assert _png(_card(tiles=[])).size == (1080, 1440)


def test_extreme_values_do_not_break_layout():
    huge = _card(sectors=[mc.SectorBar(f"Sector {i}", (-1) ** i * 9.5 * (i + 1)) for i in range(11)], up_n=2915, down_n=0, universe_n=2915)
    assert _png(huge).size == (1080, 1440)
    assert _png(_card(up_n=1, down_n=1, universe_n=2915)).size == (1080, 1440)          # tiny segments keep a visible minimum


# ------------------------------------------------------------------ data assembly
def _series(closes, end="2026-09-18"):
    dates = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame({"symbol": "X", "date": dates, "close": closes})


def test_index_tile_formats_and_direction():
    t = ctx._tile("^GSPC", "S&P 500", "index", _series([7637.76, 7650.50]), SESSION)
    assert (t.value, t.delta, t.direction) == ("7,650.50", "0.17%", 1) and len(t.spark) == 2
    v = ctx._tile("^VIX", "VIX", "level", _series([15.44, 14.81]), SESSION)
    assert (v.value, v.delta, v.direction) == ("14.81", "4.08%", -1)


def test_yield_is_shown_in_basis_points_not_percent_of_percent():
    t = ctx._tile("^TNX", "10-year yield", "yield", _series([4.947, 4.998]), SESSION)
    assert (t.value, t.delta, t.direction) == ("4.998%", "5.1 bps", 1)


def test_stale_or_short_series_is_n_a_not_a_guess():
    stale = ctx._tile("^GSPC", "S&P 500", "index", _series([1.0, 2.0], end="2026-09-17"), SESSION)
    assert (stale.value, stale.delta, stale.spark) == ("n/a", "", []) and stale.direction == 0
    assert ctx._tile("^GSPC", "S&P 500", "index", _series([1.0]), SESSION).value == "n/a"


def test_sector_bars_are_equal_weighted_sorted_and_hide_thin_or_unknown_sectors():
    stocks = [{"symbol": f"T{i}", "ret1_pct": v} for i, v in enumerate([1, 2, 3, 4, 5])]            # Tech mean +3
    stocks += [{"symbol": f"E{i}", "ret1_pct": -1.0} for i in range(6)]                              # Energy mean -1
    stocks += [{"symbol": "R1", "ret1_pct": 9.0}, {"symbol": "R2", "ret1_pct": 9.0}]                 # Real Estate: only 2 stocks
    stocks += [{"symbol": "U1", "ret1_pct": 1.0}, {"symbol": "N1", "ret1_pct": 1.0}]                 # Unknown / no sector
    sector_of = {**{f"T{i}": "Technology" for i in range(5)}, **{f"E{i}": "Energy" for i in range(6)},
                 "R1": "Real Estate", "R2": "Real Estate", "U1": "Unknown"}
    bars, unclassified = ctx.sector_bars(stocks, sector_of)
    assert [(b.name, round(b.change_pct, 2)) for b in bars] == [("Technology", 3.0), ("Energy", -1.0)]
    assert unclassified == 2                                                                         # U1 (Unknown) and N1 (missing)


# ------------------------------------------------------------------ telegram photo upload
class _Resp:
    status_code = 200

    def json(self):
        return {"ok": True, "result": {"message_id": 42}}


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, data=None, files=None, timeout=None):
        self.calls.append({"url": url, "json": json, "data": data, "files": files})
        return _Resp()


def test_send_photo_uploads_multipart_with_caption_and_keyboard():
    s = _Session()
    tg = TelegramClient("123456789:" + "A" * 35, "-100123", dry_run=False, session=s, sleep=lambda _: None)
    kb = {"inline_keyboard": [[{"text": "Open bot", "url": "https://t.me/x"}]]}
    assert tg.send_photo(b"\x89PNGdata", "<b>hi</b>", silent=True, reply_markup=kb) == 42
    call = s.calls[0]
    assert call["url"].endswith("/sendPhoto") and call["json"] is None and "photo" in call["files"]
    assert call["data"]["caption"] == "<b>hi</b>" and call["data"]["parse_mode"] == "HTML"
    assert call["data"]["disable_notification"] == "true" and json.loads(call["data"]["reply_markup"]) == kb


def test_send_photo_rejects_an_over_long_caption_and_never_touches_the_network_in_dry_run():
    tg = TelegramClient(None, None, dry_run=True)
    with pytest.raises(TelegramError):
        tg.send_photo(b"x", "y" * (CAPTION_MAX + 1))
    assert tg.send_photo(b"x", "ok") is None


def test_card_text_is_free_of_advice_style_words():
    import re
    banned = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*)\b", re.I)
    src = open(os.path.join(ROOT, "mechanism", "alerts", "market_card.py"), encoding="utf-8").read()
    strings = re.findall(r'"([^"\n]{6,})"', src.split("def render_market_card")[0].split("def _fmt_int")[1]) + \
        re.findall(r'"([^"\n]{6,})"', src.split("def render_market_card")[1])
    hits = [(m.group(0), s) for s in strings for m in banned.finditer(s)]
    assert not hits, hits
    assert "investment advice" not in src.lower() and texts.DISCLAIMER_SHORT                # the picture carries no disclaimer; the pinned post does
