"""D3: reconstructed rows are excluded from live / point-in-time reads unless explicitly opted in; the database refuses any other
provenance; reconstructing an already-observed key never alters it."""
import psycopg2
import pytest

import mi_samples as S
from mi_fixtures import conn, connect, mi_env  # noqa: F401  (explicit fixtures: no top-level `conftest` name that could shadow research/tests)
from market_intelligence import provenance as P
from market_intelligence import store


# ---- pure ------------------------------------------------------------------------------------------------------------
def test_default_scope_is_observed_only():
    assert P.read_scope() == ("observed",)


def test_opt_in_widens_scope_and_is_the_only_way():
    assert set(P.read_scope(include_reconstructed=True)) == {"observed", "reconstructed"}
    assert P.read_scope("reconstructed", include_reconstructed=True) == ("reconstructed",)
    assert P.read_scope("observed") == ("observed",)


def test_asking_for_reconstructed_without_the_opt_in_is_refused():
    with pytest.raises(ValueError):
        P.read_scope("reconstructed")


def test_unknown_provenance_is_refused():
    for bad in ("", "OBSERVED", "backfilled", "live"):
        with pytest.raises(ValueError):
            P.read_scope(bad)


def test_sql_filter_is_parameterised():
    clause, params = P.sql_filter("m.provenance")
    assert clause == "m.provenance = ANY(%s)" and params == (["observed"],)


# ---- database --------------------------------------------------------------------------------------------------------
def _write(conn, provenance, **kw):
    rs = S.relative_strength(provenance)
    basis = "recomputed from stored prices" if provenance == "reconstructed" else None
    cur = conn.cursor()
    out = store.write_session(cur, S.regime(), rs, provenance, "unit-test", "test@abc", reconstruction_basis=basis, **kw)
    conn.commit()
    return out


def test_a_no_argument_query_cannot_return_a_reconstructed_row(conn):
    _write(conn, "reconstructed")
    cur = conn.cursor()
    assert store.get_market_snapshot(cur) is None
    assert store.list_market_snapshots(cur) == []
    assert store.get_sector_snapshots(cur, S.T) == []
    _write(conn, "observed")
    assert store.get_market_snapshot(cur)["provenance"] == "observed"
    assert [r["provenance"] for r in store.list_market_snapshots(cur)] == ["observed"]
    assert {r["provenance"] for r in store.get_sector_snapshots(cur, S.T)} == {"observed"}


def test_opt_in_returns_both_and_labels_each_row(conn):
    _write(conn, "observed")
    _write(conn, "reconstructed")
    cur = conn.cursor()
    rows = store.list_market_snapshots(cur, include_reconstructed=True)
    assert sorted(r["provenance"] for r in rows) == ["observed", "reconstructed"]
    assert store.get_market_snapshot(cur, S.T, "reconstructed", include_reconstructed=True)["provenance"] == "reconstructed"
    # with both present, an opted-in "latest" prefers the observed one
    assert store.get_market_snapshot(cur, include_reconstructed=True)["provenance"] == "observed"
    with pytest.raises(ValueError):
        store.get_market_snapshot(cur, S.T, "reconstructed")


def test_the_check_refuses_any_other_provenance(conn):
    cur = conn.cursor()
    for tbl, cols, vals in (
        ("universe_snapshot", "(session_date, provenance, n_symbols, n_classified, sector_map, sector_source, sector_asof_rule, "
         "sector_pit_safe, content_hash, code_ref)", "(%s, %s, 1, 1, '{}', 's', 'r', false, %s, 'c')"),
    ):
        for bad in ("live", "OBSERVED", "backfill", ""):
            cur.execute("SAVEPOINT s")
            with pytest.raises(psycopg2.errors.CheckViolation):
                cur.execute(f"INSERT INTO {tbl} {cols} VALUES {vals}", (S.T, bad, "0" * 64))
            cur.execute("ROLLBACK TO SAVEPOINT s")


def test_reconstructed_needs_a_basis_and_observed_must_not_have_one(conn):
    cur = conn.cursor()
    base = ("INSERT INTO universe_snapshot (session_date, provenance, n_symbols, n_classified, sector_map, sector_source, sector_asof_rule, "
            "sector_pit_safe, content_hash, code_ref, reconstruction_basis) VALUES (%s,%s,1,1,'{}','s','r',false,%s,'c',%s)")
    for prov, basis in (("reconstructed", None), ("observed", "why")):
        cur.execute("SAVEPOINT s")
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(base, (S.T, prov, "0" * 64, basis))
        cur.execute("ROLLBACK TO SAVEPOINT s")


def test_a_reconstructed_sector_map_can_never_claim_pit_safety(conn):
    cur = conn.cursor()
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("INSERT INTO universe_snapshot (session_date, provenance, n_symbols, n_classified, sector_map, sector_source, "
                    "sector_asof_rule, sector_pit_safe, content_hash, code_ref, reconstruction_basis) "
                    "VALUES (%s,'reconstructed',1,1,'{}','s','r',true,%s,'c','b')", (S.T, "0" * 64))


def test_reconstructing_an_existing_observed_key_does_not_alter_it(conn):
    first = _write(conn, "observed")
    cur = conn.cursor()
    cur.execute("SELECT id, content_hash, captured_at, regime_score FROM market_snapshot WHERE provenance = 'observed'")
    before = cur.fetchall()
    _write(conn, "reconstructed")
    again = _write(conn, "observed")                      # re-capturing is a no-op, not an overwrite
    assert again["market"].created is False and again["market"].id == first["market"].id
    cur.execute("SELECT id, content_hash, captured_at, regime_score FROM market_snapshot WHERE provenance = 'observed'")
    assert cur.fetchall() == before
    cur.execute("SELECT count(*) FROM market_snapshot")
    assert cur.fetchone()[0] == 2                          # observed + reconstructed live side by side, never merged


def test_an_observed_row_cannot_hang_off_a_reconstructed_parent(conn):
    _write(conn, "reconstructed")
    cur = conn.cursor()
    cur.execute("SELECT id FROM universe_snapshot WHERE provenance = 'reconstructed'")
    uid = cur.fetchone()[0]
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute("INSERT INTO market_snapshot (session_date, provenance, feature_set_version, regime_model_version, rs_model_version, "
                    "regime_state, regime_present_weight, regime_components, coverage, source, universe_snapshot_id, content_hash, code_ref) "
                    "VALUES (%s,'observed','x','a','b','UNAVAILABLE',0,'{}','{}','s',%s,%s,'c')", (S.T, uid, "0" * 64))


def test_the_writer_refuses_a_sector_map_whose_provenance_differs_from_the_row(conn):
    rs = S.relative_strength("reconstructed")
    with pytest.raises(ValueError, match="does not match"):
        store.write_session(conn.cursor(), S.regime(), rs, "observed", "unit-test", "test@abc")
