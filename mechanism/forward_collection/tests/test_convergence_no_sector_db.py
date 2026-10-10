"""Option B (owner decision, Slice 8), PROVEN: the SAME full ~260-session simulation, but a few symbols have no sector at all.

A universe with legitimate no-sector symbols converges through the data-derived Slice 5 readiness checks WITHOUT excluding those symbols and
WITHOUT fabricating a sector-relative value for them:
  * the collector still observes every session completely and never omits a symbol from the relative-strength rows;
  * the no-sector symbols' sector-relative RS is NULL (never 0, never a market-relative substitute); their market-relative RS is their own;
  * the Slice 5 data checks (5-10) all pass, in particular `relative_strength_sector_pit_safe`;
  * the no-sector candidates stay in the provisional dataset, and the status reports how sparse the sector-relative feature is;
  * the activation preflight answers YES with no owner-decision blocker.
Before Slice 8 this scenario failed `relative_strength_sector_pit_safe` and the preflight answered NO (see docs/research/LAB_SLICE8_NO_SECTOR_SEMANTICS.md).
"""
import pytest

import forward_world as FW
from forward_collection import contract as C
from forward_collection import preflight as P
from test_convergence_db import K, MAT, N_SESSIONS, status_at

NO_SECTOR = ("S0003", "S0004", "S0021")


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("conv_ns")
    with pytest.MonkeyPatch.context() as mp:
        FW.scale_universe_minimums(mp)
        gen = FW.make_world(n_days=N_SESSIONS, n_stocks=FW.SMALL_UNIVERSE, no_sector=NO_SECTOR)
        w = next(gen)
        try:
            ev = {"verdicts": []}
            strat, steps = FW.start_world(w)
            FW.run_forward_sessions(w, steps, range(N_SESSIONS), n_candidates=K, strategy=strat,
                                    on_report=lambda k, rep: ev["verdicts"].append(rep.verdict))
            ev.update(world=w, tmp=tmp, final=status_at(w, MAT, tmp))
            yield ev
        finally:
            try:
                next(gen)
            except StopIteration:
                pass


def test_every_session_is_collected_complete_and_no_no_sector_symbol_is_omitted(sim):
    w = sim["world"]
    assert sim["verdicts"] == [C.COMPLETE] * N_SESSIONS
    rows = lambda sym: w.count("stock_relative_strength", "symbol = %s AND provenance = 'observed'", (sym,))
    assert rows(NO_SECTOR[0]) > 0 and {rows(s) for s in NO_SECTOR} == {rows("S0001")}          # as many rows as a symbol WITH a sector
    assert w.count("stock_relative_strength", "symbol = ANY(%s) AND state = 'ok' AND sector IS NULL AND NOT sector_pit_safe", (list(NO_SECTOR),)) > 0


def test_a_no_sector_symbol_never_has_a_sector_relative_value_and_keeps_its_own_market_relative_one(sim):
    w = sim["world"]
    params = (list(NO_SECTOR),)
    assert w.count("stock_relative_strength", "symbol = ANY(%s) AND vs_sector_pp IS NOT NULL", params) == 0              # never a fabricated value
    assert w.count("stock_relative_strength", "symbol = ANY(%s) AND vs_sector_pp = 0", params) == 0                        # and never a zero
    assert w.count("stock_relative_strength", "symbol = ANY(%s) AND state = 'ok' AND ret_pct IS NOT NULL AND vs_spx_pp IS NOT NULL", params) > 0
    # the sector-relative value of every symbol WITH a sector is untouched
    assert w.count("stock_relative_strength", "NOT (symbol = ANY(%s)) AND state = 'ok' AND sector IS NOT NULL AND vs_sector_pp IS NOT NULL", params) > 0


def test_no_sector_candidates_stay_in_the_research_universe(sim):
    w = sim["world"]
    n = w.count("candidate_observation", "snapshot_id IN (SELECT id FROM feature_snapshot WHERE sector IS NULL)")
    assert n > 0, "the scenario must really contain candidates without a sector"
    assert w.count("candidate_observation") > n


def test_the_slice5_data_checks_all_pass_with_no_sector_symbols_retained(sim):
    doc = sim["final"]
    failed = sorted(f["id"] for f in doc["readiness_failures"] if f["scope"] == "slice5_data_check")
    assert failed == [] and doc["contract"]["data_checks_all_pass"] is True, failed
    by_id = {c["id"]: c for c in doc["contract"]["data_checks"]}
    assert by_id["relative_strength_sector_pit_safe"]["passed"] is True
    assert doc["contract"]["sector_pit_unsafe_cells"] == 0


def test_the_status_reports_the_sparse_sector_relative_feature_without_a_threshold(sim):
    sr = sim["final"]["contract"]["sector_relative"]
    assert sr["threshold"] is None and sr["null_not_zero"] > 0
    assert 0 < sr["observed_sector_relative_rows"] < sr["rows"]
    assert sr["states"].get("no_sector", 0) == sr["null_not_zero"]


def test_the_preflight_answers_yes_without_an_owner_decision_blocker(sim):
    w = sim["world"]
    spec = FW.status_spec_doc(w, train=(0, 1), validation=(2, 3), test=(4, 5), maturity=5, cutoff=w.at(w.sessions[5], 6, days_after=2))
    rep = P.run_preflight(w.connect, spec, env={})
    assert rep["collector_stack_ready_for_activation"] == "YES" and rep["blockers"] == [], rep["blockers"]
    assert "owner_decision" not in {k for c in rep["checks"] for k in c}
    n = next(c for c in rep["checks"] if c["id"] == "no_sector_policy_resolved")
    assert n["ok"] is True and n["detail"]["policy"] == "B_null_sector_relative"
    assert n["detail"]["symbols_without_pit_safe_sector_now"] == len(NO_SECTOR)
