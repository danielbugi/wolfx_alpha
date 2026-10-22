"""Data-driven research capabilities (strategy_analytics/availability.py) against a real throwaway schema.

Regression for 2026-10-22: the static CAPABILITIES table declared forward labels, candidate observations and feature
snapshots "not collected" while production held 29k / 15k / 15k rows. Availability is now measured: state
(available / partial / unavailable), provenance, rows, period, ledger coverage, source and point-in-time limits.
Skips only when Postgres is unreachable; the CI real-Postgres step fails on any skip."""
import os
import re
import sys
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from strategy_analytics import analytics, availability as AV, research  # noqa: E402
from test_research_analytics import capture, fetcher  # noqa: E402

S0, S1, S2 = date(2099, 1, 12), date(2099, 1, 13), date(2099, 1, 14)


def _ledger(seed, symbol, session, observation_id=None, snapshot_id=None, status="open", outcome_r=None, mae_r=None):
    """Lineage is all-or-none (signal_ledger_lineage_all_or_none): a linked row carries feature set, observation and snapshot."""
    if observation_id and not snapshot_id:
        snapshot_id = seed.execute("SELECT snapshot_id FROM candidate_observation WHERE id = %s", (observation_id,)).fetchone()[0]
    fsv = "t0_v1" if observation_id else None
    seed.execute("INSERT INTO signal_ledger (symbol, signal_date, direction, entry_price, atr, stop_price, target1_price, target2_price, "
                 "target3_price, strategy_id, strategy_version, feature_set_version, observation_id, feature_snapshot_id, status, outcome_r, mae_r, "
                 "last_evaluated_date, resolved_date, bars_held) VALUES (%s, %s, 1, 10, 1, 8, 12, 14, 16, %s, 'v1', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                 (symbol, session, seed.strategy_id(), fsv, observation_id, snapshot_id, status, outcome_r, mae_r,
                  S2 if status != "open" else session, S2 if status != "open" else None, 2 if status != "open" else None))


def _label(seed, obs_id, symbol, t0, horizon=1, final=False):
    common = dict(observation_id=obs_id, horizon_sessions=horizon, label_version="fwd_v1", methodology_version="fwd_v1",
                  symbol=symbol, direction=1, t0_session=t0, horizon_session=S2, computed_as_of_session=S2, benchmark_symbol="^GSPC",
                  benchmark_state="not_evaluated", bars_expected=horizon, price_source="stock_prices", price_basis="adjusted",
                  calendar_source="stock_prices", input_hash="d" * 64, code_ref="test")
    if final:
        common.update(label_status="final", path_state="complete", data_quality="ok", benchmark_state="unavailable",
                      reference_close=10.5, horizon_close=11,
                      entry_close_captured=10.5, basis_ratio=1.0, raw_return=0.0476, directional_return=0.0476, bars_observed=horizon,
                      mfe=0.05, mae=-0.01)
    else:
        common.update(label_status="void", void_reason="test", path_state="not_evaluated", data_quality="not_evaluated")
    cols = ", ".join(common)
    seed.execute(f"INSERT INTO forward_return_label ({cols}) VALUES ({', '.join('%(' + k + ')s' for k in common)})", common)


# ----------------------------------------------------------------------------------------------------------- guards
def test_no_capability_is_declared_by_a_constant():
    src = open(os.path.join(ROOT, "mechanism", "strategy_analytics", "definitions.py"), encoding="utf-8").read()
    assert not re.search(r"^CAPABILITIES\s*[:=]", src, re.M)
    av = open(os.path.join(ROOT, "mechanism", "strategy_analytics", "availability.py"), encoding="utf-8").read()
    assert '"available": True' not in av and '"available": False' not in av


def test_catalog_describes_but_never_claims_availability():
    for v in AV.catalog().values():
        assert "available" not in v and v["source"] and v["pit_limitations"]


