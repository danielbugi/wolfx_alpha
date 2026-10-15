"""The dashboard against the REAL stored rows: a few dozen sessions are replayed through the real tracker on a throwaway production-shaped database, then the read-only fetch layer and the pure
aggregation are run and EVERY headline number is checked against an independent SQL aggregate of the same stored rows. The dashboard can therefore never drift from the stored events."""
import json

import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
import test_paper_tracker_db as T
from test_paper_tracker_validation_db import FIRST, build_env
from research import paper_dashboard as D
from research import paper_dashboard_io as IO


@pytest.fixture(scope="module")
def world():
    e, teardown = build_env("pdash")
    try:
        src = T.build_source()
        days = T.DAYS[FIRST:FIRST + 70]
        for d in days:
            assert T.run_session(e, d, src, activation=(d == days[0]), enrich=False).status == "processed"
        conn = e.admin()
        conn.set_session(readonly=True)
        cur = conn.cursor()
        payload = D.build_forward_payload(**IO.fetch_forward_inputs(cur))
        yield e, cur, payload, days
        conn.rollback()
        conn.close()
    finally:
        teardown()


def one(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()


def test_the_payload_is_forward_labelled_json_safe_and_describes_the_latest_stored_session(world):
    e, cur, p, days = world
    assert p["available"] is True and p["source"] == "forward" and p["sessions_processed"] == len(days) and p["since"] == days[0].isoformat()
    json.dumps(p, allow_nan=False)
    row = one(cur, "SELECT session_date, n_registered, n_entered, n_closed, n_open_after FROM paper_session ORDER BY session_date DESC LIMIT 1")
    ls = p["latest_session"]
    assert (ls["session_date"], ls["n_registered"], ls["n_entered"], ls["n_closed"], ls["n_open_after"]) == (row[0].isoformat(), row[1], row[2], row[3], row[4])


def test_position_counts_per_variant_equal_independent_sql_counts(world):
    e, cur, p, days = world
    for v in p["variants"]:
        reg, ent, clo, nen = one(cur, "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_event x WHERE x.observation_id = o.id AND x.event_type = 'entered')), "
                                      "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_event x WHERE x.observation_id = o.id AND x.event_type = 'closed')), "
                                      "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_event x WHERE x.observation_id = o.id AND x.event_type = 'not_enterable')) "
                                      "FROM paper_observation o JOIN strategy_variant sv ON sv.id = o.variant_id WHERE sv.variant_key = %s", (v["variant_key"],))
        pos = v["positions"]
        assert pos["registered"] == reg and pos["closed"] == clo and pos["not_enterable"] == nen and pos["open"] == ent - clo and pos["pending_entry"] == reg - ent - nen, v["variant_key"]
    assert sum(v["positions"]["closed"] for v in p["variants"]) > 50, "the replay must have produced real closed positions"


def test_realised_net_r_at_every_cost_mfe_and_mae_equal_independent_sql_aggregates(world):
    e, cur, p, days = world
    for v in p["variants"]:
        for bps in ("0", "10", "25"):
            n, mean = one(cur, "SELECT count(*), avg((c.payload -> 'r_net_std' ->> %s)::float) FROM paper_event c JOIN paper_observation o ON o.id = c.observation_id JOIN strategy_variant sv ON sv.id = o.variant_id "
                               "WHERE c.event_type = 'closed' AND sv.variant_key = %s", (bps, v["variant_key"]))
            got = v["closed"]["net_r_std"][bps]
            assert got["n"] == n and got["mean"] == pytest.approx(mean, abs=1e-4), (v["variant_key"], bps)
        mfe, mae, bars = one(cur, "SELECT avg((c.payload ->> 'mfe_r_std')::float), avg((c.payload ->> 'mae_r_std')::float), avg((c.payload ->> 'bars_held')::float) FROM paper_event c "
                                  "JOIN paper_observation o ON o.id = c.observation_id JOIN strategy_variant sv ON sv.id = o.variant_id WHERE c.event_type = 'closed' AND sv.variant_key = %s", (v["variant_key"],))
        assert v["closed"]["mfe_r"]["mean"] == pytest.approx(mfe, abs=1e-4) and v["closed"]["mae_r"]["mean"] == pytest.approx(mae, abs=1e-4) and v["closed"]["bars_held"]["mean"] == pytest.approx(bars, abs=1e-4)
        assert v["closed"]["net_r_std"]["0"]["mean"] >= v["closed"]["net_r_std"]["10"]["mean"] >= v["closed"]["net_r_std"]["25"]["mean"]


def test_open_position_marks_equal_an_independent_sql_mark_to_market(world):
    e, cur, p, days = world
    for v in p["variants"]:
        n, avg_gross = one(cur, "SELECT count(*), avg(o.direction * (px.close - (en.payload ->> 'entry_price')::float) / (en.payload ->> 'unit')::float) "
                                "FROM paper_observation o JOIN strategy_variant sv ON sv.id = o.variant_id "
                                "JOIN paper_event en ON en.observation_id = o.id AND en.event_type = 'entered' "
                                "JOIN LATERAL (SELECT close FROM stock_prices WHERE symbol = o.symbol ORDER BY date DESC LIMIT 1) px ON TRUE "
                                "WHERE sv.variant_key = %s AND NOT EXISTS (SELECT 1 FROM paper_event c WHERE c.observation_id = o.id AND c.event_type = 'closed')", (v["variant_key"],))
        o = v["open"]
        assert o["n"] == n and o["marked"] == n and o["unmarked"] == 0
        if n:
            assert o["unrealised_gross_r"]["mean"] == pytest.approx(avg_gross, abs=1e-4)


def test_open_plus_pending_equals_the_stored_open_state_of_the_latest_session(world):
    e, cur, p, days = world
    n_open_after = one(cur, "SELECT n_open_after FROM paper_session ORDER BY session_date DESC LIMIT 1")[0]
    assert sum(v["positions"]["open"] + v["positions"]["pending_entry"] for v in p["variants"]) == n_open_after


def test_completeness_matches_the_stored_session_rows_and_health_is_reported(world):
    e, cur, p, days = world
    rows = p["completeness"]["sessions"]
    assert len(rows) == len(days) and [r["session"] for r in rows] == [d.isoformat() for d in days]
    for r in rows[:3]:
        n_sql = one(cur, "SELECT count(DISTINCT (symbol, direction)) FROM paper_observation WHERE signal_session = %s", (r["session"],))[0]
        assert r["candidates"] == n_sql
    assert {h["name"] for h in p["health"]} >= {"session_continuity", "market_regime_coverage", "relative_strength_coverage", "catalyst_context", "open_positions_marked"}


def test_the_whole_run_is_read_only_and_absent_storage_is_an_explicit_unavailable_answer(world):
    e, cur, p, days = world
    count = lambda: (one(cur, "SELECT count(*) FROM paper_session")[0], one(cur, "SELECT count(*) FROM paper_observation")[0], one(cur, "SELECT count(*) FROM paper_event")[0])
    before = count()
    IO.forward_payload(cur)
    assert before == count()
    cur.connection.rollback()                                                                # release this read-only transaction's locks before the DDL below
    conn2 = e.admin()
    try:
        c2 = conn2.cursor()
        c2.execute("ALTER TABLE paper_event RENAME TO paper_event_gone")                    # inside a transaction that is rolled back below
        out = IO.forward_payload(c2)
        assert out["available"] is False and out["source"] == "forward" and "not provisioned" in out["reason"]
    finally:
        conn2.rollback()
        conn2.close()
