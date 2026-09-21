"""Unit tests for the First Light digest, long side (no database, no network).

Run:  python -m pytest mechanism/alerts/tests -q      (from the repo root)
"""
import html
import os
import re
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from alerts import digest_builder as db_  # noqa: E402
from alerts import digest_format as fmt  # noqa: E402
from alerts.alert_builder import Skip  # noqa: E402
from alerts.telegram_client import MAX_LEN  # noqa: E402


def _flat(n=300, last_close=None, last_low=None, last_high=None, last_vol=1_000_000.0, seed=0):
    """A flat 90..110 channel of a ~$100 stock; only the last bar can differ."""
    rng = np.random.default_rng(seed)
    c = 100 + rng.normal(0, 0.05, n)
    df = pd.DataFrame({"date": pd.bdate_range(end="2026-09-18", periods=n), "open": c, "high": np.full(n, 110.0),
                       "low": np.full(n, 90.0), "close": c, "volume": np.full(n, 1_000_000.0)})
    i = n - 1
    if last_close is not None:
        df.loc[i, ["close", "open"]] = last_close
        df.loc[i, "high"] = last_high if last_high is not None else max(110.0, last_close)
        df.loc[i, "low"] = last_low if last_low is not None else min(90.0, last_close)
    df.loc[i, "volume"] = last_vol
    return df


def test_long_groups_follow_the_screener_rules():
    assert db_.analyze(_flat(last_close=112, last_high=113, last_low=108))["cat"] == "breakout"
    assert db_.analyze(_flat(last_close=108.5, last_high=109, last_low=100))["cat"] == "near_breakout"
    mid = db_.analyze(_flat())
    assert mid["cat"] is None and mid["close"] > 0 and mid["rvol"] is not None             # kept for the snapshot, just no group


def test_short_side_is_classified_but_never_reported():
    breakdown, near_breakdown = _flat(last_close=88, last_high=92, last_low=87), _flat(last_close=91, last_high=100, last_low=90.5)
    rows = []
    for sym, df in (("DN", breakdown), ("NDN", near_breakdown)):
        r = db_.analyze(df)
        r["symbol"] = sym
        rows.append(r)
    assert [r["cat"] for r in rows] == ["breakdown", "near_breakdown"]
    d = db_.build_digest(rows, min_dv=0)
    assert d["counts"] == {"breakout": 0, "near_breakout": 0} and all(not d["boards"][c]["gainers"] for c in d["boards"])


def test_next_day_close_under_the_new_channel_high_is_near_breakout_and_new():
    df = _flat(last_close=112, last_high=113, last_low=108)                                # day 1 breaks out; channel high is now 113
    df = pd.concat([df, pd.DataFrame({"date": [df["date"].iloc[-1] + pd.offsets.BDay(1)], "open": [112], "high": [113.5],
                                      "low": [111], "close": [112.5], "volume": [1e6]})], ignore_index=True)
    r = db_.analyze(df)
    assert r["cat"] == "near_breakout" and r["prev_cat"] == "breakout" and r["new"] is True
    assert r["below_high_pct"] == pytest.approx((113.5 - 112.5) / 112.5 * 100, rel=1e-6)


def test_new_flag_and_facts():
    r = db_.analyze(_flat(last_close=112, last_high=113, last_low=108, last_vol=6_000_000))
    assert r["new"] is True and r["prev_cat"] is None
    assert r["below_high_pct"] == pytest.approx((113 - 112) / 112 * 100, rel=1e-6)          # computed for every stock
    assert r["rvol"] == pytest.approx(6.0, rel=0.01) and r["range_atr"] > 0
    assert r["ret1_pct"] == pytest.approx((112 / 100 - 1) * 100, abs=0.2)


def test_skips_short_history_and_price_discontinuities():
    with pytest.raises(Skip):
        db_.analyze(_flat(n=40))
    bad = _flat()
    bad.loc[250:, ["open", "high", "low", "close"]] *= 0.1                                 # unadjusted reverse split
    with pytest.raises(Skip):
        db_.analyze(bad)


def _rows():
    specs = {"AAA": (6e6, 112, 113, 108), "BBB": (2e6, 111.5, 116, 106), "CCC": (9e6, 111, 111.5, 110), "DDD": (3e6, 108.5, 109, 100)}
    rows = []
    for k, (sym, (vol, cl, hi, lo)) in enumerate(specs.items()):
        r = db_.analyze(_flat(last_close=cl, last_high=hi, last_low=lo, last_vol=vol, seed=k))
        r["symbol"] = sym
        rows.append(r)
    return rows


def test_lists_rank_by_their_formula_and_respect_the_dollar_volume_floor():
    d = db_.build_digest(_rows(), top_n=5, min_dv=5e6)
    b = d["boards"]["breakout"]
    assert d["counts"] == {"breakout": 3, "near_breakout": 1} and b["eligible"] == 3       # $100 x 1M sh = $100M/day, all eligible
    assert [r["symbol"] for r in b["gainers"]] == ["AAA", "BBB", "CCC"]                    # closes 112 / 111.5 / 111 vs ~100
    assert b["volume"][0]["symbol"] == "CCC"                                               # 9x volume leads
    assert b["atr"][0]["symbol"] == "BBB"                                                  # widest range vs ATR
    hi = db_.build_digest(_rows(), top_n=5, min_dv=1e12)
    assert hi["boards"]["breakout"]["volume"] == [] and hi["counts"]["breakout"] == 3      # the floor limits lists, not counts


def test_list_ranks_are_recorded_for_the_snapshot():
    rows = _rows()
    d = db_.build_digest(rows, top_n=2, min_dv=0)
    first = d["boards"]["breakout"]["gainers"][0]
    assert first["list_ranks"]["gainers"] == 1
    ranked = [r for r in rows if r.get("list_ranks")]
    assert ranked and all(set(r["list_ranks"]) <= set(db_.LISTS) for r in ranked)


