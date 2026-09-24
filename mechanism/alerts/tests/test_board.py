"""The momentum board (board.py, its post and picture) and the deeper, collapsible digest lists (digest_format.group_messages).
Synthetic prices only: no database, no network. Every expected number below is worked out by hand in the test that uses it."""
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
from alerts import board as bd  # noqa: E402
from alerts import channel_cards as cc  # noqa: E402
from alerts import channel_content as cx  # noqa: E402
from alerts import digest_builder as db_  # noqa: E402
from alerts import digest_format as fmt  # noqa: E402
from alerts.market_stats import Wide  # noqa: E402
from alerts.star import is_starred, starred_lists  # noqa: E402
from alerts.telegram_client import split_message, text_length  # noqa: E402

BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money)\b", re.I)

N = 40
DATES = pd.bdate_range(end="2026-09-18", periods=N)
SESSION = DATES[-1].date()
PRIOR = [d.date() for d in DATES[-6:-1]]                     # the five sessions before SESSION


def make_wide(today_moves, high_low=None, extra=None):
    """Every symbol sits at 100 for N-1 sessions, then closes `100 * (1 + move/100)` on the last one. high_low = {sym: (high, low)} for the last bar."""
    close = pd.DataFrame(100.0, index=DATES, columns=list(today_moves))
    for sym, mv in today_moves.items():
        close.loc[DATES[-1], sym] = 100.0 * (1 + mv / 100)
    high, low = close * 1.02, close * 0.98
    for sym, (h, l) in (high_low or {}).items():
        high.loc[DATES[-1], sym], low.loc[DATES[-1], sym] = h, l
    for fn in (extra or []):
        fn(close, high, low)
    return Wide(open=close.copy(), high=high, low=low, close=close, volume=pd.DataFrame(1_000_000.0, index=DATES, columns=close.columns))


def listed(*symbols, sessions=PRIOR[:1], category="breakout"):
    return pd.DataFrame([{"session_date": s, "symbol": sym, "category": category} for sym in symbols for s in sessions])


MOVES = {"UP1": 5.0, "UP2": 3.0, "UP3": 1.0, "UP4": 0.5, "DN": -2.0, "FLAT": 0.0}


# ================================================================== board.compute
def test_the_podium_ranks_todays_change_among_stocks_that_closed_higher_and_counts_the_whole_pool():
    b = bd.compute(listed(*MOVES), make_wide(MOVES), SESSION)
    assert [r["symbol"] for r in b["top"]] == ["UP1", "UP2", "UP3"]
    assert [r["symbol"] for r in b["more"]] == ["UP4"]                                   # the loser and the unchanged stock are never placed
    assert (b["n_pool"], b["n_measured"], b["n_excluded"]) == (6, 6, 0)
    assert (b["higher"], b["lower"], b["flat"]) == (4, 1, 1)                             # the denominator covers everything, not just the podium
    assert b["top"][0]["ret1_pct"] == pytest.approx(5.0) and b["top"][0]["close"] == pytest.approx(105.0)


def test_fewer_than_three_gainers_give_fewer_places_and_never_crown_a_loser():
    moves = {"UP1": 2.0, "DN1": -1.0, "DN2": -3.0}
    b = bd.compute(listed(*moves), make_wide(moves), SESSION)
    assert [r["symbol"] for r in b["top"]] == ["UP1"] and b["more"] == [] and b["lower"] == 2


def test_none_when_nobody_closed_higher_when_there_is_no_pool_and_when_prices_are_stale():
    moves = {"DN1": -1.0, "FLAT": 0.0}
    assert bd.compute(listed(*moves), make_wide(moves), SESSION) is None                # a red day for the pool: nothing to crown
    assert bd.compute(listed(*MOVES).iloc[0:0], make_wide(MOVES), SESSION) is None      # no stored lists yet
    assert bd.compute(listed(*MOVES), make_wide(MOVES), PRIOR[-1]) is None              # prices do not end on the session date


def test_todays_own_listings_are_not_part_of_the_pool():
    rows = pd.concat([listed("UP1", "UP2"), listed("UP3", sessions=[SESSION])], ignore_index=True)
    b = bd.compute(rows, make_wide(MOVES), SESSION)
    assert {r["symbol"] for r in b["top"]} == {"UP1", "UP2"} and b["n_pool"] == 2       # UP3 was listed today, so it is not "carried over"


