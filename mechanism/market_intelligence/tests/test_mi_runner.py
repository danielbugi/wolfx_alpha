"""Market Intelligence runner (`mi_v2`) against real Postgres in a throwaway schema: session-explicit, fail-closed, deterministic, idempotent,
concurrency-safe, honest about provenance and sector point-in-time evidence, and never inventing a value for something it could not measure."""
import threading
from datetime import date

import numpy as np
import pandas as pd
import pytest

import mi_fixtures  # noqa: F401  (path setup)
from mi_runner_fixtures import EARLY, T, runner_env  # noqa: F401
from market_intelligence import inputs, runner, store


def _counts(env, table):
    with env.connect() as c:
        cur = c.cursor()
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def _run(env, session=T, provenance="reconstructed", apply=True, fsv=None, **kw):
    kw.setdefault("code_ref", "test-ref")
    return runner.run(env.connect, session, provenance=provenance, apply=apply,
                      feature_set_version=fsv or env.fsv(), **kw)


def _row(env, fsv, provenance, session=T):
    with env.connect() as c:
        return store.get_market_snapshot(c.cursor(), session, provenance, include_reconstructed=True, feature_set_version=fsv)


# ---------------------------------------------------------------- dry run, fail closed, explicit arguments
def test_a_dry_run_reads_everything_and_writes_nothing(runner_env):
    before = [_counts(runner_env, t) for t in ("universe_snapshot", "market_snapshot", "sector_snapshot")]
    rep = _run(runner_env, apply=False)
    assert rep.applied is False and rep.created is None and rep.content_hash is None
    assert rep.regime_state in ("RISK_ON", "RISK_OFF", "NEUTRAL") and rep.n_universe == 1100
    assert [_counts(runner_env, t) for t in ("universe_snapshot", "market_snapshot", "sector_snapshot")] == before


def test_the_session_and_provenance_are_explicit_arguments(runner_env):
    with pytest.raises(TypeError):
        runner.run(runner_env.connect, apply=False)                          # no session
    with pytest.raises(TypeError):
        runner.run(runner_env.connect, T, apply=False)                       # no provenance: there is no default
    with pytest.raises(TypeError):
        runner.run(runner_env.connect, "2026-09-30", provenance="reconstructed")
    with pytest.raises(ValueError):
        runner.run(runner_env.connect, T, provenance="live")
    with pytest.raises(ValueError):
        runner.run(runner_env.connect, T, provenance="reconstructed", sector_rule="anything")
    with pytest.raises(ValueError):
        runner.run(runner_env.connect, T, provenance="reconstructed", apply=True)    # writing needs a code_ref


@pytest.mark.parametrize("bad", [date(2026, 9, 26), date(2026, 10, 3), date(2015, 1, 5)])
def test_a_date_that_is_not_a_real_session_is_refused_and_writes_nothing(runner_env, bad):
    before = _counts(runner_env, "market_snapshot")
    with pytest.raises(runner.SessionNotAvailable):
        _run(runner_env, session=bad)
    assert _counts(runner_env, "market_snapshot") == before


def test_a_partially_loaded_session_is_refused(runner_env):
    late = date(2026, 10, 1)
    with runner_env.connect() as c:
        cur = c.cursor()
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close) SELECT symbol, %s, close, close, close, close FROM stock_prices "
                    "WHERE date = %s AND symbol < 'S0050'", (late, T))
        c.commit()
    try:
        with pytest.raises(runner.SessionNotAvailable):
            _run(runner_env, session=late)
    finally:
        with runner_env.connect() as c:
            c.cursor().execute("DELETE FROM stock_prices WHERE date = %s", (late,))
            c.commit()


# ---------------------------------------------------------------- what is written
def test_apply_writes_universe_market_and_sector_rows_with_the_measurements(runner_env):
    fsv = runner_env.fsv()
    rep = _run(runner_env, fsv=fsv)
    assert rep.created is True and rep.sectors_written == 4 and rep.provenance == "reconstructed"
    m = _row(runner_env, fsv, "reconstructed")
    assert m["regime_state"] == rep.regime_state and m["content_hash"] == rep.content_hash
    assert m["sector_pit_safe"] is False and m["reconstruction_basis"].startswith("mi_runner_v2")
    meas = m["coverage"]["measurements"]
    assert meas["versions"] == {"regime": "risk_regime_v1", "rs": "rs_v1", "breadth": "breadth_v1", "sector": "sector_v1"}
    assert set(meas["breadth"]["market"]) == {"above_sma50", "above_sma200", "new_high_52w", "new_low_52w", "positive_ret_20",
                                              "advancing_1d", "declining_1d"}
    assert {s["sector"] for s in meas["sectors"]} == {"Technology", "Energy", "Healthcare", "Tiny"}
    with runner_env.connect() as c:
        rows = store.get_sector_snapshots(c.cursor(), T, "reconstructed", True, fsv)
    assert {r["sector"] for r in rows} == {"Technology", "Energy", "Healthcare", "Tiny"}


