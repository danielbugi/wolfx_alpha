"""Activation-readiness validation of the forward paper tracker, on throwaway production-shaped databases (never production):

  * deterministic: two independent databases fed the same sessions end with identical hashes, event for event;
  * interrupted execution recovers: a connection dropped before commit, and a crash half-way through the event inserts, leave NOTHING behind and the retry converges to the uninterrupted result;
  * no duplicate events, one entered / closed per observation, the hash chain verifies;
  * variants are independent: each variant's stored positions equal that variant simulated ALONE;
  * absent catalyst / relative-strength / market-regime sections are recorded as explicitly absent, with a reason;
  * nothing outside the three tracker tables is touched.
"""
from datetime import timedelta

import numpy as np
import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
import full_env as FE
import test_paper_tracker_db as T
from research import experiment_runner as E
from research import paper_runner as PR
from research import paper_store as ST
from research import strategy_variants as SV

FIRST, LAST = 25, 130            # session indexes replayed: signals start at 25 and positions have time to close


def build_env(prefix):
    e, teardown = FE.make_env(prefix)
    conn = e.admin()
    cur = conn.cursor()
    SV.register(cur, SV.build_specs())
    for s in T.SYMBOLS:
        o, h, l, c = T.DATA[s]
        for i, d in enumerate(T.DAYS):
            cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,%s,%s,%s,%s,%s,1000)", (s, d, float(o[i]), float(h[i]), float(l[i]), float(c[i]), float(c[i])))
    conn.commit()
    conn.close()
    return e, teardown


class CrashingCursor:
    """Forwards everything to a real cursor but raises on the Nth INSERT INTO paper_event: a crash half-way through the writes."""

    def __init__(self, cur, nth):
        self.cur, self.nth, self.seen = cur, nth, 0

    def execute(self, sql, *a, **k):
        if "INSERT INTO paper_event" in str(sql):
            self.seen += 1
            if self.seen == self.nth:
                raise RuntimeError("simulated crash in the middle of the event inserts")
        if "INSERT INTO paper_session" in str(sql):                                      # the session row is the LAST write: a session with fewer events still crashes after its observations/events
            raise RuntimeError("simulated crash just before the session row")
        return self.cur.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self.cur, name)


def counts(e):
    return {t: T.count(e, t) for t in T.TABLES}


def fingerprint(e):
    conn = e.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT session_date, open_state_hash FROM paper_session ORDER BY session_date")
        sessions = cur.fetchall()
        cur.execute("SELECT o.symbol, o.direction, o.signal_session, v.variant_key, o.observation_hash FROM paper_observation o JOIN strategy_variant v ON v.id = o.variant_id ORDER BY 4, 1, 2, 3")
        obs = cur.fetchall()
        cur.execute("SELECT e.session_date, e.event_type, e.event_hash, e.prev_hash FROM paper_event e JOIN paper_observation o ON o.id = e.observation_id JOIN strategy_variant v ON v.id = o.variant_id "
                    "ORDER BY v.variant_key, o.symbol, o.direction, o.signal_session, e.seq")
        events = cur.fetchall()
        return sessions, obs, events
    finally:
        conn.close()


