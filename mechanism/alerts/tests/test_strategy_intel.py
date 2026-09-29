"""Strategy Intelligence in the private assistant (strategy_intel.py + strategy_screens.py + run_bot.py wiring).

Three layers:
  * pure parsing (filters, callback tokens, typed questions) -- no database;
  * authorization / leak tests through the REAL dispatcher with a stub source -- no database: only the owner ever reaches it;
  * end-to-end against real Postgres (skips when unreachable, like mechanism/screeners/tests): the canonical numbers, read-only
    enforcement, pagination, filters, detail and health states, and the owner's flows through the dispatcher.
"""
import os
import re
import sys
import uuid
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import strategy_intel as si  # noqa: E402
from alerts import strategy_screens as ss  # noqa: E402
from qa_harness import OWNER_ID, answers, drive, everything_shown, msg, sent, tap, texts_edited, texts_sent  # noqa: E402

strip = lambda t: re.sub(r"<[^>]+>", "", t)  # noqa: E731
MEMBER = 5


# =================================================================== pure: filters, tokens, intents
def test_list_spec_token_round_trip_and_strict_decoding():
    spec = si.ListSpec("bullish", "a", "A", True, "grade")
    assert spec.token() == "UaAtg" and si.ListSpec.from_token("UaAtg") == spec
    assert si.ListSpec().token() == "----n" and si.ListSpec.from_token("----n") == si.ListSpec()
    for bad in ("", "UaAt", "XaAtg", "UzAtg", "UaGtg", "UaAxg", "UaAtq", "UaAtg;DROP"):
        assert si.ListSpec.from_token(bad) is None


@pytest.mark.parametrize("args,expected", [
    ("", si.ListSpec()),
    ("bullish", si.ListSpec(direction="bullish")),
    ("bearish open", si.ListSpec(direction="bearish", lifecycle="a")),
    ("bullish grade A", si.ListSpec(direction="bullish", grade="A")),
    ("A-grade today", si.ListSpec(grade="A", today=True)),
    ("b grade", si.ListSpec(grade="B")),
    ("resolved", si.ListSpec(lifecycle="r")),
    ("held", si.ListSpec(lifecycle="h")),
    ("by grade", si.ListSpec(sort="grade")),
])
def test_signal_filters(args, expected):
    assert si.parse_list_args(args) == (expected, None)


def test_an_unknown_filter_word_is_reported_not_ignored():
    assert si.parse_list_args("bullish zebra") == (None, "zebra")


@pytest.mark.parametrize("text,kind,detail", [
    ("How is Donchian doing?", "summary", None),
    ("How many signals do we have?", "summary", None),
    ("What's our win rate?", "performance", None),
    ("Show me today's bullish signals", "list", si.ListSpec(direction="bullish", today=True)),
    ("Any bearish signals?", "list", si.ListSpec(direction="bearish")),
    ("Show A grade signals", "list", si.ListSpec(grade="A")),
    ("Show open trades", "list", si.ListSpec(lifecycle="a")),
    ("Any held signals?", "list", si.ListSpec(lifecycle="h")),
    ("What's happening with VLGEA?", "symbol", "VLGEA"),
    ("What happened to aapl?", "symbol", "AAPL"),
    ("Show VLGEA", "symbol", "VLGEA"),
    ("Compare bullish and bearish", "directions", None),
    ("Is strategy data healthy?", "health", None),
    ("Which strategies do we track?", "strategies", None),
    ("What's considered a win?", "definition", "winner"),
    ("What does R mean?", "definition", "outcome_r"),
    ("Which signals are best?", "best", None),
])
def test_typed_questions_map_to_one_deterministic_query(text, kind, detail):
    intent = si.parse_intent(text)
    assert intent is not None and intent.kind == kind, intent
    if kind == "list":
        assert intent.spec == detail
    elif kind == "symbol":
        assert intent.symbol == detail
    elif kind == "definition":
        assert intent.term == detail