def test_regime_components_and_breadth_counts_are_the_same_numbers(runner_env):
    fsv = runner_env.fsv()
    _run(runner_env, fsv=fsv)
    m = _row(runner_env, fsv, "reconstructed")
    comps = m["regime_components"]
    mk = m["coverage"]["measurements"]["breadth"]["market"]
    assert comps["c3_breadth_sma50"]["raw"]["n_above"] == mk["above_sma50"]["numerator"]
    assert comps["c3_breadth_sma50"]["raw"]["n_valid"] == mk["above_sma50"]["denominator"]
    assert comps["c4_breadth_sma200"]["raw"]["n_above"] == mk["above_sma200"]["numerator"]
    assert comps["c5_net_highs_lows"]["raw"]["n_new_highs"] == mk["new_high_52w"]["numerator"]
    assert set(comps) == {"c1_index_trend", "c2_index_momentum", "c3_breadth_sma50", "c4_breadth_sma200", "c5_net_highs_lows",
                          "c6_volatility", "c7_risk_appetite"}
    assert all("raw" in c and "score" in c and "present" in c for c in comps.values())


def test_a_thin_sector_is_stored_with_null_measurements_not_zeros(runner_env):
    fsv = runner_env.fsv()
    _run(runner_env, fsv=fsv)
    with runner_env.connect() as c:
        tiny = [r for r in store.get_sector_snapshots(c.cursor(), T, "reconstructed", True, fsv) if r["sector"] == "Tiny"][0]
    assert tiny["n_members"] == 3 and tiny["sec_ret_20"] is None and tiny["rank_20"] is None and tiny["sec_vs_spx_20"] is None
    meas = _row(runner_env, fsv, "reconstructed")["coverage"]["measurements"]["sectors"]
    t = [s for s in meas if s["sector"] == "Tiny"][0]
    assert t["strength"]["strength_state"] == "not_available" and t["trend"]["ret_20"] is None
    assert t["breadth"]["above_sma50"]["state"] == "insufficient_eligible" and t["breadth"]["above_sma50"]["pct"] is None


# ---------------------------------------------------------------- determinism / idempotency / concurrency / restatement
def test_the_same_inputs_give_the_same_hash_independent_of_version_label_and_run_time(runner_env):
    a, b = _run(runner_env), _run(runner_env)
    assert a.created and b.created and a.content_hash == b.content_hash


def test_a_rerun_writes_nothing_and_reports_no_difference(runner_env):
    fsv = runner_env.fsv()
    first = _run(runner_env, fsv=fsv)
    n = [_counts(runner_env, t) for t in ("universe_snapshot", "market_snapshot", "sector_snapshot")]
    second = _run(runner_env, fsv=fsv)
    assert first.created is True and second.created is False and second.sectors_written == 0
    assert second.content_hash == first.content_hash and second.differs_from_stored is False
    assert [_counts(runner_env, t) for t in ("universe_snapshot", "market_snapshot", "sector_snapshot")] == n


def test_concurrent_runs_write_each_row_exactly_once(runner_env):
    fsv = runner_env.fsv()
    out, errs = [], []

    def go():
        try:
            out.append(_run(runner_env, fsv=fsv))
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errs, errs
    assert sorted(r.created for r in out) == [False, False, False, True]
    assert {r.content_hash for r in out} == {out[0].content_hash}
    with runner_env.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM market_snapshot WHERE feature_set_version = %s", (fsv,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM sector_snapshot WHERE feature_set_version = %s", (fsv,))
        assert cur.fetchone()[0] == 4


