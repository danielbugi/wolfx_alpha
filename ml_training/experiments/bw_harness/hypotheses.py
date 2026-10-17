"""The five pre-registered hypotheses (docs/research/BIG_WINNER_DISCOVERY.md section 5), the negative control H0, and the verdict rules of section 5.1. Definitions are code here and are NOT adjustable
at run time: the only free numbers are the quantile thresholds that are FROZEN on the TRAIN segment (`freeze_thresholds`) and written into the results.

Longs are primary. Shorts are secondary and use mirror definitions fixed in this file before any result was computed (documented per hypothesis); they get no verdict.

Everything is evaluated on independent-episode first signals only, per chronological segment (validation, test), with the matched-control design, a within-date estimate, a permutation test, Holm correction over H1-H5,
and the support rules of section 5.1. HISTORICAL RESULTS ARE HYPOTHESIS-GENERATION OR REPLICATION EVIDENCE: the history has been examined before, so there is no untouched historical holdout.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import inference as I
from . import matching as M
from .config import Config
from .splits import TEST, TRAIN, VALIDATION

PRIMARY, BW10, RETURN, VOLNEUTRAL, GRADED = "ge5r_120", "ge10r_120", "ret_label_120", "vn_label_120", "tier_120"
LABELS = {"BW5@120 (primary, R-based)": PRIMARY, "BW10@120 (R-based)": BW10, "return-based": RETURN, "vol-neutral": VOLNEUTRAL, "graded tier (0-3)": GRADED}
REALISED = ("realised_a_atr_stop_fixed_2r", "realised_a_atr_stop_fixed_3r", "realised_b_donchian_trail_10", "realised_c_atr_trail_3x", "realised_d_hybrid_atr_stop_to_donchian_10")


def _f(df: pd.DataFrame, col: str) -> np.ndarray:
    return df[col].to_numpy(dtype=float)


def freeze_thresholds(df: pd.DataFrame, cfg: Config) -> Dict[str, Any]:
    """Quantile thresholds from the TRAIN segment's eligible independent-episode first signals, per direction. Nothing else is learned from data."""
    thr: Dict[str, Any] = {}
    for d, name in ((1, "long"), (-1, "short")):
        g = df[(df["direction"] == d) & (df["segment"] == TRAIN) & df["eligible"] & df["is_episode_first"]]
        thr[name] = {
            "breadth_hi": float(np.nanquantile(g["breadth_50"], cfg.breadth_top_tercile_q)) if g["breadth_50"].notna().any() else float("nan"),
            "breadth_lo": float(np.nanquantile(g["breadth_50"], 1.0 - cfg.breadth_top_tercile_q)) if g["breadth_50"].notna().any() else float("nan"),
            "relvol_hi": float(np.nanquantile(g["vol_ratio_50"], cfg.relvol_top_q)) if g["vol_ratio_50"].notna().any() else float("nan"),
            "relvol_ctrl": float(np.nanquantile(g["vol_ratio_50"], cfg.relvol_control_below_q)) if g["vol_ratio_50"].notna().any() else float("nan"),
            "n_train_episodes": int(len(g)),
        }
    return thr


@dataclass(frozen=True)
class Spec:
    id: str
    name: str
    treated: Callable[[pd.DataFrame, Dict[str, float], Config], np.ndarray]
    control: Callable[[pd.DataFrame, Dict[str, float], Config], np.ndarray]
    observed_sector_only: bool = False          # H5: the confirmatory population has an observed (dated) sector
    note: str = ""


def _nn(*arrs):
    m = np.ones(len(arrs[0]), bool)
    for a in arrs:
        m &= np.isfinite(a)
    return m


