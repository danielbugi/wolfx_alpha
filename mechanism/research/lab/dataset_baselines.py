"""Diagnostic baselines over a finished dataset (pure, deterministic, no fitting).

These are DESCRIPTIONS of what the labelled history says, split by train / validation. They are not models, there is no hyperparameter and no
search, and nothing here supports a claim that a stratum predicts anything: the strata are fixed in advance (`BASELINE_VERSION`), the
number of comparisons is reported, and an in-sample number is never an out-of-sample result. The TEST split is withheld unless the caller
explicitly asks for it (the registry's one-shot rule is enforced by the runner, not here).

Missing is never zero. A metric needs `min_sample` observations (manifest config); below that the block carries its `n` and a status, and no
number at all. Every metric block states its own sample size, because the denominators differ (a void label has no return; a missing benchmark
has no excess return).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.lab import dataset_contract as C
from research.lab.manifest import LabError, SPLITS

BASELINE_VERSION = "lab_baselines_v1"
Z95 = 1.959963984540054
RS_BUCKETS: Tuple[Tuple[str, float, float], ...] = (("rs_pct_0_33", 0.0, 100.0 / 3), ("rs_pct_33_67", 100.0 / 3, 200.0 / 3),
                                                    ("rs_pct_67_100", 200.0 / 3, 100.0 + 1e-9))
DISCLAIMER = ("Descriptive diagnostics over a fixed set of strata. In-sample numbers (train/validation) are not evidence of predictive power; "
              "no comparison here is corrected for multiple testing, no strata were selected by their outcomes, and the test window is "
              "evaluated at most once through the experiment registry.")


def _r(x: float) -> float:
    return round(float(x), 12)


def _mean(v: Sequence[float]) -> float:
    return math.fsum(v) / len(v)


def _sd(v: Sequence[float], m: float) -> float:
    return math.sqrt(math.fsum((x - m) ** 2 for x in v) / (len(v) - 1))


def _median(v: Sequence[float]) -> float:
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def wilson(k: int, n: int) -> Tuple[float, float]:
    p = k / n
    d = 1 + Z95 ** 2 / n
    centre = (p + Z95 ** 2 / (2 * n)) / d
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95 ** 2 / (4 * n * n)) / d
    return centre - half, centre + half


def summarize(values: Sequence[float], min_sample: int, *, with_hit: bool) -> Dict[str, Any]:
    n = len(values)
    if n < min_sample:
        return {"n": n, "status": "insufficient_sample", "min_sample": min_sample}
    m = _mean(values)
    sd = _sd(values, m)
    se = sd / math.sqrt(n)
    out: Dict[str, Any] = {"n": n, "status": "ok", "mean": _r(m), "median": _r(_median(values)), "sd": _r(sd), "se": _r(se),
                           "mean_ci95_normal": [_r(m - Z95 * se), _r(m + Z95 * se)], "t_vs_zero": _r(m / se) if se > 0 else None}
    if with_hit:
        k = sum(1 for x in values if x > 0)
        lo, hi = wilson(k, n)
        out.update({"hit_rate": _r(k / n), "hit_rate_ci95_wilson": [_r(lo), _r(hi)]})
    return out


def block(rows: Sequence[Mapping[str, Any]], min_sample: int) -> Dict[str, Any]:
    """Metric block for one stratum: counts for every label state, then summaries over the rows each metric is actually defined for."""
    final = [r for r in rows if r["label_status"] == "final"]
    ret = [float(r["directional_return"]) for r in final if r["directional_return"] is not None]
    exc = [float(r["directional_excess_return"]) for r in final if r["benchmark_state"] == "ok" and r["directional_excess_return"] is not None]
    return {"n_rows": len(rows), "n_final": len(final), "n_void": sum(1 for r in rows if r["label_status"] == "void"),
            "n_label_missing": sum(1 for r in rows if r["label_status"] == C.MISSING),
            "directional_return": summarize(ret, min_sample, with_hit=True),
            "directional_excess_return": summarize(exc, min_sample, with_hit=False)}


def welch(a: Sequence[float], b: Sequence[float], min_sample: int) -> Dict[str, Any]:
    """Difference of means a - b with a Welch t statistic. No p-value is produced on purpose."""
    n1, n2 = len(a), len(b)
    if n1 < min_sample or n2 < min_sample:
        return {"n_a": n1, "n_b": n2, "status": "insufficient_sample", "min_sample": min_sample}
    m1, m2 = _mean(a), _mean(b)
    v1, v2 = _sd(a, m1) ** 2 / n1, _sd(b, m2) ** 2 / n2
    se = math.sqrt(v1 + v2)
    return {"n_a": n1, "n_b": n2, "status": "ok", "mean_difference": _r(m1 - m2), "se": _r(se),
            "welch_t": _r((m1 - m2) / se) if se > 0 else None,
            "welch_df": _r((v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))) if se > 0 else None}


def _ret(rows: Sequence[Mapping[str, Any]]) -> List[float]:
    return [float(r["directional_return"]) for r in rows if r["label_status"] == "final" and r["directional_return"] is not None]


def _by_split(rows: Sequence[Mapping[str, Any]], include_test: bool) -> Dict[str, Optional[Sequence[Mapping[str, Any]]]]:
    return {s: ([r for r in rows if r["split"] == s] if (s != "test" or include_test) else None) for s in SPLITS}


def _stratified(rows: Sequence[Mapping[str, Any]], label_of, min_sample: int, include_test: bool) -> Dict[str, Any]:
    labels = sorted({label_of(r) for r in rows})
    out: Dict[str, Any] = {}
    for s, sel in _by_split(rows, include_test).items():
        if sel is None:
            out[s] = {"status": "withheld", "note": "the test window is evaluated once, through the experiment registry"}
            continue
        out[s] = {lab: block([r for r in sel if label_of(r) == lab], min_sample) for lab in sorted({label_of(r) for r in sel})}
    out["strata_labels"] = labels if include_test else sorted({label_of(r) for r in rows if r["split"] != "test"})
    return out


def regime_stratum(r: Mapping[str, Any]) -> str:
    return r["regime_state"] if r["market__state"] == C.OK and r["regime_state"] else f"<{r['market__state']}>"


def rs_stratum(r: Mapping[str, Any]) -> str:
    if r["rs__state"] != C.OK:
        return f"<{r['rs__state']}>"
    p = r["rs_percentile"]
    if p is None:
        return "<rs_percentile_null>"
    if not (0.0 <= p <= 100.0):
        raise LabError([f"rs_percentile {p} is outside 0..100"])
    return next(name for name, lo, hi in RS_BUCKETS if lo <= p < hi)


def catalyst_stratum(r: Mapping[str, Any]) -> str:
    return r["catalyst__state"] if r["catalyst__state"] != C.OK else "<ok>"


def run_baselines(rows: Sequence[Mapping[str, Any]], cfg: C.DatasetConfig, *, include_test: bool = False) -> Dict[str, Any]:
    """All baselines at the manifest's primary horizon (plus a per-horizon table for the unconditional baseline). Deterministic in `rows`."""
    ms = cfg.min_sample
    primary = [r for r in rows if r["horizon_sessions"] == cfg.primary_horizon]
    tracked = [r for r in primary if r["tracked_intent"]]
    out: Dict[str, Any] = {"version": BASELINE_VERSION, "primary_horizon": cfg.primary_horizon, "min_sample": ms,
                           "include_test": include_test, "disclaimer": DISCLAIMER, "baselines": {}}
    b = out["baselines"]

    horizons = sorted({r["horizon_sessions"] for r in rows})
    b["null_unconditional"] = {
        "population": "every candidate observation in the dataset (breakouts, near-breakouts, guard-rejected): an unconditional reference, "
                      "not a tradable rule",
        "by_split": {s: (None if sel is None else block(sel, ms)) for s, sel in _by_split(primary, include_test).items()},
        "by_horizon": {str(h): {s: (None if sel is None else block(sel, ms))
                                for s, sel in _by_split([r for r in rows if r["horizon_sessions"] == h], include_test).items()}
                       for h in horizons}}
    for s, v in list(b["null_unconditional"]["by_split"].items()):
        if v is None:
            b["null_unconditional"]["by_split"][s] = {"status": "withheld"}
    for h, d in b["null_unconditional"]["by_horizon"].items():
        for s, v in list(d.items()):
            if v is None:
                d[s] = {"status": "withheld"}

    sig: Dict[str, Any] = {"population": "the strategy's own selection (tracked_intent) versus the rest of the candidates (its control group)",
                           "strata": _stratified(primary, lambda r: "tracked" if r["tracked_intent"] else "control", ms, include_test),
                           "by_signal_type": _stratified(primary, lambda r: r["signal_type"], ms, include_test), "contrast_tracked_minus_control": {}}
    for s, sel in _by_split(primary, include_test).items():
        sig["contrast_tracked_minus_control"][s] = ({"status": "withheld"} if sel is None else
                                                    welch(_ret([r for r in sel if r["tracked_intent"]]),
                                                          _ret([r for r in sel if not r["tracked_intent"]]), ms))
    b["donchian_signal"] = sig

    pop = "the strategy's own selection (tracked_intent) only"
    b["donchian_x_regime"] = {"population": pop, "stratum_rule": "regime_state when the market snapshot was usable, else '<market state>'",
                              "strata": _stratified(tracked, regime_stratum, ms, include_test),
                              "availability": {s: _hist_of(tracked, s, "market__state", include_test) for s in SPLITS}}
    b["donchian_x_rs"] = {"population": pop,
                          "stratum_rule": "fixed terciles of rs_percentile (0..100): " + ", ".join(f"{n}" for n, _, _ in RS_BUCKETS)
                                          + "; '<state>' when unavailable",
                          "strata": _stratified(tracked, rs_stratum, ms, include_test),
                          "availability": {s: _hist_of(tracked, s, "rs__state", include_test) for s in SPLITS}}
    b["donchian_x_catalyst"] = {"population": pop,
                                "stratum_rule": "catalyst state: catalyst_known | event_unclassified | none_observed | unknown_availability",
                                "strata": _stratified(tracked, catalyst_stratum, ms, include_test),
                                "availability": {s: _hist_of(tracked, s, "catalyst__state", include_test) for s in SPLITS},
                                "caveat": "none_observed is the absence of a usable classified event, not proof of no catalyst"}
    out["n_comparisons"] = _count_blocks(b)
    return out


def _hist_of(rows: Sequence[Mapping[str, Any]], split: str, col: str, include_test: bool) -> Any:
    if split == "test" and not include_test:
        return {"status": "withheld"}
    h: Dict[str, int] = {}
    for r in rows:
        if r["split"] == split:
            h[str(r[col])] = h.get(str(r[col]), 0) + 1
    return dict(sorted(h.items()))


def _count_blocks(o: Any) -> int:
    if isinstance(o, Mapping):
        return (1 if "directional_return" in o and "n_rows" in o else 0) + sum(_count_blocks(v) for v in o.values())
    if isinstance(o, list):
        return sum(_count_blocks(v) for v in o)
    return 0
