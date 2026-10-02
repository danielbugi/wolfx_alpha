"""rs_v1: sector / relative-strength definitions on synthetic data. Pure, no database."""
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from market_intelligence import relative_strength as RS  # noqa: E402

T = date(2026, 9, 30)
N_DAYS = 80


def _idx(n=N_DAYS, end=T):
    return pd.bdate_range(end=pd.Timestamp(end), periods=n)


def _panel(rets_by_sym, n=N_DAYS):
    """close panel where each symbol's close at t-h is 100 and at t is 100*(1+ret_h) for every h at once is impossible, so use one
    path per symbol: close_k = 100 * (1 + r)^(k/n) -> a constant daily growth g. ret_h = (1+g)^h - 1."""
    idx = _idx(n)
    cols = {}
    for sym, g in rets_by_sym.items():
        cols[sym] = 100.0 * (1.0 + g) ** np.arange(n)
    return pd.DataFrame(cols, index=idx)


def _universe(n_stocks=1200, sectors=("Tech", "Energy", "Health"), seed=1):
    rng = np.random.default_rng(seed)
    g = rng.normal(0.001, 0.004, n_stocks)
    syms = [f"S{i:04d}" for i in range(n_stocks)]
    close = _panel(dict(zip(syms, g)))
    sec = {s: sectors[i % len(sectors)] for i, s in enumerate(syms)}
    return close, sec, g


def _spx(g=0.0005, n=N_DAYS):
    return pd.Series(4000 * (1 + g) ** np.arange(n), index=_idx(n))


def _run(close, sec, disc=None, **kw):
    return RS.compute(T, close, sec, {"^GSPC": _spx()}, disc if disc is not None else {}, **kw)


def test_stock_return_formula_for_all_three_horizons():
    close, sec, g = _universe()
    r = _run(close, sec)
    for h in (5, 20, 60):
        expected = ((1 + g) ** h - 1) * 100
        np.testing.assert_allclose(r.stocks[f"ret_{h}"].to_numpy(), expected, rtol=1e-9, atol=1e-9)


def test_missing_endpoint_bar_gives_null_but_an_interior_gap_does_not():
    close, sec, g = _universe()
    close.iloc[-1 - 20, 0] = np.nan                    # the t-20 bar of S0000 is missing -> ret_20 NULL, ret_5 / ret_60 fine
    close.iloc[-10, 1] = np.nan                        # an interior bar of S0001 is missing -> spec: only the endpoints are required
    r = _run(close, sec)
    assert np.isnan(r.stocks.loc["S0000", "ret_20"]) and not np.isnan(r.stocks.loc["S0000", "ret_5"])
    assert not np.isnan(r.stocks.loc["S0001", "ret_20"])


def test_a_stock_with_too_little_history_is_null_not_zero():
    close, sec, g = _universe()
    close.iloc[:30, 2] = np.nan                        # only 50 bars -> ret_60 needs bar t-60 which is missing
    r = _run(close, sec)
    assert np.isnan(r.stocks.loc["S0002", "ret_60"]) and not np.isnan(r.stocks.loc["S0002", "ret_20"])
    short = close.iloc[-30:]                           # fewer than 61 sessions held -> every ret_60 NULL
    r2 = _run(short, sec)
    assert r2.stocks["ret_60"].isna().all() and r2.universe[60]["ret"] is None and r2.universe[60]["n_valid"] == 0


def test_discontinuity_inside_the_window_nulls_that_horizon_only():
    close, sec, g = _universe()
    d = close.index
    disc = {"S0003": [d[-1 - 7].date()],               # inside (t-20, t] and (t-60, t] but outside (t-5, t]
            "S0004": [d[-1 - 20].date()],              # exactly t-20: the window is (t-h, t], so NOT inside for h=20, inside for h=60
            "S0005": [d[-1 - 21].date()]}              # before t-20
    r = _run(close, sec, disc)
    s = r.stocks
    assert not np.isnan(s.loc["S0003", "ret_5"]) and np.isnan(s.loc["S0003", "ret_20"]) and np.isnan(s.loc["S0003", "ret_60"])
    assert not np.isnan(s.loc["S0004", "ret_20"]) and np.isnan(s.loc["S0004", "ret_60"])
    assert not np.isnan(s.loc["S0005", "ret_20"]) and np.isnan(s.loc["S0005", "ret_60"])
    assert r.coverage["n_excluded_discontinuity"] == {"5": 0, "20": 1, "60": 3}


