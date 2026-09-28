#!/usr/bin/env python3
# mechanism/screeners/evaluate_signal_ledger.py
"""
Daily job: walk every still-open row of `signal_ledger` (mechanism/add_signal_ledger_tables.sql)
forward against the latest stock_prices bars and resolve it to a real result once one is known.

Resolution model (deliberately simpler than ml_training/features/price_features.py::plan_outcomes()'s
3-tranche average -- see mechanism/shared/trade_plan.py's docstring for why the two stay separate):
one row resolves at the FIRST bar where either its stop or any target price is touched, taking the
highest target level reached that bar if more than one is crossed in a single bar, with the stop
winning any same-bar tie (identical tie-break rule to plan_outcomes(), pinned by
mechanism/shared/tests/test_trade_plan.py). If PLAN_HORIZON_BARS bars pass with nothing touched, the
row resolves 'expired' at its mark-to-market R. This gives the track record a single, honest
per-signal outcome ("stopped" / "target1/2/3" / "expired" / still "open") rather than a continuous
per-tranche score -- which is what a disclosed track record needs to show back to a user, per the
2026-09-27/28 monetization strategy deck.

Freshness: no separate coverage-threshold gate (unlike check_price_freshness.py, which exists to
decide whether an IRREVERSIBLE Telegram send should happen on a specific session). This job just
uses whatever stock_prices actually has as of MAX(date) -- if the daily price update hasn't landed
yet, no new bars exist since a row's signal_date, so nothing advances and the row is picked up again,
correctly, next run. Idempotent: a row already resolved (status != 'open') is never touched again,
and every UPDATE re-guards WHERE status = 'open' so two overlapping runs can't double-resolve one.

    python mechanism/screeners/evaluate_signal_ledger.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared import db, setup_logging  # noqa: E402
from shared.trade_plan import PLAN_HORIZON_BARS  # noqa: E402

logger = setup_logging("evaluate_signal_ledger")

OPEN_ROWS_SQL = """
    SELECT id, symbol, signal_date, direction, entry_price, stop_price,
           target1_price, target2_price, target3_price
    FROM signal_ledger
    WHERE status = 'open' AND last_evaluated_date < %s
"""

BARS_SQL = """
    SELECT date, high, low, close FROM stock_prices
    WHERE symbol = %s AND date > %s AND date <= %s
    ORDER BY date ASC
    LIMIT %s
"""

RESOLVE_SQL = """
    UPDATE signal_ledger
    SET status = %(status)s, outcome_r = %(outcome_r)s, mae_r = %(mae_r)s,
        resolved_date = %(resolved_date)s, bars_held = %(bars_held)s,
        last_evaluated_date = %(last_evaluated_date)s
    WHERE id = %(id)s AND status = 'open'
"""

STILL_OPEN_SQL = """
    UPDATE signal_ledger
    SET mae_r = %(mae_r)s, last_evaluated_date = %(last_evaluated_date)s
    WHERE id = %(id)s AND status = 'open'
"""


def _resolve(direction: int, entry_price: float, stop_price: float, target_prices: List[float],
             bars: List[Tuple[date, float, float, float]]) -> Tuple[Optional[str], Optional[float],
                                                                       Optional[date], Optional[int], float]:
    """Returns (status, outcome_r, resolved_date, bars_held, mae_r). status is None (still open,
    but mae_r may have moved) when nothing resolves within the given bars."""
    r = abs(entry_price - stop_price)
    mae_r = 0.0
    for i, (bar_date, high, low, close) in enumerate(bars, start=1):
        adverse = (entry_price - low) if direction == 1 else (high - entry_price)
        mae_r = max(mae_r, adverse / r)

        stop_touched = (low <= stop_price) if direction == 1 else (high >= stop_price)
        if stop_touched:
            return "stopped", -1.0, bar_date, i, mae_r

        # Highest target level touched THIS bar, if any (a big enough bar can clear more than one).
        hit_level = 0
        for lvl, tp in enumerate(target_prices, start=1):
            touched = (high >= tp) if direction == 1 else (low <= tp)
            if touched:
                hit_level = lvl
        if hit_level:
            return f"target{hit_level}", float(hit_level), bar_date, i, mae_r

    if len(bars) >= PLAN_HORIZON_BARS:
        last_date, _, _, last_close = bars[-1]
        outcome_r = direction * (last_close - entry_price) / r
        return "expired", outcome_r, last_date, len(bars), mae_r

    return None, None, None, None, mae_r  # not enough bars yet -- stays open


def run(latest_session: date) -> Dict[str, int]:
    open_rows = db.execute_dict_query(OPEN_ROWS_SQL, (latest_session,))
    counts = {"resolved": 0, "advanced": 0, "unchanged": 0}

    for row in open_rows:
        bars = db.execute_dict_query(
            BARS_SQL, (row["symbol"], row["signal_date"], latest_session, PLAN_HORIZON_BARS))
        if not bars:
            counts["unchanged"] += 1
            continue

        bar_tuples = [(b["date"], float(b["high"]), float(b["low"]), float(b["close"])) for b in bars]
        target_prices = [float(row["target1_price"]), float(row["target2_price"]), float(row["target3_price"])]
        status, outcome_r, resolved_date, bars_held, mae_r = _resolve(
            row["direction"], float(row["entry_price"]), float(row["stop_price"]), target_prices, bar_tuples)

        if status is not None:
            db.execute_insert(RESOLVE_SQL, {
                "id": row["id"], "status": status, "outcome_r": outcome_r, "mae_r": mae_r,
                "resolved_date": resolved_date, "bars_held": bars_held, "last_evaluated_date": latest_session,
            })
            counts["resolved"] += 1
        else:
            db.execute_insert(STILL_OPEN_SQL, {"id": row["id"], "mae_r": mae_r, "last_evaluated_date": latest_session})
            counts["advanced"] += 1

    logger.info(f"evaluate_signal_ledger: {len(open_rows)} open rows -> "
                f"{counts['resolved']} resolved, {counts['advanced']} advanced, {counts['unchanged']} unchanged")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.parse_args()

    rows = db.execute_query("SELECT MAX(date) FROM stock_prices")
    latest_session = rows[0][0] if rows else None
    if latest_session is None:
        logger.warning("evaluate_signal_ledger: stock_prices is empty, nothing to evaluate")
        return 0

    run(latest_session)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
