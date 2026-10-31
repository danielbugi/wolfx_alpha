"""Post-mortem of a completed experiment: where did it work, where did it fail, and what is the one hypothesis worth testing next. PURE (numpy + stdlib).

The rule that keeps this honest: the analysis may look ONLY at TRAIN positions (it refuses anything else), so the test period plays no part in choosing the next hypothesis
and stays a clean, single out-of-sample test of it. The subgroup search is declared up front: single features in their top or bottom tercile and pairs of features both in their top
tercile, thresholds from the TRAIN tercile edges, intervals widened for the number of candidates looked at (Bonferroni on the percentile level), and a stability check across
the two halves of the train period. The top candidate becomes a registered hypothesis even when it does NOT pass the multiple-testing bar (it is then labelled exploratory); what
decides it is the pre-registered out-of-sample test with the parent as a control, not this analysis.
"""
from __future__ import annotations

import itertools
import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

from research.strategy_lab.engine import FEATURES

ALPHA = 0.05
MIN_N = 300
SELECTION_RULE = ("candidates: each feature in its top tercile, each feature in its bottom tercile, and every pair of features both in their top tercile (tercile edges from the TRAIN rows); "
                  f"a candidate needs n >= {MIN_N}, a mean net R whose Bonferroni-adjusted {int((1 - ALPHA) * 100)}% month-block interval lies above 0, and a positive mean in both halves of the train period; "
                  "the candidate with the highest adjusted lower bound is registered as the next hypothesis whether or not it passes (labelled exploratory when it does not)")

_LABEL = {"direction": "direction (+1 long, -1 short)", "surprise_abs": "surprise size", "reaction_dir": "reaction in the trade's direction", "relvol_event": "relative volume on the reaction days",
          "range_expansion": "range expansion on the reaction days", "gap_dir": "opening gap in the trade's direction", "pre_trend_20_dir": "the 20-session trend into the report"}


class AnalysisError(ValueError):
    pass


def _month_means(values: np.ndarray, months: np.ndarray, n_boot: int, seed: int) -> np.ndarray:
    keys = sorted(set(months.tolist()))
    blocks = [values[months == k] for k in keys]
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(blocks), len(blocks))
        out[b] = np.concatenate([blocks[i] for i in pick]).mean()
    return out


def _stats(v: np.ndarray, months: np.ndarray, m_tests: int, n_boot: int, seed: int) -> Dict[str, Any]:
    means = _month_means(v, months, n_boot, seed)
    lo, hi = np.percentile(means, [100 * ALPHA / 2, 100 * (1 - ALPHA / 2)])
    alo, ahi = np.percentile(means, [100 * ALPHA / 2 / m_tests, 100 * (1 - ALPHA / 2 / m_tests)])
    return {"n": int(len(v)), "mean_r": round(float(v.mean()), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)], "adjusted_ci": [round(float(alo), 4), round(float(ahi), 4)],
            "win_rate": round(float((v > 0).mean()), 4)}