def test_discontinuity_argument_is_required():
    close, sec, _ = _universe()
    with pytest.raises(TypeError):
        RS.compute(T, close, sec, {"^GSPC": _spx()})            # an absent argument is not silently "none detected"


def test_sector_median_needs_five_valid_members_and_keeps_counts():
    syms = [f"A{i}" for i in range(5)] + [f"B{i}" for i in range(4)] + [f"U{i:04d}" for i in range(1100)]
    rng = np.random.default_rng(3)
    close = _panel({s: g for s, g in zip(syms, rng.normal(0.001, 0.003, len(syms)))})
    sec = {**{f"A{i}": "Alpha" for i in range(5)}, **{f"B{i}": "Beta" for i in range(4)}, **{f"U{i:04d}": "Mega" for i in range(1100)}}
    r = _run(close, sec)
    rows = {x.sector: x for x in r.sectors}
    a, b = rows["Alpha"].per_horizon[20], rows["Beta"].per_horizon[20]
    assert a["n_valid"] == 5 and a["ret"] is not None and a["n_excluded"] == 0
    assert b["n_valid"] == 4 and b["ret"] is None and b["vs_spx"] is None and b["vs_univ"] is None and rows["Beta"].n_members == 4
    assert rows["Beta"].rank_20 is None and rows["Alpha"].rank_20 is not None          # excluded from ranking; row still present
    assert r.coverage["n_sectors"] == 3 and r.coverage["n_sectors_ranked"] == 2
    # an invalid member shrinks n_valid and the sector falls under the floor
    close2 = close.copy(); close2.iloc[-1 - 20, close2.columns.get_loc("A0")] = np.nan
    a2 = {x.sector: x for x in _run(close2, sec).sectors}["Alpha"]
    assert a2.per_horizon[20]["n_valid"] == 4 and a2.per_horizon[20]["n_excluded"] == 1 and a2.per_horizon[20]["ret"] is None


def test_sector_return_is_the_median_not_the_mean():
    syms = [f"S{i:04d}" for i in range(1200)]
    g = {s: 0.001 for s in syms}
    g["S0000"] = 0.05                                                                 # one extreme member in the sector
    close = _panel(g)
    sec = {s: ("Tech" if i < 7 else "Other") for i, s in enumerate(syms)}
    row = {x.sector: x for x in _run(close, sec).sectors}["Tech"]
    assert row.per_horizon[20]["ret"] == pytest.approx(((1.001) ** 20 - 1) * 100)


def test_four_comparisons_are_separate_and_consistent():
    close, sec, g = _universe()
    r = _run(close, sec)
    spx20 = ((1 + 0.0005) ** 20 - 1) * 100
    assert r.spx_ret[20] == pytest.approx(spx20)
    s = r.stocks
    row = {x.sector: x for x in r.sectors}
    for sym in ("S0000", "S0001", "S0002"):
        sector = sec[sym]
        sh = row[sector].per_horizon[20]
        assert s.loc[sym, "vs_spx_20"] == pytest.approx(s.loc[sym, "ret_20"] - spx20)
        assert s.loc[sym, "vs_sector_20"] == pytest.approx(s.loc[sym, "ret_20"] - sh["ret"])
        assert sh["vs_spx"] == pytest.approx(sh["ret"] - spx20)
        assert sh["vs_univ"] == pytest.approx(sh["ret"] - r.universe[20]["ret"])
        # stock vs sector = stock vs index - sector vs index
        assert s.loc[sym, "vs_sector_20"] == pytest.approx(s.loc[sym, "vs_spx_20"] - sh["vs_spx"])
    assert r.universe[20]["ret"] == pytest.approx(float(np.median(((1 + g) ** 20 - 1) * 100)))


def test_without_a_benchmark_the_dependent_numbers_are_null_not_zero():
    close, sec, _ = _universe()
    r = RS.compute(T, close, sec, {}, {})
    assert all(v is None for v in r.spx_ret.values())
    assert r.stocks["vs_spx_20"].isna().all()
    assert all(x.per_horizon[20]["vs_spx"] is None and x.per_horizon[20]["ret"] is not None for x in r.sectors)


