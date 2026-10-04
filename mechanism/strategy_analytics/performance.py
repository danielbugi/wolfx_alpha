# mechanism/strategy_analytics/performance.py
"""
Strategy-generic performance engine: conditional breakdowns of any strategy's resolved signals.

Input is a strategy row from analytics.get_strategy() (id, strategy_key, strategy_version); nothing here knows about
Donchian. Output follows analytics.py's metric convention (value + n + state) and is read-only.

A breakdown dimension is either computable from columns signal_ledger really stores (every one is frozen at signal time,
or is the outcome -- never an attribute looked up later), or it is declared unavailable with the data it would need.
An unavailable dimension returns state not_available; it is never approximated or reconstructed.
"""
from __future__ import annotations

from typing import Any, Dict, List

from strategy_analytics.analytics import Fetch, _clean
from strategy_analytics.definitions import (
    DEFINITIONS_VERSION, LEGACY_UNSCORED_MODEL_VERSIONS, STATE_NOT_AVAILABLE, metric, not_available, ratio,
)

MAX_GROUPS = 200

# Whitelisted SQL expressions over signal_ledger columns. The key is the only thing a caller can choose.
DIMENSIONS: Dict[str, Dict[str, Any]] = {
    "direction": {"sql": "CASE direction WHEN 1 THEN 'bullish' ELSE 'bearish' END",
                  "label": "Direction", "source": "signal_ledger.direction"},
    "exit": {"sql": "status", "label": "Exit type", "source": "signal_ledger.status"},
    "quality_grade": {"sql": "COALESCE(quality_grade, 'ungraded')", "label": "Quality grade (at signal time)",
                      "source": "signal_ledger.quality_grade"},
    "sector": {"sql": "COALESCE(sector, 'unclassified')", "label": "Sector (at signal time)",
               "source": "signal_ledger.sector"},
    "signal_month": {"sql": "to_char(signal_date, 'YYYY-MM')", "label": "Signal month",
                     "source": "signal_ledger.signal_date"},
    "holding_bars": {"sql": "CASE WHEN bars_held IS NULL THEN 'unresolved' WHEN bars_held <= 5 THEN '01-05' "
                            "WHEN bars_held <= 10 THEN '06-10' WHEN bars_held <= 15 THEN '11-15' ELSE '16+' END",
                     "label": "Holding period (trading bars)", "source": "signal_ledger.bars_held"},
    "model_scored": {"sql": "CASE WHEN model_version IS NULL OR lower(btrim(model_version)) = ANY(%(unscored)s) "
                            "THEN 'unscored' ELSE 'scored' END",
                     "label": "Scored by a validated model", "source": "signal_ledger.model_version"},
}

# Dimensions the product wants that the ledger cannot support. Each names what is missing.
UNAVAILABLE_DIMENSIONS: Dict[str, Dict[str, str]] = {
    "market_regime": {"label": "Market regime at signal",
                      "requires": "market_snapshot rows (migration 24) joined by session, observed provenance only"},
    "volatility_regime": {"label": "Volatility regime at signal", "requires": "market_snapshot (migration 24)"},
    "relative_strength": {"label": "Relative strength at signal",
                          "requires": "sector_snapshot / feature_snapshot (migrations 22, 24)"},
    "earnings_proximity": {"label": "Earnings proximity",
                           "requires": "market_event (migration 25) with a known_at earlier than the signal"},
    "catalyst": {"label": "Catalyst type", "requires": "a versioned catalyst classification of PIT-safe events"},
    "ml_score_bucket": {"label": "ML score bucket",
                        "requires": "the score itself -- signal_ledger stores only the model version, not the probability"},
}

# Metrics the ledger cannot support at all, whatever the grouping.
UNAVAILABLE_METRICS: Dict[str, Dict[str, str]] = {
    "max_favourable_excursion": {"reason": "MFE is not stored; needs the forward path (fwd_v1 labels)"},
    "post_exit_trajectory": {"reason": "the ledger stops tracking price at exit"},
    "per_row_expiry_horizon": {"reason": "the expiry horizon is a strategy constant, not stored on each row"},
    "drawdown_in_r": {"reason": "needs a portfolio/sequencing rule; it is not a property of independent signals"},
}

# Declared exit rules per (strategy_key, strategy_version). A strategy with no declaration reports not_available rather
# than inheriting another strategy's rules. Pinned to mechanism/shared/trade_plan.py by a drift test.
EXIT_RULES: Dict[tuple, Dict[str, Any]] = {
    ("donchian_breakout", "v1"): {"stop_atr_mult": 2.0, "target_atr_mults": (2.0, 4.0, 6.0), "expiry_bars": 20,
                                  "r_unit": "stop distance = 2 x ATR", "source": "mechanism/shared/trade_plan.py"},
}