@pytest.mark.parametrize("text", ["hello", "thanks!", "AAPL", "140.5 10", "/strategy", "show me", "what is the weather"])
def test_unsupported_or_ambiguous_text_is_not_an_intent(text):
    assert si.parse_intent(text) is None


def test_definitions_come_from_the_canonical_module():
    from strategy_analytics.definitions import DEFINITIONS
    screen = ss.definition_screen("winner")
    assert DEFINITIONS["winner"][:40] in strip(screen.text).replace("&quot;", '"')
    assert ss.definition_screen("no_such_term") is None


def test_metric_formatting_never_turns_no_data_into_zero():
    assert ss.metric({"value": None, "n": 0, "state": "no_data"}, "rate") == "—"
    assert ss.metric({"value": 0.0, "n": 2, "state": "preliminary"}, "rate") == "0.0% · Preliminary · N=2"
    assert ss.metric({"value": 0.621, "n": 87, "state": "ok"}, "rate") == "62.1% · N=87"
    assert ss.metric({"value": -0.5, "n": 9, "state": "ok"}, "r") == "−0.50R · N=9"
    assert ss.metric({"value": None, "n": None, "state": "not_available", "release": "B"}, "mag") == "— (not available, Release B)"
    assert ss.cell({"value": None, "n": 0, "state": "no_data"}, "rate") == "—"
    assert ss.px(46.11) == "46.11" and ss.px(43.7924) == "43.7924" and ss.px(10) == "10.00"