def test_universe_median_and_percentile_need_1000_valid_stocks():
    close, sec, _ = _universe(n_stocks=999)
    r = _run(close, sec)
    assert r.universe[20] == {"ret": None, "n_valid": 999}
    assert r.stocks["rs_pctile_20"].isna().all()
    assert all(x.per_horizon[20]["vs_univ"] is None for x in r.sectors)
    close, sec, _ = _universe(n_stocks=1000)
    r = _run(close, sec)
    assert r.universe[20]["n_valid"] == 1000 and r.universe[20]["ret"] is not None


def test_rs_percentile_midrank_with_ties():
    n = 1000
    syms = [f"S{i:04d}" for i in range(n)]
    g = np.linspace(-0.002, 0.002, n)
    g[10:14] = g[10]                                                                   # 4 tied returns
    close = _panel(dict(zip(syms, g)))
    r = _run(close, {s: "X" for s in syms})
    p = r.stocks["rs_pctile_20"]
    ret = r.stocks["ret_20"]
    for sym in ("S0000", "S0500", "S0999", "S0010", "S0012"):
        x = ret[sym]
        expected = 100 * ((ret < x - 1e-12).sum() + 0.5 * ((ret - x).abs() <= 1e-12).sum()) / n
        assert p[sym] == pytest.approx(expected)
    assert p["S0010"] == p["S0013"]
    assert 0 < p.min() and p.max() < 100


def test_sector_rank_is_descending_with_name_tiebreak_and_unknown_is_never_a_sector():
    n = 1210
    syms = [f"S{i:04d}" for i in range(n)]
    base = 0.001
    g = {s: base for s in syms}
    sec = {}
    for i, s in enumerate(syms):
        sec[s] = ["Zeta", "Alpha", "Mid", "Unknown", None, ""][i % 6]
    for i, s in enumerate(syms):
        if sec[s] == "Mid":
            g[s] = 0.003
    close = _panel(g)
    r = _run(close, sec)
    names = [x.sector for x in r.sectors]
    assert names == ["Alpha", "Mid", "Zeta"]                                          # 'Unknown' / None / '' are not sectors
    ranks = {x.sector: x.rank_20 for x in r.sectors}
    assert ranks["Mid"] == 1 and ranks["Alpha"] == 2 and ranks["Zeta"] == 3           # Alpha and Zeta tie on return -> name ascending
    n_none = sum(1 for v in sec.values() if v in ("Unknown", None, ""))
    assert r.coverage["n_unclassified"] == n_none and r.coverage["n_classified"] == n - n_none
    assert r.stocks["sector"].isna().sum() == n_none
    assert r.universe[20]["n_valid"] == n                                              # unclassified stocks still count in the universe median
    assert r.stocks.loc[r.stocks["sector"].isna(), "vs_sector_20"].isna().all()


def test_a_session_that_is_not_real_or_not_held_returns_an_empty_result():
    close, sec, _ = _universe()
    partial = close.copy(); partial.iloc[-1, 100:] = np.nan
    r = _run(partial, sec)
    assert r.sectors == [] and r.coverage["session_is_real"] is False and r.universe[20]["ret"] is None
    r2 = RS.compute(date(2026, 10, 5), close, sec, {"^GSPC": _spx()}, {})
    assert r2.sectors == [] and r2.coverage["session_is_real"] is False


def test_bars_after_the_session_cannot_change_the_answer():
    close, sec, g = _universe()
    extended = pd.concat([close, pd.DataFrame(close.iloc[-1:].to_numpy() * 1.5, index=[close.index[-1] + pd.Timedelta(days=1)], columns=close.columns)])
    a = _run(close, sec)
    b = _run(extended, sec)
    pd.testing.assert_frame_equal(a.stocks, b.stocks)
    assert a.session_record() == b.session_record()


def test_horizon_set_must_contain_the_ranking_horizon():
    close, sec, _ = _universe()
    with pytest.raises(ValueError):
        RS.compute(T, close, sec, {}, {}, horizons=(5, 60))


def test_records_are_json_safe_and_carry_counts_and_sector_semantics():
    close, sec, _ = _universe()
    r = _run(close, sec, sector_provenance="reconstructed")
    json.dumps(r.session_record()); json.dumps(r.sector_records())
    cov = r.coverage
    assert cov["sector_map"]["pit_safe"] is False and cov["sector_map"]["provenance"] == "reconstructed" and cov["sector_map"]["caveat"]
    assert cov["n_universe"] == 1200 and cov["min_sector_members"] == 5
    for rec in r.sector_records():
        assert {"n_members", "rank_20", "horizons"} <= set(rec)
        for h in ("5", "20", "60"):
            assert {"n_valid", "n_excluded", "ret", "vs_spx", "vs_univ"} <= set(rec["horizons"][h])


