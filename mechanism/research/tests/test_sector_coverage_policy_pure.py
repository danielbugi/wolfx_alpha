"""Slice 9: what the 90% sector-context floor and the informational sector-relative figure actually tell a consumer (no database).

These tests PIN the behaviour of the existing contract on a synthetic world (see `sector_coverage_sim.py`); they do not choose or tune a
threshold and they say nothing about vendor data. The point is to make the following facts reproducible and hard to change silently:
  * the floor is evaluated on the rows that really land in the dataset and passes at exactly 90% and fails just below;
  * "sector context" (candidate sector + sector aggregate snapshot) and "usable sector-relative RS" are different quantities that can diverge in
    either direction, so neither can stand in for the other;
  * a NULL sector-relative value is never 0 and never replaced by the market-relative value, whatever the coverage;
  * the same coverage figure can hide very different concentration (scattered cells vs whole symbols vs whole sessions);
  * a count-based floor cannot see CORRELATED missingness: with a planted structure the floor passes while the complete-case sample is biased.
"""
import pytest

import sector_coverage_sim as S
from research.lab import dataset_contract as C

FLOOR = "observed_coverage_sufficient"
PIT = "trusted_pit_date_determined"


@pytest.fixture(scope="module")
def sims():
    cache = {}

    def get(shape, f, kind=S.NO_SECTOR, **kw):
        key = (shape, f, kind, tuple(sorted(kw)))
        if key not in cache:
            cache[key] = S.run(shape, f, kind, **kw)
        return cache[key]
    return get


def test_the_simulation_covers_only_sessions_that_land_in_the_dataset_so_the_named_coverage_is_the_measured_coverage(sims):
    assert 0 < len(S.KEPT) < len(S.SESSION_IDX)
    for f in (1.0, 0.95, 0.90, 0.89, 0.75, 0.50):
        m = sims("random", f)
        assert m["sector_context_observed"] == f and m["sector_relative_usable"] == f


def test_the_baseline_has_no_gap_and_passes_every_data_check(sims):
    m = sims("random", 1.0)
    assert m["failed_checks"] == [] and m["rows_without_sector_relative"] == 0 and m["symbols_affected"] == 0
    assert m["market_relative_rs"] == 1.0


def test_the_floor_is_inclusive_at_exactly_ninety_percent_and_fails_just_below(sims):
    at90, at89 = sims("random", 0.90), sims("random", 0.89)
    assert at90["sector_context_observed"] == 0.9 and FLOOR not in at90["failed_checks"]
    assert at89["sector_context_observed"] == 0.89 and FLOOR in at89["failed_checks"]
    for f in (0.75, 0.50):
        assert FLOOR in sims("random", f)["failed_checks"]


def test_a_second_independent_gate_also_fails_when_the_gap_is_spread_over_the_whole_history(sims):
    m = sims("random", 0.89)
    assert PIT in m["failed_checks"] and m["earliest_trustworthy_pit_date"] is None


@pytest.mark.parametrize("shape", ["random", "symbol_cluster", "session_scatter", "outage_early", "outage_late"])
@pytest.mark.parametrize("f", [0.95, 0.75, 0.50])
def test_a_missing_sector_never_becomes_zero_and_never_falls_back_to_the_market_relative_value(sims, shape, f):
    m = sims(shape, f)
    assert m["market_relative_rs"] == 1.0                                 # the market-relative feature is complete in every scenario
    assert set(m["sector_relative_states"]) == {C.OK, C.NO_SECTOR}         # nothing else is invented
    assert m["rows_without_sector_relative"] == m["sector_relative_states"][C.NO_SECTOR]


def test_the_assembled_rows_carry_null_not_zero_where_the_sector_is_missing():
    prim = S.primary_rows(S.raw_world(S.mask("random", 0.75), S.NO_SECTOR))
    gaps = [r for r in prim if r["rs_vs_sector__state"] != C.OK]
    assert gaps and all(r["rs_vs_sector_pp"] is None and r["rs_sector"] is None and r["rs_vs_spx_pp"] is not None for r in gaps)
    assert all(r["rs_vs_sector_pp"] is not None for r in prim if r["rs_vs_sector__state"] == C.OK)


def test_sector_context_and_usable_sector_relative_coverage_can_diverge_in_either_direction(sims):
    unavailable = sims("random", 0.50, S.VALUE_UNAVAILABLE)             # the sector is known and fresh, but the RS model produced no value
    assert unavailable["sector_context_observed"] == 1.0 and unavailable["sector_relative_usable"] == 0.5
    assert unavailable["failed_checks"] == []                              # the floor is blind to a sparse sector-relative feature
    absent = sims("random", 0.75, S.SNAPSHOT_ABSENT)                      # the sector aggregate is missing, the stock's own sector-relative RS is fine
    assert absent["sector_relative_usable"] == 1.0 and absent["sector_context_observed"] < 0.9 and FLOOR in absent["failed_checks"]


def test_the_same_coverage_hides_very_different_concentration(sims):
    scattered, by_symbol, by_session = sims("random", 0.90), sims("symbol_cluster", 0.90), sims("session_scatter", 0.90)
    for m in (scattered, by_symbol, by_session):
        assert m["sector_relative_usable"] >= 0.9 and FLOOR not in m["failed_checks"]       # every shape passes the floor
    assert scattered["symbols_affected"] == 100 and scattered["symbols_fully_affected"] == 0
    assert by_symbol["symbols_affected"] == 10 and by_symbol["symbols_fully_affected"] == 10
    assert by_symbol["share_of_gaps_in_worst_10_symbols"] == 1.0 and scattered["share_of_gaps_in_worst_10_symbols"] < 0.25
    assert by_session["sessions_fully_affected"] == by_session["sessions_affected"] > 0 and scattered["sessions_fully_affected"] == 0
    assert by_session["worst_session_missing_fraction"] == 1.0 and scattered["worst_session_missing_fraction"] < 0.25


def test_an_early_outage_pushes_the_earliest_trustworthy_date_later_without_failing_the_floor(sims):
    early, late = sims("outage_early", 0.95), sims("outage_late", 0.95)
    assert FLOOR not in early["failed_checks"] and early["earliest_trustworthy_pit_date"] > "2023-01-02"
    assert late["earliest_trustworthy_pit_date"] == "2023-01-02"           # the same number of missing rows, but late, costs no trusted history


def test_a_count_based_floor_cannot_see_correlated_missingness():
    """PLANTED structure, synthetic: the ten highest-numbered ('smallest') symbols have a lower forward return AND are the ones without a sector.
    Every data check passes; the complete-case sample (rows with a usable sector-relative value) is nevertheless biased upward. With the same 10%
    missing at random the sample is essentially unbiased. This does not say production is biased; it says the gate could not tell."""
    order = list(S.SYMBOLS)
    clustered = S.measure(S.primary_rows(S.raw_world(S.mask("symbol_cluster", 0.90, order=order), S.NO_SECTOR, return_of=S.small_company_return)))
    scattered = S.measure(S.primary_rows(S.raw_world(S.mask("random", 0.90), S.NO_SECTOR, return_of=S.small_company_return)))
    assert clustered["failed_checks"] == [] and scattered["failed_checks"] == []
    full = clustered["mean_directional_return_all"]
    assert full == scattered["mean_directional_return_all"]
    shift_clustered = clustered["mean_directional_return_with_sector_relative"] - full
    shift_random = scattered["mean_directional_return_with_sector_relative"] - full
    assert shift_clustered > 0.0015 and abs(shift_random) < 0.0003
