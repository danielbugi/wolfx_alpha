"""Watchlist + Portfolio service (tracker.py): input parsing, add / move / re-price / remove semantics, caps, per-user isolation, the export,
and a real-Postgres round trip of the tracker SQL (skips itself when the database is unreachable)."""
import csv
import io
import os
import sys
from datetime import date, timedelta
from decimal import Decimal as Dec

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import tracker as tr  # noqa: E402
from alerts import bot_service as bs  # noqa: E402

from qa_harness import MemoryStore, STOCKS, SESSION_DATE, TODAY, stock  # noqa: E402


def svc(stocks=STOCKS, today=TODAY, **kw):
    store = MemoryStore(stocks, **kw)
    return tr.TrackerService(store, today=lambda: today), store


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("text,expected", [("140.5", Dec("140.5")), ("$1,405.50", Dec("1405.50")), (".5", Dec("0.5")), ("0", Dec("0")),
                                           ("1,000", Dec("1000")), ("$12", Dec("12"))])
def test_parse_number_accepts_plain_numbers(text, expected):
    assert tr.parse_number(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "1e9", "-5", "5.", "1,40", "1,4000", "140,5", "９９", "١٢", "nan", "inf", "0x10", "1 000", "$", "1.2.3", "+5"])
def test_parse_number_rejects_everything_else(bad):
    assert tr.parse_number(bad) is None                            # exponent / comma decimals / unicode digits / signs / words


def test_parse_add_args_covers_every_documented_form():
    a = tr.parse_add_args("AAPL")
    assert (a.symbols, a.price, a.shares, a.error) == (["AAPL"], None, None, None)
    a = tr.parse_add_args("aapl 140.5")
    assert (a.symbols, a.price, a.shares) == (["AAPL"], Dec("140.5"), None)
    a = tr.parse_add_args("$aapl 140.5 10")
    assert (a.symbols, a.price, a.shares) == (["AAPL"], Dec("140.5"), Dec("10"))
    a = tr.parse_add_args("AAPL MSFT, COIN;MSTR aapl")
    assert a.symbols == ["AAPL", "MSFT,", "COIN", "MSTR"] or a.symbols == ["AAPL", "MSFT", "COIN", "MSTR"] or a.error       # separators tolerated or refused, never guessed
    assert tr.parse_add_args("BRK.B 480 2").symbols == ["BRK.B"] and tr.parse_add_args("MSTR. 5").symbols == ["MSTR"]
    assert tr.parse_add_args("").error == "no_symbol" and tr.parse_add_args("140 10").error == "no_symbol"
    assert tr.parse_add_args("AAPL 1 2 3").error == "too_many_numbers"
    assert tr.parse_add_args("AAPL MSFT 140").error == "price_needs_one_symbol"
    for bad in ("AAPL 0", "AAPL -1", "AAPL 1e3", "AAPL 10000001", "AAPL 5 0", "AAPL 5 2000000000", "AAPL <b>", "'; DROP--", "ＭＳＴＲ", "AAPL 140,5"):
        assert tr.parse_add_args(bad).error in ("bad_number", "no_symbol"), bad


def test_parse_price_shares_is_strict():
    assert tr.parse_price_shares("140.5") == (Dec("140.5"), None, None)
    assert tr.parse_price_shares("140.5   10") == (Dec("140.5"), Dec("10"), None)
    assert tr.parse_price_shares("$1,405.50 0.25") == (Dec("1405.50"), Dec("0.25"), None)
    for bad in ("", "abc", "1 2 3", "0", "-1 5", "5 0", "5 -1", "1e5", "5,5", "10000001", "5 2000000000"):
        assert tr.parse_price_shares(bad) == (None, None, "bad_number"), bad


# ------------------------------------------------------------------ add / move / re-price
def test_watch_defaults_to_the_last_close_and_remembers_the_day():
    s, store = svc()
    r = s.add_watch(1, "MSTR")
    assert (r.code, r.kind, r.ref_price, r.ref_source, r.last_close) == ("ok", "watch", Dec("153.92"), "close", Dec("153.92"))
    row = store.tracked(1)[0]
    assert row["ref_date"] == TODAY and row["first_price"] == Dec("153.92") and row["shares"] is None


def test_watch_at_the_users_own_price_is_marked_entered():
    s, store = svc()
    r = s.add_watch(1, "MSTR", Dec("140.5"))
    assert (r.code, r.ref_price, r.ref_source) == ("ok", Dec("140.5"), "entered") and r.last_close == Dec("153.92")