def test_a_split_shaped_move_today_or_inside_the_chart_window_is_left_out_and_counted():
    moves = {"REAL": 4.0, "SPLIT": 100.0, "OLDSPLIT": 2.0, "GAP": 30.0}                 # SPLIT: 200/100 today = a 2-for-1 shape; GAP: a real +30%

    def old_split(close, high, low):                                                     # OLDSPLIT halves 5 sessions ago: a discontinuity in the chart window
        close.loc[DATES[-5]:, "OLDSPLIT"] = close.loc[DATES[-5]:, "OLDSPLIT"] * 2
        close.loc[DATES[-1], "OLDSPLIT"] = 204.0
    b = bd.compute(listed(*moves), make_wide(moves, extra=[old_split]), SESSION)
    assert [r["symbol"] for r in b["top"]] == ["GAP", "REAL"]                            # +30% is a move, not a split; 2x is not
    assert (b["n_pool"], b["n_measured"], b["n_excluded"]) == (4, 2, 2)


def test_a_symbol_without_prices_or_with_a_missing_close_is_excluded_never_guessed():
    w = make_wide({"UP1": 5.0, "HOLE": 3.0})
    w.close.loc[DATES[-1], "HOLE"] = np.nan
    b = bd.compute(listed("UP1", "HOLE", "GHOST"), w, SESSION)                            # GHOST has no column at all
    assert [r["symbol"] for r in b["top"]] == ["UP1"] and b["n_excluded"] == 2 and b["n_measured"] == 1


def test_closed_at_the_top_of_the_range_and_above_the_prior_20_day_high():
    moves = {"TOP": 2.0, "MID": 2.0, "FLATDAY": 2.0}
    hl = {"TOP": (102.0, 99.0),                 # close 102 = high -> position 1.0
          "MID": (110.0, 98.0),                 # (102 - 98) / 12 = 0.33
          "FLATDAY": (102.0, 102.0)}            # zero range: no "top of the range" claim
    b = bd.compute(listed(*moves), make_wide(moves, hl), SESSION)
    by = {r["symbol"]: r for r in b["top"]}
    assert by["TOP"]["at_day_high"] is True and by["TOP"]["range_pos"] == pytest.approx(1.0)
    assert by["MID"]["at_day_high"] is False and by["MID"]["range_pos"] == pytest.approx(4 / 12)
    assert by["FLATDAY"]["at_day_high"] is False and by["FLATDAY"]["range_pos"] is None
    assert b["at_day_high"] == 1
    # the prior 20 sessions' highest high is 102 (100 * 1.02): TOP closes at 102 (not above), so the 20-day flag is False for all three
    assert not any(r["above_20d_high"] for r in b["top"]) and b["above_20d_high"] == 0
    up = bd.compute(listed("UP1"), make_wide({"UP1": 5.0}), SESSION)                      # 105 > 102
    assert up["top"][0]["above_20d_high"] is True and up["above_20d_high"] == 1


def test_sessions_listed_first_listed_and_the_latest_group_come_from_the_stored_lists():
    rows = pd.concat([listed("UP1", sessions=[PRIOR[0], PRIOR[2], PRIOR[2]], category="near_breakout"),
                      listed("UP1", sessions=[PRIOR[4]], category="breakout")], ignore_index=True)
    r = bd.compute(rows, make_wide({"UP1": 5.0}), SESSION)["top"][0]
    assert r["listed_sessions"] == 3 and r["first_listed"] == PRIOR[0] and r["last_listed"] == PRIOR[4] and r["category"] == "breakout"
    assert len(r["closes"]) == bd.CHART_SESSIONS and r["closes"][-1] == pytest.approx(105.0) and r["dates"][-1] == SESSION


def test_the_window_lists_the_distinct_stored_sessions():
    b = bd.compute(listed("UP1", sessions=PRIOR), make_wide({"UP1": 5.0}), SESSION)
    assert b["window"] == PRIOR and b["n_sessions"] == 5


# ================================================================== the post
def _board():
    moves = {"UP1": 13.7, "UP2": 11.7, "UP3": 10.3, "UP4": 8.4, "UP5": 4.6, "DN": -1.0}
    return bd.compute(listed(*moves, sessions=PRIOR), make_wide(moves), SESSION)


def _ctx(board):
    return cx.Ctx(session=SESSION, board=board)


def test_the_board_post_is_valid_html_within_the_caption_limit_and_free_of_advice_words():
    post = cx.build_post("board", _ctx(_board()))
    assert post.kind == "board" and post.image and post.button is False
    assert tg_html.problems(post.text, cx.CAPTION_LIMIT) == []
    assert text_length(post.text) <= cx.CAPTION_LIMIT
    plain = re.sub(r"<[^>]+>", "", post.text)
    assert BANNED.findall(plain) == [] and not re.search(r"investment advice", plain, re.I)      # the disclaimer lives in the pinned post
    assert "1st" in plain and "2nd" in plain and "3rd" in plain