# ---- long definitions (primary)
def h1_t(d, t, c): return (_f(d, "rs60_pct") >= c.rs_top_pct) & (_f(d, "rs120_pct") >= c.rs_top_pct)
def h1_c(d, t, c): return (_f(d, "rs60_pct") < c.rs_control_below_pct) & (_f(d, "rs120_pct") < c.rs_control_below_pct)
def h2_t(d, t, c): return (_f(d, "index_above_200") == 1.0) & (_f(d, "breadth_50") >= t["breadth_hi"])
def h2_c(d, t, c): return _nn(_f(d, "index_above_200"), _f(d, "breadth_50")) & ~h2_t(d, t, c)
def h3_t(d, t, c): return _f(d, "pct_from_52w_high") >= c.near_high_pct
def h3_c(d, t, c): return _f(d, "pct_from_52w_high") <= c.far_high_pct
def h4_t(d, t, c): return _f(d, "chan_squeeze_pctile") <= c.squeeze_pctile_max
def h4_c(d, t, c): return _f(d, "chan_squeeze_pctile") > c.squeeze_pctile_max
def h5_t(d, t, c): return (_f(d, "sector_rs60_rank") <= c.sector_top_n) | (_f(d, "group_breakouts_10") >= c.group_breakouts_min)
def h5_c(d, t, c): return _nn(_f(d, "sector_rs60_rank"), _f(d, "group_breakouts_10")) & ~h5_t(d, t, c)
def h0_t(d, t, c): return _f(d, "vol_ratio_50") >= t["relvol_hi"]
def h0_c(d, t, c): return _f(d, "vol_ratio_50") < t["relvol_ctrl"]


# ---- short mirrors (secondary): leaders become laggards, highs become lows, "up" regime becomes "down" regime
def s1_t(d, t, c): return (_f(d, "rs60_pct") <= 1 - c.rs_top_pct) & (_f(d, "rs120_pct") <= 1 - c.rs_top_pct)
def s1_c(d, t, c): return (_f(d, "rs60_pct") > 1 - c.rs_control_below_pct) & (_f(d, "rs120_pct") > 1 - c.rs_control_below_pct)
def s2_t(d, t, c): return (_f(d, "index_above_200") == 0.0) & (_f(d, "breadth_50") <= t["breadth_lo"])
def s2_c(d, t, c): return _nn(_f(d, "index_above_200"), _f(d, "breadth_50")) & ~s2_t(d, t, c)
def s3_t(d, t, c): return _f(d, "pct_from_52w_low") <= -c.near_high_pct          # within 5% of the 52-week low
def s3_c(d, t, c): return _f(d, "pct_from_52w_low") >= -c.far_high_pct          # at least 20% above it
def s5_t(d, t, c): return (_f(d, "sector_rs60_rank") >= 9) | (_f(d, "group_breakouts_10") >= c.group_breakouts_min)
def s5_c(d, t, c): return _nn(_f(d, "sector_rs60_rank"), _f(d, "group_breakouts_10")) & ~s5_t(d, t, c)


LONG_SPECS: List[Spec] = [
    Spec("H1", "Leadership: relative-strength percentile >= 0.80 on 60 and 120 bars vs < 0.60 on both", h1_t, h1_c),
    Spec("H2", "Market regime and breadth: index above its 200-bar average and breadth in the train top tercile vs everything else", h2_t, h2_c),
    Spec("H3", "New-high breakout: within 5% of the 52-week high vs more than 20% below it", h3_t, h3_c),
    Spec("H4", "Compression: channel-width squeeze percentile <= 20 vs > 20 (after ATR% matching)", h4_t, h4_c),
    Spec("H5", "Group leadership: sector 60-bar relative-strength rank <= 3 of 11 or >= 3 other same-sector breakouts in the previous 10 sessions", h5_t, h5_c, observed_sector_only=True),
]
NEGATIVE_CONTROL = Spec("H0", "Negative control: breakout-bar relative volume (vol_ratio_50) in the train top quintile vs below the train 60th percentile", h0_t, h0_c)
SHORT_SPECS: List[Spec] = [
    Spec("H1s", "Short mirror of H1: relative-strength percentile <= 0.20 on both windows vs > 0.40 on both", s1_t, s1_c),
    Spec("H2s", "Short mirror of H2: index below its 200-bar average and breadth in the train bottom tercile vs everything else", s2_t, s2_c),
    Spec("H3s", "Short mirror of H3: within 5% of the 52-week low vs more than 20% above it", s3_t, s3_c),
    Spec("H4s", "Short: compression, same definition as H4", h4_t, h4_c),
    Spec("H5s", "Short mirror of H5: sector rank >= 9 of 11 or >= 3 other same-sector breakouts in the previous 10 sessions", s5_t, s5_c, observed_sector_only=True),
]


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ preparation
def prepare(df: pd.DataFrame, cfg: Config) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """(frame with strata and the graded tier, frozen thresholds, frozen matching bins)."""
    thr = freeze_thresholds(df, cfg)
    edges = M.freeze_bins(df, cfg)
    out = M.add_strata(df, edges)
    r = out[f"mfe_r_{cfg.primary_horizon}"].to_numpy(dtype=float)
    out["tier_120"] = np.where(np.isfinite(r), (r >= 3).astype(float) + (r >= 5) + (r >= 10), np.nan)
    return out, thr, edges


