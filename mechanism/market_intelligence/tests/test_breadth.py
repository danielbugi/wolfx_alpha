"""breadth_v1: explicit numerator / denominator / eligible / missing counts; a missing stock is never bearish; no leakage; reconciles
with the counts risk_regime_v1 stores. Pure: no database."""
import copy
from datetime import date

import numpy as np
import pandas as pd

import mi_samples  # noqa: F401  (puts mechanism/ on sys.path)
from market_intelligence import breadth as bd
from market_intelligence import risk_regime as rr

T = date(2026, 9, 30)
N_DAYS = 260


def _wide(n=1100, seed=3, days=N_DAYS):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(T), periods=days)
    syms = [f"S{i:04d}" for i in range(n)]
    c = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, (days, n)), axis=0))
    close = pd.DataFrame(c, index=idx, columns=syms)
    return close, close * 1.01, close * 0.99


def _sectors(close):
    return {s: ("Technology", "Energy", "Healthcare")[i % 3] for i, s in enumerate(close.columns)}


def test_every_count_record_is_internally_consistent():
    close, high, low = _wide()
    b = bd.compute(T, close, high, low, _sectors(close))
    assert b["session_is_real"] and b["n_universe"] == 1100
    for group in [b["market"]] + [s["metrics"] for s in b["sectors"].values()]:
        assert set(group) == set(bd.METRICS)
        for m, r in group.items():
            assert 0 <= r["numerator"] <= r["denominator"] <= r["universe"], (m, r)
            assert r["missing"] == r["universe"] - r["denominator"], (m, r)
            if r["pct"] is not None:
                assert abs(r["pct"] - 100 * r["numerator"] / r["denominator"]) < 1e-3


def test_pct_above_sma_matches_a_manual_calculation():
    close, high, low = _wide()
    b = bd.compute(T, close, high, low)["market"]["above_sma50"]
    sma = close.iloc[-50:].mean()
    manual = int((close.iloc[-1] > sma).sum())
    assert (b["numerator"], b["denominator"], b["missing"]) == (manual, 1100, 0)


def test_missing_stocks_leave_the_denominator_and_are_never_counted_bearish():
    close, high, low = _wide(n=1300)
    full = bd.compute(T, close, high, low)["market"]
    holes = list(close.columns[:150])
    c2, h2, l2 = close.copy(), high.copy(), low.copy()
    c2.loc[c2.index[:120], holes] = np.nan            # 150 stocks lack 120 sessions: no 200-bar history, 50-bar history intact
    h2.loc[h2.index[:120], holes] = np.nan
    l2.loc[l2.index[:120], holes] = np.nan
    part = bd.compute(T, c2, h2, l2)["market"]
    s200_full, s200 = full["above_sma200"], part["above_sma200"]
    assert s200["denominator"] == s200_full["denominator"] - 150
    assert s200["missing"] == 150 and s200["universe"] == 1300
    kept = close.columns[150:]
    manual = int((close.iloc[-1][kept] > close.iloc[-200:].mean()[kept]).sum())
    assert s200["numerator"] == manual                # the remaining stocks are counted exactly as before
    assert abs(s200["pct"] - 100 * manual / 1150) < 1e-3
    assert part["above_sma50"]["missing"] == 0        # their 50-bar history is intact, so SMA50 still counts them


def test_a_group_below_the_minimum_keeps_its_counts_but_withholds_the_percentage():
    close, high, low = _wide(n=900)
    m = bd.compute(T, close, high, low)["market"]["above_sma50"]
    assert m["state"] == bd.INSUFFICIENT and m["pct"] is None
    assert m["denominator"] == 900 and m["universe"] == 900 and m["numerator"] > 0


def test_short_history_makes_the_long_metrics_missing_not_zero():
    close, high, low = _wide(days=80)
    m = bd.compute(T, close, high, low)["market"]
    assert m["above_sma200"]["denominator"] == 0 and m["above_sma200"]["missing"] == 1100 and m["above_sma200"]["pct"] is None
    assert m["new_high_52w"]["denominator"] == 0 and m["new_high_52w"]["numerator"] == 0
    assert m["above_sma50"]["pct"] is not None