def test_the_board_counts_add_up_to_the_pool_by_naming_the_flat_stocks():
    b = _board()
    b["n_measured"] += 3                                                                       # three stocks closed unchanged
    plain = re.sub(r"<[^>]+>", "", cx.build_post("board", _ctx(b)).text)
    assert "5 up, 1 down, 3 flat today" in plain


def test_the_board_post_states_the_whole_pool_next_to_the_podium():
    plain = re.sub(r"<[^>]+>", "", cx.build_post("board", _ctx(_board())).text)
    assert "The 6 stocks the lists carried in the last 5 sessions: 5 up, 1 down today" in plain          # the counts add up to the group (no flat here)
    assert "Ranked by today's % change among the stocks that closed higher." in plain and "say nothing about later ones" not in plain


def test_ranks_beyond_the_podium_sit_in_a_collapsed_quote_only_when_there_are_any():
    text = cx.build_post("board", _ctx(_board())).text
    assert "<blockquote expandable>4. " in text and "More: ranks 4–5" in text
    few = bd.compute(listed("UP1", "UP2", "UP3", sessions=PRIOR), make_wide({"UP1": 3.0, "UP2": 2.0, "UP3": 1.0}), SESSION)
    assert "expandable" not in cx.build_post("board", _ctx(few)).text


def test_one_place_only_still_makes_a_valid_post_and_picture():
    one = bd.compute(listed("UP1", "DN", sessions=PRIOR), make_wide({"UP1": 3.0, "DN": -1.0}), SESSION)
    post = cx.build_post("board", _ctx(one))
    assert "1st" in post.text and "2nd" not in post.text and tg_html.problems(post.text, cx.CAPTION_LIMIT) == []
    assert Image.open(io.BytesIO(post.image)).size[0] == 1080


def test_the_board_post_is_none_without_a_board():
    assert cx.post_board(_ctx(None)) is None and cx.post_board(_ctx({})) is None


def test_the_board_picture_is_a_1080_wide_png_and_survives_a_flat_leader_chart():
    b = _board()
    img = Image.open(io.BytesIO(cc.render_board_card(b)))
    assert img.size[0] == 1080 and img.size[1] > 1100
    b["top"][0]["closes"] = [50.0] * len(b["top"][0]["closes"])                          # zero-span chart must not divide by zero
    b["top"][0]["first_listed"] = date(2000, 1, 1)                                       # a marker date outside the window is simply not drawn
    assert Image.open(io.BytesIO(cc.render_board_card(b))).size[0] == 1080


def test_board_is_a_known_kind_and_never_part_of_the_rotation():
    assert "board" in cx.KINDS and "board" in cx.BUILDERS
    for d in pd.bdate_range("2026-09-01", periods=40):
        assert "board" not in cx.pick_kinds(d.date())                                    # it is sent daily by the sender, in addition to the rotating post


# ================================================================== deeper, collapsible digest lists
def _row(sym, ret, rvol=2.0, range_atr=1.5, dv20=5e7, cat="breakout"):
    return {"symbol": sym, "cat": cat, "prev_cat": cat, "new": False, "close": 20.0 + ret, "ret1_pct": ret, "atr": 1.0, "dv20": dv20,
            "rvol": rvol, "range_atr": range_atr, "below_high_pct": None}


def _deep_digest(n=40):
    rows = [_row(f"S{i:02d}", 30.0 - i * 0.5, rvol=1.0 + (i * 7 % n) / 4, range_atr=1.0 + (i * 11 % n) / 8) for i in range(n)]
    return db_.build_digest(rows, top_n=15, min_dv=0)


def test_each_list_shows_five_rows_open_and_the_rest_collapsed():
    d = _deep_digest()
    text = fmt.format_group("breakout", d, 15, 5)
    assert text.count("<blockquote>") == 3 and text.count("<blockquote expandable>") == 3
    assert text.count("More: ranks 6–15 · tap to expand") == 3
    for lst in text.split("<b>Top ")[1:]:
        open_part, _, collapsed = lst.partition("<blockquote expandable>")
        assert [int(x) for x in re.findall(r"^(\d+)\. ", re.sub(r"<[^>]+>", "", open_part), re.M)] == [1, 2, 3, 4, 5]
        assert [int(x) for x in re.findall(r"^(\d+)\. ", re.sub(r"<[^>]+>", "", collapsed), re.M)] == list(range(6, 16))