@pytest.fixture(scope="module")
def worlds():
    a, td_a = build_env("pva")
    b, td_b = build_env("pvb")
    try:
        src = T.build_source()
        days = T.DAYS[FIRST:LAST]
        for d in days:                                                                   # A: uninterrupted
            assert T.run_session(a, d, src, activation=(d == days[0]), enrich=False).status == "processed"
        interruptions = {"dropped": 0, "half_written": 0}
        for n, d in enumerate(days):                                                     # B: interrupted before most sessions, then retried
            if n % 3 == 1:                                                               # a connection dropped before commit
                before = counts(b)
                conn = b.app_connect_factory()()
                cur = conn.cursor()
                cur.execute("SET search_path = public")
                PR.process_session(cur, d, src, write=True, code_ref="test", activation_session=(n == 0), calendar=T.DAYS, enrich=False)
                conn.close()                                                             # no commit: the process died
                assert counts(b) == before
                interruptions["dropped"] += 1
            if n % 3 == 2:                                                               # a crash after some events were inserted
                before = counts(b)
                conn = b.app_connect_factory()()
                try:
                    cur = conn.cursor()
                    cur.execute("SET search_path = public")
                    with pytest.raises(RuntimeError, match="simulated crash"):
                        PR.process_session(CrashingCursor(cur, 2), d, src, write=True, code_ref="test", calendar=T.DAYS, enrich=False)
                    conn.rollback()
                finally:
                    conn.close()
                assert counts(b) == before
                interruptions["half_written"] += 1
            assert T.run_session(b, d, src, activation=(d == days[0]), enrich=False).status == "processed"
        yield a, b, interruptions
    finally:
        td_a()
        td_b()


def test_the_interrupted_database_converges_to_exactly_the_uninterrupted_one(worlds):
    a, b, interruptions = worlds
    assert interruptions["dropped"] > 10 and interruptions["half_written"] > 10
    fa, fb = fingerprint(a), fingerprint(b)
    assert fa == fb, "interrupted + retried runs must produce the same sessions, observations and event chain as an uninterrupted run"
    sessions, obs, events = fa
    assert len(sessions) == LAST - FIRST and len(obs) > 100 and len(events) > 200


def test_two_independent_databases_are_hash_for_hash_identical(worlds):
    a, b, _ = worlds
    assert [x[1] for x in fingerprint(a)[0]] == [x[1] for x in fingerprint(b)[0]]


def test_no_duplicate_events_and_the_chain_verifies(worlds):
    a, _, _ = worlds
    conn = a.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*), count(DISTINCT event_hash) FROM paper_event")
        n, distinct = cur.fetchone()
        assert n == distinct and n > 200
        cur.execute("SELECT count(*) FROM (SELECT observation_id, seq FROM paper_event GROUP BY 1, 2 HAVING count(*) > 1) x")
        assert cur.fetchone()[0] == 0
        for et in ("entered", "closed"):
            cur.execute("SELECT count(*) FROM (SELECT observation_id FROM paper_event WHERE event_type = %s GROUP BY 1 HAVING count(*) > 1) x", (et,))
            assert cur.fetchone()[0] == 0, et
        cur.execute("SELECT count(*), count(DISTINCT observation_hash) FROM paper_observation")
        n, distinct = cur.fetchone()
        assert n == distinct
        assert ST.verify(cur) == []
    finally:
        conn.close()


def test_each_variants_stored_positions_equal_that_variant_simulated_alone(worlds):
    a, _, _ = worlds
    days = T.DAYS[FIRST:LAST]
    first = days[0]
    conn = a.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT v.variant_key, o.symbol, o.direction, o.signal_session, e.payload FROM paper_event e JOIN paper_observation o ON o.id = e.observation_id JOIN strategy_variant v ON v.id = o.variant_id "
                    "WHERE e.event_type = 'closed'")
        stored = {}
        for k, s, d, sig, pl in cur.fetchall():
            stored.setdefault(k, {})[(s, d, sig)] = pl
    finally:
        conn.close()
    variants = E.registered_variants()
    assert len(variants) == 5 and set(stored) == {v.key for v in variants}
    for v in variants:
        alone = {}
        for s in T.SYMBOLS:
            o, h, l, c = T.DATA[s]
            sigs = [x for x in T.SIGNALS[s] if first <= T.DAYS[x[0]] <= days[-1]]
            _, pos = E.simulate_symbol([v], [60], s, o, h, l, c, T.DAYS, np.full(T.N_SESSIONS, T.ATR), sigs)
            for p in pos:
                if not p.censored and p.exit_date <= days[-1] + timedelta(days=0):
                    alone[(s, p.direction, p.signal_date)] = p
        got = stored[v.key]
        assert set(got) == set(alone) and len(alone) > 10, v.key
        for k, p in alone.items():
            assert got[k]["exit_price"] == p.trade.exit_price and got[k]["reason"] == p.trade.reason and got[k]["r_gross_std"] == p.trade.r_gross, (v.key, k)
    exits = {}
    for v in variants:
        for k, pl in stored[v.key].items():
            exits.setdefault(k, set()).add((pl["exit_session"], pl["reason"]))
    assert any(len(x) > 1 for x in exits.values()), "different exit policies must produce different outcomes for the same signal"