def _pool(df: pd.DataFrame, direction: int, segments: Tuple[str, ...], cfg: Config, observed_only: bool) -> pd.DataFrame:
    m = (df["direction"] == direction) & df["segment"].isin(segments) & df["eligible"] & df["is_episode_first"] & (df[f"status_{cfg.primary_horizon}"] == "ok")
    if observed_only:
        m &= df["sector_provenance"] == "OBSERVED_PIT"
    return df[m].reset_index(drop=True)


def _mean_diff(months, yt, ym, n_boot, rng) -> Dict[str, Any]:
    ok = I.valid_sets(yt, ym)
    if not ok.any():
        return {"sets": 0, "mean_diff": None, "ci95": None}
    d = yt[ok] - ym[ok]
    mo = months[ok]
    uniq, inv = np.unique(mo, return_inverse=True)
    M_ = len(uniq)
    s = np.bincount(inv, weights=d, minlength=M_)
    n = np.bincount(inv, minlength=M_).astype(float)
    w = rng.multinomial(M_, np.full(M_, 1.0 / M_), size=n_boot).astype(float)
    boot = (w @ s) / np.maximum(w @ n, 1)
    return {"sets": int(ok.sum()), "treated_mean": round(float(yt[ok].mean()), 4), "control_mean": round(float(ym[ok].mean()), 4), "mean_diff": round(float(d.mean()), 4),
            "ci95": I.interval(boot, M_)}


