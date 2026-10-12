"""fwd_v1 against a real Postgres scratch schema (migrations 19-22 + 26 on the real base schema): the label table's
constraints and immutability, the repository, and the runner's session-explicit / idempotent / concurrency-safe behaviour.
CI must run this with a real Postgres (a skip there is a failure)."""
import dataclasses
import threading
from datetime import date, timedelta

import psycopg2
import psycopg2.errors as pgerr
import pytest

from conftest import Seed
from research.labels import fwd_v1 as f
from research.labels import repository as repo
from research.labels import runner

T0 = date(2099, 1, 5)


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


CAL = weekdays(T0, 90)


def put_prices(cur, symbol, closes, start=0, spread=0.01, skip=()):
    for i, c in enumerate(closes):
        if i in skip:
            continue
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, 1000)",
                    (symbol, CAL[start + i], c, round(c * (1 + spread), 4), round(c * (1 - spread), 4), c, c))


def put_index(cur, closes, start=0, symbol="^GSPC"):
    for i, c in enumerate(closes):
        cur.execute("INSERT INTO market_index_prices (symbol, date, open, high, low, close) VALUES (%s, %s, %s, %s, %s, %s)",
                    (symbol, CAL[start + i], c, c, c, c))


def world(connect, closes, *, n=30, direction=1, entry=None, skip=(), bench=None, symbol="AAA"):
    """One committed observation at CAL[0], its stock bars, a filler symbol covering every session (so the derived
    calendar never loses a session just because AAA has a gap), and an index series."""
    with connect() as conn:
        cur = conn.cursor()
        put_prices(cur, "FILL", [50.0] * n)
        put_prices(cur, symbol, closes, skip=skip)
        put_index(cur, bench if bench is not None else [4000.0] * n)
        conn.commit()
        s = Seed(conn)
        oid = s.observation(symbol=symbol, session=CAL[0], direction=direction,
                            entry_close=closes[0] if entry is None else entry)
        conn.commit()
    return oid


def labels(connect, **where):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT horizon_sessions, label_status, void_reason, raw_return, directional_return, benchmark_state, "
                    "path_state, mfe, mae, input_hash, computed_as_of_session FROM forward_return_label "
                    "ORDER BY observation_id, horizon_sessions")
        return cur.fetchall()


# ------------------------------------------------------------------------------------------ the table itself
@pytest.fixture
def one(lconn):
    """(connection, seed, observation id, a valid final Label for horizon 5)."""
    cur = lconn.cursor()
    put_prices(cur, "FILL", [50.0] * 30)
    put_prices(cur, "AAA", [100, 101, 102, 103, 104, 105] + [105] * 24)
    put_index(cur, [4000.0 + i for i in range(30)])
    oid = Seed(lconn).observation(symbol="AAA", session=CAL[0], entry_close=100)
    obs = f.Observation(oid, "AAA", 1, CAL[0], 100.0)
    bars = repo.fetch_bars(lconn, ["AAA"], CAL[0], CAL[10])["AAA"]
    bench = repo.fetch_benchmark(lconn, "^GSPC", CAL[0], CAL[10])
    lab = f.compute(obs, 5, CAL, bars, bench, CAL[5])
    assert lab.label_status == "final"
    return lconn, oid, lab


def expect(lconn, exc, label):
    cur = lconn.cursor()
    cur.execute("SAVEPOINT sp")
    with pytest.raises(exc):
        repo.insert_label(cur, label)
    cur.execute("ROLLBACK TO SAVEPOINT sp")


def test_valid_final_label_inserts_once_and_a_second_attempt_is_a_noop(one):
    conn, oid, lab = one
    cur = conn.cursor()
    assert repo.insert_label(cur, lab) == (True, None)
    assert repo.insert_label(cur, lab) == (False, lab.input_hash)
    cur.execute("SELECT count(*) FROM forward_return_label")
    assert cur.fetchone()[0] == 1