def test_absent_context_sections_are_recorded_as_absent_with_a_reason(worlds):
    a, _, _ = worlds
    import datetime as dt
    cutoff = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
    src = PR.CandidateSource()
    d = T.DAYS[FIRST]
    conn = a.admin()
    try:
        cur = conn.cursor()
        # 1. the tables exist but hold nothing at the cutoff. This also proves every optional query's columns and syntax against the REAL schema: a broken query would come back as "query_error:...",
        #    never as an empty result (an earlier draft used a wrong column and was reported as "not present").
        assert src._regime(cur, d, cutoff) == {"available": False, "reason": "no observed market snapshot available at the cutoff"}
        assert src._relative_strength(cur, d, ["AAA"], cutoff) == {}
        assert src._catalysts(cur, d, ["AAA"], cutoff) == "source_empty"                              # an EMPTY source is not "no catalysts in the window"
        assert PR._why("source_empty", "market_event tables") == "market_event tables: no event has been recorded yet (no job feeds this source)"
        # a source that holds events for other symbols does report "none in the window" for this one (the signals() composition uses the default)
        cur.execute("INSERT INTO market_event (event_key, symbol, event_type) VALUES ('t:ZZZ:1', 'ZZZ', 'earnings_reported')")
        cur.execute("INSERT INTO market_event_revision (event_key, revision, event_time, session_timing, published_at, known_at, known_at_basis, source, source_ref, pit_grade, status, provenance, ingested_at, payload_hash) "
                    "VALUES ('t:ZZZ:1', 1, %s, 'AMC', now(), now(), 'vendor_published', 'test', 'r1', 'A', 'reported', 'observed', now(), repeat('a', 64))", (d,))
        assert src._catalysts(cur, d, ["AAA"], dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)) == {}
        # 2. a query that breaks is reported as a query error, not as an absent table (transaction rolled back below)
        cur.execute("ALTER TABLE market_snapshot RENAME COLUMN captured_at TO captured_at_x")
        reg = src._regime(cur, d, cutoff)
        assert reg["available"] is False and reg["reason"].startswith("market_snapshot: query_error:") and "not present" not in reg["reason"]
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)
        # 3. the tables do not exist at all
        for tbl in ("market_snapshot", "stock_relative_strength", "market_event"):
            cur.execute(f"ALTER TABLE {tbl} RENAME TO {tbl}_gone")
        assert src._regime(cur, d, cutoff) == {"available": False, "reason": "market_snapshot not present"}
        assert src._relative_strength(cur, d, ["AAA"], cutoff) == "absent" and src._catalysts(cur, d, ["AAA"], cutoff) == "absent"
        assert PR._why("absent", "market_event tables") == "market_event tables not present"
        cur.execute("SELECT 1")                                                                      # the savepoint guards left the transaction usable
        assert cur.fetchone() == (1,)
    finally:
        conn.rollback()
        conn.close()
    assert T.scalar(a, "SELECT to_regclass('market_snapshot') IS NOT NULL")                          # nothing was really renamed


def test_the_validation_runs_touched_nothing_outside_the_three_tracker_tables(worlds):
    a, _, _ = worlds
    assert T.snapshot_other_tables(a)["signal_ledger"] == 0 and T.snapshot_other_tables(a)["strategies"] >= 1