# -------------------------------------------------------------------------------------------- measured, real Postgres
def test_empty_strategy_reports_unavailable_with_reasons(lconn):
    f = fetcher(lconn)
    s = analytics.get_strategy(f, "donchian_breakout", "v1")
    caps = AV.capabilities(f, s)
    for k in ("candidate_observations", "feature_snapshots", "forward_return_labels"):
        assert caps[k]["state"] == "unavailable" and caps[k]["available"] is False and caps[k]["rows"] == 0 and caps[k]["reason"]
    assert caps["signal_ledger"]["state"] == "unavailable"
    assert caps["training_ready_observations"]["state"] == "unavailable" and caps["training_ready_observations"]["computed"] is False


def test_capture_that_started_after_the_ledger_is_partial_with_exact_coverage_and_period(lconn):
    from conftest import Seed
    seed = Seed(lconn)
    _ledger(seed, "OLD", S0, status="stopped", outcome_r=-1, mae_r=1.1)               # before capture: no lineage
    _, ids = capture(seed, S1)
    snap = seed.execute("SELECT snapshot_id FROM candidate_observation WHERE id = %s", (ids["AAA"],)).fetchone()[0]
    _ledger(seed, "AAA", S1, observation_id=ids["AAA"], snapshot_id=snap, mae_r=0.2)
    _label(seed, ids["AAA"], "AAA", S1, horizon=1, final=True)
    _label(seed, ids["BBB"], "BBB", S1, horizon=1, final=False)
    f = fetcher(lconn)
    s = analytics.get_strategy(f, "donchian_breakout", "v1")
    caps = AV.capabilities(f, s)
    co, fs, fl = caps["candidate_observations"], caps["feature_snapshots"], caps["forward_return_labels"]
    assert (co["state"], co["provenance"], co["rows"], co["period"]) == ("partial", "observed", 3, {"first": S1, "last": S1})
    assert co["coverage"] == {"ledger_rows_covered": 1, "ledger_rows_total": 2} and co["sessions"] == 1
    assert (fs["state"], fs["rows"], fs["coverage"]["ledger_rows_covered"]) == ("partial", 3, 1)
    assert (fl["state"], fl["provenance"], fl["rows"], fl["final_labels"], fl["horizons_sessions"]) == ("partial", "derived", 2, 1, [1])
    assert fl["coverage"] == {"ledger_rows_covered": 1, "ledger_rows_total": 2} and fl["computed"] is False
    assert caps["signal_ledger"]["period"] == {"first": S0, "last": S1}
    mfe, post = caps["max_favourable_excursion"], caps["post_exit_trajectory"]
    assert mfe["state"] == "unavailable" and mfe["computed"] is False and mfe["derivable_ledger_rows"] == 1
    assert post["state"] == "unavailable" and post["derivable_ledger_rows"] == 1
    assert caps["max_adverse_excursion"]["state"] == "available" and caps["max_adverse_excursion"]["rows"] == 2
    for v in caps.values():
        assert v["source"] and v["pit_limitations"] and v["provenance"] in ("observed", "derived", "none")
        assert v["available"] == (v["state"] != "unavailable")


def test_summary_and_data_health_serve_measured_values(lconn):
    from conftest import Seed
    seed = Seed(lconn)
    _, ids = capture(seed, S1)
    _ledger(seed, "AAA", S1, observation_id=ids["AAA"])
    f = fetcher(lconn)
    s = analytics.get_strategy(f, "donchian_breakout", "v1")
    assert analytics.summary(f, s)["capabilities"]["candidate_observations"]["state"] == "available"   # 1 of 1 ledger rows linked
    cov = analytics.data_health(f, s)["coverage"]
    assert cov["observation_id"]["collected"] is True and cov["feature_snapshot_id"]["collected"] is True
    assert cov["feature_set_version"]["collected"] is True


def test_forward_outcomes_report_the_labels_that_exist(lconn):
    from conftest import Seed
    seed = Seed(lconn)
    _, ids = capture(seed, S1)
    f = fetcher(lconn)
    s = analytics.get_strategy(f, "donchian_breakout", "v1")
    assert research.forward_outcomes(f, s)["state"] == "no_data"
    _label(seed, ids["AAA"], "AAA", S1, final=True)
    fo = research.forward_outcomes(f, s)
    assert fo["state"] == "not_available" and fo["value"] is None and fo["labels"] == 1 and fo["final_labels"] == 1
    assert "migration 23" not in fo["reason"] and "not computed" in fo["reason"]