def test_a_shallow_list_has_no_collapsed_part_and_show_none_means_no_collapse():
    d = db_.build_digest([_row("A", 5.0), _row("B", 4.0)], top_n=15, min_dv=0)
    assert "expandable" not in fmt.format_group("breakout", d, 15, 5)
    assert "expandable" not in fmt.format_group("breakout", _deep_digest(), 15, None)      # the historic behaviour: show every row open
    assert fmt.format_group("breakout", _deep_digest(), 5).count("<blockquote>") == 3


def test_group_messages_fit_telegram_by_parsed_length_and_keep_every_row_exactly_once():
    d = _deep_digest()
    for cat in ("breakout", "near_breakout"):
        msgs = fmt.group_messages(cat, d, 15, 5)
        assert all(text_length(m) <= fmt.MESSAGE_LIMIT and tg_html.problems(m, 4096) == [] for m in msgs)
        joined = "\n".join(msgs)
        assert sum(len(re.findall(r"<b><a href", m)) for m in msgs) == len(re.findall(r"<b><a href", joined))
    only = fmt.group_messages("breakout", d, 15, 5)
    assert len(only) == 1 and fmt.group_messages("breakout", d, 15, 5, limit=10 ** 6) == only


def test_a_message_over_the_limit_continues_at_a_list_boundary_and_names_the_group_again():
    d = _deep_digest()
    msgs = fmt.group_messages("breakout", d, 15, 5, limit=1500)
    assert len(msgs) >= 2 and all(tg_html.problems(m, 4096) == [] for m in msgs)
    assert msgs[1].startswith("<b>BREAKOUT</b> (continued)")
    every = "\n".join(msgs)
    assert every.count("<b>Top gainers</b>") == every.count("<b>Top ATR</b>") == every.count("<b>Top volume</b>") == 1     # no list is cut or repeated
    assert every.count("<blockquote") == every.count("</blockquote>")


def test_parsed_length_ignores_tags_and_link_urls_and_unescapes_entities():
    row = '1. ★ <b><a href="https://finviz.com/quote.ashx?t=AAPL&amp;p=d">AAPL</a></b> $9.35 ▲13.2%'
    assert text_length(row) == len("1. ★ AAPL $9.35 ▲13.2%") and len(row) > text_length(row) + 50
    assert text_length("S&amp;P &lt;500&gt;") == len("S&P <500>")


def test_split_message_does_not_split_a_message_whose_parsed_length_fits():
    rows = [f'{i}. <b><a href="https://finviz.com/quote.ashx?t=SYM{i:02d}&amp;p=d">SYM{i:02d}</a></b> $12.34 ▲5.6%' for i in range(60)]
    text = "<blockquote>" + "\n".join(rows) + "</blockquote>"
    assert len(text) > 4000 >= text_length(text)                                        # raw is over the limit, parsed is not
    assert split_message(text) == [text]                                                 # a cut would have left an unclosed <blockquote>


def test_the_star_counts_only_the_top_five_ranks_of_two_or_more_lists():
    assert is_starred({"gainers": 1, "atr": 5}) and is_starred({"gainers": 3, "atr": 4, "volume": 15})
    assert not is_starred({"gainers": 1, "atr": 6}) and not is_starred({"gainers": 1}) and not is_starred({}) and not is_starred(None)
    assert not is_starred({"gainers": 9, "atr": 12, "volume": 15})                        # in three lists, but deep in each: no star
    assert starred_lists({"gainers": 3, "atr": 4, "volume": 15}) == ["gainers", "atr"] and starred_lists({"gainers": 1, "atr": 6}) == []


def test_deeper_lists_do_not_dilute_the_star_in_the_digest():
    # 20 stocks that rank identically in all three lists: with 15-deep lists all 15 are in three lists, but only the first five are starred
    rows = [_row(f"S{i:02d}", 30.0 - i * 0.3, rvol=9.0 - i * 0.1, range_atr=9.0 - i * 0.1) for i in range(20)]
    d = db_.build_digest(rows, top_n=15, min_dv=0)
    b = d["boards"]["breakout"]
    assert len(b["membership"]) == 15 and sorted(b["multi"]) == ["S00", "S01", "S02", "S03", "S04"]
    text = fmt.format_group("breakout", d, 15, 5)
    assert text.count("★") == 3 * 5                                                       # five starred rows in each of the three lists
    assert "★ = in the top 5 of more than one list" in fmt.format_header(date(2026, 9, 18), pd.Timestamp("2026-09-19 06:00").to_pydatetime(),
                                                                         d, 3000, {"up": 1, "down": 1})