def exit_rules(strategy: Dict[str, Any]) -> Dict[str, Any]:
    rules = EXIT_RULES.get((strategy.get("strategy_key"), strategy.get("strategy_version")))
    if rules is None:
        return not_available("no exit rules are declared for this strategy version")
    return {"state": "declared", **rules}


def contract() -> Dict[str, Any]:
    """What a consumer may request and what it will never get. Static: no I/O."""
    return {
        "definitions_version": DEFINITIONS_VERSION,
        "dimensions": {k: {"label": v["label"], "source": v["source"], "available": True}
                       for k, v in DIMENSIONS.items()},
        "unavailable_dimensions": {k: {"label": v["label"], "available": False, "state": STATE_NOT_AVAILABLE,
                                       "requires": v["requires"]} for k, v in UNAVAILABLE_DIMENSIONS.items()},
        "unavailable_metrics": {k: {"state": STATE_NOT_AVAILABLE, **v} for k, v in UNAVAILABLE_METRICS.items()},
        "metrics": ["signals", "open", "held", "resolved", "winners", "win_rate", "average_r", "median_r", "sum_r",
                    "profit_factor", "average_holding_bars", "average_mae_r"],
    }


BREAKDOWN_SQL = """
    SELECT {expr} AS bucket,
        count(*) AS signals,
        count(*) FILTER (WHERE status = 'open' AND evaluation_flag IS NULL) AS open_normal,
        count(*) FILTER (WHERE status = 'open' AND evaluation_flag IS NOT NULL) AS held,
        count(*) FILTER (WHERE status <> 'open') AS resolved,
        count(*) FILTER (WHERE status IN ('target1', 'target2', 'target3')) AS winners,
        avg(outcome_r) FILTER (WHERE status <> 'open') AS avg_r,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY outcome_r) FILTER (WHERE status <> 'open') AS median_r,
        sum(outcome_r) FILTER (WHERE status <> 'open') AS sum_r,
        sum(outcome_r) FILTER (WHERE status <> 'open' AND outcome_r > 0) AS gross_win_r,
        sum(-outcome_r) FILTER (WHERE status <> 'open' AND outcome_r < 0) AS gross_loss_r,
        avg(bars_held) FILTER (WHERE status <> 'open') AS avg_bars,
        avg(mae_r) FILTER (WHERE status <> 'open') AS avg_mae_r
    FROM signal_ledger
    WHERE strategy_id = %(sid)s
    GROUP BY 1
    ORDER BY 1
    LIMIT %(limit)s
"""


def _profit_factor(row: Dict[str, Any]) -> Dict[str, Any]:
    n = int(row.get("resolved") or 0)
    win, loss = row.get("gross_win_r"), row.get("gross_loss_r")
    if n == 0:
        return metric(None, 0)
    if not loss:
        return not_available("no losing resolved signal in this group: profit factor is undefined, not infinite")
    return metric(float(win or 0) / float(loss), n, 3)


def _group(row: Dict[str, Any]) -> Dict[str, Any]:
    n = int(row["resolved"] or 0)
    winners = int(row["winners"] or 0)
    return {
        "bucket": row["bucket"],
        "signals": int(row["signals"]),
        "open": int(row["open_normal"]),
        "held": int(row["held"]),
        "resolved": n,
        "winners": winners,
        "win_rate": ratio(winners, n),
        "average_r": metric(row["avg_r"], n, 3),
        "median_r": metric(row["median_r"], n, 3),
        "sum_r": metric(row["sum_r"], n, 3),
        "profit_factor": _profit_factor(row),
        "average_holding_bars": metric(row["avg_bars"], n, 1),
        "average_mae_r": metric(row["avg_mae_r"], n, 3),
    }


def breakdown(fetch: Fetch, strategy: Dict[str, Any], dimension: str) -> Dict[str, Any]:
    """Resolved-signal performance grouped by one whitelisted dimension. An unavailable dimension returns its
    not_available block and runs no query; an unknown one is a ValueError (the caller's mistake, not a data state)."""
    if dimension in UNAVAILABLE_DIMENSIONS:
        spec = UNAVAILABLE_DIMENSIONS[dimension]
        return {"strategy_id": strategy["id"], "dimension": dimension, "label": spec["label"],
                "state": STATE_NOT_AVAILABLE, "requires": spec["requires"], "groups": []}
    spec = DIMENSIONS.get(dimension)
    if spec is None:
        raise ValueError(f"unknown dimension: {dimension!r}")
    rows = fetch(BREAKDOWN_SQL.format(expr=spec["sql"]),
                 {"sid": strategy["id"], "limit": MAX_GROUPS, "unscored": list(LEGACY_UNSCORED_MODEL_VERSIONS)})
    groups: List[Dict[str, Any]] = [_group(_clean(r)) for r in rows]
    return {"strategy_id": strategy["id"], "dimension": dimension, "label": spec["label"],
            "state": "ok" if groups else "no_data", "definitions_version": DEFINITIONS_VERSION,
            "groups": groups, "truncated": len(groups) >= MAX_GROUPS}
