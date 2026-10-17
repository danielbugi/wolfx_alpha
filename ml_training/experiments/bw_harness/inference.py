"""Inference: rate ratios with clustered uncertainty, a permutation test, a within-date (Mantel-Haenszel) estimate and Holm's correction.

Rate ratio of a matched design: RR = (sum over matched sets of the treated outcome) / (sum over sets of the mean outcome of that set's matched controls). Sets with no control are not in the design (reported as unmatched).

Uncertainty is clustered by calendar month: whole months of matched sets are resampled with replacement (a market-wide month moves every signal in it together), which also covers the symbol-level overlap that independent
episodes already remove. Permutation: outcomes are shuffled WITHIN (direction, month) blocks of the pool and the statistic is recomputed with the matched sets held fixed (matching never looked at an outcome).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .matching import MatchResult


def set_arrays(y: np.ndarray, m: MatchResult) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(y_treated, mean_y_controls, n_controls) per matched set; sets whose treated or all controls have a missing outcome carry NaN and are dropped by `valid_sets`."""
    yt = y[m.treated_pos]
    cp = m.control_pos
    yc = np.where(cp >= 0, y[np.where(cp >= 0, cp, 0)], np.nan)
    n = np.isfinite(yc).sum(axis=1)
    with np.errstate(invalid="ignore"):
        mean = np.where(n > 0, np.nansum(yc, axis=1) / np.maximum(n, 1), np.nan)
    return yt, mean, n


def valid_sets(yt: np.ndarray, ym: np.ndarray) -> np.ndarray:
    return np.isfinite(yt) & np.isfinite(ym)


def rate_ratio(yt: np.ndarray, ym: np.ndarray) -> Optional[float]:
    ok = valid_sets(yt, ym)
    den = float(ym[ok].sum())
    return float(yt[ok].sum() / den) if ok.any() and den > 0 else None


def month_block_bootstrap(months: np.ndarray, yt: np.ndarray, ym: np.ndarray, n_boot: int, rng: np.random.Generator) -> Dict[str, object]:
    """Percentile interval of the rate ratio (and of the difference of rates) when whole months of matched sets are resampled."""
    ok = valid_sets(yt, ym)
    months, yt, ym = months[ok], yt[ok], ym[ok]
    if len(yt) == 0:
        return {"ci95": None, "diff_ci95": None, "n_months": 0}
    uniq, inv = np.unique(months, return_inverse=True)
    M = len(uniq)
    st = np.bincount(inv, weights=yt, minlength=M)
    sc = np.bincount(inv, weights=ym, minlength=M)
    sn = np.bincount(inv, minlength=M).astype(float)
    w = rng.multinomial(M, np.full(M, 1.0 / M), size=n_boot).astype(float)         # (B, M) month counts
    T, C, N = w @ st, w @ sc, w @ sn
    with np.errstate(divide="ignore", invalid="ignore"):
        rr = np.where(C > 0, T / C, np.nan)
        diff = (T - C) / N
    rr = rr[np.isfinite(rr)]
    return {"ci95": [round(float(x), 4) for x in np.percentile(rr, [2.5, 97.5])] if len(rr) else None,
            "diff_ci95": [round(float(x), 5) for x in np.percentile(diff, [2.5, 97.5])], "n_months": int(M)}


def permutation_p(y: np.ndarray, block: np.ndarray, m: MatchResult, observed_rr: float, n_perm: int, rng: np.random.Generator) -> Dict[str, object]:
    """Two-sided p-value of |log RR| against outcomes shuffled within `block` (direction x month) over the whole pool; matched sets held fixed."""
    n = len(y)
    finite = np.isfinite(y)
    grouped = np.argsort(block, kind="stable")
    tp, cp = m.treated_pos, m.control_pos
    has = cp >= 0
    cp0 = np.where(has, cp, 0)
    obs = abs(np.log(observed_rr)) if observed_rr and observed_rr > 0 else None
    if obs is None:
        return {"p_two_sided": None, "n_perm": 0}
    ge = 0
    used = 0
    yy = np.where(finite, y, 0.0)
    fin = finite.astype(float)
    for _ in range(n_perm):
        keys = rng.random(n)
        idx = np.lexsort((keys, block))
        yp = np.empty(n)
        fp = np.empty(n)
        yp[grouped] = yy[idx]
        fp[grouped] = fin[idx]
        yt = yp[tp]
        ft = fp[tp]
        yc = np.where(has, yp[cp0], 0.0)
        fc = np.where(has, fp[cp0], 0.0)
        nc = fc.sum(axis=1)
        mean = np.where(nc > 0, yc.sum(axis=1) / np.maximum(nc, 1), np.nan)
        ok = (ft > 0) & np.isfinite(mean)
        den = mean[ok].sum()
        if den <= 0:
            continue
        rr = yt[ok].sum() / den
        if rr <= 0:
            ge += 1                                              # an RR of 0 is as extreme as it gets in the shortfall direction
            used += 1
            continue
        used += 1
        if abs(np.log(rr)) >= obs:
            ge += 1
    return {"p_two_sided": round((1 + ge) / (1 + used), 5), "n_perm": int(used)}


