"""Member features (CHANNEL_CONTENT_MILESTONES.md M4): full lists, aligned timeframes, a stock's past breakouts, the week on the user's own lists,
and the scan file - as pure screens, and as complete journeys through the real dispatcher (fake Telegram, in-memory store)."""
import csv
import io
import os
import re
import sys
from datetime import date, timedelta
from decimal import Decimal as Dec

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

import tg_html  # noqa: E402
from alerts import insights, screens  # noqa: E402

from qa_harness import SESSION, SESSION_DATE, STOCKS, TODAY, buttons, documents, drive, edits, msg, sent, service, stock, tap  # noqa: E402

BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                    r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money)\b", re.I)
strip = lambda t: re.sub(r"<[^>]+>", "", t)                                          # noqa: E731


def rows(n=40):
    out = []
    for i in range(n):
        out.append(stock(f"S{i:02d}", "breakout", 10 + i, ret=float(i), rvol=1 + (i % 7), rng=1 + (i % 5) / 2, below=None, ranks=None))
    return out


def clean(screen):
    assert tg_html.problems(screen.text) == [], tg_html.problems(screen.text)
    assert BANNED.findall(strip(screen.text)) == [], BANNED.findall(strip(screen.text))
    for row in screen.rows:
        assert len(row) <= 3
        for b in row:
            assert b.text and len((b.data or "").encode()) <= 64, b
    return screen


# ================================================================== D9 full lists
def test_the_full_list_sorts_by_the_chosen_measure_pages_and_puts_unknowns_last():
    data = rows(40)
    data[3]["rvol"] = None                                                            # an unknown value never floats to the top
    s = clean(insights.full_list(SESSION, data, "b", "v", 0, {}, TODAY))
    first = re.findall(r"<b>(S\d\d)</b>", s.text)
    assert len(first) == insights.FULL_PAGE_SIZE and "S03" not in first
    assert "sorted by volume" in s.text and "40 stocks closed above the prior 20-day high" in s.text
    last_page = clean(insights.full_list(SESSION, data, "b", "v", 99, {}, TODAY))      # a page past the end clamps instead of failing
    assert "S03" in last_page.text and "3/3" in [b.text for r in last_page.rows for b in r]


def test_the_full_list_buttons_carry_the_context_back_and_the_order_toggles():
    s = insights.full_list(SESSION, rows(20), "b", "a", 1, {"S05": "watch"}, TODAY)
    flat = [(b.text, b.data) for r in s.rows for b in r]
    assert ("● ATR", "fl:b:a:0") in flat and ("Gainers", "fl:b:g:0") in flat and ("Volume", "fl:b:v:0") in flat
    assert ("Aligned timeframes", "al:0") in flat and ("‹ Today's lists", "td:b:0") in flat
    assert any(d and d.endswith(":fba1") for _, d in flat if d and d.startswith("sc:"))       # a card opened from here remembers group, order and page
    assert screens.back_button("fba1").data == "fl:b:a:1" and "All breakout" in screens.back_button("fba1").text


def test_the_context_parser_still_reads_the_old_codes_and_rejects_garbage():
    assert screens.parse_ctx("tb0") == ("t", "b", 0) and screens.parse_ctx("p2") == ("p", "", 2) and screens.parse_ctx("w") == ("w", "", 0)
    assert screens.parse_ctx("x") == ("x", "", 0) and screens.parse_ctx("t") == ("x", "", 0) and screens.parse_ctx("fxz9") == ("x", "", 0)
    assert screens.parse_ctx("fng12") == ("f", "ng", 12)


def test_the_full_list_says_so_when_there_is_no_data_or_no_stock():
    assert "no scan data" in insights.full_list(None, [], "b", "g", 0, {}).text
    assert "No stock in this group today." in insights.full_list(SESSION, [], "n", "g", 0, {}, TODAY).text