def test_restated_prices_never_rewrite_a_stored_row_and_are_reported(runner_env):
    fsv = runner_env.fsv()
    first = _run(runner_env, fsv=fsv)
    stored = _row(runner_env, fsv, "reconstructed")
    with runner_env.connect() as c:
        cur = c.cursor()
        cur.execute("UPDATE stock_prices SET close = close * 1.5, high = high * 1.5, low = low * 1.5 WHERE date = %s AND symbol < 'S0300'", (T,))
        c.commit()
    try:
        again = _run(runner_env, fsv=fsv)
        assert again.created is False and again.differs_from_stored is True and again.warnings
        assert _row(runner_env, fsv, "reconstructed")["content_hash"] == first.content_hash == stored["content_hash"]
    finally:
        with runner_env.connect() as c:
            c.cursor().execute("UPDATE stock_prices SET close = round(close / 1.5, 4), high = round(high / 1.5, 4), low = round(low / 1.5, 4) "
                               "WHERE date = %s AND symbol < 'S0300'", (T,))
            c.commit()


def test_a_new_feature_set_version_is_the_only_way_to_correct(runner_env):
    a, b = _run(runner_env), _run(runner_env)
    assert a.created and b.created                       # two versions of the same session coexist; neither edits the other


# ---------------------------------------------------------------- provenance and sector point-in-time evidence
def test_observed_is_allowed_only_for_the_latest_session_with_a_pit_evidenced_sector_map(runner_env):
    fsv = runner_env.fsv()
    rep = _run(runner_env, provenance="observed", fsv=fsv)
    assert rep.created and rep.sector_audit["pit_evidenced"] is True and rep.sector_audit["n_projected_backwards"] == 0
    m = _row(runner_env, fsv, "observed")
    assert m["provenance"] == "observed" and m["sector_pit_safe"] is True and m["reconstruction_basis"] is None
    assert "write time" in m["sector_asof_rule"]


def test_observed_is_refused_for_an_older_session(runner_env):
    with pytest.raises(runner.ProvenanceRefused, match="not a live capture"):
        _run(runner_env, session=EARLY, provenance="observed")


def test_observed_is_refused_with_a_projected_sector_map(runner_env):
    with pytest.raises(runner.ProvenanceRefused, match="point-in-time"):
        _run(runner_env, provenance="observed", sector_rule=inputs.RULE_PROJECTED)


def test_observed_is_refused_when_a_discontinuity_was_detected_after_the_session(runner_env):
    with runner_env.connect() as c:
        c.cursor().execute("INSERT INTO price_discontinuities (symbol, date, kind, detected_at) VALUES ('S0001', %s, 'jump_up', %s)",
                           (date(2026, 9, 20), "2026-10-02 12:00:00"))
        c.commit()
    try:
        with pytest.raises(runner.ProvenanceRefused, match="discontinuities"):
            _run(runner_env, provenance="observed")
        rep = _run(runner_env, provenance="reconstructed")           # allowed, but flagged
        assert any("detected after the session" in w for w in rep.warnings)
    finally:
        with runner_env.connect() as c:
            c.cursor().execute("DELETE FROM price_discontinuities WHERE symbol = 'S0001'")
            c.commit()


def test_a_reconstructed_row_is_never_pit_safe_and_says_how_it_was_made(runner_env):
    fsv = runner_env.fsv()
    _run(runner_env, session=EARLY, fsv=fsv)
    m = _row(runner_env, fsv, "reconstructed", EARLY)
    assert m["sector_pit_safe"] is False and "pit_evidenced" in m["reconstruction_basis"] and "restates history in place" in m["reconstruction_basis"]


def test_the_pit_rule_classifies_only_symbols_with_evidence_of_what_was_known(runner_env):
    with runner_env.connect() as c:
        smap, audit = inputs.load_sector_map(c, EARLY, inputs.RULE_PIT_EVIDENCED)
    assert len(smap) == 50 and audit["n_symbols_with_rows"] == 150           # only 150 symbols have any row dated <= EARLY
    assert audit["n_without_evidence"] == 100 and audit["n_projected_backwards"] == 0       # 100 of them were written long after their date
    assert audit["pit_evidenced"] is True and audit["latest_row_date"] == "2026-09-01"
    assert set(smap) == {f"S{i:04d}" for i in range(100, 150)}


def test_the_projected_rule_covers_more_symbols_and_admits_to_projecting_backwards(runner_env):
    with runner_env.connect() as c:
        smap, audit = inputs.load_sector_map(c, EARLY, inputs.RULE_PROJECTED)
    assert len(smap) == 150 and audit["n_projected_backwards"] == 100 and audit["pit_evidenced"] is False