def mantel_haenszel(date_index: np.ndarray, treated: np.ndarray, control: np.ndarray, y: np.ndarray, months: np.ndarray, n_boot: int, rng: np.random.Generator) -> Dict[str, object]:
    """Within-date risk ratio: strata are trading dates, each contributes its treated and its (unmatched) control signals; a date with only one group contributes nothing. MH RR = sum(a*n0/N) / sum(c*n1/N)."""
    ok = np.isfinite(y) & (treated | control)
    d, t, yy, mo = date_index[ok], treated[ok], y[ok], months[ok]
    df = pd.DataFrame({"d": d, "t": t.astype(bool), "y": yy, "m": mo})
    g1 = df[df.t].groupby("d").agg(n1=("y", "size"), a=("y", "sum"))
    g0 = df[~df.t].groupby("d").agg(n0=("y", "size"), c=("y", "sum"))
    g = g1.join(g0, how="inner").join(df.groupby("d")["m"].first())
    if g.empty:
        return {"rr": None, "ci95": None, "n_dates": 0, "n_treated": 0, "n_controls": 0}
    N = (g.n1 + g.n0).to_numpy(dtype=float)
    num_i = (g.a * g.n0).to_numpy() / N
    den_i = (g.c * g.n1).to_numpy() / N
    rr = float(num_i.sum() / den_i.sum()) if den_i.sum() > 0 else None
    mon = g["m"].to_numpy()
    uniq, inv = np.unique(mon, return_inverse=True)
    M = len(uniq)
    sn = np.bincount(inv, weights=num_i, minlength=M)
    sd = np.bincount(inv, weights=den_i, minlength=M)
    w = rng.multinomial(M, np.full(M, 1.0 / M), size=n_boot).astype(float)
    bn, bd = w @ sn, w @ sd
    with np.errstate(divide="ignore", invalid="ignore"):
        boot = np.where(bd > 0, bn / bd, np.nan)
    boot = boot[np.isfinite(boot)]
    return {"rr": round(rr, 4) if rr is not None else None, "ci95": [round(float(x), 4) for x in np.percentile(boot, [2.5, 97.5])] if len(boot) else None,
            "n_dates": int(len(g)), "n_treated": int(g.n1.sum()), "n_controls": int(g.n0.sum())}


def holm(p: Dict[str, Optional[float]], alpha: float = 0.05) -> Dict[str, Dict[str, object]]:
    """Holm-Bonferroni step-down over the supplied p-values. Missing p-values are treated as 1 (not testable)."""
    keys = list(p)
    vals = np.array([1.0 if p[k] is None else float(p[k]) for k in keys])
    order = np.argsort(vals, kind="stable")
    m = len(keys)
    adj = np.empty(m)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * vals[i]))
        adj[i] = run
    return {k: {"p": (None if p[k] is None else float(vals[i])), "p_holm": round(float(adj[i]), 5), "reject": bool(adj[i] < alpha)} for i, k in enumerate(keys)}


def year_consistency(years: np.ndarray, yt: np.ndarray, ym: np.ndarray, considered: Sequence[int], min_sets: int) -> Dict[str, object]:
    """Rate ratio per calendar year over the matched sets of that year; a year counts only with at least `min_sets` sets."""
    ok = valid_sets(yt, ym)
    rows = {}
    for yr in considered:
        m = ok & (years == yr)
        n = int(m.sum())
        rr = rate_ratio(yt[m], ym[m]) if n else None
        rows[str(yr)] = {"sets": n, "rr": None if rr is None else round(rr, 3), "counts": n >= min_sets}
    return rows