def test_multi_membership_needs_two_lists():
    d = db_.build_digest(_rows(), top_n=1, min_dv=0)
    b = d["boards"]["breakout"]
    assert all(len(ls) >= 2 for ls in b["multi"].values())
    assert set(b["membership"]) >= set(b["multi"])


def test_messages_use_plain_words_fit_telegram_and_escape_symbols():
    rows = _rows()
    rows[0]["symbol"] = "A<B>"
    d = db_.build_digest(rows, top_n=5, min_dv=0)
    now = datetime(2026, 9, 20, 3, 0)
    header = fmt.format_header(datetime(2026, 9, 18).date(), now, d, 3070, {"up": 1042, "down": 1977},
                               ["S&P 500 +0.2%", "VIX (volatility index) 14.8"])
    groups = [fmt.format_group(c, d) for c in db_.LONG_CATEGORIES]
    text = "\n".join([header] + groups)
    assert all(len(m) < MAX_LEN for m in [header] + groups)
    assert "A<B>" not in text and "A&lt;B&gt;" in text
    first = header.splitlines()[0]
    assert "First Light" in first and "3 breakouts" in first and "1 near breakouts" in first     # the push preview carries the data
    for words in ("S&amp;P 500 +0.2%", "VIX (volatility index) 14.8", "Stocks up 1,042", "Not investment advice",
                  "<blockquote expandable>", "ATR (average true range)"):
        assert words in header
    for words in ("BREAKOUT", "Top gainers", "Top ATR", "Top volume"):
        assert words in groups[0]
    assert "NEAR BREAKOUT" in groups[1] and "below high" in groups[1]
    assert not any(ch in text for ch in "🚀🎯🕳🧗🔊⚡🫀🪤🆕📈")                              # no unexplained emojis
    assert "TRAPDOOR" not in text and "Breakdown" not in text                               # long side only


def test_each_list_is_a_quote_block_and_the_market_block_is_optional():
    d = db_.build_digest(_rows(), top_n=5, min_dv=0)
    group = fmt.format_group("breakout", d)
    assert group.count("<blockquote>") == 3 and group.count("</blockquote>") == 3
    args = (datetime(2026, 9, 18).date(), datetime(2026, 9, 20, 3, 0), d, 3070, {"up": 1042, "down": 1977}, ["S&P 500 +0.2%"])
    with_market, without = fmt.format_header(*args), fmt.format_header(*args, with_market=False)
    assert "Stocks up 1,042" in with_market and "S&amp;P 500" in with_market
    assert "Stocks up" not in without and "S&amp;P" not in without and "<b>Groups</b>" in without      # the image carries it
    assert "★ = the stock is in more than one list" in with_market
    caption = fmt.format_caption(datetime(2026, 9, 18).date(), d)
    assert len(caption) <= 1024 and caption.splitlines()[0].startswith("<b>First Light</b> · Fri 18 Sep · 3 breakouts")
    assert "not investment advice" in caption.lower()


def test_rows_are_two_short_lines_for_a_phone():
    near = {"symbol": "SBET", "close": 9.35, "ret1_pct": 13.2, "rvol": 2.1, "range_atr": 1.9, "below_high_pct": 1.4,
            "new": True, "prev_cat": None}
    head, detail = fmt._row(1, "near_breakout", "gainers", near, {"SBET": ["gainers", "atr"]}).split("\n")
    plain = lambda x: html.unescape(re.sub(r"<[^>]+>", "", x))
    assert plain(head) == "1. ★ SBET $9.35 ▲13.2% · NEW" and plain(detail) == fmt.INDENT + "vol 2.1× · 1.4% below high"
    assert max(len(plain(head)), len(plain(detail))) <= 38                                  # about one phone line
    assert "<b><a href" in head                                                             # every ticker is a bold link
    down = fmt._row(2, "near_breakout", "gainers", {**near, "symbol": "DWN", "ret1_pct": -0.6}, {})
    assert "▼0.6%" in down and "★" not in down and "+" not in plain(down.split("\n")[0])   # arrow carries the sign; no star without 2 lists
    brk = {**near, "prev_cat": "near_breakout", "new": True}
    assert "below high" not in fmt._row(1, "breakout", "volume", brk, {})                   # breakouts do not show it
    assert plain(fmt._row(1, "breakout", "atr", brk, {}).split(chr(10))[1]) == fmt.INDENT + "range 1.9× ATR · was near yesterday"


def test_markers_new_only_on_near_breakouts_and_promotion_on_breakouts():
    near = {"symbol": "N", "close": 10.0, "ret1_pct": 1.0, "rvol": 2.0, "range_atr": 1.5, "below_high_pct": 1.2,
            "new": True, "prev_cat": None}
    brk = {**near, "symbol": "B", "prev_cat": "near_breakout"}
    assert "NEW" in fmt._row(1, "near_breakout", "volume", near, {}) and "1.2% below high" in fmt._row(1, "near_breakout", "volume", near, {})
    assert "NEW" not in fmt._row(1, "breakout", "volume", {**brk, "prev_cat": None}, {})
    assert "was near yesterday" in fmt._row(1, "breakout", "volume", brk, {})
    assert "<b>" in fmt._row(1, "breakout", "atr", brk, {"B": ["atr", "volume"]}) and "range 1.5× ATR" in fmt._row(1, "breakout", "atr", brk, {})


def test_empty_list_says_none_today():
    d = db_.build_digest([], top_n=5)
    assert "none today" in fmt.format_group("breakout", d)
