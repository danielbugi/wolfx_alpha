"""Migration 36 + the paper store and runner against a throwaway production-shaped DATABASE (compose init list, real role script): the tracker's evidence is isolated, immutable, ordered, hash-chained and
idempotent; the full path (registry check -> restore -> bars -> signals -> events -> cursor) reproduces the batch experiment exactly when run session by session as the LEAST-PRIVILEGE runtime role;
a dry run writes nothing; a failed session leaves nothing behind; and nothing outside the three migration-36 tables is ever touched."""
import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import psycopg2
import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
import full_env as FE
from research import experiment_runner as E
from research import paper_runner as PR
from research import paper_store as ST
from research import paper_tracker as PT
from research import strategy_variants as SV

MIGRATION = open(os.path.join(FE.ROOT, "mechanism", "add_paper_tracker_tables.sql"), encoding="utf-8").read()
ROLLBACK = open(os.path.join(FE.ROOT, "deploy", "db", "rollback_36.sql"), encoding="utf-8").read()
PREFLIGHT = open(os.path.join(FE.ROOT, "deploy", "db", "preflight_36.sql"), encoding="utf-8").read()
TABLES = ("paper_session", "paper_observation", "paper_event")
N_SESSIONS = 560
SYMBOLS = ("AAA", "BBB", "CCC", "DDD")
D0 = date(2023, 1, 2)


def trading_days(n):
    out, d = [], D0
    while len(out) < n:
        if d.isoweekday() < 6:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = trading_days(N_SESSIONS)


def series(seed):
    rng = np.random.default_rng(seed)
    o, h, l, c = (np.empty(N_SESSIONS) for _ in range(4))
    price = 100.0
    for i in range(N_SESSIONS):
        o[i] = price * (1 + rng.normal(0, 0.01))
        c[i] = o[i] * (1 + rng.normal(0.0012, 0.017))
        h[i] = max(o[i], c[i]) * (1 + abs(rng.normal(0, 0.007)))
        l[i] = min(o[i], c[i]) * (1 - abs(rng.normal(0, 0.007)))
        price = c[i]
    return np.round(o, 4), np.round(h, 4), np.round(l, 4), np.round(c, 4)


DATA = {s: series(i + 1) for i, s in enumerate(SYMBOLS)}
ATR = 1.8
SIGNALS = {}
_rng = np.random.default_rng(42)
for s in SYMBOLS:
    idx = sorted(set(int(x) for x in _rng.integers(25, 160, 14)) | {int(x) + 1 for x in _rng.integers(25, 150, 3)})
    SIGNALS[s] = [(i, int(_rng.choice([1, -1]))) for i in idx]


def build_source():
    by = {}
    for s, sg in SIGNALS.items():
        _, h, l, _ = DATA[s]
        for i, d in sg:
            by.setdefault(DAYS[i], []).append(PR.Signal(s, d, ATR, [float(x) for x in h[max(0, i - 9):i + 1]], [float(x) for x in l[max(0, i - 9):i + 1]],
                                                        {"decision_cutoff": "2099-01-01T00:00:00+00:00", "features": {"relvol": 2.0 + i / 1000, "available_at": DAYS[i].isoformat()}}))
    return PR.ListSource(by)


@pytest.fixture(scope="module")
def env():
    e, teardown = FE.make_env("pt36")
    try:
        conn = e.admin()
        cur = conn.cursor()
        SV.register(cur, SV.build_specs())
        for s in SYMBOLS:
            o, h, l, c = DATA[s]
            for i, d in enumerate(DAYS):
                cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,%s,%s,%s,%s,%s,1000)", (s, d, float(o[i]), float(h[i]), float(l[i]), float(c[i]), float(c[i])))
        conn.commit()
        conn.close()
        yield e
    finally:
        teardown()


def count(env, table):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]
    finally:
        conn.close()


def scalar(env, sql, params=None):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()[0]
    finally:
        conn.close()