def test_adding_again_changes_nothing_but_a_new_price_restarts_the_reference():
    s, store = svc()
    s.add_watch(1, "MSTR", Dec("140"))
    first = store.tracked(1)[0]["first_added_at"]
    r = s.add_watch(1, "MSTR")
    assert r.code == "already" and store.tracked(1)[0]["ref_price"] == Dec("140")           # no silent overwrite
    later = tr.TrackerService(store, today=lambda: TODAY + timedelta(days=2))
    r = later.add_watch(1, "MSTR", Dec("150"))
    row = store.tracked(1)[0]
    assert r.code == "updated" and row["ref_price"] == Dec("150") and row["ref_date"] == TODAY + timedelta(days=2)
    assert row["first_price"] == Dec("140") and row["first_added_at"] == first               # the very first add is remembered


def test_moving_a_watched_stock_to_the_portfolio_keeps_its_first_added_facts():
    s, store = svc()
    s.add_watch(1, "MSTR", Dec("120"))
    r = s.add_hold(1, "MSTR", Dec("140"), Dec("10"))
    row = store.tracked(1)[0]
    assert r.code == "ok" and r.moved_from_watch and row["kind"] == "hold" and row["shares"] == Dec("10")
    assert row["ref_price"] == Dec("140") and row["first_price"] == Dec("120") and len(store.tracked(1)) == 1     # one row per symbol


def test_a_holding_is_never_downgraded_by_add_watch():
    s, store = svc()
    s.add_hold(1, "MSTR", Dec("140"), Dec("10"))
    r = s.add_watch(1, "MSTR", Dec("100"))
    row = store.tracked(1)[0]
    assert r.code == "already_held" and row["kind"] == "hold" and row["ref_price"] == Dec("140") and row["shares"] == Dec("10")


def test_repricing_a_holding_without_shares_keeps_the_saved_shares():
    s, store = svc()
    s.add_hold(1, "MSTR", Dec("140"), Dec("10"))
    r = s.add_hold(1, "MSTR", Dec("141"), None)
    row = store.tracked(1)[0]
    assert r.code == "updated" and row["ref_price"] == Dec("141") and row["shares"] == Dec("10")
    s.add_hold(1, "MSTR", Dec("141"), Dec("12.5"))
    assert store.tracked(1)[0]["shares"] == Dec("12.5")


def test_a_holding_can_be_added_at_the_last_close_without_shares():
    s, store = svc()
    r = s.add_hold(1, "COIN")
    row = store.tracked(1)[0]
    assert (r.code, row["kind"], row["ref_source"], row["shares"], row["ref_price"]) == ("ok", "hold", "close", None, Dec("194.25"))


def test_unknown_symbols_a_missing_snapshot_and_missing_prices_are_reported_not_guessed():
    s, store = svc()
    assert s.add_watch(1, "ZZZZ").code == "unknown_symbol" and store.tracked(1) == []
    assert s.add_watch(1, "MSTR", Dec("0")).code == "bad_number" and s.add_watch(1, "MSTR", Dec("-3")).code == "bad_number"
    assert tr.TrackerService(MemoryStore(session=None), today=lambda: TODAY).add_watch(1, "MSTR").code == "no_snapshot"
    store.prices.pop("MSTR")                                          # in the scan, but no stored close at all
    store._stocks["MSTR"] = {**store._stocks["MSTR"], "close": None}
    assert s.add_watch(1, "MSTR").code == "no_price" and s.add_watch(1, "MSTR", Dec("140")).code == "ok"          # a typed price still works


def test_the_last_close_falls_back_to_the_snapshot_close_when_stock_prices_has_none():
    s, store = svc()
    store.prices.pop("MSTR")
    assert s.add_watch(1, "MSTR").ref_price == Dec("153.92")


