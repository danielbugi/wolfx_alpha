"""The channel's extra posts (CHANNEL_CONTENT_MILESTONES.md M1-M2): market_stats maths on synthetic prices, the PNG cards, every post's copy
(valid Telegram HTML, no advice words, the disclaimer, caption limits), the weekday rotation, and the size tag on digest rows."""
import io
import os
import re
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

import tg_html  # noqa: E402
from PIL import Image  # noqa: E402
from alerts import channel_cards as cc  # noqa: E402
from alerts import channel_content as cx  # noqa: E402
from alerts import digest_format as df_  # noqa: E402
from alerts import market_stats as ms  # noqa: E402
from alerts.market_card import Tile  # noqa: E402

BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money)\b", re.I)


# ================================================================== synthetic prices
def make_prices(n_days=300, symbols=("AAA", "BBB", "CCC", "DDD"), start="2025-01-01", drift=None, seed=1):
    """Long table like send_daily_digest.load_universe_history: business days, a random walk per symbol."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n_days)
    rows = []
    for k, sym in enumerate(symbols):
        step = (drift or {}).get(sym, 0.0)
        close = 50 * np.exp(np.cumsum(rng.normal(step, 0.01, n_days)))
        for i, d in enumerate(dates):
            c = float(close[i])
            rows.append({"symbol": sym, "date": d, "open": c * 0.999, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 2_000_000 + 1000 * k})
    return pd.DataFrame(rows)


def test_to_wide_drops_a_stray_partial_date_so_rolling_windows_survive():
    df = make_prices(260)
    stray = pd.DataFrame([{**df.iloc[5].to_dict(), "date": pd.Timestamp("2025-03-02")}])   # a weekend row carried by ONE symbol
    df = pd.concat([df, stray], ignore_index=True)
    w = ms.to_wide(df)
    assert pd.Timestamp("2025-03-02") not in w.dates
    a200, n200 = ms.pct_above_sma(w, 200)
    assert n200 == 4                                                                # without the filter every stock would have a NaN in its window


def test_to_wide_keeps_only_requested_symbols_and_duplicates_collapse():
    df = make_prices(30)
    w = ms.to_wide(pd.concat([df, df]), ["AAA", "CCC"])
    assert list(w.close.columns) == ["AAA", "CCC"] and len(w.close) == 30


def test_pct_above_sma_counts_only_stocks_with_enough_history():
    df = make_prices(120, symbols=("AAA", "BBB"))
    late = make_prices(40, symbols=("NEW",), start=df["date"].iloc[-40].strftime("%Y-%m-%d"))   # only 40 bars
    w = ms.to_wide(pd.concat([df, late]))
    _, n50 = ms.pct_above_sma(w, 50)
    assert n50 == 2                                                                 # NEW (40 bars) is left out of the 50-day percentage


def test_pct_above_sma_is_100_for_a_steady_riser_and_0_for_a_steady_faller():
    dates = pd.bdate_range("2025-01-01", periods=80)
    rows = []
    for sym, sign in (("UP", 1), ("DOWN", -1)):
        for i, d in enumerate(dates):
            c = 100 + sign * i
            rows.append({"symbol": sym, "date": d, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1_000_000})
    w = ms.to_wide(pd.DataFrame(rows))
    pct, n = ms.pct_above_sma(w, 50)
    assert n == 2 and pct.iloc[-1] == 50.0                                          # one of the two is above its average


def test_health_counts_new_highs_and_lows_on_the_last_bar():
    dates = pd.bdate_range("2025-01-01", periods=260)
    rows = []
    for sym in ("HIGH", "LOW", "FLAT"):
        for i, d in enumerate(dates):
            base = 100.0
            hi = lo = base
            if i == 100:
                hi, lo = 120.0, 80.0                                                 # everyone had a wider range earlier in the year
            if i == len(dates) - 1 and sym == "HIGH":
                hi = 130.0
            if i == len(dates) - 1 and sym == "LOW":
                lo = 70.0
            rows.append({"symbol": sym, "date": d, "open": base, "high": max(hi, base + 1), "low": min(lo, base - 1), "close": base, "volume": 1e6})
    h = ms.health(ms.to_wide(pd.DataFrame(rows)))
    assert (h.new_highs, h.new_lows, h.n_range) == (1, 1, 3)


def test_value_ago_returns_none_when_history_is_short_or_missing():
    s = pd.Series([10.0, 20.0, 30.0])
    assert ms.value_ago(s, 1) == 20.0 and ms.value_ago(s, 3) is None
    assert ms.value_ago(pd.Series([np.nan, 5.0]), 1) is None


def _table():
    return pd.DataFrame({
        "close": [10, 20, 30, 40, 50], "prev_close": [10, 20, 30, 40, 50],
        "ret1_pct": [1.0, -1.0, 2.0, 3.0, 0.0], "gap_pct": [8.0, -6.0, 5.0, 1.0, np.nan],
        "rvol": [3.0, 2.5, np.nan, 5.0, 1.0], "dv20": [9e6, 9e6, 9e6, 1e6, 9e6],
        "hi100": [10.1, 25, 31, 41, np.nan], "hi252": [10.2, 21, 30.3, 41, np.nan],
        "below_hi100_pct": [1.0, 25.0, 3.3, 2.5, np.nan], "below_hi252_pct": [2.0, 5.0, 1.0, 2.5, np.nan]},
        index=["A", "B", "C", "D", "E"])


def test_gap_lists_threshold_liquidity_and_sort():
    g = ms.gap_lists(_table())
    assert [r["symbol"] for r in g["ups"]] == ["A", "C"] and [r["symbol"] for r in g["downs"]] == ["B"]
    assert g["n_up"] == 2 and g["n_down"] == 1 and g["n_pool"] == 3                 # D is below the $5M floor, E has no gap value


def test_near_high_list_needs_volume_and_proximity_and_skips_unknown():
    n = ms.near_high_list(_table())
    assert [r["symbol"] for r in n["rows"]] == ["A"] and n["n"] == 1                # B is 5% away, C has no volume figure, D is illiquid


def test_aligned_breakouts_counts_short_history_separately_never_as_a_pass():
    a = ms.aligned_breakouts(_table(), ["A", "B", "E", "ZZZ"])                       # ZZZ has no price row at all
    assert a["n_breakouts"] == 4 and a["n_priced"] == 3
    assert (a["n_week"], a["n_year"], a["n_both"]) == (1, 1, 1) and a["symbols"] == ["A"]
    assert a["n_short_week"] == 1 and a["n_short_year"] == 1                         # E has neither range


def test_sector_changes_uses_the_median_and_drops_thin_sectors():
    dates = pd.bdate_range("2025-01-01", periods=30)
    rows = []
    moves = {"T1": 10, "T2": 10, "T3": 10, "T4": 10, "T5": 900,                       # one 900% outlier in Tech must not move the median
             "H1": -5, "H2": -5, "H3": -5, "H4": -5, "H5": -5, "X1": 50, "X2": 50}
    for sym, pct in moves.items():
        for i, d in enumerate(dates):
            c = 100 * (1 + pct / 100) if i == len(dates) - 1 else 100.0
            rows.append({"symbol": sym, "date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1e6})
    w = ms.to_wide(pd.DataFrame(rows))
    sector_of = {**{s: "Tech" for s in moves if s.startswith("T")}, **{s: "Health" for s in moves if s.startswith("H")},
                 "X1": "Thin", "X2": "Thin"}
    bars, unclassified = ms.sector_changes(w, sector_of, 20)
    assert [(b[0], round(b[1]), b[2]) for b in bars] == [("Tech", 10, 5), ("Health", -5, 5)]   # "Thin" has 2 stocks (< 5): not shown
    assert unclassified == 0


def test_expiry_helpers():
    assert ms.is_monthly_options_expiry("2026-09-18") and ms.is_quarterly_expiry("2026-09-18")
    assert ms.is_monthly_options_expiry("2026-10-16") and not ms.is_quarterly_expiry("2026-10-16")
    assert not ms.is_monthly_options_expiry("2026-09-17") and not ms.is_monthly_options_expiry("2026-09-25")


# ================================================================== cards
def _health_card(**kw):
    dates = list(pd.bdate_range("2026-06-25", periods=60).date)
    base = dict(session=date(2026, 9, 18), universe_n=2915, n50=2865, n200=2778, n_range=2783, dates=dates,
                above50=list(np.linspace(65, 32, 60)), above200=list(np.linspace(66, 53, 60)), ref_sessions=10,
                new_highs=40, new_lows=126, breakouts=51, near_breakouts=282)
    base.update(kw)
    return cc.HealthCard(**base)


def test_the_cards_render_valid_pngs_of_the_expected_width():
    for png in (cc.render_health_card(_health_card()),
                cc.render_sector_card(date(2026, 9, 18), 20, [cc.SectorBar("Tech", 1.5), cc.SectorBar("Energy", -2.0)], 3, 2915),
                cc.render_macro_card(date(2026, 9, 18), [Tile("Gold", "4,424.90", "0.57%", 1), Tile("Bitcoin", "n/a")])):
        im = Image.open(io.BytesIO(png))
        assert im.format == "PNG" and im.width == 1080 and 300 < im.height <= 1400            # the macro card with one valid tile is a one-row card


def test_the_health_card_survives_missing_history_and_nan_points():
    cc.render_health_card(_health_card(above50=[50.0], above200=[50.0], dates=[date(2026, 9, 18)]))            # one point: no line, no crash
    cc.render_health_card(_health_card(above50=[float("nan")] * 60))
    cc.render_sector_card(date(2026, 9, 18), 20, [], 0, 0)                                                        # no sectors: still a card


def test_health_tiles_show_the_direction_and_say_na_when_unknown():
    t = cc.health_tiles(_health_card())
    assert t[0].value == "32%" and t[0].direction == -1 and "10 sessions ago" in t[0].delta
    assert cc.health_tiles(_health_card(above50=[float("nan")]))[0].value == "n/a"
    flat = cc.health_tiles(_health_card(above50=[50.0] * 60))[0]
    assert flat.direction == 0


def test_the_line_colours_pass_the_documented_validation_pair():
    assert (cc.SERIES_50, cc.SERIES_200) == ("#4f86e8", "#bf8514")                  # the pair validated with validate_palette.js (dark, #0a1a3c)


# ================================================================== posts
def _ctx(**kw):
    dates = pd.bdate_range("2026-06-25", periods=60)
    h = ms.Health(session=dates[-1], above50=pd.Series(np.linspace(65, 32, 60), index=dates), above200=pd.Series(np.linspace(66, 53, 60), index=dates),
                  n50=2865, n200=2778, new_highs=40, new_lows=126, n_range=2783, ref_sessions=10)
    base = dict(session=date(2026, 9, 18), universe_n=2915, up_n=980, down_n=1894, counts={"breakout": 51, "near_breakout": 282}, health=h,
                sp500_pct=0.17, table=_table(), breakout_symbols=["A", "B", "C"],
                sector_bars=[("Tech", 1.5, 400), ("Energy", -2.0, 120)], sector_unclassified=8,
                macro_tiles=[Tile("Gold", "4,424.90", "0.57%", 1), Tile("Dollar index", "100.22", "0.00%", 0), Tile("Bitcoin", "n/a")],
                base={"n": 329986, "stopped": 0.512, "tp3": 0.085, "first_year": 2018,
                      "by_year": [{"year": 2018, "n": 26000, "stopped": 0.588}, {"year": 2019, "n": 38000, "stopped": 0.452}, {"year": 2026, "n": 10, "stopped": 0.9}]},
                recap={"days": [{"date": date(2026, 9, 14 + i), "breakout": 50 + i, "near": 200 + i, "up_pct": 40.0 + i} for i in range(5)],
                       "persistent": ["GEMI", "MSTR"], "persist_min": 3, "sectors": [("Tech", 1.0, 400), ("Energy", -1.0, 100)], "sector_sessions": 5},
                week_number=38,
                earnings_today=[{"symbol": "AAPL", "sector": "Technology", "close": 338.98, "eps_estimate": 1.98},
                                {"symbol": "MSFT", "sector": "Technology", "close": 512.10, "eps_estimate": 4.72}])
    base.update(kw)
    return cx.Ctx(**base)


ALL = ["health", "sector", "macro", "gaps", "near_highs", "aligned", "base_rate", "recap", "promo", "disclaimer", "assistant", "earnings_today"]


@pytest.mark.parametrize("kind", ALL)
def test_every_post_is_valid_telegram_html_within_limits(kind):
    post = cx.build_post(kind, _ctx())
    assert post is not None and post.kind == kind
    limit = cx.CAPTION_LIMIT if post.image else 4096
    assert tg_html.problems(post.text, limit) == [] and len(post.text) <= limit


@pytest.mark.parametrize("kind", ALL)
def test_every_post_has_no_advice_words_no_claims_and_no_disclaimer_of_its_own(kind):
    """The disclaimers live in the pinned post (test_notices.py checks every kind has a note there). Only the dedicated disclaimer notice repeats one."""
    plain = re.sub(r"<[^>]+>", "", cx.build_post(kind, _ctx()).text)
    assert BANNED.findall(plain) == [], BANNED.findall(plain)
    assert not re.search(r"\d+\s?%\s+(gain|return|profit|win)", plain, re.I)
    if kind != "disclaimer":
        assert not re.search(r"investment advice|survivor bias|not a forecast|say nothing about", plain, re.I)


@pytest.mark.parametrize("promo", cx.PROMOS, ids=lambda p: p["key"])
def test_every_education_and_promotion_text_is_clean_and_says_what_access_is(promo):
    plain = re.sub(r"<[^>]+>", "", promo["text"])
    assert BANNED.findall(plain) == [] and tg_html.problems(promo["text"]) == []
    if promo["assistant"]:                                                            # a post that promotes the assistant says what access is: on request, limited
        assert "on request" in cx.PRIVATE.lower() and "seats limited" in cx.PRIVATE.lower()
        assert "beta" not in cx.PRIVATE.lower() and not re.search(r"free|\$|price|sign ?up|subscribe", cx.PRIVATE, re.I)     # no beta wording, no price


def test_posts_return_none_when_an_ingredient_is_missing_instead_of_inventing_one():
    assert cx.post_health(_ctx(health=None)) is None
    assert cx.post_sector(_ctx(sector_bars=[])) is None and cx.post_sector(_ctx(sector_bars=None)) is None
    assert cx.post_macro(_ctx(macro_tiles=[Tile("Bitcoin", "n/a")])) is None
    assert cx.post_gaps(_ctx(table=None)) is None and cx.post_near_highs(_ctx(table=None)) is None
    assert cx.post_aligned(_ctx(breakout_symbols=[])) is None
    assert cx.post_base_rate(_ctx(base=None)) is None and cx.post_base_rate(_ctx(base={"n": 10, "by_year": []})) is None
    assert cx.post_recap(_ctx(recap={"days": [{"date": date(2026, 9, 18), "breakout": 1, "near": 1, "up_pct": 1}]})) is None   # fewer than 3 sessions
    assert cx.post_earnings_today(_ctx(earnings_today=[])) is None and cx.post_earnings_today(_ctx(earnings_today=None)) is None


def test_earnings_today_post_states_the_count_and_facts_no_timing_claim():
    text = cx.post_earnings_today(_ctx()).text
    assert text.startswith("<b>2 companies in the covered universe report today.</b>")
    assert "Technology · $338.98 · EPS est. 1.98" in text
    assert "before" not in text.lower() and "after" not in text.lower() and "open" not in text.lower()  # no BMO/AMC claim -- not in the stored data


def test_earnings_today_post_singular_wording_for_one_reporter():
    text = cx.post_earnings_today(_ctx(earnings_today=[{"symbol": "AAPL", "sector": None, "close": None, "eps_estimate": None}])).text
    assert text.startswith("<b>1 company in the covered universe reports today.</b>")
    assert "no additional facts on file" in text


def test_earnings_today_post_collapses_past_25_into_an_expandable_quote():
    many = [{"symbol": f"SYM{i}", "sector": None, "close": None, "eps_estimate": None} for i in range(30)]
    text = cx.post_earnings_today(_ctx(earnings_today=many)).text
    assert text.startswith("<b>30 companies in the covered universe report today.</b>")
    assert "More: 26" in text and "<blockquote expandable>" in text
    assert all(f">SYM{i}<" in text for i in range(30))          # all 30 present (25 inline + 5 in the expandable quote), none dropped
    assert text.index(">SYM24<") < text.index("<blockquote expandable>") < text.index(">SYM25<")  # the split lands exactly at 25


def test_the_days_hook_is_chosen_by_rule_and_names_a_divergence_first():
    from alerts.digest_format import headline
    assert headline(0.17, 980, 1894, 2915) == "Index up. 65% of stocks down."             # the 18 Sep session: the index tile alone hides this
    assert headline(-0.5, 2000, 800, 2915) == "Index down. 69% of stocks up."
    assert headline(0.5, 2000, 800, 2915) == "Broad rally. 69% of stocks up."           # agree and broad
    assert headline(-0.5, 500, 2200, 2915) == "Broad sell-off. 75% of stocks down."
    assert headline(0.2, 1400, 1300, 2915) == "Mixed. 48% of stocks up."
    assert headline(0.02, 980, 1894, 2915) == "Broad sell-off. 65% of stocks down."      # a flat index (< 0.05%) is not called "up"; 64.97% is not "mixed"
    assert headline(0.2, 1300, 1300, 2915) == "Mixed. 45% of stocks up."
    assert headline(None, 980, 1894, 2915) == "Broad sell-off. 65% of stocks down."      # no index value: breadth only
    assert headline(0.5, 0, 0, 10) is None and headline(0.5, 5, 5, 0) is None            # nothing to say = no hook, never a made-up one


def test_health_post_leads_with_the_hook_and_states_the_before_numbers_only_when_they_exist():
    text = cx.post_health(_ctx()).text
    assert text.startswith("<b>Index up. 65% of stocks down.</b>\nFri 18 Sep · S&amp;P 500 ▲0.2%")
    assert re.search(r"\(10 sessions ago: \d+%\)", text)
    short = _ctx()
    short.health.above50 = short.health.above50.tail(5)
    short.health.above200 = short.health.above200.tail(5)
    assert "sessions ago" not in cx.post_health(short).text


def test_the_gaps_post_carries_the_options_expiry_note_only_on_expiry_fridays():
    assert "options-expiry" in cx.post_gaps(_ctx(session=date(2026, 9, 18))).text              # third Friday, quarterly
    assert "Monthly options-expiry" in cx.post_gaps(_ctx(session=date(2026, 10, 16))).text
    assert "options-expiry" not in cx.post_gaps(_ctx(session=date(2026, 9, 17))).text


def test_base_rate_post_ignores_years_with_too_few_cases_in_its_range():
    text = cx.post_base_rate(_ctx()).text
    assert "45% (2019) to 59% (2018)" in text and "90%" not in text                             # 2026 has 10 cases: not a range endpoint


def test_the_aligned_post_never_names_stocks_and_offers_the_assistant_button():
    p = cx.post_aligned(_ctx())
    assert p.button and "finviz" not in p.text and "The names are in the assistant." in p.text
    assert p.text.startswith("<b>3 broke out.") and "beta" not in p.text.lower()          # the hook leads; the names stay behind the door


def test_a_market_without_a_value_is_left_out_of_the_macro_post_and_card_never_shown_as_na():
    p = cx.post_macro(_ctx())
    assert "n/a" not in p.text and "Bitcoin" not in p.text and "Gold ▲0.57%" in p.text and "Dollar index unchanged" in p.text
    assert Image.open(io.BytesIO(p.image)).width == 1080
    assert cx.post_macro(_ctx(macro_tiles=[Tile("Bitcoin", "n/a")])) is None                       # nothing honest to show


def test_the_sector_title_follows_the_facts_and_rotation_is_never_claimed():
    all_down = cx.post_sector(_ctx(sector_bars=[("Tech", -1.7, 400), ("Energy", -8.9, 120)])).text
    assert all_down.startswith("<b>Every sector is lower than 20 sessions ago.</b>") and "Smallest drop: Tech ▼1.7%" in all_down and "Largest drop: Energy ▼8.9%" in all_down
    all_up = cx.post_sector(_ctx(sector_bars=[("Tech", 3.0, 400), ("Energy", 1.0, 120)])).text
    assert all_up.startswith("<b>Every sector is higher than 20 sessions ago.</b>") and "Largest gain: Tech ▲3.0%" in all_up
    mixed = cx.post_sector(_ctx()).text
    assert mixed.startswith("<b>1 of 2 sectors are higher than 20 sessions ago.</b>") and "Strongest: Tech" in mixed and "Weakest: Energy" in mixed
    assert "rotation" not in all_down.lower()


def test_the_recap_lists_days_sectors_and_persistent_names():
    text = cx.post_recap(_ctx()).text
    assert "Breakouts: Mon 50" in text and "Tech ▲1.0% led" in text and "GEMI" in text and "on 3+ of 5 days" in text


def test_the_recap_shows_one_company_once_when_it_is_stored_under_two_tickers():
    r = _ctx().recap
    text = cx.post_recap(_ctx(recap={**r, "persistent": ["BRK.B", "BRK/B", "MSTR"]})).text
    assert text.count("BRK") == 2 and "(2)" in text                                # the ticker in the link text and in its address, counted once as a company


# ================================================================== rotation
def test_rotation_gives_each_weekday_its_kind_and_falls_back_safely():
    assert cx.pick_kinds(date(2026, 9, 14))[0] == "sector"                                      # Monday, ISO week 38 (even index -> first option)
    assert cx.pick_kinds(date(2026, 9, 15))[0] == "gaps" and cx.pick_kinds(date(2026, 9, 16))[0] == "health"
    assert cx.pick_kinds(date(2026, 9, 17))[0] == "near_highs" and cx.pick_kinds(date(2026, 9, 18))[0] == "promo"
    assert cx.pick_kinds(date(2026, 9, 21))[0] == "macro"                                       # the next Monday alternates
    for d in (date(2026, 9, 14), date(2026, 9, 18)):
        ks = cx.pick_kinds(d)
        assert len(set(ks)) == len(ks) and "health" in ks and "promo" in ks                     # a chain that always contains something that can be said


def test_the_news_kind_is_only_tried_when_enabled():
    tue_news_week = date(2026, 9, 22)                                                           # Tuesday of the alternate week
    assert cx.pick_kinds(tue_news_week, news_enabled=False)[0] == "gaps"
    assert cx.pick_kinds(tue_news_week, news_enabled=True)[0] == "news"


def test_the_first_thursday_of_the_month_is_the_base_rate_card_and_weekends_only_run_the_recap():
    assert cx.pick_kinds(date(2026, 10, 1))[0] == "base_rate"
    assert cx.pick_kinds(date(2026, 10, 8))[0] != "base_rate"
    assert cx.pick_kinds(date(2026, 9, 19)) == ["recap"]


def test_the_promotion_rotates_through_all_formats_by_week():
    assert {cx.promo_for(w)["key"] for w in range(len(cx.PROMOS))} == {p["key"] for p in cx.PROMOS}


# ================================================================== the size tag on digest rows
def _row(**kw):
    r = {"symbol": "ABC", "close": 5.81, "ret1_pct": 31.2, "rvol": 10.5, "range_atr": 3.6, "below_high_pct": None, "prev_cat": "near_breakout", "new": False}
    r.update(kw)
    return r


def test_the_small_cap_tag_appears_only_for_a_known_market_value_under_two_billion():
    assert "small cap" in df_._row(1, "breakout", "gainers", _row(mcap=350e6), {})
    assert "small cap" not in df_._row(1, "breakout", "gainers", _row(mcap=5e9), {})
    assert "small cap" not in df_._row(1, "breakout", "gainers", _row(), {})                    # unknown market value: no tag, never a guess
    assert "small cap" not in df_._row(1, "breakout", "gainers", _row(mcap=None), {})


def test_the_legend_explains_the_small_cap_tag():
    assert "small cap = market value under $2B" in df_._legend()


# ================================================================== the sender (send_channel_posts.py)
def _sender():
    try:
        from alerts import send_channel_posts as scp
    except Exception as ex:                                                          # noqa: BLE001  (no database in this environment)
        pytest.skip(f"send_channel_posts needs the shared database module: {ex}")
    return scp


class FakeTG:
    def __init__(self):
        self.photos, self.messages, self.kinds = [], [], []

    def send_photo(self, png, caption="", silent=False, reply_markup=None, kind=None):
        self.photos.append((caption, silent, reply_markup))
        self.kinds.append(kind)

    def send_message(self, text, disable_preview=True, silent=False, reply_markup=None, kind=None):
        self.messages.append((text, silent, reply_markup))
        self.kinds.append(kind)


def test_extra_posts_are_silent_and_only_assistant_posts_carry_the_button():
    scp = _sender()
    tg = FakeTG()
    scp.send_post(tg, cx.build_post("health", _ctx()), "some_bot")
    scp.send_post(tg, cx.build_post("gaps", _ctx()), "some_bot")
    scp.send_post(tg, cx.build_post("aligned", _ctx()), "some_bot")
    assert tg.photos == [(tg.photos[0][0], True, None)]                               # silent, no button
    assert tg.messages[0][1] is True and tg.messages[0][2] is None                     # gaps: silent text, no button
    kb = tg.messages[1][2]["inline_keyboard"][0][0]
    assert kb["url"].startswith("https://t.me/some_bot?start=ch") and kb["text"] == "Request access"
    scp.send_post(tg, cx.build_post("aligned", _ctx()), None)                          # unknown bot username: no button rather than a broken one
    assert tg.messages[2][2] is None
    assert tg.kinds == ["health", "gaps", "aligned", "aligned"]                        # each post is labelled for the message ledger


def test_an_over_long_caption_aborts_instead_of_being_cut_by_telegram():
    scp = _sender()
    post = cx.build_post("health", _ctx())
    post.text = post.text + "x" * 1100
    with pytest.raises(SystemExit, match="caption"):
        scp.send_post(FakeTG(), post, "b")


def test_the_sender_goes_through_the_production_lock_and_refuses_to_flood_the_channel():
    src = open(os.path.join(ROOT, "mechanism", "alerts", "send_channel_posts.py"), encoding="utf-8").read()
    assert "TelegramClient.from_env(args.to, dry_run=False)" in src                    # the lock lives inside from_env
    assert "--all --send is only allowed with --to owner" in src                       # every kind at once may only go to the private review chat
    assert "if not args.send:" in src and "DRY RUN" in src                             # dry run by default


def test_every_kind_the_rotation_can_return_is_a_known_kind():
    for d in pd.bdate_range("2026-09-01", periods=60):
        for news in (False, True):
            assert set(cx.pick_kinds(d.date(), news)) <= set(cx.KINDS)


# ================================================================== backfill session selection + macro tiles
def test_backfill_picks_the_newest_complete_sessions_and_skips_stored_ones():
    try:
        from alerts import backfill_snapshots as bf
    except Exception as ex:                                                          # noqa: BLE001
        pytest.skip(str(ex))
    d = lambda n: date(2026, 9, n)                                                    # noqa: E731
    counts = {d(14): 3000, d(15): 3000, d(16): 40, d(17): 3000, d(18): 3000}          # the 16th is a partial load (40 symbols), not a session
    assert bf.pick_sessions(counts, 3, set()) == [d(15), d(17), d(18)]
    assert bf.pick_sessions(counts, 3, {d(17)}) == [d(15), d(18)]
    assert bf.pick_sessions(counts, 3, {d(17)}, replace=True) == [d(15), d(17), d(18)]
    assert bf.pick_sessions({}, 5, set()) == []


def test_macro_tiles_format_bitcoin_without_decimals_and_say_na_without_a_bar_on_the_session():
    from alerts import market_context as mc
    df = pd.DataFrame({"symbol": "BTC-USD", "date": [date(2026, 9, 17), date(2026, 9, 18)], "close": [76403.77, 81065.95]})
    t = mc._tile("BTC-USD", "Bitcoin", "price0", df, date(2026, 9, 18))
    assert t.value == "81,066" and t.direction == 1
    assert mc._tile("BTC-USD", "Bitcoin", "price0", df, date(2026, 9, 21)).value == "n/a"       # no bar for the session: say so, do not guess


# ================================================================== M3: the news post (channel_news.py + post_news)
from datetime import datetime, timedelta, timezone  # noqa: E402

from alerts import channel_news as cn  # noqa: E402

NOW = datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)


def _item(headline, hours_ago=3, url=None, source="Wire"):
    return {"published_at": NOW - timedelta(hours=hours_ago), "headline": headline, "source": source, "url": url or f"https://example.com/{abs(hash(headline))}"}


def test_parse_keeps_valid_rows_for_the_symbol_and_drops_malformed_ones():
    payload = {"news": [
        {"url": "https://a.com/1", "headline": "  Company  announces\nresults ", "created_at": "2026-09-18T15:00:00Z", "symbols": ["GEMI", "X"], "source": "Wire"},
        {"url": "javascript:alert(1)", "headline": "bad link", "created_at": "2026-09-18T15:00:00Z", "symbols": ["GEMI"]},
        {"url": "https://a.com/1", "headline": "duplicate url", "created_at": "2026-09-18T16:00:00Z", "symbols": ["GEMI"]},
        {"url": "https://a.com/2", "headline": "other symbol", "created_at": "2026-09-18T16:00:00Z", "symbols": ["ZZZ"]},
        {"url": "https://a.com/3", "headline": "no timezone", "created_at": "2026-09-18T16:00:00", "symbols": ["GEMI"]},
        {"headline": "no url", "created_at": "2026-09-18T16:00:00Z", "symbols": ["GEMI"]}]}
    got = cn.parse(payload, "GEMI")
    assert [g["headline"] for g in got] == ["Company announces results"] and cn.parse({}, "GEMI") == [] and cn.parse(None, "GEMI") == []


@pytest.mark.parametrize("headline", ["Analyst upgrades XYZ to Buy", "XYZ price target raised to $50", "Is XYZ a buy now?", "Zacks: XYZ stock rating",
                                      "Downgrade for XYZ", "Top picks for the week", "Should you invest in XYZ?"])
def test_advice_like_headlines_are_recognised(headline):
    assert cn.is_advice_like(headline)


@pytest.mark.parametrize("headline", ["XYZ announces quarterly results", "XYZ to acquire ABC for $2 billion", "FDA approves XYZ treatment", "XYZ CEO steps down"])
def test_plain_news_headlines_pass_the_filter(headline):
    assert not cn.is_advice_like(headline)


def test_collect_keeps_fresh_non_advice_headlines_limits_per_symbol_and_survives_failures():
    data = {"AAA": [_item("AAA wins contract"), _item("Analyst upgrades AAA"), _item("AAA opens plant"), _item("AAA hires CFO")],
            "BBB": [_item("BBB old news", hours_ago=100)], "CCC": [_item("CCC files report")]}

    def fetch(sym):
        if sym == "DDD":
            raise RuntimeError("provider down")
        return data.get(sym, [])
    got = cn.collect(["AAA", "BBB", "CCC", "DDD", "EEE"], fetch=fetch, now=NOW)
    assert list(got["items"]) == ["AAA", "CCC"]                                     # BBB is stale, DDD failed, EEE has nothing
    assert [h["headline"] for h in got["items"]["AAA"]] == ["AAA wins contract", "AAA opens plant"]   # advice-like dropped, 2 per symbol
    assert got["failed"] == 1 and got["dropped_advice"] == 1


def _news_ctx(items, movers=None):
    movers = movers or [{"symbol": s, "ret1_pct": 5.0, "group": "breakout"} for s in items]
    return _ctx(news={"movers": movers, "items": items, "failed": 0, "dropped_advice": 0})


def test_the_news_post_is_valid_html_escapes_third_party_text_and_shows_headline_link_and_source_only():
    items = {"AAA": [_item('AAA <b>beats</b> & "surprises"', url="https://example.com/a?x=1&y=2", source="Wire")]}
    post = cx.build_post("news", _news_ctx(items))
    assert tg_html.problems(post.text) == [] and post.image is None and len(post.text) <= 4096
    assert "&lt;b&gt;beats&lt;/b&gt; &amp;" in post.text and "x=1&amp;y=2" in post.text and "Wire" in post.text
    assert "Headlines via Alpaca" in post.text and not re.search(r"investment advice", post.text, re.I)      # the source, not a disclaimer
    own_copy = re.sub(r"<a [^>]*>.*?</a>", "", post.text)                              # our own words (third-party headlines excluded) obey the guard
    assert BANNED.findall(re.sub(r"<[^>]+>", "", own_copy)) == []


def test_the_news_post_is_none_without_headlines_and_stays_under_the_length_cap():
    assert cx.post_news(_ctx(news=None)) is None and cx.post_news(_news_ctx({})) is None
    many = {f"S{i}": [_item("A fairly long headline about an ordinary company event " * 3, url=f"https://example.com/{i}/{j}") for j in range(2)] for i in range(40)}
    post = cx.post_news(_news_ctx(many, [{"symbol": s, "ret1_pct": 1.0, "group": "breakout"} for s in many]))
    assert len(post.text) <= 4096 and tg_html.problems(post.text) == []


def test_movers_are_starred_stocks_first_then_top_gainers_without_duplicates():
    scp = _sender()
    rows = [{"symbol": s, "ret1_pct": v} for s, v in (("A", 9.0), ("B", 8.0), ("C", 7.0), ("D", 6.0), ("E", 5.0))]
    digest = {"boards": {"breakout": {"multi": {"C": ["gainers", "atr"]}, "gainers": rows[:4]},
                         "near_breakout": {"multi": {}, "gainers": [rows[0], rows[4]]}}}
    got = scp.pick_movers(rows, digest, limit=5)
    assert [m["symbol"] for m in got] == ["C", "A", "B", "E"] or [m["symbol"] for m in got] == ["C", "A", "B", "D", "E"][:len(got)]
    assert len({m["symbol"] for m in got}) == len(got) and got[0] == {"symbol": "C", "ret1_pct": 7.0, "group": "breakout"}


def test_a_stock_missing_yesterdays_bar_still_counts_for_range_measures_but_has_no_daily_figures():
    dates = pd.bdate_range("2025-01-01", periods=260)
    rows = []
    for sym in ("OK", "GAP"):
        for i, d in enumerate(dates):
            if sym == "GAP" and i == len(dates) - 2:
                continue                                                             # no bar the day before the session
            rows.append({"symbol": sym, "date": d, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 5_000_000})
    t = ms.last_session_table(ms.to_wide(pd.DataFrame(rows)))
    assert set(t.index) == {"OK", "GAP"}                                                # the stock is not dropped ...
    assert pd.isna(t.loc["GAP", "ret1_pct"]) and pd.isna(t.loc["GAP", "gap_pct"])       # ... its 1-day figures are unknown, not zero
    assert ms.aligned_breakouts(t, ["OK", "GAP"])["n_priced"] == 2
    assert "GAP" not in [r["symbol"] for r in ms.gap_lists(t, threshold=0.0)["ups"]]     # and it cannot enter a list that needs those figures


# ================================================================== M5.1: the list scoreboard (scoreboard.py + post_scoreboard)
from alerts import scoreboard as sb  # noqa: E402


def _closes_table(n=30, seed=3):
    """UP rises 1%/day, DOWN falls 1%/day, FLAT stays flat, SPLIT halves on day 12 (a 2-for-1 shaped jump)."""
    idx = pd.bdate_range("2026-06-01", periods=n)
    d = {"UP": 100 * 1.01 ** np.arange(n), "DOWN": 100 * 0.99 ** np.arange(n), "FLAT": np.full(n, 100.0)}
    split = np.full(n, 100.0)
    split[12:] = 50.0
    d["SPLIT"] = split
    return pd.DataFrame(d, index=idx)


def test_forward_changes_are_exact_and_none_without_enough_later_sessions():
    c = _closes_table()
    s = c.index[0].date()
    f = sb.forward_changes(c, s, 5)
    assert round(f["UP"], 6) == round((1.01 ** 5 - 1) * 100, 6) and round(f["DOWN"], 6) == round((0.99 ** 5 - 1) * 100, 6) and f["FLAT"] == 0.0
    assert sb.forward_changes(c, c.index[-3].date(), 5) is None                          # only 2 later sessions exist: never extrapolated
    assert sb.forward_changes(c, date(2020, 1, 1), 5) is None                            # an unknown session


def test_forward_changes_leave_out_a_window_that_contains_a_split_shaped_jump():
    c = _closes_table()
    f = sb.forward_changes(c, c.index[10].date(), 5)                                      # the window crosses the halving on day 12
    assert pd.isna(f["SPLIT"]) and not pd.isna(f["UP"])                                   # left out, not "repaired" into a -50% loss
    assert sb.forward_changes(c, c.index[0].date(), 5)["SPLIT"] == 0.0                    # a clean window is measured normally


def test_compute_compares_the_lists_with_the_whole_universe_over_the_same_sessions():
    c = _closes_table(30)
    sessions = [d.date() for d in c.index[:10]]
    app = pd.DataFrame([{"session_date": s, "symbol": "UP", "category": "breakout"} for s in sessions]
                       + [{"session_date": s, "symbol": "DOWN", "category": "near_breakout"} for s in sessions])
    uni = {s: ["UP", "DOWN", "FLAT"] for s in sessions}
    out = sb.compute(app, uni, c, horizons=(1, 5))
    b5, n5 = out["groups"]["breakout"][5], out["groups"]["near_breakout"][5]
    assert b5["n"] == 10 and b5["up_share"] == 100.0 and round(b5["median"], 4) == round((1.01 ** 5 - 1) * 100, 4)
    assert n5["up_share"] == 0.0 and n5["median"] < 0
    assert b5["universe"]["n"] == 30 and round(b5["universe"]["up_share"], 6) == round(100 / 3, 6) and b5["universe"]["median"] == 0.0     # UP, DOWN, FLAT
    assert out["first"] == sessions[0] and out["last"] == sessions[-1] and out["sessions"] == 10


def test_compute_reports_only_horizons_with_enough_sessions_behind_them():
    c = _closes_table(30)
    sessions = [d.date() for d in c.index[:10]]
    app = pd.DataFrame([{"session_date": s, "symbol": "UP", "category": "breakout"} for s in sessions])
    uni = {s: ["UP"] for s in sessions}
    horizons = sb.compute(app, uni, c, horizons=(1, 5, 20))["groups"]["breakout"]
    assert set(horizons) == {1, 5, 20}                                                      # 30 dates: sessions 0-9 all have 20 later sessions
    few = sb.compute(app.iloc[:0].copy(), {}, c)
    assert few["groups"]["breakout"] == {}
    late = pd.DataFrame([{"session_date": d.date(), "symbol": "UP", "category": "breakout"} for d in c.index[20:28]])
    assert set(sb.compute(late, {d.date(): ["UP"] for d in c.index[20:28]}, c, horizons=(1, 5, 20))["groups"]["breakout"]) == {1, 5}     # sessions 20-24 have 5 later sessions (exactly MIN_SESSIONS), none has 20
    assert sb.compute(pd.DataFrame(columns=["session_date", "symbol", "category"]), {}, c)["groups"] == {"breakout": {}, "near_breakout": {}}


def _scoreboard_ctx(**kw):
    h = {"n": 526, "up_share": 41.2, "median": -0.6, "sessions": 59, "universe": {"n": 100000, "up_share": 47.9, "median": -0.04}}
    base = {"first": date(2026, 6, 25), "last": date(2026, 9, 18), "sessions": 60, "groups": {"breakout": {1: h, 5: {**h, "median": 1.234}}, "near_breakout": {}}}
    base.update(kw)
    return _ctx(scoreboard=base)


def test_the_scoreboard_post_states_both_sides_never_shows_negative_zero_and_leaves_the_caveats_to_the_pinned_post():
    p = cx.post_scoreboard(_scoreboard_ctx())
    plain = re.sub(r"<[^>]+>", "", p.text)
    assert tg_html.problems(p.text) == [] and BANNED.findall(plain) == [] and not re.search(r"investment advice", plain, re.I)
    assert "41% higher, median -0.6% (526 stock-days)" in plain and "All liquid stocks over the same sessions: 48% higher, median 0.0%" in plain
    assert "5 sessions later: 41% higher, median +1.2%" in plain and "Near breakout lists" not in plain      # a group without any horizon is not shown
    assert "not independent tests" not in plain and "say nothing about the next ones" not in plain          # both caveats moved to the pinned post
    assert "A stock-day is one stock on one session's lists." in plain and "60 sessions of lists, 25 Jun – 18 Sep" in plain
    assert "-0.0" not in plain


def test_the_scoreboard_post_is_none_without_enough_history():
    assert cx.post_scoreboard(_ctx(scoreboard=None)) is None
    assert cx.post_scoreboard(_ctx(scoreboard={"first": None, "last": None, "sessions": 0, "groups": {"breakout": {}, "near_breakout": {}}})) is None


def test_the_scoreboard_is_opt_in_in_the_rotation():
    first_friday = date(2026, 10, 2)
    assert "scoreboard" not in cx.pick_kinds(first_friday)                                  # off by default: its numbers may be unflattering
    assert cx.pick_kinds(first_friday, scoreboard_enabled=True)[0] == "scoreboard"
    assert "scoreboard" not in cx.pick_kinds(date(2026, 10, 9), scoreboard_enabled=True)   # only the FIRST Friday


def test_the_recap_says_how_many_persistent_names_it_does_not_list():
    names = [f"S{i:02d}" for i in range(18)]
    r = _ctx().recap
    text = cx.post_recap(_ctx(recap={**r, "persistent": names})).text
    assert "(18)" in text and "+8" in text and text.count("<b><a ") == 10 and tg_html.problems(text) == []
    assert "+" not in cx.post_recap(_ctx(recap={**r, "persistent": names[:5]})).text.split("(5)")[1].split("\n")[0]