def test_rows_dated_after_the_session_are_never_read(runner_env):
    with runner_env.connect() as c:
        smap, _ = inputs.load_sector_map(c, EARLY, inputs.RULE_PROJECTED)
        assert all(f"S{i:04d}" in smap for i in range(100, 150))
        close, high, low = inputs.load_price_panel(c, EARLY)
        assert close.index.max() == pd.Timestamp(EARLY) and high.index.max() == low.index.max() == pd.Timestamp(EARLY)
        idx = inputs.load_index_closes(c, EARLY)
        assert all(s.index.max() <= pd.Timestamp(EARLY) for s in idx.values()) and set(idx) == {"^GSPC", "^VIX", "^RUT"}
        smap_t, _ = inputs.load_sector_map(c, T, inputs.RULE_PIT_EVIDENCED)
    assert len(smap_t) == 1100                                    # the 2026-09-28 rows only become visible from that date on


def test_an_empty_pit_map_is_reported_as_empty_not_neutral(runner_env):
    rep = _run(runner_env, session=date(2026, 6, 1), apply=False)
    assert rep.sector_audit["n_classified"] == 0 and rep.n_sectors == 0
    assert any("no symbol has a point-in-time-evidenced sector" in w for w in rep.warnings)


def test_reading_defaults_to_observed_only(runner_env):
    fsv = runner_env.fsv()
    _run(runner_env, session=EARLY, fsv=fsv)                     # reconstructed only
    with runner_env.connect() as c:
        assert store.get_market_snapshot(c.cursor(), EARLY, feature_set_version=fsv) is None
        assert store.get_market_snapshot(c.cursor(), EARLY, include_reconstructed=True, feature_set_version=fsv) is not None


# ---------------------------------------------------------------- per-stock relative strength
def test_stock_relative_strength_measures_stock_vs_market_and_vs_sector(runner_env):
    df = runner.stock_relative_strength(runner_env.connect, T, ["S0001", "S0002", "NOPE"])
    assert list(df.index) == ["S0001", "S0002", "NOPE"]
    assert df.loc["NOPE"].isna().all() or df.loc["NOPE"].drop("sector").isna().all()      # an unknown symbol is NaN, never 0
    full = runner.stock_relative_strength(runner_env.connect, T)
    assert len(full) == 1100
    with runner_env.connect() as c:
        idx = inputs.load_index_closes(c, T)
    spx20 = (idx["^GSPC"].iloc[-1] / idx["^GSPC"].iloc[-21] - 1) * 100
    r = full.loc["S0001"]
    assert abs(r["vs_spx_20"] - (r["ret_20"] - spx20)) < 1e-9
    members = full[full["sector"] == r["sector"]]
    assert abs(r["vs_sector_20"] - (r["ret_20"] - np.median(members["ret_20"].dropna()))) < 1e-9
    assert 0 <= r["rs_pctile_20"] <= 100


def test_stock_relative_strength_fails_closed_on_a_non_session(runner_env):
    with pytest.raises(runner.SessionNotAvailable):
        runner.stock_relative_strength(runner_env.connect, date(2026, 10, 3))


def test_a_stock_without_a_pit_sector_has_no_vs_sector_measurement(runner_env):
    df = runner.stock_relative_strength(runner_env.connect, EARLY, ["S0001", "S0120"])      # S0001: no evidence at EARLY; S0120: clean row
    assert pd.isna(df.loc["S0001", "vs_sector_20"]) and df.loc["S0001", "sector"] is None


# ---------------------------------------------------------------- opt-in per-stock persistence (migration 29)
def test_per_stock_rows_are_written_only_when_asked_in_the_same_transaction_and_idempotently(runner_env):
    fsv = runner_env.fsv()
    plain = _run(runner_env, session=EARLY, fsv=fsv)
    assert plain.stock_rs_written is None and _counts(runner_env, "stock_relative_strength") == 0
    assert _run(runner_env, session=EARLY, fsv=fsv, apply=False, with_stock_rs=True).stock_rs_written is None   # a dry run writes nothing
    assert _counts(runner_env, "stock_relative_strength") == 0
    first = _run(runner_env, session=EARLY, fsv=runner_env.fsv(), with_stock_rs=True)
    assert first.stock_rs_written == 1100 * 3 and _counts(runner_env, "stock_relative_strength") == 1100 * 3
    again = _run(runner_env, session=EARLY, fsv=first.feature_set_version, with_stock_rs=True)
    assert again.stock_rs_written == 0 and _counts(runner_env, "stock_relative_strength") == 1100 * 3
    with runner_env.connect() as c:
        rows = store.get_stock_rs(c.cursor(), EARLY, feature_set_version=first.feature_set_version, include_reconstructed=True)
    assert len(rows) == 1100 * 3 and {r["provenance"] for r in rows} == {"reconstructed"}
    assert all(r["sector_pit_safe"] is False for r in rows)
