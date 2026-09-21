"""Unit tests for the private assistant's building blocks: logic, cost/abuse controls, wording guard and a few wiring checks (driven with
fake updates -- no network). Postgres round-trip tests skip themselves when the database is unreachable. The scenario tests of the whole
assistant (onboarding, lists, cards, tracking) are in test_assistant_flows.py; access in test_bot_qa.py section K.

Run:  python -m pytest mechanism/alerts/tests -q      (from the repo root)
"""
import os
import re
import sys
import warnings
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from alerts import bot_service as bs  # noqa: E402
from alerts import digest_builder as db_  # noqa: E402
from alerts import digest_format as fmt  # noqa: E402
from alerts import snapshot  # noqa: E402
from alerts import texts  # noqa: E402

from qa_harness import (BOT_USERNAME, MemoryStore, drive, keyboard_urls, msg, sent, service, stock, tap,  # noqa: E402
                        texts_sent)


# ------------------------------------------------------------------ pure logic
def test_parse_symbols_validates_shape_dedupes_and_caps():
    assert bs.parse_symbols("aapl, $msft  BRK.B; aapl 123 TOOLONGSYMBOLNAME") == ["AAPL", "MSFT", "BRK.B"]
    assert bs.parse_symbols(None) == [] and bs.parse_symbols("") == []
    assert bs.parse_symbols("A B C D E", limit=3) == ["A", "B", "C"]


def test_rate_limiter_is_a_sliding_window_per_user():
    t = [0.0]
    rl = bs.RateLimiter(3, 60, now=lambda: t[0])
    assert [rl.allow(1) for _ in range(4)] == [True, True, True, False]
    assert rl.allow(2) is True                                                     # another user is unaffected
    t[0] = 61
    assert rl.allow(1) is True                                                     # the window moved on


def test_levels_reply_shows_the_framework_facts_and_the_honest_context():
    text = service().render_levels("mstr", today=date(2026, 9, 21))
    plain = re.sub(r"<[^>]+>", "", text)
    assert "MSTR · ATR risk framework" in plain and "reference price $153.92" in plain
    assert "Group: Breakout · day ▲16.4%" in plain and "ATR(14): $9.35 = 6.1% of price" in plain
    assert "Risk level (2×ATR below the price)" in plain and "$135.22 · ▼12.2% · this distance is 1R" in plain
    assert "1R  $172.62 ▲12.2% (+2×ATR)" in plain and "2R  $191.33 ▲24.3% (+4×ATR)" in plain and "3R  $210.03 ▲36.5% (+6×ATR)" in plain
    assert "Reward-to-risk of these distances: 1:1 · 2:1 · 3:1" in plain
    assert "Avg daily trading value: $50.0M" in plain and "Volume 3.1× its 50-day median" in plain
    assert "roughly break-even" in plain and "not investment advice" in plain.lower()
    visible = text.split("<blockquote expandable>")[0]                                  # what a reader sees before expanding
    assert texts.LEVELS_NOTE in visible and "Nothing says price will reach any level" in visible   # the key caveat is never collapsed
    assert "Range 2.6× ATR" in plain and "× atr" not in plain                            # regression: capitalize() once made it "atr"
    assert "<blockquote expandable>" in text and len(text) < 4096
    assert "0.0% below" not in plain                                                     # breakouts do not show the distance
    near = re.sub(r"<[^>]+>", "", service().render_levels("COIN"))
    assert "Group: Near breakout" in near and "1.0% below 20-day high" in near


def test_levels_never_invent_numbers():
    s = service()
    assert "is not in the daily scan" in s.render_levels("ZZZZ")
    assert "not available for MSFT" in s.render_levels("MSFT")                               # no ATR stored
    assert "not available for WILD" in s.render_levels("WILD")                               # 2 x ATR is not below the price
    assert "Send one symbol" in s.render_levels("") and "Send one symbol" in s.render_levels(None) and "Send one symbol" in s.render_levels("123")
    assert "may be out of date" in s.render_levels("MSTR", today=date(2026, 9, 30))
    assert "no scan data yet" in bs.BotService(MemoryStore(session=None)).render_levels("MSTR")
    assert re.sub(r"<[^>]+>", "", s.render_levels("aapl msft")).startswith("AAPL ·")           # only the first symbol is used


# ------------------------------------------------------------------ wording guard (public audience, educational only)
BANNED = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|"
                    r"profit\w*|winners?|guarantee\w*|alpha)\b", re.I)