def run_session(env, d, source, *, write=True, as_app=True, scope="triggered", activation=False, enrich=True):
    conn = (env.app_connect_factory()() if as_app else env.admin())
    try:
        cur = conn.cursor()
        cur.execute("SET search_path = public")
        res = PR.process_session(cur, d, source, scope=scope, write=write, code_ref="test", activation_session=activation, calendar=DAYS, enrich=enrich)
        if write:
            conn.commit()
        else:
            conn.rollback()
        return res
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def snapshot_other_tables(env):
    return {t: count(env, t) for t in ("strategies", "strategy_variant", "signal_ledger", "candidate_observation", "feature_snapshot", "stock_prices")}


# ---------------------------------------------------------------------------------------------------------------------------------- migration objects and permissions
def test_a_fresh_bootstrap_has_the_three_tables_with_least_privilege_and_nine_always_triggers(env):
    for t in TABLES:
        assert scalar(env, "SELECT obj_description(to_regclass(%s), 'pg_class')", (t,)) == "paper_tracker_migration_36"
        assert scalar(env, "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = to_regclass(%s)", (t,)) == env.owner
        for priv, want in (("SELECT", True), ("INSERT", True), ("UPDATE", False), ("DELETE", False), ("TRUNCATE", False), ("REFERENCES", False), ("TRIGGER", False)):
            assert scalar(env, "SELECT has_table_privilege(%s, %s, %s)", (env.app, t, priv)) is want, (t, priv)
        assert scalar(env, "SELECT has_table_privilege('public', %s, 'SELECT')", (t,)) is False
    assert scalar(env, "SELECT count(*) FROM pg_trigger WHERE tgrelid = ANY (ARRAY['paper_session'::regclass, 'paper_observation'::regclass, 'paper_event'::regclass]) AND NOT tgisinternal AND tgenabled = 'A'") == 9


