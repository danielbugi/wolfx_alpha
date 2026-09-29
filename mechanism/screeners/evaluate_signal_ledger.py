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

Flags (mechanism/add_signal_ledger_eval_flags.sql -- two columns, two different states):

  resolution_flag='same_bar_stop_and_target' -- set on a row that resolved 'stopped' on a bar that
      ALSO touched a target. Daily OHLC can't say which came first; the conservative stop result is
      kept (and is what the public track record shows), the flag records that it was a convention,
      not an observation. Unambiguous resolutions leave it NULL.

  evaluation_flag='split_suspect' -- set, and the row deliberately NOT resolved, when a
      close-to-close step on the row's evaluation path (signal-day close = entry_price, then each
      forward bar) matches alerts/price_guard.is_split_like(), the repo's one shared split rule.
      The stored entry/stop/targets may no longer share stock_prices' price scale (the vendor feeds
      are split-adjusted and repopulate_from_tiingo.py can restate history in place), so resolving
      across that step could fabricate an outcome. Detect -> hold -> review; never adjust or guess a
      factor here. Only the path up to the bar being evaluated counts: a row whose stop/target was
      reached BEFORE a later jump resolves normally on that earlier bar.

  Lifecycle of a held row: the run that flags it logs ONE warning and sets
  last_evaluated_date (= when the hold began). Every later run skips it (OPEN_ROWS_SQL excludes
  evaluation_flag IS NOT NULL) and only reports the held total in its single summary line -- no
  per-row repeat warnings. It stays status='open', so it keeps occupying its (symbol, strategy,
  direction) open-position slot and new signals for that slot are skipped by the writer until it is
  reviewed -- another reason Data Health must surface held rows. After manual review,
  `--clear-evaluation-flag <id>` clears it (guarded: only an open row whose flag is set); the next run
  re-evaluates from signal_date as usual, and re-flags it if the same jump is still in the stored
  series. Release A has no "accept this jump" or level-adjustment path: a genuine split or a real
  ~50%/~3x move stays held. A future corporate-action workflow would restate levels, then clear.

Invalid bars: a bar whose high/low/close is NULL, non-finite or <= 0, or that is internally
inconsistent (low > high, close outside [low, high]), is never evaluated. The walk stops at it --
nothing from that bar or any later bar is used, since skipping it could hide the day the stop or a
target was really hit -- and the row gets NO write at all (not even mae_r/last_evaluated_date), with
a warning naming symbol, date and reason. No flag is persisted: the condition lives in stock_prices
itself, so every run re-checks it (and warns again) until the bar is corrected, and Data Health can
derive it from stock_prices directly. Production had zero such bars in 2026 when this was added.

Missing/stale data: no separate coverage-threshold gate (unlike check_price_freshness.py, which
exists to decide whether an IRREVERSIBLE Telegram send should happen on a specific session). This job
uses whatever stock_prices has as of MAX(date) -- a symbol with no bar after its signal_date (late
data, gap, delisting) simply isn't advanced ("unchanged") and is replayed from signal_date next run.
Idempotent: every write re-guards WHERE status='open' AND evaluation_flag IS NULL, so a resolved or
held row is never touched again and two overlapping runs can't double-resolve or double-flag one.

    python mechanism/screeners/evaluate_signal_ledger.py
    python mechanism/screeners/evaluate_signal_ledger.py --clear-evaluation-flag 1234
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from datetime import date
from typing import Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from alerts.price_guard import is_split_like  # noqa: E402
from shared import db, setup_logging  # noqa: E402
from shared.trade_plan import PLAN_HORIZON_BARS  # noqa: E402

logger = setup_logging("evaluate_signal_ledger")

SAME_BAR_STOP_AND_TARGET = "same_bar_stop_and_target"
SPLIT_SUSPECT = "split_suspect"

OPEN_ROWS_SQL = """
    SELECT id, symbol, signal_date, direction, entry_price, stop_price,
           target1_price, target2_price, target3_price
    FROM signal_ledger
    WHERE status = 'open' AND evaluation_flag IS NULL AND last_evaluated_date < %s
"""