def test_market_counts_reconcile_with_risk_regime_components():
    close, high, low = _wide()
    mk = bd.compute(T, close, high, low)["market"]
    comps = {c.key: c for c in rr.breadth_components(close, high, low, pd.Timestamp(T))}
    c3, c4, c5 = comps["c3_breadth_sma50"].raw, comps["c4_breadth_sma200"].raw, comps["c5_net_highs_lows"].raw
    assert (mk["above_sma50"]["numerator"], mk["above_sma50"]["denominator"]) == (c3["n_above"], c3["n_valid"])
    assert (mk["above_sma200"]["numerator"], mk["above_sma200"]["denominator"]) == (c4["n_above"], c4["n_valid"])
    assert (mk["new_high_52w"]["numerator"], mk["new_low_52w"]["numerator"], mk["new_high_52w"]["denominator"]) == \
        (c5["n_new_highs"], c5["n_new_lows"], c5["n_range"])


def test_bars_after_the_session_are_ignored():
    close, high, low = _wide()
    cut = close.index[-6]
    a = bd.compute(cut.date(), close, high, low, _sectors(close))
    b = bd.compute(cut.date(), close.loc[:cut], high.loc[:cut], low.loc[:cut], _sectors(close))
    assert a == b


def test_a_date_that_is_not_a_real_session_yields_no_session_never_zeros():
    close, high, low = _wide()
    weekend = date(2026, 10, 3)
    b = bd.compute(weekend, close, high, low)
    assert b["session_is_real"] is False
    assert all(r["state"] == bd.NO_SESSION and r["pct"] is None and r["numerator"] is None for r in b["market"].values())
    thin = close.copy()
    thin.iloc[-1, 100:] = np.nan                     # a partial load: fewer than half the symbols have today's bar
    assert bd.compute(T, thin, high, low)["session_is_real"] is False
    assert bd.compute(T, None, None, None)["session_is_real"] is False


def test_sector_groups_follow_the_sector_map_and_unclassified_stay_market_only():
    close, high, low = _wide()
    smap = _sectors(close)
    for s in list(close.columns[:40]):
        smap[s] = None
    for s in list(close.columns[40:60]):
        smap[s] = "Unknown"
    b = bd.compute(T, close, high, low, smap)
    assert b["n_unclassified"] == 60 and b["n_universe"] == 1100
    assert sum(s["universe"] for s in b["sectors"].values()) == 1040
    assert set(b["sectors"]) == {"Technology", "Energy", "Healthcare"}        # 'Unknown' is never a sector of its own
    assert b["market"]["above_sma50"]["universe"] == 1100


def test_a_thin_sector_withholds_pct_below_five_eligible_members():
    close, high, low = _wide()
    smap = _sectors(close)
    for s in list(close.columns[:4]):
        smap[s] = "Tiny"
    r = bd.compute(T, close, high, low, smap)["sectors"]["Tiny"]["metrics"]["above_sma50"]
    assert r["state"] == bd.INSUFFICIENT and r["pct"] is None and r["denominator"] == 4


def test_participation_metrics_match_manual_returns():
    close, high, low = _wide()
    m = bd.compute(T, close, high, low)["market"]
    r20 = close.iloc[-1] / close.iloc[-21] - 1
    assert m["positive_ret_20"]["numerator"] == int((r20 > 0).sum())
    d1 = close.iloc[-1] - close.iloc[-2]
    assert m["advancing_1d"]["numerator"] == int((d1 > 0).sum()) and m["declining_1d"]["numerator"] == int((d1 < 0).sum())
    assert m["advancing_1d"]["numerator"] + m["declining_1d"]["numerator"] <= m["advancing_1d"]["denominator"]


def test_a_non_positive_close_is_missing_not_bearish():
    close, high, low = _wide()
    c2 = close.copy()
    c2.iloc[-1, :10] = 0.0
    m = bd.compute(T, c2, high, low)["market"]["above_sma50"]
    assert m["missing"] == 10 and m["denominator"] == 1090


def test_the_result_does_not_mutate_its_inputs():
    close, high, low = _wide()
    before = (close.copy(), high.copy(), low.copy())
    smap = _sectors(close)
    snap = copy.deepcopy(smap)
    bd.compute(T, close, high, low, smap)
    assert close.equals(before[0]) and high.equals(before[1]) and low.equals(before[2]) and smap == snap