def _digest_rows():
    rows = []
    for sym, cat, prev, new in (("AAA", "breakout", "near_breakout", True), ("BBB", "near_breakout", None, True)):
        rows.append({"symbol": sym, "cat": cat, "prev_cat": prev, "new": new, "close": 10.0, "ret1_pct": 2.0, "dv20": 5e7,
                     "rvol": 3.0, "range_atr": 2.0, "below_high_pct": 1.0})
    return rows


def test_every_static_string_is_free_of_advice_style_words():
    """The channel messages and every fixed string of the assistant. (Rendered screens are swept in test_assistant_flows.py.)"""
    s = service()
    digest = db_.build_digest(_digest_rows(), min_dv=0)
    header = fmt.format_header(date(2026, 9, 18), datetime(2026, 9, 20, 3, 0), digest, 2915, {"up": 1, "down": 2}, ["S&P 500 +0.2%"])
    static = [v for k, v in vars(texts).items() if isinstance(v, str) and k.isupper() and k not in ("ONBOARDING",)]
    static += list(texts.ONBOARDING.values()) + list(texts.DEFINITIONS.values())
    strings = [*static, header, *(fmt.format_group(c, digest) for c in db_.LONG_CATEGORIES),
               s.render_levels("MSTR"), s.render_levels("COIN"), s.render_levels("MSFT"), s.render_levels("ZZZ"),
               *(b["text"] for row in fmt.header_keyboard("some_bot")["inline_keyboard"] for b in row)]
    hits = [(m.group(0), x[:60]) for x in strings for m in BANNED.finditer(re.sub(r"<[^>]+>", "", x.replace("{uid}", "1")))]
    assert not hits, hits


def test_disclaimer_says_educational_and_not_advice():
    for t in (texts.DISCLAIMER_SHORT, texts.DISCLAIMER_LONG):
        assert re.search(r"not (an? )?investment advice|not investment advice", re.sub(r"<[^>]+>", "", t), re.I)
    assert "educational" in texts.DISCLAIMER_LONG.lower() and "own decisions" in texts.DISCLAIMER_LONG


def test_popup_texts_fit_telegrams_200_character_limit():
    assert set(texts.DEFINITIONS) == {"atr", "vol", "groups"}
    assert all(len(v) <= 200 for v in texts.DEFINITIONS.values()), {k: len(v) for k, v in texts.DEFINITIONS.items()}
    cb = [b["callback_data"] for row in fmt.header_keyboard("some_bot")["inline_keyboard"] for b in row if "callback_data" in b]
    assert sorted(cb) == sorted("def:" + k for k in texts.DEFINITIONS)
    assert fmt.header_keyboard("some_bot")["inline_keyboard"][-1][0]["url"] == "https://t.me/some_bot?start=ch"


def test_snapshot_row_shape_matches_the_table():
    r = {"symbol": "AAA", "cat": "breakout", "prev_cat": None, "close": 1.0, "ret1_pct": 2.0, "rvol": 3.0, "range_atr": None,
         "below_high_pct": 0.5, "dv20": 9.0}
    row = snapshot._stock_row(date(2026, 9, 18), r)
    assert len(row) == 12 and row[:4] == (date(2026, 9, 18), "AAA", "breakout", None) and row[-2] is None and row[-1] is None
    assert snapshot._stock_row(date(2026, 9, 18), {**r, "list_ranks": {"gainers": 1}})[-2].adapted == {"gainers": 1}
    assert snapshot._stock_row(date(2026, 9, 18), {**r, "atr": 0.5})[-1] == 0.5                      # ATR feeds /levels


# ------------------------------------------------------------------ wiring basics (the full scenarios are in test_assistant_flows.py)
def test_levels_command_needs_the_notice_first_then_answers():
    svc = service()
    svc.store.set_access(5, "active")
    _, calls = drive([msg(5, "/levels MSTR")], svc, enroll=False)
    said = texts_sent(calls)
    assert len(said) == 1 and said[0] == texts.ONBOARDING[1]                                  # the guide starts; no levels before the notice
    _, calls = drive([msg(5, "/levels mstr"), msg(5, "/levels", 2)])
    said = texts_sent(calls)
    assert any("MSTR</b> · ATR risk framework" in t for t in said) and any("Send one symbol" in t for t in said)


def test_popup_definitions_work_without_acknowledgement_and_are_alerts():
    _, calls = drive([tap(9, "def:atr")], enroll=False)
    from qa_harness import answers
    got = answers(calls)
    assert len(got) == 1 and got[0].show_alert is True and got[0].text == texts.DEFINITIONS["atr"]