def test_caps_are_per_list_and_apply_to_new_rows_and_moves():
    many = [stock(f"S{i:02d}", close=10 + i) for i in range(30)]
    s, store = svc(many)
    codes = [s.add_watch(1, f"S{i:02d}").code for i in range(27)]
    assert codes.count("ok") == tr.CAP_WATCH and codes[-2:] == ["cap_full", "cap_full"]
    assert s.add_watch(1, "S00").code == "already"                    # an existing row is never blocked by the cap
    assert [s.add_hold(2, f"S{i:02d}", Dec("5"), Dec("1")).code for i in range(27)].count("ok") == tr.CAP_HOLD
    for i in range(tr.CAP_HOLD - 1):                                   # user 1: 24 watched stocks move into the portfolio (S24 stays watched)
        assert s.add_hold(1, f"S{i:02d}", Dec("5"), Dec("1")).code == "ok"
    assert s.add_hold(1, "S26", Dec("5"), Dec("1")).code == "ok"       # the 25th holding, added directly
    assert s.add_hold(1, "S24", Dec("5"), Dec("1")).code == "cap_full"  # a move into a full portfolio is refused ...
    assert [r["kind"] for r in store.tracked(1) if r["symbol"] == "S24"] == ["watch"]     # ... and the stock stays on the watchlist
    assert s.add_hold(1, "S00", Dec("6"), Dec("2")).code == "updated"  # re-pricing a holding is fine at the cap


# ------------------------------------------------------------------ remove / isolation / views
def test_remove_and_erase_only_touch_the_callers_rows():
    s, store = svc()
    s.add_watch(1, "MSTR")
    s.add_hold(1, "COIN", Dec("190"), Dec("5"))
    s.add_watch(2, "MSTR", Dec("100"))
    assert s.remove(1, "MSTR") == 1 and s.remove(1, "MSTR") == 0 and [r["symbol"] for r in store.tracked(2)] == ["MSTR"]
    assert s.delete_everything(1) == 1 and store.tracked(1) == [] and store.tracked(2)[0]["ref_price"] == Dec("100")
    assert s.get(1, "MSTR") is None and s.view(1, "MSTR") is None and s.get(2, "MSTR")["kind"] == "watch"


@pytest.mark.parametrize("evil", ["MSTR' OR '1'='1", "'; DROP TABLE bot_tracked; --", "MSTR%", "*", "", "M" * 40, "ＭＳＴＲ"])
def test_hostile_symbols_never_match_other_users_rows(evil):
    s, store = svc()
    s.add_watch(2, "MSTR")
    assert s.remove(1, evil) == 0 and s.get(1, evil) is None and s.add_watch(1, evil).code == "unknown_symbol"
    assert [r["symbol"] for r in store.tracked(2)] == ["MSTR"]


def test_views_are_sorted_best_first_with_unavailable_last_and_totals_only_for_the_portfolio():
    s, store = svc()
    s.add_watch(1, "MSTR", Dec("100"))                                 # +53.9%
    s.add_watch(1, "COIN", Dec("200"))                                 # -2.9%
    s.add_watch(1, "AAPL", Dec("180"))                                 # +5.2%
    store.facts["AAPL"] = {"jump_dates": [TODAY + timedelta(days=1)], "ref_close": None}          # a split after the reference -> n/a
    views, totals = s.views(1, "watch")
    assert [v.symbol for v in views] == ["MSTR", "COIN", "AAPL"] and views[-1].note == "adjusted" and totals is None
    s.add_hold(1, "MSFT", Dec("400"), Dec("2"))
    views, totals = s.views(1, "hold")
    assert [v.symbol for v in views] == ["MSFT"] and totals.value == Dec("820.00") and totals.cost == Dec("800.00")
    assert s.views(3) == ([], None)


def test_export_csv_is_complete_and_uses_plain_numbers():
    s, store = svc()
    s.add_hold(1, "MSTR", Dec("140"), Dec("10"))
    s.add_watch(1, "COIN")
    store.facts["COIN"] = {"jump_dates": [TODAY + timedelta(days=1)], "ref_close": None}
    rows = list(csv.reader(io.StringIO(s.export_csv(1))))
    assert rows[0] == ["symbol", "list", "your_price", "price_source", "date_added", "shares", "last_close", "close_date",
                       "change_since_added_pct", "note"]
    by = {r[0]: r for r in rows[1:]}
    assert by["MSTR"] == ["MSTR", "portfolio", "140", "entered", TODAY.isoformat(), "10", "153.92", SESSION_DATE.isoformat(), "9.94", ""]
    assert by["COIN"][1] == "watchlist" and by["COIN"][8] == "" and by["COIN"][9] == "adjusted"      # an n/a is empty + a reason, never a number
    assert len(rows) == 3 and s.export_csv(2).count("\n") == 1                                          # another user: header only


# ------------------------------------------------------------------ Postgres (real SQL)
def _pg():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from shared import db
        db.execute_dict_query("SELECT 1 FROM bot_tracked LIMIT 1")
        return db
    except Exception as e:                                                          # noqa: BLE001
        pytest.skip(f"Postgres unreachable or tracker tables missing: {type(e).__name__}")