def test_a_second_attempt_with_different_inputs_does_not_overwrite_and_reports_the_stored_hash(one):
    conn, oid, lab = one
    cur = conn.cursor()
    repo.insert_label(cur, lab)
    other = dataclasses.replace(lab, raw_return=0.5, directional_return=0.5, input_hash="e" * 64)
    inserted, stored = repo.insert_label(cur, other)
    assert inserted is False and stored == lab.input_hash
    cur.execute("SELECT raw_return FROM forward_return_label")
    assert cur.fetchone()[0] == pytest.approx(0.05)


@pytest.mark.parametrize("sql", ["UPDATE forward_return_label SET raw_return = 0.9",
                                 "DELETE FROM forward_return_label",
                                 "TRUNCATE forward_return_label"])
def test_the_table_is_immutable_for_every_mutation(one, sql):
    conn, oid, lab = one
    cur = conn.cursor()
    repo.insert_label(cur, lab)
    cur.execute("SAVEPOINT sp")
    with pytest.raises(pgerr.IntegrityConstraintViolation):
        cur.execute(sql)
    cur.execute("ROLLBACK TO SAVEPOINT sp")


def test_immutability_survives_the_replication_role_bypass(one):
    conn, oid, lab = one
    cur = conn.cursor()
    repo.insert_label(cur, lab)
    cur.execute("SET LOCAL session_replication_role = replica")
    for sql in ("UPDATE forward_return_label SET raw_return = 0.9", "DELETE FROM forward_return_label"):
        cur.execute("SAVEPOINT sp")
        with pytest.raises(pgerr.IntegrityConstraintViolation):
            cur.execute(sql)
        cur.execute("ROLLBACK TO SAVEPOINT sp")


def test_the_observation_identity_is_checked_on_insert(one):
    conn, oid, lab = one
    expect(conn, pgerr.IntegrityConstraintViolation, dataclasses.replace(lab, symbol="ZZZ"))
    expect(conn, pgerr.IntegrityConstraintViolation, dataclasses.replace(lab, direction=-1, directional_return=-lab.raw_return))
    expect(conn, pgerr.IntegrityConstraintViolation, dataclasses.replace(lab, t0_session=CAL[1]))
    expect(conn, pgerr.ForeignKeyViolation, dataclasses.replace(lab, observation_id=oid + 999))


@pytest.mark.parametrize("change", [
    dict(horizon_sessions=2, bars_expected=2),                                    # not a declared horizon
    dict(label_status="other"),
    dict(void_reason="x"),                                                        # a final label carries no void reason
    dict(horizon_close=None),
    dict(raw_return=None, directional_return=None),
    dict(benchmark_state="maybe"),
    dict(benchmark_return=None),                                                  # ok state needs the return
    dict(benchmark_state="unavailable"),                                          # unavailable must not carry a return
    dict(path_state="complete", mfe=None),
    dict(path_state="incomplete"),                                                # incomplete must not carry excursions
    dict(mfe=-0.01), dict(mae=0.01),
    dict(directional_return=-0.05),                                               # must equal direction * raw
    dict(horizon_session=CAL[0]),                                                 # not after T0
    dict(computed_as_of_session=CAL[4]),                                          # computed before the horizon existed
    dict(bars_expected=4), dict(bars_observed=9),
    dict(data_quality="great"),
])
def test_final_label_check_constraints_reject_inconsistent_rows(one, change):
    conn, oid, lab = one
    expect(conn, (pgerr.CheckViolation, pgerr.IntegrityConstraintViolation), dataclasses.replace(lab, **change))


def test_a_void_label_carries_a_reason_and_no_outcome(one):
    conn, oid, lab = one
    bars = repo.fetch_bars(conn, ["AAA"], CAL[0], CAL[10])["AAA"]
    del bars[CAL[5]]
    void = f.compute(f.Observation(oid, "AAA", 1, CAL[0], 100.0), 5, CAL, bars, {}, CAL[8])
    assert void.label_status == "void"
    cur = conn.cursor()
    assert repo.insert_label(cur, void)[0] is True
    expect(conn, pgerr.CheckViolation, dataclasses.replace(void, void_reason=None, horizon_sessions=10, bars_expected=10))
    expect(conn, pgerr.CheckViolation, dataclasses.replace(void, horizon_sessions=3, bars_expected=3, raw_return=0.1,
                                                           directional_return=0.1))