HELD_COUNT_SQL = """
    SELECT count(*) AS n FROM signal_ledger WHERE status = 'open' AND evaluation_flag IS NOT NULL
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
        resolution_flag = %(resolution_flag)s, last_evaluated_date = %(last_evaluated_date)s
    WHERE id = %(id)s AND status = 'open' AND evaluation_flag IS NULL
"""

STILL_OPEN_SQL = """
    UPDATE signal_ledger
    SET mae_r = %(mae_r)s, last_evaluated_date = %(last_evaluated_date)s
    WHERE id = %(id)s AND status = 'open' AND evaluation_flag IS NULL
"""

HOLD_SQL = """
    UPDATE signal_ledger
    SET evaluation_flag = %(evaluation_flag)s, mae_r = %(mae_r)s, last_evaluated_date = %(last_evaluated_date)s
    WHERE id = %(id)s AND status = 'open' AND evaluation_flag IS NULL
"""

CLEAR_FLAG_SQL = """
    UPDATE signal_ledger SET evaluation_flag = NULL
    WHERE id = %s AND status = 'open' AND evaluation_flag IS NOT NULL
"""


class Resolution(NamedTuple):
    status: Optional[str]              # None = still open (or held, when evaluation_flag is set)
    outcome_r: Optional[float]
    resolved_date: Optional[date]
    bars_held: Optional[int]
    mae_r: float
    resolution_flag: Optional[str] = None
    evaluation_flag: Optional[str] = None
    flag_date: Optional[date] = None   # the bar that tripped the split rule or failed validation
    invalid_reason: Optional[str] = None  # set when the walk stopped at an invalid bar (never persisted)


def _invalid_bar_reason(high: Optional[float], low: Optional[float], close: Optional[float]) -> Optional[str]:
    for name, v in (("high", high), ("low", low), ("close", close)):
        if v is None:
            return f"{name} is NULL"
        if not math.isfinite(v):
            return f"{name} is not finite ({v})"
        if v <= 0:
            return f"{name} is non-positive ({v})"
    if low > high:
        return f"low {low} > high {high}"
    if not low <= close <= high:
        return f"close {close} outside [low {low}, high {high}]"
    return None


def _resolve(direction: int, entry_price: float, stop_price: float, target_prices: List[float],
             bars: List[Tuple[date, Optional[float], Optional[float], Optional[float]]]) -> Resolution:
    r = abs(entry_price - stop_price)
    mae_r = 0.0
    prev_close = entry_price
    for i, (bar_date, high, low, close) in enumerate(bars, start=1):
        reason = _invalid_bar_reason(high, low, close)
        if reason is not None:
            return Resolution(None, None, None, None, mae_r, flag_date=bar_date, invalid_reason=reason)

        # Checked before this bar is trusted for anything: across a split-shaped step the stored
        # levels and this bar may not be in the same price scale.
        if prev_close > 0 and is_split_like(close / prev_close):
            return Resolution(None, None, None, None, mae_r, evaluation_flag=SPLIT_SUSPECT, flag_date=bar_date)
        prev_close = close

        adverse = (entry_price - low) if direction == 1 else (high - entry_price)
        mae_r = max(mae_r, adverse / r)

        # Highest target level touched THIS bar, if any (a big enough bar can clear more than one).
        hit_level = 0
        for lvl, tp in enumerate(target_prices, start=1):
            touched = (high >= tp) if direction == 1 else (low <= tp)
            if touched:
                hit_level = lvl

        stop_touched = (low <= stop_price) if direction == 1 else (high >= stop_price)
        if stop_touched:
            flag = SAME_BAR_STOP_AND_TARGET if hit_level else None
            return Resolution("stopped", -1.0, bar_date, i, mae_r, resolution_flag=flag)
        if hit_level:
            return Resolution(f"target{hit_level}", float(hit_level), bar_date, i, mae_r)

    if len(bars) >= PLAN_HORIZON_BARS:
        last_date, _, _, last_close = bars[-1]
        outcome_r = direction * (last_close - entry_price) / r
        return Resolution("expired", outcome_r, last_date, len(bars), mae_r)

    return Resolution(None, None, None, None, mae_r)  # not enough bars yet -- stays open


def _guarded_update(sql: str, params) -> int:
    """Run one guarded UPDATE and return how many rows it actually changed -- 0 means another run
    already resolved/held the row, which is how the counts and warnings stay exact under overlap."""
    with db.get_sync_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount


def run(latest_session: date) -> Dict[str, int]:
    open_rows = db.execute_dict_query(OPEN_ROWS_SQL, (latest_session,))
    counts = {"resolved": 0, "advanced": 0, "unchanged": 0, "flagged": 0, "same_bar": 0, "invalid_blocked": 0}

    def _num(v):
        return None if v is None else float(v)

    for row in open_rows:
        bars = db.execute_dict_query(
            BARS_SQL, (row["symbol"], row["signal_date"], latest_session, PLAN_HORIZON_BARS))
        if not bars:
            counts["unchanged"] += 1
            continue

        bar_tuples = [(b["date"], _num(b["high"]), _num(b["low"]), _num(b["close"])) for b in bars]
        target_prices = [float(row["target1_price"]), float(row["target2_price"]), float(row["target3_price"])]
        res = _resolve(row["direction"], float(row["entry_price"]), float(row["stop_price"]),
                       target_prices, bar_tuples)

        if res.invalid_reason is not None:
            counts["invalid_blocked"] += 1
            logger.warning(
                f"evaluate_signal_ledger: INVALID BAR id={row['id']} {row['symbol']} {res.flag_date}: "
                f"{res.invalid_reason} -- not evaluated past this bar, row left open and unchanged")
        elif res.evaluation_flag is not None:
            if _guarded_update(HOLD_SQL, {"id": row["id"], "evaluation_flag": res.evaluation_flag,
                                          "mae_r": res.mae_r, "last_evaluated_date": latest_session}):
                counts["flagged"] += 1
                logger.warning(
                    f"evaluate_signal_ledger: HOLD id={row['id']} {row['symbol']} (signal {row['signal_date']}) "
                    f"evaluation_flag={res.evaluation_flag} -- split-like close-to-close step on {res.flag_date}; "
                    f"left open and excluded from evaluation until reviewed (--clear-evaluation-flag {row['id']})")
        elif res.status is not None:
            if _guarded_update(RESOLVE_SQL, {
                    "id": row["id"], "status": res.status, "outcome_r": res.outcome_r, "mae_r": res.mae_r,
                    "resolved_date": res.resolved_date, "bars_held": res.bars_held,
                    "resolution_flag": res.resolution_flag, "last_evaluated_date": latest_session}):
                counts["resolved"] += 1
                if res.resolution_flag == SAME_BAR_STOP_AND_TARGET:
                    counts["same_bar"] += 1
        else:
            if _guarded_update(STILL_OPEN_SQL, {"id": row["id"], "mae_r": res.mae_r,
                                                "last_evaluated_date": latest_session}):
                counts["advanced"] += 1

    counts["held"] = int(db.execute_dict_query(HELD_COUNT_SQL)[0]["n"])
    logger.info(f"evaluate_signal_ledger: session {latest_session}: {len(open_rows)} open rows examined -> "
                f"{counts['resolved']} resolved ({counts['same_bar']} same-bar stop+target), "
                f"{counts['advanced']} advanced, {counts['unchanged']} unchanged (no new bar), "
                f"{counts['invalid_blocked']} blocked by an invalid bar, "
                f"{counts['flagged']} newly held; {counts['held']} held in total (evaluation_flag set)")
    return counts


def clear_evaluation_flag(ledger_id: int) -> bool:
    """The one controlled path out of a hold, run by a person after reviewing the row. Only clears an
    open row that actually carries a flag; returns False (and changes nothing) otherwise."""
    cleared = _guarded_update(CLEAR_FLAG_SQL, (ledger_id,)) == 1
    if cleared:
        logger.warning(f"evaluate_signal_ledger: evaluation_flag CLEARED on id={ledger_id} by manual review; "
                       f"the next run re-evaluates it from signal_date (and re-holds it if the jump is still there)")
    else:
        logger.warning(f"evaluate_signal_ledger: nothing cleared -- id={ledger_id} is not an open row with an "
                       f"evaluation_flag")
    return cleared


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--clear-evaluation-flag", type=int, metavar="LEDGER_ID",
                    help="after manual review, clear evaluation_flag on this open row and exit (no evaluation run)")
    args = ap.parse_args()

    if args.clear_evaluation_flag is not None:
        return 0 if clear_evaluation_flag(args.clear_evaluation_flag) else 1

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