# ================================================================== D10 aligned timeframes
def test_alignment_needs_enough_stored_bars_and_says_none_when_it_cannot_tell():
    r = {"close": 100.0}
    assert insights.alignment(r, None) == (None, None)
    assert insights.alignment(r, {"hi100": 102, "hi252": 130, "n": 252}) == (True, False)
    assert insights.alignment(r, {"hi100": 102, "hi252": 130, "n": 150}) == (True, None)          # < 200 bars: no 52-week verdict
    assert insights.alignment(r, {"hi100": 102, "hi252": 101, "n": 50}) == (None, None)


def test_the_aligned_screen_ranks_both_first_counts_short_history_and_never_lists_a_non_match():
    data = [stock("BOTH", "breakout", 100, ret=1, rvol=2, rng=1, below=None), stock("WEEK", "breakout", 100, ret=9, rvol=2, rng=1, below=None),
            stock("FAR", "breakout", 100, ret=5, rvol=2, rng=1, below=None), stock("NEW", "breakout", 100, ret=7, rvol=2, rng=1, below=None)]
    highs = {"BOTH": {"hi100": 101, "hi252": 102, "n": 252}, "WEEK": {"hi100": 102, "hi252": 150, "n": 252},
             "FAR": {"hi100": 150, "hi252": 160, "n": 252}, "NEW": {"hi100": 100, "hi252": 100, "n": 30}}
    s = clean(insights.aligned_screen(SESSION, data, highs, {}, 0, TODAY))
    order = re.findall(r"<b>([A-Z]+)</b>", s.text)
    assert order[:2] == ["BOTH", "WEEK"] and "FAR" not in order and "NEW" not in order
    assert "2 of them also closed within 3%" in s.text and "(1 both)" in s.text
    assert "Not enough stored history to measure: 1 for the 20-week high, 1 for the 52-week high" in s.text


def test_the_aligned_screen_with_no_match_says_so():
    s = clean(insights.aligned_screen(SESSION, [stock("A", "breakout", 100, ret=1, rvol=1, rng=1, below=None)],
                                      {"A": {"hi100": 200, "hi252": 200, "n": 252}}, {}, 0, TODAY))
    assert "None of today's breakouts is within 3%" in s.text


# ================================================================== D13 a stock's past breakouts
def _hist(**kw):
    base = {"n_total": 30, "n_matured": 28, "first_year": 2018, "n_risk": 14, "n_up3": 3,
            "rows": [{"date": date(2026, 8, 12), "risk": True, "up3": False}, {"date": date(2026, 7, 1), "risk": False, "up3": True},
                     {"date": date(2026, 6, 2), "risk": False, "up3": False}]}
    base.update(kw)
    return base


def test_the_history_screen_states_counts_and_each_outcome_in_plain_words():
    s = clean(insights.history_screen("MSTR", _hist(), "tb0"))
    assert "30 breakouts; 28 have been followed for 20 sessions" in s.text
    assert "14 fell to a level 2× ATR below the breakout price" in s.text and "3 rose to a level 6× ATR above it before that happened" in s.text
    assert "12 Aug 2026 · fell to the 2× ATR level" in s.text and "01 Jul 2026 · rose to the 6× ATR level first" in s.text
    assert "neither level within 20 sessions" in s.text and "not a forecast" in s.text
    assert [(b.text, b.data) for r in s.rows for b in r] == [("‹ MSTR", "sc:MSTR:tb0")]


def test_the_history_screen_warns_about_small_samples_and_handles_no_history():
    assert "Only a few cases" in insights.history_screen("X", _hist(n_total=4, n_matured=3, n_risk=1, n_up3=0), "x").text
    none = clean(insights.history_screen("X", {"n_total": 0}, "x"))
    assert "No 20-day-high breakout is stored" in none.text
    assert "No 20-day-high breakout is stored" in insights.history_screen("X", None, "x").text


# ================================================================== D15 the week on the user's own lists
def _closes(values, end=SESSION_DATE):
    n = len(values)
    return [(end - timedelta(days=n - 1 - i), Dec(str(v))) for i, v in enumerate(values)]