def evaluate(pool: pd.DataFrame, spec: Spec, thr: Dict[str, float], cfg: Config, seed: int, permutation: bool = True, realised: bool = True) -> Dict[str, Any]:
    """One hypothesis on one pool. All randomness comes from `seed`."""
    rng = np.random.default_rng(seed)
    n = len(pool)
    out: Dict[str, Any] = {"spec": spec.id, "pool_signals": int(n)}
    if n == 0:
        return {**out, "status": "empty_pool"}
    t_mask = spec.treated(pool, thr, cfg)
    c_mask = spec.control(pool, thr, cfg) & ~t_mask
    out["treated_candidates"] = int(t_mask.sum())
    out["control_candidates"] = int(c_mask.sum())
    if t_mask.sum() == 0 or c_mask.sum() == 0:
        return {**out, "status": "no_treated_or_no_controls"}
    m = M.match(pool, t_mask, c_mask, cfg, rng)
    out["matched_sets"] = m.n_sets
    out["unmatched_treated"] = m.n_unmatched
    out["controls_used"] = m.n_controls
    if m.n_sets == 0:
        return {**out, "status": "no_matches"}
    months = pool["month"].to_numpy()[m.treated_pos]
    out["balance_after_matching"] = M.balance(pool, m)
    labels: Dict[str, Any] = {}
    for name, col in LABELS.items():
        y = pool[col].to_numpy(dtype=float)
        yt, ym, _ = I.set_arrays(y, m)
        rr = I.rate_ratio(yt, ym)
        ok = I.valid_sets(yt, ym)
        row: Dict[str, Any] = {"column": col, "sets": int(ok.sum()), "treated_rate": round(float(yt[ok].mean()), 4) if ok.any() else None,
                               "control_rate": round(float(ym[ok].mean()), 4) if ok.any() else None, "rr": None if rr is None else round(rr, 4)}
        row.update(I.month_block_bootstrap(months, yt, ym, cfg.n_boot, rng))
        if col == PRIMARY and permutation and rr is not None:
            block = (pool["direction"].astype(str) + "|" + pool["month"].astype(str)).to_numpy()
            codes = pd.factorize(block)[0]
            row["permutation"] = I.permutation_p(y, codes, m, rr, cfg.n_perm, rng)
        labels[name] = row
    out["labels"] = labels
    y = pool[PRIMARY].to_numpy(dtype=float)
    out["within_date_mh"] = I.mantel_haenszel(pool["date_index"].to_numpy(), t_mask, c_mask, y, pool["month"].to_numpy(), cfg.n_boot, rng)
    yt, ym, _ = I.set_arrays(y, m)
    out["year_consistency_in_pool"] = I.year_consistency(pool["year"].to_numpy()[m.treated_pos], yt, ym, cfg.years_considered, cfg.min_sets_per_year)
    if realised:
        rz: Dict[str, Any] = {}
        for col in REALISED:
            if col in pool.columns:
                ryt, rym, _ = I.set_arrays(pool[col].to_numpy(dtype=float), m)
                rz[col] = _mean_diff(months, ryt, rym, cfg.n_boot, rng)
        out["realised_net_r_cap60_treated_minus_control"] = rz
    out["status"] = "ok"
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the whole run
def run_all(df: pd.DataFrame, thr: Dict[str, Any], cfg: Config) -> Dict[str, Any]:
    results: Dict[str, Any] = {"long": {}, "short": {}, "all_years": {}}
    for direction, specs, side in ((1, LONG_SPECS + [NEGATIVE_CONTROL], "long"), (-1, SHORT_SPECS + [NEGATIVE_CONTROL], "short")):
        th = thr[side]
        for si, spec in enumerate(specs):
            res: Dict[str, Any] = {}
            for seg in (VALIDATION, TEST):
                pool = _pool(df, direction, (seg,), cfg, observed_only=spec.observed_sector_only)
                res[seg] = evaluate(pool, spec, th, cfg, seed=cfg.seed + 1000 * (1 if direction == 1 else 2) + 10 * si + (0 if seg == VALIDATION else 1))
            if spec.observed_sector_only:                       # replication on reconstructed sector rows (not used for the verdict)
                for seg in (VALIDATION, TEST):
                    pool = _pool(df, direction, (seg,), cfg, observed_only=False)
                    pool = pool[pool["sector_provenance"] == "RECONSTRUCTED"].reset_index(drop=True)
                    res[f"{seg}_reconstructed_replication"] = evaluate(pool, spec, th, cfg, seed=cfg.seed + 5000 + 10 * si + (0 if seg == VALIDATION else 1), realised=False)
            allpool = _pool(df, direction, (TRAIN, VALIDATION, TEST), cfg, observed_only=spec.observed_sector_only)
            res["all_years"] = evaluate(allpool, spec, th, cfg, seed=cfg.seed + 9000 + 10 * si, permutation=False, realised=False)
            results[side][spec.id] = res
    for side, ids in (("long", [s.id for s in LONG_SPECS]), ("short", [s.id for s in SHORT_SPECS])):
        for seg in (VALIDATION, TEST):
            p = {i: _primary_p(results[side][i][seg]) for i in ids}
            h = I.holm(p, cfg.alpha)
            for i in ids:
                results[side][i][seg]["holm"] = h[i]
    results["verdicts"] = {s.id: verdict(results["long"][s.id], results["long"]["H0"], cfg, s) for s in LONG_SPECS}
    nc: Dict[str, Any] = {}
    for side in ("long", "short"):
        nc[side] = {}
        for seg in (VALIDATION, TEST):
            lab = _lab(results[side]["H0"][seg], "BW5@120 (primary, R-based)")
            nc[side][seg] = {"rr": lab and lab.get("rr"), "ci95": lab and lab.get("ci95"), "null": bool(lab and lab.get("ci95") and lab["ci95"][0] <= 1.0 <= lab["ci95"][1])}
        nc[side]["null_in_both_segments"] = all(nc[side][s]["null"] for s in (VALIDATION, TEST))
    results["negative_control"] = nc
    return results