def test_mature_reader_returns_final_labels_only(one):
    conn, oid, lab = one
    cur = conn.cursor()
    repo.insert_label(cur, lab)
    bars = repo.fetch_bars(conn, ["AAA"], CAL[0], CAL[12])["AAA"]
    del bars[CAL[10]]
    void = f.compute(f.Observation(oid, "AAA", 1, CAL[0], 100.0), 10, CAL, bars, {}, CAL[13])
    assert void.label_status == "void"
    repo.insert_label(cur, void)
    got = repo.fetch_mature_labels(conn)
    assert [r["horizon_sessions"] for r in got] == [5] and all(r["label_status"] == "final" for r in got)
    assert repo.fetch_mature_labels(conn, horizon=10) == []


# ------------------------------------------------------------------------------------------ runner
def test_dry_run_writes_nothing_and_apply_writes_exactly_the_matured_horizons(labels_env):
    _, connect = labels_env
    world(connect, [100 + i for i in range(30)])
    dry = runner.run(connect, CAL[3], apply=False)
    assert dry.final == 2 and dry.inserted == 0 and labels(connect) == []          # horizons 1 and 3
    rep = runner.run(connect, CAL[3], apply=True)
    assert (rep.final, rep.void, rep.inserted) == (2, 0, 2)
    assert [r[0] for r in labels(connect)] == [1, 3]
    assert rep.pending == {"horizon_not_reached": 4}


def test_rerun_is_idempotent_and_a_later_as_of_adds_only_new_horizons(labels_env):
    _, connect = labels_env
    world(connect, [100 + i for i in range(30)])
    runner.run(connect, CAL[5], apply=True)
    before = labels(connect)
    again = runner.run(connect, CAL[5], apply=True)
    assert again.final == 0 and again.inserted == 0 and labels(connect) == before
    later = runner.run(connect, CAL[10], apply=True)
    assert later.inserted == 1 and [r[0] for r in labels(connect)] == [1, 3, 5, 10]
    assert labels(connect)[:3] == before                                           # earlier labels untouched


def test_run_outcome_is_independent_of_when_it_runs_and_of_data_after_as_of(labels_env):
    _, connect = labels_env
    world(connect, [100 + i for i in range(30)])
    a = runner.run(connect, CAL[3], apply=False)
    with connect() as conn:                                                       # data arrives for LATER sessions
        cur = conn.cursor()
        cur.execute("UPDATE stock_prices SET close = close * 3, high = high * 3, low = low * 3 WHERE date > %s AND symbol = 'AAA'",
                    (CAL[3],))
        conn.commit()
    b = runner.run(connect, CAL[3], apply=False)
    assert (a.final, a.void, a.pending) == (b.final, b.void, b.pending)
    runner.run(connect, CAL[3], apply=True)
    rows = labels(connect)
    assert rows[0][3] == pytest.approx(1 / 100) and rows[1][3] == pytest.approx(3 / 100)    # CAL[1]=101, CAL[3]=103


def test_as_of_without_bars_fails_closed(labels_env):
    _, connect = labels_env
    world(connect, [100.0] * 10, n=10)
    with pytest.raises(f.CalendarError):
        runner.run(connect, CAL[12], apply=True)                                   # no stock bars on that date
    assert labels(connect) == []


def test_an_index_session_missing_from_the_stock_calendar_fails_closed(labels_env):
    _, connect = labels_env
    world(connect, [100.0] * 12, n=12)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM stock_prices WHERE date = %s", (CAL[4],))        # the whole session vanishes from stocks
        conn.commit()
    with pytest.raises(f.CalendarError):
        runner.run(connect, CAL[8], apply=True)