def test_week_rows_measure_five_sessions_with_decimal_maths_and_report_shares_value():
    tracked = [{"symbol": "AAA", "kind": "hold", "shares": Dec("10")}, {"symbol": "BBB", "kind": "watch", "shares": None}]
    closes = {"AAA": _closes([100, 101, 102, 103, 104, 110]), "BBB": _closes([50, 49, 48, 47, 46, 45])}
    r = {x["symbol"]: x for x in insights.week_rows(tracked, closes)}
    assert r["AAA"]["pct"] == Dec("10") and r["AAA"]["change"] == Dec("100")                  # (110-100) x 10 shares
    assert r["BBB"]["pct"] == Dec("-10") and r["BBB"]["change"] is None                        # a watchlist stock has no value


def test_week_rows_never_invent_a_move_for_short_missing_or_split_like_series():
    tracked = [{"symbol": s, "kind": "watch", "shares": None} for s in ("SHORT", "NONE", "SPLIT")]
    closes = {"SHORT": _closes([1, 2, 3]), "SPLIT": _closes([100, 101, 50.5, 50.6, 50.7, 50.8])}      # 101 -> 50.5 is a 2-for-1 shaped jump
    r = {x["symbol"]: x for x in insights.week_rows(tracked, closes)}
    assert r["SHORT"]["pct"] is None and r["SHORT"]["note"] == "not enough stored days"
    assert r["NONE"]["pct"] is None and r["NONE"]["note"] == "no recent price is stored"
    assert r["SPLIT"]["pct"] is None and "adjusted" in r["SPLIT"]["note"]


def test_the_week_screen_sorts_best_first_shows_na_with_the_reason_and_the_portfolio_change():
    tracked = [{"symbol": "AAA", "kind": "hold", "shares": Dec("10")}, {"symbol": "BBB", "kind": "watch", "shares": None},
               {"symbol": "CCC", "kind": "watch", "shares": None}]
    closes = {"AAA": _closes([100, 101, 102, 103, 104, 110]), "BBB": _closes([50, 49, 48, 47, 46, 45])}
    s = clean(insights.week_screen(insights.week_rows(tracked, closes), SESSION, TODAY))
    assert s.text.index("AAA") < s.text.index("BBB") and "CCC</b>  n/a - no recent price is stored" in s.text
    assert "$100.00 change in value" in s.text and "portfolio" in s.text and "watchlist" in s.text
    assert "no stocks on your lists yet" in insights.week_screen([], SESSION, TODAY).text


# ================================================================== D9 the scan file
def test_the_scan_csv_lists_every_stock_with_empty_cells_for_unknowns():
    data = [stock("AAA", "breakout", 10.5, ret=2.0, rvol=3.0, rng=1.5, below=None, atr=0.5), stock("BBB", None, 20.0, ret=-1.0, rvol=None, rng=None, below=None)]
    text = insights.scan_csv(SESSION, data)
    got = list(csv.reader(io.StringIO(text)))
    assert got[0] == insights.CSV_HEADER
    assert got[1][:3] == ["2026-09-18", "AAA", "Breakout"] and got[1][3] == "10.5000" and got[1][8] == "0.5000"
    assert got[2][1] == "BBB" and got[2][5] == "" and got[2][6] == "" and got[2][8] == ""          # unknown = empty, never zero


# ================================================================== journeys through the real dispatcher
def _svc(**store):
    svc = service(STOCKS)
    svc.store.set_access(5, "active")
    svc.store.acknowledge(5)
    for k, v in store.items():
        setattr(svc.store, k, v)
    return svc


def test_today_offers_the_full_list_and_the_full_list_opens_a_card_that_leads_back():
    svc = _svc()
    svc, calls = drive([tap(5, "td:b:0", 1), tap(5, "fl:b:g:0", 2), tap(5, "sc:MSTR:fbg0", 3)], svc)
    e = edits(calls)
    assert ("All stocks", "fl:b:g:0") in buttons(e[0])                                               # the harness session has no counts: a neutral label
    assert "All breakout" in e[1].text and "MSTR" in e[1].text
    assert ("‹ All breakout", "fl:b:g:0") in buttons(e[2]) and ("Past breakouts", "hs:MSTR:fbg0") in buttons(e[2])


