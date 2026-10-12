"""Per-stock rs_v1 persistence: the pure record builder, migration 29's CHECKs/immutability, the store writer/reader and the runner flag."""
import math
import os

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.errors
import pytest

import mi_samples as S
import mi_fixtures
from mi_fixtures import ROOT, conn, connect, mi_env  # noqa: F401
from market_intelligence import relative_strength as RS
from market_intelligence import stock_rs_rows as rows
from market_intelligence import store


def _rs(provenance="observed", n=1200):
    return S.relative_strength(provenance, n)


def _fails(cur, sql, params=None, exc=psycopg2.Error, match=None):
    cur.execute("SAVEPOINT s")
    with pytest.raises(exc) as e:
        cur.execute(sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    if match:
        assert match in str(e.value)


# ---------------------------------------------------------------- pure record builder
def test_one_record_per_stock_and_horizon_in_a_deterministic_order_copied_from_the_model():
    rs = _rs()
    recs = rows.stock_rs_records(rs)
    assert len(recs) == len(rs.stocks) * len(rs.horizons)
    assert recs == rows.stock_rs_records(rs)
    keys = [(r["symbol"], r["horizon_sessions"]) for r in recs]
    assert keys == sorted(keys)
    r = next(x for x in recs if x["symbol"] == "S0007" and x["horizon_sessions"] == 20)
    st = rs.stocks.loc["S0007"]
    assert r["ret_pct"] == pytest.approx(st["ret_20"]) and r["vs_spx_pp"] == pytest.approx(st["vs_spx_20"])
    assert r["rs_percentile"] == pytest.approx(st["rs_pctile_20"]) and r["vs_sector_pp"] == pytest.approx(st["vs_sector_20"])
    assert r["state"] == "ok" and r["benchmark_symbol"] == "^GSPC" and r["n_universe_valid"] == rs.universe[20]["n_valid"]


def test_an_unmeasurable_stock_is_stored_unavailable_with_nulls_never_zero_and_never_dropped():
    close, sector_of, idx = S.panel(1200)
    close.loc[close.index[:-30], "S0003"] = np.nan                  # too little history for the 60-session horizon only
    rs = RS.compute(S.T, close, sector_of, idx, {}, sector_provenance="observed")
    recs = {(r["symbol"], r["horizon_sessions"]): r for r in rows.stock_rs_records(rs)}
    short, ok = recs[("S0003", 60)], recs[("S0003", 5)]
    assert short["state"] == "unavailable" and short["ret_pct"] is None and short["vs_spx_pp"] is None and short["rs_percentile"] is None
    assert ok["state"] == "ok"
    assert all(v is None or (isinstance(v, (int, float)) and math.isfinite(v)) for r in recs.values() for v in
               (r["ret_pct"], r["vs_spx_pp"], r["vs_sector_pp"], r["rs_percentile"]))


def test_pit_safe_requires_a_pit_safe_sector_map_and_a_sector():
    obs = rows.stock_rs_records(_rs("observed"))
    rec = rows.stock_rs_records(_rs("reconstructed"))
    assert all(r["sector_pit_safe"] == (r["sector"] is not None) for r in obs)       # observed map: safe exactly where a sector exists
    assert not any(r["sector_pit_safe"] for r in rec)                   # a reconstructed map can never claim PIT safety
    assert not any(r["sector_pit_safe"] for r in obs if r["sector"] is None)


def test_records_hash_depends_on_content_and_identity():
    recs = rows.stock_rs_records(_rs())
    h = rows.records_hash(recs, "2026-09-30", "rs_v1", "mi_v2", "observed")
    assert h == rows.records_hash(recs, "2026-09-30", "rs_v1", "mi_v2", "observed")
    assert h != rows.records_hash(recs, "2026-09-30", "rs_v2", "mi_v2", "observed")
    assert h != rows.records_hash(recs[:-1], "2026-09-30", "rs_v1", "mi_v2", "observed")


def test_an_empty_universe_yields_no_records():
    class Empty:
        stocks = pd.DataFrame()
    assert rows.stock_rs_records(Empty()) == []


# ---------------------------------------------------------------- migration 29 constraints and immutability
BASE = ("INSERT INTO stock_relative_strength (session_date, symbol, horizon_sessions, model_version, feature_set_version, provenance, "
        "reconstruction_basis, sector, sector_pit_safe, state, ret_pct, vs_spx_pp, vs_sector_pp, rs_percentile, n_universe_valid, "
        "benchmark_symbol, run_content_hash, code_ref) VALUES ")
H = "a" * 64


def _row(**o):
    d = dict(sd="2026-09-30", sym="AAA", h=20, mv="rs_v1", fv="mi_v2", prov="observed", basis=None, sec="Tech", pit=True, state="ok", ret=1.5,
             spx=0.5, vsec=0.2, pct=55.0, n=1100, bench="^GSPC", hh=H, code="c")
    d.update(o)
    return BASE + "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", list(d.values())


@pytest.mark.parametrize("over,exc", [
    (dict(prov="reconstructed"), psycopg2.errors.CheckViolation),                    # reconstructed needs a basis
    (dict(basis="x"), psycopg2.errors.CheckViolation),                               # observed must not carry one
    (dict(prov="reconstructed", basis="x", pit=True), psycopg2.errors.CheckViolation),   # reconstructed can never be pit safe
    (dict(sec=None, pit=True, vsec=None), psycopg2.errors.CheckViolation),
    (dict(state="ok", ret=None, spx=None, vsec=None, pct=None), psycopg2.errors.CheckViolation),    # ok needs a value
    (dict(state="unavailable"), psycopg2.errors.CheckViolation),                     # unavailable must not carry one (no stand-in numbers)
    (dict(pct=101.0), psycopg2.errors.CheckViolation),
    (dict(ret=float("nan")), psycopg2.errors.CheckViolation),
    (dict(h=0), psycopg2.errors.CheckViolation),
    (dict(hh="zz"), psycopg2.errors.CheckViolation),
    (dict(sec=None, vsec=0.2, pit=False), psycopg2.errors.CheckViolation),           # a sector-relative value needs a sector
])
def test_check_constraints_refuse_dishonest_rows(conn, over, exc):
    cur = conn.cursor()
    _fails(cur, *_row(**over), exc=exc)


def test_unavailable_rows_are_stored_as_nulls_and_the_key_is_unique(conn):
    cur = conn.cursor()
    cur.execute(*_row(state="unavailable", ret=None, spx=None, vsec=None, pct=None, sym="BBB"))
    cur.execute(*_row())
    cur.execute("SELECT ret_pct, created_at FROM stock_relative_strength WHERE symbol = 'BBB'")
    r = cur.fetchone()
    assert r[0] is None and r[1] is not None
    _fails(cur, *_row(), exc=psycopg2.errors.UniqueViolation)
    cur.execute(*_row(mv="rs_v2"))                                                   # a new definition is a new row beside the old


@pytest.mark.parametrize("sql", ["UPDATE stock_relative_strength SET ret_pct = 0", "DELETE FROM stock_relative_strength",
                                 "TRUNCATE stock_relative_strength"])
def test_rows_are_immutable_even_for_the_owner(conn, sql):
    cur = conn.cursor()
    cur.execute(*_row())
    _fails(cur, sql, exc=psycopg2.errors.IntegrityConstraintViolation, match="append-only")


def test_created_at_is_stamped_by_the_database_not_the_caller(conn):
    cur = conn.cursor()
    cur.execute(*_row())
    cur.execute("SELECT abs(extract(epoch FROM (created_at - now()))) < 60 FROM stock_relative_strength")
    assert cur.fetchone()[0]


def test_migration_29_is_reapplyable_and_refuses_a_foreign_table(conn):
    cur = conn.cursor()
    with open(os.path.join(ROOT, "mechanism", "add_stock_relative_strength_table.sql"), encoding="utf-8") as fh:
        sql = fh.read()
    cur.execute(sql)
    cur.execute("DROP TABLE stock_relative_strength")
    cur.execute("CREATE TABLE stock_relative_strength (x int)")
    _fails(cur, sql, match="migration 29 refused")


# ---------------------------------------------------------------- store writer / reader
def test_write_is_idempotent_complete_and_leaves_stored_rows_alone_when_inputs_are_restated(conn):
    cur = conn.cursor()
    rs = _rs(n=1100)
    w1 = store.write_stock_rs(cur, rs, "observed", "mi_v2", "ref")
    assert w1["written"] == w1["n_records"] == 1100 * 3 and not w1["differs_from_stored"]
    w2 = store.write_stock_rs(cur, rs, "observed", "mi_v2", "ref")
    assert w2["written"] == 0 and w2["run_content_hash"] == w1["run_content_hash"] and not w2["differs_from_stored"]
    close, sector_of, idx = S.panel(1100)
    close.iloc[-1, 0] *= 1.05                                                       # a restated closing price
    rs2 = RS.compute(S.T, close, sector_of, idx, {}, sector_provenance="observed")
    w3 = store.write_stock_rs(cur, rs2, "observed", "mi_v2", "ref")
    assert w3["written"] == 0 and w3["differs_from_stored"]                         # nothing overwritten; the restatement is flagged
    cur.execute("SELECT ret_pct FROM stock_relative_strength WHERE symbol = 'S0000' AND horizon_sessions = 5")
    assert cur.fetchone()[0] == pytest.approx(rs.stocks.loc["S0000", "ret_5"])


def test_the_writer_refuses_a_sector_map_whose_provenance_differs_from_the_row_provenance(conn):
    with pytest.raises(ValueError):
        store.write_stock_rs(conn.cursor(), _rs("reconstructed", 1100), "observed", "mi_v2", "ref")


def test_reader_is_observed_only_by_default_and_never_shows_a_row_that_did_not_exist_yet(conn):
    cur = conn.cursor()
    store.write_stock_rs(cur, _rs("observed", 1100), "observed", "mi_v2", "ref")
    store.write_stock_rs(cur, _rs("reconstructed", 1100), "reconstructed", "mi_v2", "ref", reconstruction_basis="test")
    obs = store.get_stock_rs(cur, S.T, ["S0001"])
    assert len(obs) == 3 and {r["provenance"] for r in obs} == {"observed"}
    both = store.get_stock_rs(cur, S.T, ["S0001"], include_reconstructed=True)
    assert {r["provenance"] for r in both} == {"observed", "reconstructed"}
    cur.execute("SELECT min(created_at) FROM stock_relative_strength")
    first = cur.fetchone()[0]
    import datetime as dt
    assert store.get_stock_rs(cur, S.T, ["S0001"], known_by=first - dt.timedelta(seconds=1)) == []
    with pytest.raises(ValueError):
        store.get_stock_rs(cur, S.T, known_by=dt.datetime(2026, 1, 1))


def test_an_unavailable_row_reads_back_as_none_not_zero(conn):
    cur = conn.cursor()
    close, sector_of, idx = S.panel(1100)
    close.loc[close.index[:-30], "S0003"] = np.nan
    rs = RS.compute(S.T, close, sector_of, idx, {}, sector_provenance="observed")
    store.write_stock_rs(cur, rs, "observed", "mi_v2", "ref")
    (r,) = [x for x in store.get_stock_rs(cur, S.T, ["S0003"]) if x["horizon_sessions"] == 60]
    assert r["state"] == "unavailable" and r["ret_pct"] is None and r["rs_percentile"] is None