# =================================================================== authorization (no database)
class StubIntel:
    """Records every call; any call from a non-owner path is a leak."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append(name)
            if self.fail:
                raise RuntimeError("database down")
            raise AssertionError(f"unexpected call {name}")
        return f


NON_OWNER_ATTEMPTS = ["/si", "/strategies", "/strategy", "/performance", "/directions", "/signals", "/signals bullish", "/signal VLGEA",
                      "/open", "/resolved", "/health", "How is Donchian doing?", "What happened to VLGEA?", "Any held signals?",
                      "Is strategy data healthy?", "Compare bullish and bearish"]


def test_a_member_never_reaches_strategy_intelligence_by_command_text_or_button():
    stub = StubIntel()
    ups = [msg(MEMBER, t, i + 1) for i, t in enumerate(NON_OWNER_ATTEMPTS)]
    ups += [tap(MEMBER, d, 100 + i) for i, d in enumerate(["si:s:1", "si:h:1", "si:l:1:-----:0", "si:d:1:463:x:0", "si:z"])]
    _, calls = drive(ups, strategy=stub)
    assert stub.calls == []
    shown = " ".join(strip(t) for t in everything_shown(calls))
    for leak in ("DONCHIAN", "Win rate", "STRATEGY DATA HEALTH", "Strategy Intelligence", "ledger", "43.7924"):
        assert leak not in shown, leak
    assert {a.text for a in answers(calls)} == {"Unknown item."}                  # taps reveal nothing about what they were


def test_a_stranger_gets_only_the_refusal_and_nothing_is_queried():
    stub = StubIntel()
    _, calls = drive([msg(777, "/strategy"), msg(777, "How is Donchian doing?", 2), tap(777, "si:s:1", 3)], strategy=stub, enroll=False)
    assert stub.calls == []
    assert not any("DONCHIAN" in strip(t) for t in everything_shown(calls))


def test_the_owner_in_a_group_gets_no_strategy_data():
    stub = StubIntel()
    _, calls = drive([msg(OWNER_ID, "/strategy", chat_type="group", chat_id=-100), msg(OWNER_ID, "/signals", 2, chat_type="group", chat_id=-100)],
                     strategy=stub)
    assert stub.calls == [] and not any("DONCHIAN" in strip(t) for t in everything_shown(calls))


def test_owner_without_a_configured_source_or_during_an_outage_gets_an_honest_unavailable():
    _, calls = drive([msg(OWNER_ID, "/strategy")], strategy=None)
    assert texts_sent(calls) == [si.UNAVAILABLE]
    stub = StubIntel(fail=True)
    _, calls = drive([msg(OWNER_ID, "/strategy"), msg(OWNER_ID, "/signals", 2), tap(OWNER_ID, "si:s:1", 3)], strategy=stub)
    assert texts_sent(calls) == [si.UNAVAILABLE, si.UNAVAILABLE] and texts_edited(calls) == [si.UNAVAILABLE]
    assert not any(re.search(r"\d+(\.\d+)?%", strip(t)) for t in everything_shown(calls))   # no fallback number


def test_forged_or_malformed_owner_callbacks_are_refused():
    stub = StubIntel()
    bad = ["si:", "si:s", "si:s:abc", "si:l:1:XXXXX:0", "si:l:1:-----:-1", "si:d:1:463:bad:0", "si:q:1", "si:s:1:extra"]
    _, calls = drive([tap(OWNER_ID, d, i + 1) for i, d in enumerate(bad)], strategy=stub)
    assert stub.calls == [] and {a.text for a in answers(calls)} == {"Unknown item."}


def test_owner_filter_words_that_are_not_understood_get_guidance():
    stub = StubIntel()
    _, calls = drive([msg(OWNER_ID, "/signals zebra"), msg(OWNER_ID, "/signal", 2), msg(OWNER_ID, "/signal A B", 3)], strategy=stub)
    t = texts_sent(calls)
    assert "zebra" in t[0] and "/signals" in t[0] and "/signal VLGEA" in t[1] and "/signal VLGEA" in t[2] and stub.calls == []


# =================================================================== real Postgres
@pytest.fixture(scope="module")
def pg():
    try:
        import psycopg2
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        c = psycopg2.connect(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
                             dbname=os.getenv("DB_NAME", "trading_production"), user=os.getenv("DB_USER", "trading_user"),
                             password=os.getenv("DB_PASSWORD", ""), connect_timeout=3)
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SELECT evaluation_flag FROM signal_ledger LIMIT 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable or signal_ledger lacks migration 21: {type(e).__name__}")
    yield c
    c.close()


class ConnDb:
    """The one method readonly_fetch() needs from shared.database (a connection context manager), over a plain connection."""

    def __init__(self, conn):
        self.conn = conn

    def get_sync_connection(self):
        import contextlib
        conn = self.conn

        @contextlib.contextmanager
        def cm():
            conn.autocommit = False
            try:
                yield conn
            finally:
                conn.rollback()
                conn.autocommit = True
        return cm()


@pytest.fixture
def world(pg):
    """A throwaway strategy with a known ledger + price bars (2099 dates, so the test owns the latest market session)."""
    cur = pg.cursor()
    key = "zz_tg_" + uuid.uuid4().hex[:8]
    cur.execute("INSERT INTO strategies (strategy_key, strategy_version, description) VALUES (%s, 'v1', 'test') RETURNING id", (key,))
    sid = cur.fetchone()[0]
    symbols, ids = set(), {}
    sessions = [date(2099, 3, 2) + timedelta(days=i) for i in range(12) if (date(2099, 3, 2) + timedelta(days=i)).weekday() < 5]

    def bar(sym, d, low=99.0):
        symbols.add(sym)
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES (%s,%s,100,101,%s,100,1) "
                    "ON CONFLICT DO NOTHING", (sym, d, low))

    def sig(sym, sd, direction=1, status="open", r=None, bars=None, grade="A", eflag=None, rflag=None, last=None):
        symbols.add(sym)
        e, risk = 100.0, 4.0
        cur.execute("""INSERT INTO signal_ledger (symbol, signal_date, direction, entry_price, atr, stop_price, target1_price, target2_price,
            target3_price, sector, quality_grade, status, outcome_r, mae_r, resolved_date, bars_held, last_evaluated_date, strategy_id,
            strategy_version, resolution_flag, evaluation_flag) VALUES (%s,%s,%s,%s,2,%s,%s,%s,%s,'Technology',%s,%s,%s,%s,%s,%s,%s,%s,'v1',%s,%s)
            RETURNING id""", (sym, sd, direction, e, e - direction * risk, e + direction * risk, e + direction * 2 * risk, e + direction * 3 * risk,
                              grade, status, r, 0.4 if status != "open" else None, sd + timedelta(days=bars or 0) if status != "open" else None,
                              bars, last or sd, sid, rflag, eflag))
        ids[f"{sym}@{sd}"] = cur.fetchone()[0]

    for d in sessions:
        bar("ZTCAL", d)
    yield {"cur": cur, "sid": sid, "key": key, "sig": sig, "bar": bar, "sessions": sessions, "ids": ids,
           "intel": si.StrategyIntel(si.readonly_fetch(ConnDb(pg)))}
    cur.execute("DELETE FROM signal_ledger WHERE strategy_id = %s", (sid,))
    if symbols:
        cur.execute("DELETE FROM stock_prices WHERE symbol = ANY(%s)", (list(symbols),))
    cur.execute("DELETE FROM strategies WHERE id = %s", (sid,))


def strategy_of(w):
    chosen, _ = w["intel"].resolve(w["sid"])
    return chosen


def test_readonly_fetch_refuses_any_write(world):
    import psycopg2
    fetch = world["intel"].fetch
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        fetch("INSERT INTO strategies (strategy_key, strategy_version) VALUES ('zz_should_fail', 'v1') RETURNING id")
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        fetch("UPDATE signal_ledger SET status = 'stopped' WHERE strategy_id = %s RETURNING id", (world["sid"],))
    world["cur"].execute("SELECT count(*) FROM strategies WHERE strategy_key = 'zz_should_fail'")
    assert world["cur"].fetchone()[0] == 0


def test_summary_with_no_resolved_signals_shows_dashes_not_zeros(world):
    s = world["sessions"]
    for i in range(3):
        world["sig"](f"ZTO{i}", s[-1], 1 if i else -1)
    sm = world["intel"].summary(strategy_of(world))
    t = strip(ss.summary_screen(sm).text)
    assert re.search(r"Signals\s+3", t) and re.search(r"Open\s+3", t) and re.search(r"Resolved\s+0", t) and re.search(r"Held\s+0", t)
    assert re.search(r"Bullish\s+2", t) and re.search(r"Bearish\s+1", t)
    assert re.search(r"Win rate\s+—", t) and re.search(r"Average R\s+—", t) and re.search(r"Median R\s+—", t)
    assert "No resolved signals yet." in t and "0%" not in t and "0.00R" not in t
    p = strip(ss.performance_screen(sm).text)
    assert "No resolved signals yet." in p and "Release B" in p
    d = strip(ss.directions_screen(sm).text)
    assert "not which side performs better" in d and "Bull" in d


def test_preliminary_and_ok_samples_carry_their_n(world):
    s = world["sessions"]
    for i, (st, r) in enumerate([("target1", 1.0), ("stopped", -1.0), ("target2", 2.0)]):
        world["sig"](f"ZTP{i}", s[0], 1, st, r, 3)
    sm = world["intel"].summary(strategy_of(world))
    p = strip(ss.performance_screen(sm).text)
    assert "66.7% · Preliminary · N=3" in p and "+0.67R · Preliminary · N=3" in p
    for i in range(3):
        world["sig"](f"ZTQ{i}", s[1], -1, "expired", 0.3 if i else -0.2, 20)
    p = strip(ss.performance_screen(world["intel"].summary(strategy_of(world))).text)
    assert "33.3% · N=6" in p and re.search(r"positive\s+2", p) and re.search(r"negative\s+1", p)
    assert "an expiry is never a win" in p
    d = strip(ss.directions_screen(world["intel"].summary(strategy_of(world))).text)
    assert "~66.7%" in d and "~0.0%" in d                                             # both directions preliminary (N=3 each)


def test_signal_list_pagination_and_filters(world):
    s = world["sessions"]
    for i in range(10):
        world["sig"](f"ZTL{i}", s[i % 3], 1 if i < 6 else -1, grade="A" if i % 2 else "C")
    world["sig"]("ZTR0", s[0], 1, "target1", 1.0, 2)
    world["sig"]("ZTH0", s[1], -1, eflag="split_suspect", last=s[3])
    st, intel = strategy_of(world), world["intel"]
    page, _ = intel.signals(st, si.ListSpec(), 0)
    screen = ss.list_screen(st, page, si.ListSpec())
    t = strip(screen.text)
    assert page["total"] == 12 and "Showing 1–8 of 12" in t and len(page["items"]) == si.PAGE_SIZE
    nxt = [b for row in screen.rows for b in row if b.text == "Next ▶"]
    assert nxt and nxt[0].data == f"si:l:{st['id']}:----n:8"
    page2, _ = intel.signals(st, si.ListSpec(), 8)
    assert len(page2["items"]) == 4 and not page2["has_more"]
    assert {i["id"] for i in page["items"]}.isdisjoint({i["id"] for i in page2["items"]})
    count = lambda spec: intel.signals(st, spec, 0)[0]["total"]                             # noqa: E731
    assert count(si.ListSpec(direction="bullish")) == 7 and count(si.ListSpec(direction="bearish")) == 5
    assert count(si.ListSpec(lifecycle="a")) == 11 and count(si.ListSpec(lifecycle="o")) == 10
    assert count(si.ListSpec(lifecycle="h")) == 1 and count(si.ListSpec(lifecycle="r")) == 1
    assert count(si.ListSpec(grade="A")) == 7 and count(si.ListSpec(direction="bullish", grade="A")) == 4
    today, ref = intel.signals(st, si.ListSpec(today=True), 0)
    assert ref == s[-1] and today["total"] == 0                                          # "today" = the latest market session
    held = strip(ss.list_screen(st, intel.signals(st, si.ListSpec(lifecycle="h"), 0)[0], si.ListSpec(lifecycle="h")).text)
    assert "HELD (split suspect)" in held and "HELD · SIGNALS" in held
    resolved = strip(ss.list_screen(st, intel.signals(st, si.ListSpec(lifecycle="r"), 0)[0], si.ListSpec(lifecycle="r")).text)
    assert "TARGET 1 · +1.00R · 2 sessions" in resolved


def test_no_resolved_list_says_so_without_an_error(world):
    world["sig"]("ZTN0", world["sessions"][0])
    st = strategy_of(world)
    t = strip(ss.list_screen(st, world["intel"].signals(st, si.ListSpec(lifecycle="r"), 0)[0], si.ListSpec(lifecycle="r")).text)
    assert "No resolved signals yet." in t


def test_symbol_lookup_detail_and_history(world):
    s = world["sessions"]
    world["sig"]("ZTSY", s[0], 1, "stopped", -1.0, 1, rflag="same_bar_stop_and_target")
    world["sig"]("ZTSY", s[4], -1)
    world["bar"]("ZTSY", s[5])
    st, intel = strategy_of(world), world["intel"]
    page, d = intel.symbol(st, "ZTSY")
    screen = ss.detail_screen(d, "x", 0, page["items"])
    t = strip(screen.text)
    assert d["identity"]["signal_date"] == s[4] and "▼ Bearish" in t and "Status: OPEN" in t   # the newest signal is shown first
    assert re.search(r"Stop \(−1R\)\s+104\.00", t) and re.search(r"Target 1 \(\+1R\)\s+96\.00", t)   # direction-aware levels
    assert "Trading sessions since signal: 1" in t and "not collected (Release B)" in t and "not scored (no validated model)" in t
    assert "Other ZTSY signals: 1" in t and any(b.data.startswith(f"si:d:{st['id']}:") for row in screen.rows for b in row)
    old = intel.signal(st, world["ids"][f"ZTSY@{s[0]}"])
    ot = strip(ss.detail_screen(old).text)
    assert "Outcome: −1.00R" in ot and "ambiguous: stop and target on the same bar" in ot
    assert intel.symbol(st, "ZZZNONE")[1] is None and strip(ss.not_found("ZZZNONE").text) == "No tracked signal found for ZZZNONE."


def test_held_detail(world):
    s = world["sessions"]
    world["sig"]("ZTHD", s[1], 1, eflag="split_suspect", last=s[3])
    st, intel = strategy_of(world), world["intel"]
    _, d = intel.symbol(st, "ZTHD")
    t = strip(ss.detail_screen(d).text)
    assert "Status: HELD (split suspect)" in t


def test_health_states(world):
    s = world["sessions"]
    world["sig"]("ZTOK", s[-1])
    st, intel = strategy_of(world), world["intel"]
    t = strip(ss.health_screen(intel.health(st)).text)
    assert "Status: HEALTHY" in t and re.search(r"Integrity\s+0", t) and "waiting for first forward session" in t
    assert "Evaluator history: not persisted" in t and "⚠" not in t
    world["sig"]("ZTHL", s[1], eflag="split_suspect", last=s[2])                   # held
    world["bar"]("ZTST", s[0])
    world["sig"]("ZTST", s[0])                                                     # stale: no bar since the first session
    for d in s[2:]:
        world["bar"]("ZTBD", d, low=0.0 if d == s[4] else 99.0)
    world["sig"]("ZTBD", s[2])                                                     # invalid price bar on the path
    t = strip(ss.health_screen(intel.health(st)).text)
    assert "⚠ DATA HEALTH NEEDS ATTENTION" in t and "1 signal held for review" in t and "1 stale symbol" in t
    assert "1 blocked by an invalid price bar" in t and "ZTST" in t
    world["sig"]("ZTIV", s[0], status="open", r=None)
    world["cur"].execute("UPDATE signal_ledger SET outcome_r = 1 WHERE symbol = 'ZTIV' AND strategy_id = %s", (world["sid"],))
    t = strip(ss.health_screen(intel.health(st)).text)
    assert "⚠ INTEGRITY VIOLATION" in t and "1 open with an outcome" in t


def test_owner_flows_through_the_real_dispatcher(world):
    s = world["sessions"]
    for i in range(9):
        world["sig"](f"ZTD{chr(65 + i)}", s[0], 1 if i < 4 else -1)
    intel, sid = world["intel"], world["sid"]
    from qa_harness import service
    svc = service()
    svc.store.acknowledge(OWNER_ID)                         # typed text (not commands) needs the notice accepted, as for everyone
    ups = [msg(OWNER_ID, f"/strategy {world['key']}"), msg(OWNER_ID, "/signals bearish", 2), msg(OWNER_ID, "/signal ZTDB", 3),
           tap(OWNER_ID, f"si:l:{sid}:D---n:0", 4), tap(OWNER_ID, f"si:l:{sid}:----n:8", 5),
           tap(OWNER_ID, f"si:h:{sid}", 6), msg(OWNER_ID, "What happened to ZTDC?", 7), msg(OWNER_ID, "What's considered a win?", 8)]
    _, calls = drive(ups, svc, strategy=intel)
    t = [strip(x) for x in texts_sent(calls)]
    assert "ZZ_TG_" in t[0].upper() and re.search(r"Signals\s+9", t[0])
    assert "BEARISH · SIGNALS" in t[1] and "5 found" in t[1]
    assert "ZTDB" in t[2] and "▲ Bullish" in t[2]
    assert "ZTDC" in t[3] and "first profit target" in t[4]                                   # natural language -> symbol, then the canonical definition
    e = [strip(x) for x in texts_edited(calls)]
    assert "5 found" in e[0] and "Showing 9–9 of 9" in e[1] and "STRATEGY DATA HEALTH" in e[2]
    for m in sent(calls):
        assert len(m.text) < 4096