def test_the_full_command_and_the_aligned_command_answer_with_data():
    svc = _svc(highs={"MSTR": {"hi100": 154.0, "hi252": 155.0, "n": 252}})
    _, calls = drive([msg(5, "/full"), msg(5, "/full near"), msg(5, "/aligned")], svc)
    texts_ = [m.text for m in sent(calls)]
    assert "All breakout" in texts_[0] and "All near breakout" in texts_[1]
    assert "Breakouts on longer timeframes" in texts_[2] and "MSTR" in texts_[2] and "near 20-week high · near 52-week high" in texts_[2]


def test_history_command_and_button_show_the_stored_cases_and_reject_bad_input():
    hist = {"MSTR": _hist()}
    svc = _svc(history=hist)
    _, calls = drive([msg(5, "/history MSTR"), msg(5, "/history"), msg(5, "/history NOT A SYMBOL"), tap(5, "hs:MSTR:tb0", 2)], svc)
    s = sent(calls)
    assert "MSTR</b> · past 20-day-high breakouts since 2018" in s[0].text
    assert "Send one symbol" in s[1].text and "Send one symbol" in s[2].text
    assert "past 20-day-high breakouts" in edits(calls)[0].text


def test_the_week_command_reads_only_the_callers_own_list():
    svc = _svc()
    svc.store.prices["MSTR"] = _closes([100, 101, 102, 103, 104, 110])
    svc.store.upsert_tracked(5, "MSTR", "watch", Dec("100"), "close", SESSION_DATE, None, Dec("100"))
    svc.store.set_access(6, "active")
    svc.store.acknowledge(6)
    _, calls = drive([msg(5, "/week"), msg(6, "/week")], svc)
    mine, theirs = sent(calls)[0].text, sent(calls)[1].text
    assert "MSTR" in mine and "▲10.0%" in mine
    assert "MSTR" not in theirs and "no stocks on your lists" in theirs                              # isolation: another member's list is never shown


def test_the_scan_command_sends_a_csv_document_of_the_whole_scan():
    svc = _svc()
    _, calls = drive([msg(5, "/scan")], svc)
    doc = documents(calls)[0]
    assert doc.document.filename == "first_light_scan_2026-09-18.csv"
    body = doc.document.data.decode("utf-8-sig")
    assert body.splitlines()[0].startswith("us_close_date,symbol,group") and body.count("\n") == len(STOCKS) + 1


def test_the_new_commands_refuse_strangers_and_are_not_answered_in_groups():
    svc = service(STOCKS)                                                                             # nobody is enrolled
    _, calls = drive([msg(9, "/scan"), msg(9, "/full"), msg(9, "/week")], svc, enroll=False)
    assert not documents(calls) and sent(calls)                                                        # strangers get the refusal, never a file
    assert all("MSTR" not in m.text and "S&P" not in m.text for m in sent(calls))
    svc2 = _svc()
    _, gcalls = drive([msg(5, "/scan", chat_type="group"), msg(5, "/full", chat_type="group")], svc2)
    assert not documents(gcalls) and all("private chat" in m.text.lower() for m in sent(gcalls))     # a group only gets the neutral pointer


def test_the_help_screen_lists_the_new_commands():
    text = screens.help_screen(False).text
    for cmd in ("/full", "/aligned", "/history AAPL", "/week", "/scan"):
        assert cmd in text
    assert BANNED.findall(strip(text)) == []


def test_a_database_outage_fails_closed_for_the_new_commands():
    svc = _svc()
    svc.store.broken = False
    svc, calls = drive([msg(5, "/full")], svc)
    svc.store.broken = True
    _, calls = drive([msg(5, "/scan"), msg(5, "/week"), tap(5, "fl:b:g:0", 2)], svc)
    assert not documents(calls)                                                                       # no data, no crash-loop: an error reply or nothing


# ================================================================== M5.3 custom screens
def _scan():
    return [stock("AAA", "breakout", 12.0, ret=5.0, rvol=4.0, rng=2.0, below=None), stock("BBB", "breakout", 8.0, ret=9.0, rvol=6.0, rng=3.0, below=None),
            stock("CCC", "near_breakout", 30.0, ret=1.0, rvol=2.0, rng=1.0, below=1.2), stock("DDD", "near_breakout", 55.0, ret=-2.0, rvol=None, rng=None, below=0.4),
            stock("EEE", None, 70.0, ret=0.5, rvol=1.0, rng=1.0, below=None)]