def analyze(rows: Sequence[Mapping[str, Any]], *, seed: int = 20261031, n_boot: int = 600) -> Dict[str, Any]:
    """`rows`: the PARENT's TRAIN signal positions (each: split, features, net_r, month, year, signal_date, direction). Raises on any row that is not from the train split."""
    if not rows:
        raise AnalysisError("no rows to analyse")
    bad = [r for r in rows if r["split"] != "train"]
    if bad:
        raise AnalysisError(f"{len(bad)} row(s) are not from the TRAIN split; the analysis must never see the test period")
    net = np.array([r["net_r"] for r in rows], dtype=float)
    months = np.array([r["month"] for r in rows])
    years = np.array([r["year"] for r in rows])
    ordinals = np.array([r["signal_date"].toordinal() for r in rows])
    half = ordinals <= np.median(ordinals)
    F = {f: np.array([r["features"].get(f, np.nan) for r in rows], dtype=float) for f in FEATURES}
    edges = {f: (float(np.nanquantile(F[f], 1 / 3)), float(np.nanquantile(F[f], 2 / 3))) for f in FEATURES}

    cands: List[Dict[str, Any]] = []
    for f in FEATURES:
        cands.append({"name": f"{f} top tercile", "conditions": [{"feature": f, "op": ">=", "value": round(edges[f][1], 4)}]})
        cands.append({"name": f"{f} bottom tercile", "conditions": [{"feature": f, "op": "<=", "value": round(edges[f][0], 4)}]})
    for a, b in itertools.combinations(FEATURES, 2):
        cands.append({"name": f"{a} + {b} top terciles", "conditions": [{"feature": a, "op": ">=", "value": round(edges[a][1], 4)}, {"feature": b, "op": ">=", "value": round(edges[b][1], 4)}]})
    m = len(cands)

    def mask(conds):
        k = np.ones(len(rows), dtype=bool)
        for c in conds:
            x = F[c["feature"]]
            k &= np.isfinite(x) & ((x >= c["value"]) if c["op"] == ">=" else (x <= c["value"]))
        return k

    evaluated = []
    for i, cd in enumerate(cands):
        k = mask(cd["conditions"])
        if k.sum() < 20:
            continue
        s = _stats(net[k], months[k], m, n_boot, seed + 10 + i)
        s["mean_first_half"] = round(float(net[k & half].mean()), 4) if (k & half).any() else None
        s["mean_second_half"] = round(float(net[k & ~half].mean()), 4) if (k & ~half).any() else None
        stable = bool(s["mean_first_half"] is not None and s["mean_second_half"] is not None and s["mean_first_half"] > 0 and s["mean_second_half"] > 0)
        s["stable_across_halves"] = stable
        s["passes_selection"] = bool(s["n"] >= MIN_N and s["adjusted_ci"][0] > 0 and stable)
        evaluated.append({**cd, **s})
    ranked = sorted((e for e in evaluated if e["n"] >= MIN_N), key=lambda e: (-e["adjusted_ci"][0], -e["n"], e["name"]))
    best = ranked[0] if ranked else None

    overall = _stats(net, months, 1, n_boot, seed + 1)
    patterns: List[Dict[str, str]] = []
    for d, name in ((1, "longs"), (-1, "shorts")):
        k = np.array([r["direction"] == d for r in rows])
        if k.sum() >= 100:
            s = _stats(net[k], months[k], 1, n_boot, seed + 2)
            if s["ci95"][1] < 0 or s["mean_r"] < overall["mean_r"] - 0.05:
                patterns.append({"pattern": f"{name} lose more than the pool", "evidence": f"{name}: mean {s['mean_r']:+.3f} R, 95% interval {s['ci95']}, n {s['n']} (pool {overall['mean_r']:+.3f})"})
    by_year = {int(y): (int((years == y).sum()), round(float(net[years == y].mean()), 4)) for y in sorted(set(years.tolist()))}
    neg = [y for y, (n, mean) in by_year.items() if n >= 100 and mean < 0]
    if neg:
        patterns.append({"pattern": "the result is not stable across years", "evidence": "negative years: " + ", ".join(f"{y} ({by_year[y][1]:+.3f} R, n {by_year[y][0]})" for y in neg)})
    top = np.sort(net)[::-1]
    k1 = max(1, int(len(net) * 0.01))
    if top[:k1].sum() > 0 and net[net > 0].sum() > 0 and top[:k1].sum() / net[net > 0].sum() > 0.2:
        patterns.append({"pattern": "profits depend on a few trades", "evidence": f"the top 1% of trades supply {top[:k1].sum() / net[net > 0].sum():.0%} of gross profit"})
    if overall["ci95"][0] <= 0 <= overall["ci95"][1]:
        patterns.append({"pattern": "the pool is indistinguishable from zero", "evidence": f"mean {overall['mean_r']:+.3f} R, 95% interval {overall['ci95']}, n {overall['n']}"})
    singles = [e for e in evaluated if len(e["conditions"]) == 1 and e["n"] >= MIN_N]
    for e in sorted(singles, key=lambda e: e["mean_r"])[:2]:
        if e["mean_r"] < overall["mean_r"] - 0.03:
            patterns.append({"pattern": f"weak subgroup: {e['name']}", "evidence": f"mean {e['mean_r']:+.3f} R (pool {overall['mean_r']:+.3f}), n {e['n']}"})
    return {"schema": "strategy_lab_analysis_v1", "source_rows": "parent strategy, TRAIN split only", "n_rows": int(len(rows)), "overall": overall, "by_year": {str(y): {"n": n, "mean_r": mean} for y, (n, mean) in by_year.items()},
            "candidates_tested": m, "selection_rule": SELECTION_RULE, "edges": {f: [round(edges[f][0], 4), round(edges[f][1], 4)] for f in FEATURES},
            "subgroups": [{k: v for k, v in e.items()} for e in ranked[:12]], "best": best, "n_passing": int(sum(1 for e in ranked if e["passes_selection"])),
            "failure_patterns": patterns}


def readable(conditions: Sequence[Mapping[str, Any]]) -> str:
    return " and ".join(f"{_LABEL.get(c['feature'], c['feature'])} {'at least' if c['op'] == '>=' else 'at most'} {c['value']:g}" for c in conditions)