def test_group_chatter_is_ignored_and_commands_only_get_the_private_chat_pointer():
    _, calls = drive([msg(5, "hello everyone", chat_type="supergroup")])
    assert calls == []                                                                     # no chatter, no noise in groups
    svc = service()
    _, calls = drive([msg(5, "/portfolio", chat_type="supergroup")], svc)
    said = texts_sent(calls)
    assert said == [texts.GROUP_POINTER]                                                   # never a personal list in a public group
    assert keyboard_urls(sent(calls)[0]) == [f"https://t.me/{BOT_USERNAME}?start=help"]


def test_rate_limit_stops_a_flood_before_any_database_work():
    from alerts.bot_service import RateLimiter
    svc = service()
    _, calls = drive([msg(5, "/about", n) for n in range(1, 5)], svc, msg_limit=RateLimiter(2, 3600))
    said = texts_sent(calls)
    assert sum("Too many requests" in t for t in said) == 2 and len(svc.store.users) == 1


# ------------------------------------------------------------------ Postgres round trips (skip when the database is unreachable)
def _db(table):
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from shared import db
        db.execute_dict_query(f"SELECT 1 FROM {table} LIMIT 1")
        return db
    except Exception as e:                                                          # noqa: BLE001
        pytest.skip(f"Postgres unreachable or table {table} missing: {type(e).__name__}")


def test_pgstore_user_round_trip_with_real_postgres():
    db = _db("bot_users")
    store, uid = bs.PgStore(db), 990_000_000_001
    try:
        store.touch_user(uid)
        assert store.access_status(uid) is None                                         # being a known user is not being authorised
        assert store.is_acknowledged(uid) is False
        store.acknowledge(uid)
        store.acknowledge(uid)                                                        # idempotent
        assert store.is_acknowledged(uid) is True
        session = store.latest_session()
        assert session is None or store.stocks(session["session_date"], []) == {}
    finally:
        db.execute_insert("DELETE FROM bot_users WHERE telegram_user_id = %s", (uid,))


def test_access_sql_round_trip_with_real_postgres():
    """The access-layer SQL (add_assistant_tables.sql) against the real database: invitation single use, expiry, the revoked-user
    rule, revoke -> purge of saved data, audit. Throw-away ids and hashes, all deleted afterwards."""
    db = _db("bot_access")
    store, owner, uid, other = bs.PgStore(db), 990_000_000_100, 990_000_000_101, 990_000_000_102
    ok_hash, old_hash = "e" * 64, "f" * 64
    try:
        assert store.access_status(uid) is None
        store.add_invite(ok_hash, owner, 72, "QA note")
        store.add_invite(old_hash, owner, -1, None)                                   # already expired
        assert store.redeem_invite(old_hash, uid) == ("invalid", None)
        assert store.redeem_invite("0" * 64, uid) == ("invalid", None)                # unknown code
        assert store.redeem_invite(ok_hash, uid) == ("ok", "QA note") and store.access_status(uid) == "active"
        assert store.redeem_invite(ok_hash, other) == ("invalid", None)               # single use
        assert store.access_status(other) is None                                     # a failed redemption leaves no row
        counts = store.access_counts()
        assert counts["active"] >= 1 and counts["open_invites"] >= 0
        store.upsert_tracked(uid, "AAPL", "watch", Decimal("100"), "close", date(2026, 9, 18), None, Decimal("100"))
        store.set_access(uid, "revoked", owner)
        assert store.access_status(uid) == "revoked"
        store.add_invite("d" * 64, owner, 72, None)
        assert store.redeem_invite("d" * 64, uid) == ("revoked", None)                # revoked: cannot come back through a link
        assert store.purge_revoked(30) == 0 and [r["symbol"] for r in store.tracked(uid)] == ["AAPL"]   # inside the window: kept
        db.execute_insert("UPDATE bot_access SET revoked_at = NOW() - INTERVAL '31 days' WHERE telegram_user_id = %s", (uid,))
        assert store.purge_revoked(30) >= 1
        assert store.tracked(uid) == [] and store.access_status(uid) == "revoked"     # data gone, the block stays
        store.set_access(uid, "active", owner)                                        # an explicit approve re-admits
        assert store.access_status(uid) == "active"
        store.audit(owner, "approve", uid)
        assert db.execute_dict_query("SELECT COUNT(*) n FROM bot_audit WHERE actor = %s", (owner,))[0]["n"] == 1
    finally:
        db.execute_insert("DELETE FROM bot_access WHERE telegram_user_id IN (%s, %s, %s)", (uid, other, owner))
        db.execute_insert("DELETE FROM bot_invites WHERE created_by = %s", (owner,))
        db.execute_insert("DELETE FROM bot_audit WHERE actor = %s", (owner,))
        db.execute_insert("DELETE FROM bot_users WHERE telegram_user_id = %s", (uid,))