def test_the_screen_grammar_accepts_only_the_documented_forms():
    spec, err = insights.parse_screen("near price>=10 below<1.5 sort=vol top=5")
    assert err is None and spec == {"group": "near", "filters": [("price", ">=", 10.0), ("below", "<", 1.5)], "sort": "vol", "top": 5}
    assert insights.parse_screen("")[0]["group"] == "breakout" and insights.parse_screen("")[0]["top"] == insights.SCREEN_DEFAULT_TOP
    for bad in ("price>ten", "__import__('os')", "vol>3;drop", "sort=marketcap", "top=0", "top=31", "top=x", "price>10>5", "close>1", "vol>1e9"):
        spec, err = insights.parse_screen(bad)
        assert spec is None and err, bad
    assert insights.parse_screen(" ".join(["vol>1"] * 7))[1] == "Use at most 6 filters."


def test_nothing_typed_is_ever_evaluated_the_error_echo_is_short_and_escaped():
    _, err = insights.parse_screen("<script>" + "x" * 200)
    assert err and len(err) < 80 and tg_html.problems(err.replace("<", "&lt;").replace(">", "&gt;")) == []


def test_run_screen_filters_sorts_and_never_matches_an_unknown_value():
    spec = insights.parse_screen("all vol>1.5")[0]
    assert [r["symbol"] for r in insights.run_screen(spec, _scan())] == ["BBB", "AAA", "CCC"]      # DDD has no volume figure: it cannot match; EEE has no group
    assert [r["symbol"] for r in insights.run_screen(insights.parse_screen("scan sort=price")[0], _scan())] == ["EEE", "DDD", "CCC", "AAA", "BBB"]
    assert [r["symbol"] for r in insights.run_screen(insights.parse_screen("breakout price<10")[0], _scan())] == ["BBB"]
    assert [r["symbol"] for r in insights.run_screen(insights.parse_screen("near below<1")[0], _scan())] == ["DDD"]
    assert insights.run_screen(insights.parse_screen("breakout vol>100")[0], _scan()) == []


def test_the_screen_screen_echoes_the_rules_counts_matches_and_offers_cards():
    spec = insights.parse_screen("all vol>1.5 top=2")[0]
    s = clean(insights.screen_screen(SESSION, spec, insights.run_screen(spec, _scan()), {"BBB": "watch"}, TODAY))
    assert "Breakout + Near breakout · volume × &gt; 1.5 · sorted by day change %" in s.text
    assert "3 stocks match, showing the first 2" in s.text and "✓" in s.text
    assert [b.data for r in s.rows for b in r] == ["sc:BBB:x", "sc:AAA:x"]
    empty = clean(insights.screen_screen(SESSION, insights.parse_screen("breakout vol>100")[0], [], {}, TODAY))
    assert "0 stocks match" in empty.text and "Loosen a filter" in empty.text


def test_the_screen_command_answers_usage_errors_and_results_and_refuses_strangers():
    svc = _svc()
    _, calls = drive([msg(5, "/screen"), msg(5, "/screen breakout vol>3", 2), msg(5, "/screen vol>abc", 3), msg(5, "/screen all sort=price top=3", 4)], svc)
    out = [m.text for m in sent(calls)]
    assert "Filter today's scan" in out[0] and "Groups: breakout" in out[0]
    assert "Your screen" in out[1] and "MSTR" in out[1]
    assert "I could not read" in out[2] and "Filter today's scan" in out[2]
    assert "Your screen" in out[3]
    stranger = service(STOCKS)
    _, calls = drive([msg(9, "/screen all")], stranger, enroll=False)
    assert all("MSTR" not in m.text for m in sent(calls))


def test_help_lists_the_screen_command():
    assert "/screen breakout vol&gt;3" in screens.help_screen(False).text