def _primary_p(res: Dict[str, Any]) -> Optional[float]:
    try:
        return res["labels"]["BW5@120 (primary, R-based)"]["permutation"]["p_two_sided"]
    except (KeyError, TypeError):
        return None


def _lab(res: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    try:
        return res["labels"][name]
    except (KeyError, TypeError):
        return None


def verdict(res: Dict[str, Any], h0: Dict[str, Any], cfg: Config, spec: Spec) -> Dict[str, Any]:
    """Section 5.1 applied mechanically. Returns the verdict and each criterion with its evidence. 'Supported (historical)' is NOT an edge."""
    crit: Dict[str, Any] = {}
    segs = (VALIDATION, TEST)
    if spec.observed_sector_only and res[VALIDATION].get("status") != "ok":
        return {"verdict": "NOT_EVALUABLE_PROVENANCE", "reason": "the validation segment has no observed (dated) sector or industry, so the confirmatory run cannot be completed; the reconstructed-sector replication is reported separately",
                "criteria": {}}
    prim = "BW5@120 (primary, R-based)"
    a = {}
    for s in segs:
        lab = _lab(res[s], prim)
        ok = bool(lab and lab.get("rr") and lab.get("ci95") and lab["rr"] >= cfg.min_rr and lab["ci95"][0] > 1.0)
        a[s] = {"rr": lab and lab.get("rr"), "ci95": lab and lab.get("ci95"), "pass": ok}
    crit["a_rate_ratio_and_interval_in_validation_and_test"] = {**a, "pass": all(a[s]["pass"] for s in segs)}
    yc = res["all_years"].get("year_consistency_in_pool", {}) if isinstance(res.get("all_years"), dict) else {}
    counted = [v for v in yc.values() if v.get("counts")]
    same = [v for v in counted if v["rr"] is not None and v["rr"] > 1.0]
    crit["b_same_sign_in_enough_years"] = {"years_counting": len(counted), "years_with_rr_above_1": len(same), "needed": cfg.min_years_same_sign,
                                           "pass": len(counted) >= cfg.min_years_same_sign and len(same) >= cfg.min_years_same_sign, "by_year": yc}
    h = {s: (res[s].get("holm") or {}) for s in segs}
    crit["c_survives_holm"] = {**{s: h[s] for s in segs}, "pass": all(h[s].get("reject") for s in segs)}
    r = {}
    for s in segs:
        lab = _lab(res[s], "return-based")
        r[s] = bool(lab and lab.get("rr") and lab.get("ci95") and lab["rr"] > 1.0 and lab["ci95"][0] > 1.0)
    crit["d_holds_on_return_based_label"] = {**r, "pass": all(r.values())}
    e = {}
    est = {}
    for s in segs:
        mh = res[s].get("within_date_mh") or {}
        est[s] = mh.get("rr") is not None
        e[s] = bool(mh.get("rr") and mh.get("ci95") and mh["rr"] > 1.0 and mh["ci95"][0] > 1.0)
    crit["e_survives_matching_and_within_date_test"] = {**e, "within_date_estimable": est, "pass": all(e.values()),
                                                        "note": "a hypothesis about a property of the DATE (H2) has no within-date contrast, so the criterion cannot be met; it is reported as not estimable, not as a negative finding" if not all(est.values()) else ""}
    f = {}
    for s in segs:
        lab = _lab(h0[s], prim)
        f[s] = bool(lab and lab.get("ci95") and lab["ci95"][0] <= 1.0 <= lab["ci95"][1])
    crit["f_negative_control_null"] = {**f, "pass": all(f.values())}
    core = all(crit[k]["pass"] for k in ("a_rate_ratio_and_interval_in_validation_and_test", "b_same_sign_in_enough_years", "c_survives_holm", "e_survives_matching_and_within_date_test", "f_negative_control_null"))
    if core and crit["d_holds_on_return_based_label"]["pass"]:
        v = "SUPPORTED_HISTORICAL"
    elif core:
        v = "SUPPORTED_HISTORICAL_R_LABEL_ONLY"
    else:
        v = "NOT_SUPPORTED"
    return {"verdict": v, "criteria": crit,
            "note": "Supported (historical) is hypothesis-generation or replication evidence on data that has been examined before; it is not an edge and earns a forward test only."}