def test_void_after_grace_is_written_as_void_and_never_as_an_outcome(labels_env):
    _, connect = labels_env
    world(connect, [100.0] * 30, skip=(5,))                                         # AAA has no bar on its horizon-5 session
    early = runner.run(connect, CAL[6], apply=True)
    assert early.pending.get("awaiting_inputs") == 1 and [r[0] for r in labels(connect)] == [1, 3]
    late = runner.run(connect, CAL[5 + f.VOID_GRACE_SESSIONS], apply=True)
    rows = {r[0]: r for r in labels(connect)}
    assert rows[5][1] == "void" and rows[5][2] == f.REASON_MISSING_HORIZON and rows[5][3] is None
    assert late.void == 1


def test_short_observation_end_to_end(labels_env):
    _, connect = labels_env
    world(connect, [100, 99, 98, 97, 96, 90] + [90] * 24, direction=-1)
    runner.run(connect, CAL[5], apply=True)
    r5 = {r[0]: r for r in labels(connect)}[5]
    assert r5[3] == pytest.approx(-0.10) and r5[4] == pytest.approx(0.10) and r5[5] == "ok" and r5[6] == "complete"
    assert r5[7] >= 0 >= r5[8]


def test_concurrent_runs_write_each_label_exactly_once(labels_env):
    _, connect = labels_env
    world(connect, [100 + i * 0.5 for i in range(30)])
    reports, errors = [], []

    def go():
        try:
            reports.append(runner.run(connect, CAL[12], apply=True))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    rows = labels(connect)
    assert [r[0] for r in rows] == [1, 3, 5, 10]
    assert sum(r.inserted for r in reports) == 4
    assert sum(r.already_present for r in reports) + 4 == sum(r.evaluated for r in reports) - sum(
        sum(r.pending.values()) for r in reports)


def test_restatement_is_reported_and_the_stored_label_is_not_rewritten(labels_env):
    _, connect = labels_env
    world(connect, [100 + i for i in range(30)])
    runner.run(connect, CAL[5], apply=True)
    before = labels(connect)
    assert runner.restatement_report(connect, CAL[5]) == []
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE stock_prices SET close = close * 1.0123 WHERE symbol = 'AAA' AND date = %s", (CAL[4],))
        conn.commit()
    rep = runner.restatement_report(connect, CAL[5])
    assert {(r["horizon"]) for r in rep} == {5} and rep[0]["change"] == "inputs_restated"     # h=1, h=3 windows exclude CAL[4]
    assert labels(connect) == before                                                           # never rewritten
    again = runner.run(connect, CAL[5], apply=True)
    assert again.inserted == 0


def test_split_adjusted_history_between_t0_and_label_time_is_flagged_not_silently_accepted(labels_env):
    _, connect = labels_env
    world(connect, [100.0] * 30)
    with connect() as conn:
        cur = conn.cursor()                                                        # the provider halves history up to CAL[2]
        cur.execute("UPDATE stock_prices SET open=open/2, high=high/2, low=low/2, close=close/2, adj_close=adj_close/2 "
                    "WHERE symbol='AAA' AND date <= %s", (CAL[2],))
        conn.commit()
    runner.run(connect, CAL[5 + f.VOID_GRACE_SESSIONS], apply=True)
    rows = {r[0]: r for r in labels(connect)}
    assert rows[1][1] == "final" and rows[1][3] == pytest.approx(0.0)      # window T0..T1 sits inside the halved block
    assert rows[3][1] == "void" and rows[3][2] == f.REASON_DISCONTINUITY                          # jump at CAL[3]
    assert rows[5][1] == "void" and rows[5][2] == f.REASON_DISCONTINUITY


def test_entry_close_vs_stored_basis_is_recorded(labels_env):
    _, connect = labels_env
    world(connect, [50.0] * 30, entry=100.0)                                       # stored history is half the captured entry close
    runner.run(connect, CAL[1], apply=True)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT data_quality, basis_ratio, reference_close, entry_close_captured FROM forward_return_label")
        dq, ratio, ref, entry = cur.fetchone()
    assert dq == "basis_adjusted" and ratio == pytest.approx(2.0) and float(ref) == 50 and float(entry) == 100