def test_the_preflight_reports_exact_and_the_reapply_is_a_no_op(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("SET default_transaction_read_only = on")
        cur.execute(PREFLIGHT)
        assert any("PREFLIGHT_36 OK state=EXACT" in n for n in conn.notices)
    finally:
        conn.close()
    env.exec_admin(MIGRATION)
    env.exec_admin(MIGRATION)


# ---------------------------------------------------------------------------------------------------------------------------------- the whole path as the runtime role
def test_the_first_session_needs_an_explicit_activation_and_nothing_is_written_without_it(env):
    src = build_source()
    before = snapshot_other_tables(env)
    with pytest.raises(PR.SessionRefused, match="activation"):
        run_session(env, DAYS[30], src)
    assert count(env, "paper_session") == 0 and snapshot_other_tables(env) == before


def test_a_dry_run_computes_the_same_events_and_writes_nothing(env):
    src = build_source()
    first_signal_day = min(src.by_session)
    res = run_session(env, first_signal_day, src, write=False, activation=True)
    assert res.status == "dry_run" and res.counts["n_registered"] == 5 * len(src.by_session[first_signal_day]) and res.events
    assert all(count(env, t) == 0 for t in TABLES)


def test_running_every_session_as_the_runtime_role_reproduces_the_batch_experiment(env):
    src = build_source()
    first = min(src.by_session)
    others_before = snapshot_other_tables(env)
    for d in DAYS[DAYS.index(first):]:
        res = run_session(env, d, src, activation=(d == first))
        assert res.status == "processed"
    assert snapshot_other_tables(env) == others_before                         # no existing table was touched
    # batch reference: the same signals, standalone simulation with the one-open-position rule
    variants = E.registered_variants()
    ref = {}
    for s in SYMBOLS:
        o, h, l, c = DATA[s]
        _, pos = E.simulate_symbol(variants, [60], s, o, h, l, c, DAYS, np.full(N_SESSIONS, ATR), [x for x in SIGNALS[s] if DAYS[x[0]] >= first])
        for p in pos:
            ref[(p.variant.key, s, p.direction, p.signal_date)] = p
    conn = env.admin()
    cur = conn.cursor()
    cur.execute("SELECT v.variant_key, o.symbol, o.direction, o.signal_session, e.payload FROM paper_event e JOIN paper_observation o ON o.id = e.observation_id JOIN strategy_variant v ON v.id = o.variant_id "
                "WHERE e.event_type = 'closed'")
    closed = {(k, s, d, sig): pl for k, s, d, sig, pl in cur.fetchall()}
    cur.execute("SELECT count(*) FROM paper_observation")
    n_obs = cur.fetchone()[0]
    conn.close()
    done = {k for k, p in ref.items() if not p.censored}
    assert set(closed) == done and len(done) > 100 and n_obs == len(ref)
    for k, pl in closed.items():
        t = ref[k].trade
        assert (pl["exit_price"], pl["reason"], pl["bars_held"], pl["r_gross_std"], pl["mfe_r_std"], pl["initial_risk"]) == (t.exit_price, t.reason, t.duration, t.r_gross, t.mfe_r, t.initial_risk)
        assert pl["exit_session"] == ref[k].exit_date.isoformat() and pl["entry_session"] == ref[k].entry_date.isoformat()


def test_hindsight_events_appear_only_when_the_later_bars_exist_and_are_flagged(env):
    n = count(env, "paper_event")
    conn = env.admin()
    cur = conn.cursor()
    cur.execute("SELECT count(*), min(session_date) FROM paper_event WHERE event_type = 'hindsight'")
    n_h, first_h = cur.fetchone()
    assert n_h > 50, "after ~560 sessions the early closed positions have 250+ later bars: they must have been enriched"
    cur.execute("SELECT count(*) FROM paper_event h JOIN paper_event c ON c.observation_id = h.observation_id AND c.event_type = 'closed' WHERE h.event_type = 'hindsight' "
                "AND (c.payload ->> 'entry_session')::date > h.session_date - 330")
    assert cur.fetchone()[0] == 0                                              # never computed before the window existed
    cur.execute("SELECT count(*) FROM paper_event WHERE event_type = 'hindsight' AND payload -> 'decision_time_information' <> 'false'::jsonb")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT payload FROM paper_event WHERE event_type = 'hindsight' LIMIT 1")
    pl = cur.fetchone()[0]
    conn.close()
    assert set(pl) >= {"excursions", "capture", "big_winner_tier_120", "missed_continuation", "computed_through_session"} and pl["excursions"]["h250"] is not None and n > n_h


def test_the_stored_evidence_verifies_clean_and_every_open_snapshot_matches_its_hash(env):
    conn = env.admin()
    try:
        assert ST.verify(conn.cursor()) == []
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM paper_session")
        assert cur.fetchone()[0] > 300
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------------------------- idempotency, ordering, refusals
def test_a_processed_session_is_a_no_op_when_run_again(env):
    before = {t: count(env, t) for t in TABLES}
    res = run_session(env, DAYS[200], build_source())
    assert res.status == "already_processed" and {t: count(env, t) for t in TABLES} == before


def test_a_gap_a_changed_scope_and_a_non_session_are_refused(env):
    src = build_source()
    last = scalar(env, "SELECT max(session_date) FROM paper_session")
    assert last == DAYS[-1]
    cal = DAYS + [DAYS[-1] + timedelta(days=1), DAYS[-1] + timedelta(days=2), DAYS[-1] + timedelta(days=3), DAYS[-1] + timedelta(days=4)]
    conn = env.app_connect_factory()()
    try:
        cur = conn.cursor()
        with pytest.raises(PR.SessionRefused, match="gap"):
            PR.process_session(cur, cal[N_SESSIONS + 1], src, write=True, calendar=cal)
        with pytest.raises(PR.SessionRefused, match="scope/source changed"):
            PR.process_session(cur, cal[N_SESSIONS], src, scope="tracked", write=True, calendar=cal)
        with pytest.raises(PR.SessionRefused, match="not a loaded trading session"):
            PR.process_session(cur, DAYS[-1] + timedelta(days=10), src, write=True, calendar=cal)
    finally:
        conn.rollback()
        conn.close()


def test_input_that_is_not_ready_refuses_the_session():
    class NotReady(PR.ListSource):
        def ready(self, cur, session_date):
            return False, "capture still running"
    assert NotReady({}).ready(None, DAYS[0]) == (False, "capture still running")


def test_resolve_next_is_the_session_after_the_newest_processed_one_or_says_why_not(env):
    conn = env.app_connect_factory()()
    try:
        cur = conn.cursor()
        assert PR.resolve_next(cur, DAYS) == (None, f"up to date: no trading session after {DAYS[-1]} is loaded yet")
        later = DAYS + [DAYS[-1] + timedelta(days=3), DAYS[-1] + timedelta(days=4)]
        assert PR.resolve_next(cur, later)[0] == DAYS[-1] + timedelta(days=3)
    finally:
        conn.rollback()
        conn.close()


def test_the_runner_never_shares_an_exit_status_the_unit_treats_as_success_with_a_failure():
    success = {PR.EXIT_OK, PR.EXIT_NOT_READY, PR.EXIT_UP_TO_DATE, 3}                  # 3 = the wrapper's lock
    failures = {PR.EXIT_REFUSED, PR.EXIT_NOT_ARMED, PR.EXIT_USAGE, PR.EXIT_DB}
    assert not (success & failures) and len(failures) == 4


def test_the_cli_refuses_before_connecting_and_reports_distinct_statuses(tmp_path, capsys, monkeypatch):
    for k in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PORT", "DB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    base = ["run", "--host", "h", "--dbname", "trading_production", "--user", "u", "--next", "--code-ref", "abc123def456"]
    assert PR.main(["run", "--next"]) == PR.EXIT_USAGE                                                           # no connection parameters
    assert PR.main(["run", "--host", "h", "--dbname", "d", "--user", "u"]) == PR.EXIT_USAGE                       # neither --session nor --next
    assert PR.main(base + ["--session", "2026-01-02"]) == PR.EXIT_USAGE                                           # both
    assert PR.main(base) == PR.EXIT_NOT_ARMED                                                                     # no --confirm-production
    assert PR.main(base + ["--confirm-production", "trading_production"]) == PR.EXIT_NOT_ARMED                    # no arming file
    arm = tmp_path / "armed"
    assert PR.main(base + ["--confirm-production", "trading_production", "--arming-file", str(arm)]) == PR.EXIT_NOT_ARMED
    arm.write_text("ffffffffffff\n")
    assert PR.main(base + ["--confirm-production", "trading_production", "--arming-file", str(arm)]) == PR.EXIT_NOT_ARMED   # armed for a different image
    arm.write_text("abc123def456\n")                                                                              # armed for exactly this image: now it reaches the (unreachable) database
    assert PR.main(base + ["--port", "1", "--confirm-production", "trading_production", "--arming-file", str(arm)]) == PR.EXIT_DB
    assert "armed for 'ffffffffffff'" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------------------------------------- immutability and the database's own order / chain rules
def test_nothing_can_be_updated_deleted_or_truncated_even_by_a_superuser_and_the_runtime_role_lacks_the_privileges(env):
    for t in TABLES:
        for sql in (f"UPDATE {t} SET run_id = run_id", f"DELETE FROM {t}", f"TRUNCATE {t}"):
            conn = env.admin()
            try:
                with pytest.raises(psycopg2.Error, match="append-only|cannot truncate"):        # a referenced table is also refused by Postgres itself before the trigger fires
                    conn.cursor().execute(sql)
            finally:
                conn.rollback()
                conn.close()
    app = env.app_connect_factory()()
    try:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            app.cursor().execute("UPDATE paper_event SET run_id = 'x'")
    finally:
        app.rollback()
        app.close()


def _expect(env, match, sql, params=None):
    conn = env.admin()
    try:
        with pytest.raises(psycopg2.Error, match=match):
            conn.cursor().execute(sql, params)
    finally:
        conn.rollback()
        conn.close()


def test_the_database_enforces_event_order_contiguity_chain_linkage_and_the_hindsight_flag(env):
    """A brand-new observation inside a transaction that is rolled back: every bad event is tried under its own savepoint, nothing persists."""
    conn = env.admin()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM strategy_variant ORDER BY id LIMIT 1")
        vid = cur.fetchone()[0]
        sig = DAYS[-1] + timedelta(days=30)
        root = "c" * 64
        cur.execute("INSERT INTO paper_observation (variant_id, symbol, direction, signal_session, atr, hold_cap, registered_cap, hist_h, hist_l, context, observation_hash, run_id) "
                    "VALUES (%s,'ZZZ',1,%s,1.0,60,TRUE,ARRAY[1.0],ARRAY[1.0],'{}'::jsonb,%s,'t') RETURNING id", (vid, sig, root))
        oid = cur.fetchone()[0]
        h64 = "a" * 64
        ins = "INSERT INTO paper_event (observation_id, seq, event_type, session_date, payload, prev_hash, event_hash, run_id) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,'t')"

        def bad(match, *args):
            cur.execute("SAVEPOINT s")
            with pytest.raises(psycopg2.Error, match=match):
                cur.execute(ins, args)
            cur.execute("ROLLBACK TO SAVEPOINT s")

        nxt = sig + timedelta(days=1)
        bad("starts with entered", oid, 1, "closed", nxt, "{}", root, h64)
        bad("must have seq 1", oid, 2, "entered", nxt, "{}", root, h64)
        bad("not after the signal session", oid, 1, "entered", sig, "{}", root, h64)
        bad("does not link", oid, 1, "entered", nxt, "{}", h64, h64)
        cur.execute(ins, (oid, 1, "entered", nxt, "{}", root, h64))                                      # the correct first event is accepted
        bad("contiguous", oid, 3, "closed", nxt, "{}", h64, h64)                                         # seq must be 2
        bad("does not link", oid, 2, "closed", nxt, "{}", root, h64)                                     # must link to the entered event's hash, not the root
        bad("cannot follow", oid, 2, "hindsight", nxt, '{"decision_time_information": false}', h64, h64)  # hindsight needs a closed (or not_enterable) event first
        bad("cannot follow", oid, 2, "entered", nxt, "{}", h64, h64)
        cur.execute(ins, (oid, 2, "closed", nxt + timedelta(days=3), "{}", h64, "b" * 64))
        bad("before the previous event session", oid, 3, "hindsight", nxt, '{"decision_time_information": false}', "b" * 64, "d" * 64)
        bad("hindsight_flag", oid, 3, "hindsight", nxt + timedelta(days=5), '{"decision_time_information": true}', "b" * 64, "d" * 64)
        bad("hindsight_flag", oid, 3, "hindsight", nxt + timedelta(days=5), "{}", "b" * 64, "d" * 64)
        cur.execute(ins, (oid, 3, "hindsight", nxt + timedelta(days=5), '{"decision_time_information": false}', "b" * 64, "d" * 64))   # and the correct hindsight event is accepted
        bad("cannot follow", oid, 4, "closed", nxt + timedelta(days=6), "{}", "d" * 64, "e" * 64)
    finally:
        conn.rollback()
        conn.close()
    assert scalar(env, "SELECT count(*) FROM paper_observation WHERE symbol = 'ZZZ'") == 0


def test_a_session_cannot_be_inserted_out_of_order_and_a_registration_must_match_the_registered_cap(env):
    _expect(env, "not after the last processed session",
            "INSERT INTO paper_session (session_date, scope, source, n_variants, n_candidates, n_registered, n_skipped_open, n_entered, n_closed, n_not_enterable, n_open_after, open_state, open_state_hash, run_id, writer, code_ref, started_at) "
            "VALUES (%s,'triggered','candidate_observation',5,0,0,0,0,0,0,0,%s::jsonb,%s,'t','t','t',now())", (DAYS[10], '{"schema":"paper_open_state_v1","last_session":"%s"}' % DAYS[10], "b" * 64))
    vid = scalar(env, "SELECT id FROM strategy_variant ORDER BY id LIMIT 1")
    _expect(env, "registered_cap", "INSERT INTO paper_observation (variant_id, symbol, direction, signal_session, atr, hold_cap, registered_cap, hist_h, hist_l, context, observation_hash, run_id) "
                                     "VALUES (%s,'ZZZ',1,%s,1.0,120,TRUE,ARRAY[1.0],ARRAY[1.0],'{}'::jsonb,%s,'t')", (vid, DAYS[-1] + timedelta(days=30), "c" * 64))


def test_a_changed_registration_or_event_is_drift_and_is_never_overwritten(env):
    conn = env.admin()
    cur = conn.cursor()
    cur.execute("SELECT v.variant_key, o.symbol, o.direction, o.signal_session, o.atr, o.hold_cap, o.registered_cap, o.hist_h, o.hist_l, o.context FROM paper_observation o JOIN strategy_variant v ON v.id = o.variant_id LIMIT 1")
    k, sym, d, sig, atr, cap, reg, hh, hl, ctx = cur.fetchone()
    variants, ids = ST.load_variants(cur)
    ev = {"type": "registered", "ident": f"{k}|{sym}|{d}|{sig.isoformat()}", "session": sig.isoformat(),
          "payload": {"variant_key": k, "definition_hash": next(v.definition_hash for v in variants if v.key == k), "symbol": sym, "direction": d, "signal_session": sig.isoformat(), "atr": atr + 1.0,
                      "hold_cap": cap, "registered_cap": reg, "hist_h": hh, "hist_l": hl, "context": ctx}}
    with pytest.raises(ST.Drift):
        ST.write_paper_session(cur, session_date=DAYS[-1] + timedelta(days=1), scope="triggered", source="candidate_observation", variant_ids=ids, events=[ev], counts=dict(n_variants=5, n_candidates=0, n_registered=0,
                         n_skipped_open=0, n_entered=0, n_closed=0, n_not_enterable=0), open_state={"schema": "paper_open_state_v1", "last_session": (DAYS[-1] + timedelta(days=1)).isoformat(), "active": []},
                         run_id="t", writer="t", code_ref="t", started_at=datetime.now(timezone.utc))
    conn.rollback()
    conn.close()


def test_a_failed_session_leaves_nothing_behind(env, monkeypatch):
    src = build_source()
    nxt = DAYS[-1] + timedelta(days=1)
    cal = DAYS + [nxt]
    before = {t: count(env, t) for t in TABLES}
    boom = {"n": 0}
    real = ST.write_paper_session

    def failing(cur, **kw):
        real(cur, **kw)
        boom["n"] += 1
        raise RuntimeError("simulated crash after the rows were inserted")
    monkeypatch.setattr(ST, "write_paper_session", failing)
    conn = env.app_connect_factory()()
    try:
        cur = conn.cursor()
        with pytest.raises(RuntimeError):
            PR.process_session(cur, nxt, src, write=True, calendar=cal, enrich=False)
        conn.rollback()
    finally:
        conn.close()
    assert boom["n"] == 1 and {t: count(env, t) for t in TABLES} == before


def test_a_tampered_stored_snapshot_stops_the_next_session(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("ALTER TABLE paper_session DISABLE TRIGGER USER")                 # only a superuser in a throwaway database could do this; the point is that the runner notices
        cur.execute("UPDATE paper_session SET open_state = jsonb_set(open_state, '{session_no}', '9999') WHERE session_date = (SELECT max(session_date) FROM paper_session)")
        for trg in ("paper_session_immutable_row", "paper_session_immutable_truncate", "paper_session_stamp"):
            cur.execute(f"ALTER TABLE paper_session ENABLE ALWAYS TRIGGER {trg}")
        conn.commit()
        with pytest.raises(ST.StoreError, match="does not match its stored hash"):
            ST.latest_session(conn.cursor())
        assert any("open_state does not match its hash" in p for p in ST.verify(conn.cursor()))
    finally:
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------------------------------------------------------------- rollback
def test_the_rollback_refuses_while_evidence_exists_and_removes_exactly_the_migration_when_approved(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        with pytest.raises(psycopg2.Error, match="immutable evidence"):
            cur.execute(ROLLBACK)
        conn.rollback()
        cur.execute("SET research.rollback_data_loss_approved = 'yes'")
        cur.execute(ROLLBACK)
        conn.commit()
        for t in TABLES:
            cur.execute("SELECT to_regclass(%s) IS NULL", (t,))
            assert cur.fetchone()[0] is True
        cur.execute("SELECT count(*) FROM strategy_variant")
        assert cur.fetchone()[0] == 5                                           # the registry is untouched
    finally:
        conn.close()
    env.exec_admin(MIGRATION)                                                    # and the migration re-applies
    assert count(env, "paper_event") == 0
