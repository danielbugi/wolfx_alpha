"""sector_v1: sector measurements are the stored rs_v1 / breadth_v1 numbers, joined; unavailable stays None; strength label is a declared tercile."""
import pytest

import mi_samples as S
from market_intelligence import breadth as bd
from market_intelligence import relative_strength as RS
from market_intelligence import sector_intel as si


@pytest.mark.parametrize("rank,n,expected", [
    (1, 9, "leading"), (3, 9, "leading"), (4, 9, "in_line"), (6, 9, "in_line"), (7, 9, "lagging"), (9, 9, "lagging"),
    (1, 11, "leading"), (4, 11, "leading"), (5, 11, "in_line"), (8, 11, "lagging"),
    (None, 9, "not_available"), (1, 2, "not_available"), (2, 2, "not_available")])
def test_strength_state_terciles(rank, n, expected):
    assert si.strength_state(rank, n) == expected


def _inputs():
    close, sector_of, idx = S.panel()
    rel = RS.compute(S.T, close, sector_of, idx, {}, sector_provenance="observed")
    import pandas as pd
    high, low = close * 1.01, close * 0.99
    return rel, bd.compute(S.T, close, high, low, sector_of)


def test_each_record_carries_exactly_the_measurements_of_its_inputs():
    rel, b = _inputs()
    recs = {r["sector"]: r for r in si.build(rel, b)}
    assert set(recs) == {s.sector for s in rel.sectors}
    for s in rel.sectors:
        r = recs[s.sector]
        assert r["n_members"] == s.n_members and r["version"] == si.SECTOR_VERSION
        for h in (5, 20, 60):
            assert r["trend"][f"ret_{h}"] == s.per_horizon[h]["ret"]
            assert r["strength"][f"vs_spx_{h}"] == s.per_horizon[h]["vs_spx"]
            assert r["strength"][f"vs_univ_{h}"] == s.per_horizon[h]["vs_univ"]
            assert r["coverage"][str(h)] == {"n_valid": s.per_horizon[h]["n_valid"], "n_excluded": s.per_horizon[h]["n_excluded"]}
        assert r["strength"]["rank_20"] == s.rank_20
        assert r["breadth"]["above_sma50"] == b["sectors"][s.sector]["metrics"]["above_sma50"]
        assert r["trend"]["pct_above_sma50"] == b["sectors"][s.sector]["metrics"]["above_sma50"]["pct"]
        assert set(r["participation"]) == {"positive_ret_20", "new_high_52w", "new_low_52w", "advancing_1d", "declining_1d"}


def test_unavailable_measurements_stay_none_not_zero():
    rel, b = _inputs()
    r = si.build(rel, b)[0]
    assert r["trend"]["pct_above_sma200"] is None                       # the 80-session panel has no 200-day history
    assert r["breadth"]["above_sma200"]["denominator"] == 0 and r["breadth"]["above_sma200"]["state"] == bd.INSUFFICIENT
    assert r["participation"]["new_high_52w"]["pct"] is None


def test_strength_state_is_derived_from_the_ranks_of_the_ranked_sectors():
    rel, b = _inputs()
    recs = si.build(rel, b)
    n = sum(1 for s in rel.sectors if s.rank_20 is not None)
    assert n == 3 and {r["strength"]["n_ranked"] for r in recs} == {3}
    by_rank = {r["strength"]["rank_20"]: r["strength"]["strength_state"] for r in recs}
    assert by_rank == {1: "leading", 2: "in_line", 3: "lagging"}


def test_a_missing_breadth_entry_yields_none_participation_not_a_crash():
    rel, _ = _inputs()
    recs = si.build(rel, {"sectors": {}})
    assert all(r["trend"]["pct_above_sma50"] is None and r["participation"]["positive_ret_20"] is None for r in recs)