def test_only_an_observed_sector_map_may_claim_pit_safety():
    assert RS.sector_map_semantics("observed")["pit_safe"] is True
    rec = RS.sector_map_semantics("reconstructed")
    assert rec["pit_safe"] is False and "not proven" in rec["caveat"]
    with pytest.raises(ValueError):
        RS.sector_map_semantics("captured")


# ------------------------------------------------------------------ parity with the existing definitions
def test_parity_with_t0_v1_ret_nd_on_a_clean_series():
    from ml_training.features import price_features as pf
    rng = np.random.default_rng(7)
    n = 120
    c = 50 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    px = pd.DataFrame({"date": _idx(n), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6})
    ind = pf.compute_indicators(px)
    pos = np.array([n - 1])
    feats = pf.build_breakout_features(ind, pos, np.array([1.0]))
    filler = _panel({f"F{i:04d}": 0.001 for i in range(1000)}, n=n)
    close = filler.assign(X=c)
    r = RS.compute(T, close, {}, {}, {})
    for h in (5, 20, 60):
        assert r.stocks.loc["X", f"ret_{h}"] == pytest.approx(float(feats[f"ret_{h}d"].iloc[0]), rel=1e-12)


def test_disclosed_difference_from_t0_v1_when_a_symbol_has_an_interior_missing_bar():
    # t0_v1 indexes the symbol's OWN bars (a missing bar shifts the lag); rs_v1 indexes the session calendar, so it does not.
    from ml_training.features import price_features as pf
    n = 120
    c = np.linspace(50, 60, n)
    idx = _idx(n)
    px = pd.DataFrame({"date": idx, "open": c, "high": c, "low": c, "close": c, "volume": 1e6}).drop(index=n - 3).reset_index(drop=True)
    feats = pf.build_breakout_features(pf.compute_indicators(px), np.array([len(px) - 1]), np.array([1.0]))
    filler = _panel({f"F{i:04d}": 0.001 for i in range(1000)}, n=n)
    close = filler.assign(X=pd.Series(c, index=idx).drop(idx[n - 3]))
    r = RS.compute(T, close, {}, {}, {})
    assert r.stocks.loc["X", "ret_5"] == pytest.approx((c[-1] / c[-6] - 1) * 100)           # calendar lag: 5 sessions
    assert float(feats["ret_5d"].iloc[0]) != pytest.approx(r.stocks.loc["X", "ret_5"])        # own-bar lag: 5 BARS = 6 sessions


def test_parity_with_the_published_sector_median_when_there_are_no_discontinuities():
    from alerts import market_stats as ms
    close, sec, _ = _universe(n_stocks=1203)
    w = ms.Wide(close, close, close, close, close)
    published, _unclassified = ms.sector_changes(w, sec, sessions=20)
    r = _run(close, sec)
    mine = {x.sector: x.per_horizon[20]["ret"] for x in r.sectors}
    for name, med, n in published:
        assert mine[name] == pytest.approx(med) and r_n(r, name) == n


def r_n(r, name):
    return {x.sector: x.per_horizon[20]["n_valid"] for x in r.sectors}[name]


def test_the_published_function_ignores_discontinuities_and_rs_v1_does_not():
    from alerts import market_stats as ms
    close, sec, _ = _universe(n_stocks=1203)
    victim = "S0000"
    disc = {victim: [close.index[-5].date()]}
    published, _ = ms.sector_changes(ms.Wide(close, close, close, close, close), sec, sessions=20)
    r = _run(close, sec, disc)
    pub_n = {name: n for name, _, n in published}
    assert pub_n[sec[victim]] == r_n(_run(close, sec), sec[victim])             # published counts the victim ...
    assert r_n(r, sec[victim]) == pub_n[sec[victim]] - 1                        # ... rs_v1 excludes it


def test_module_reads_no_clock():
    src = open(os.path.join(ROOT, "mechanism", "market_intelligence", "relative_strength.py"), encoding="utf-8").read()
    for banned in ("date.today", "datetime.now", "time.time", "utcnow"):
        assert banned not in src