def test_tracker_sql_round_trip_with_real_postgres():
    """bot_tracked upsert / isolation / cascade, and the price queries (quotes, history_facts with a split, bars) against stock_prices
    using a throw-away symbol; everything is deleted afterwards."""
    db = _pg()
    store, u1, u2, sym = bs.PgStore(db), 990_000_000_201, 990_000_000_202, "ZZQATEST"
    d0 = date.today() - timedelta(days=12)                       # inside the 30-day window the quote query looks back over
    try:
        db.execute_insert("DELETE FROM stock_prices WHERE symbol = %s", (sym,))
        for i, close in enumerate([100, 101, 102, 103, 51.5, 52, 53]):        # a 2-for-1 split appears on day 4 (103 -> 51.5)
            db.execute_insert("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                              (sym, d0 + timedelta(days=i), close, close + 1, close - 1, close, close, 1000))
        # -- tracker rows
        store.upsert_tracked(u1, sym, "watch", Dec("100"), "close", d0, None, Dec("100"))
        store.upsert_tracked(u1, sym, "hold", Dec("101.5"), "entered", d0 + timedelta(days=1), Dec("2.5"), Dec("999"))   # first_price must NOT change
        store.upsert_tracked(u2, sym, "watch", Dec("50"), "close", d0, None, Dec("50"))
        row = store.tracked(u1)[0]
        assert (row["kind"], row["ref_price"], row["shares"], row["first_price"]) == ("hold", Dec("101.5"), Dec("2.5"), Dec("100"))
        assert [r["ref_price"] for r in store.tracked(u2)] == [Dec("50")] and len(store.tracked(u1)) == 1      # isolated, one row per symbol
        assert store.remove_tracked(u2, ["OTHER"]) == 0 and store.remove_tracked(u2, [sym]) == 1 and store.tracked(u1)
        # -- quotes + the price guard inputs
        q = store.quotes([sym, "NOSUCHSYM"])
        assert q[sym]["close"] == Dec("53") and q[sym]["prev_close"] == Dec("52") and "NOSUCHSYM" not in q
        facts = store.history_facts([(sym, d0 + timedelta(days=1))])[sym]
        assert facts["jump_dates"] == [d0 + timedelta(days=4)] and facts["ref_close"] == Dec("101")           # the split day; close on the ref day
        assert store.history_facts([(sym, d0 + timedelta(days=5))])[sym]["jump_dates"] == []                     # split is before this reference
        from alerts import performance as perf
        v = perf.build_view(store.tracked(u1)[0], q[sym], facts)
        assert v.note == perf.NOTE_ADJUSTED and v.since_pct is None                                              # 53 vs 101.5 is a split, not -48%
        # -- bars (chart input) are oldest first
        bars = store.bars(sym, 5)
        assert [b["close"] for b in bars] == [Dec(x) for x in ("102", "103", "51.5", "52", "53")]
        # -- the tracker rows go with the user
        db.execute_insert("DELETE FROM bot_users WHERE telegram_user_id = %s", (u1,))
        assert store.tracked(u1) == []
        # -- news + chart caches
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        item = {"published_at": now, "headline": "H", "source": "s", "url": "https://x.test/1", "url_hash": "a" * 64}
        store.save_news(sym, [item, item], True, now)
        store.save_news(sym, [item], True, now)                                  # the same headline is never stored twice
        assert len(store.news_recent(sym, 10)) == 1 and store.news_state(sym)["ok"] is True
        store.save_news(sym, [], False, now)
        assert store.news_state(sym)["ok"] is False and len(store.news_recent(sym, 10)) == 1
        assert store.chart_file_id(sym, d0) is None
        store.save_chart_file_id(sym, d0, "file-1")
        store.save_chart_file_id(sym, d0, "file-2")
        assert store.chart_file_id(sym, d0) == "file-2"
    finally:
        db.execute_insert("DELETE FROM stock_prices WHERE symbol = %s", (sym,))
        db.execute_insert("DELETE FROM bot_users WHERE telegram_user_id IN (%s, %s)", (u1, u2))
        db.execute_insert("DELETE FROM news_items WHERE symbol = %s", (sym,))
        db.execute_insert("DELETE FROM news_fetched WHERE symbol = %s", (sym,))
        db.execute_insert("DELETE FROM bot_chart_cache WHERE symbol = %s", (sym,))
