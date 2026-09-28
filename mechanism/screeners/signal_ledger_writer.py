# mechanism/screeners/signal_ledger_writer.py
"""
Writes today's actual breakout signals into `signal_ledger` (mechanism/add_signal_ledger_tables.sql)
so mechanism/screeners/evaluate_signal_ledger.py has something to track forward to a real result.

Called from multi_timeframe_screener.py's screen_all_symbols() immediately AFTER save_results() --
the existing JSON output path (breakout_results/*.json, frontend_data/latest_*.json) is written
first and is never affected by anything here. The caller wraps this in a try/except that never
re-raises, per this repo's safety invariant that the post-market Telegram package (which runs
BEFORE the screener step in automation_pipeline.sh, so is structurally unaffected either way)
and the screener's own JSON output must never depend on this table existing or being healthy.

Only real bullish/bearish breakouts get a row -- near-breakout signals ('near_bullish',
'near_bearish') aren't trade plans and have nothing to evaluate forward.

Every signal is stamped with a strategy identity (`strategy_id`/`strategy_version`, denormalized
onto the row -- see add_strategy_identity_release_a.sql) and, when the screener already computed
one, the serving `model_version` (Release A does not yet write a feature snapshot, so
`feature_set_version`/`observation_id`/`feature_snapshot_id` stay NULL until Release B).

Duplicate-signal policy is TWO separate invariants, not one (2026-09-28 correction -- the original
single UNIQUE(symbol, signal_date, direction) only ever enforced the first of these):

  EVENT IDEMPOTENCY  -- INSERT ... ON CONFLICT (symbol, strategy_id, direction, signal_date)
                        DO UPDATE, never SELECT-then-INSERT (same discipline as
                        mechanism/alerts/post_delivery.py). Its WHERE guard only lets a same-day
                        re-run refresh a row's levels while the evaluator hasn't yet advanced it
                        past its entry day.

  POSITION INVARIANT -- at most one OPEN row per (symbol, strategy_id, direction) regardless of
                        which day each signal fired on. Checked here (a cheap SELECT before
                        attempting the insert, so a routine "still open" case never even reaches
                        the database's own enforcement) AND enforced by a partial unique index in
                        the schema itself (idx_signal_ledger_one_open_position) as the real,
                        race-safe guarantee -- this app-level check is an optimization, not the
                        source of truth.

last_evaluated_date is set explicitly to signal_date on INSERT, never left to the column's
DEFAULT CURRENT_DATE -- a write that happens after midnight Israel time for a session that closed
the evening before (the same rollover post_delivery.py's module docstring warns about) would
otherwise get a last_evaluated_date one calendar day ahead of signal_date, breaking the WHERE
guard above on the very first write.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from shared.trade_plan import compute_levels

logger = logging.getLogger(__name__)

TRACKED_SIGNAL_TYPES = {"bullish_breakout": 1, "bearish_breakout": -1}

DEFAULT_STRATEGY_KEY = "donchian_breakout"
DEFAULT_STRATEGY_VERSION = "v1"

OPEN_POSITION_EXISTS_SQL = """
    SELECT 1 FROM signal_ledger
    WHERE symbol = %s AND strategy_id = %s AND direction = %s AND status = 'open'
      AND signal_date != %s
    LIMIT 1
"""

UPSERT_SQL = """
    INSERT INTO signal_ledger
        (symbol, signal_date, direction, entry_price, atr,
         stop_price, target1_price, target2_price, target3_price,
         sector, quality_grade, last_evaluated_date,
         strategy_id, strategy_version, model_version)
    VALUES
        (%(symbol)s, %(signal_date)s, %(direction)s, %(entry_price)s, %(atr)s,
         %(stop_price)s, %(target1_price)s, %(target2_price)s, %(target3_price)s,
         %(sector)s, %(quality_grade)s, %(signal_date)s,
         %(strategy_id)s, %(strategy_version)s, %(model_version)s)
    ON CONFLICT (symbol, strategy_id, direction, signal_date) DO UPDATE SET
        entry_price = EXCLUDED.entry_price,
        atr = EXCLUDED.atr,
        stop_price = EXCLUDED.stop_price,
        target1_price = EXCLUDED.target1_price,
        target2_price = EXCLUDED.target2_price,
        target3_price = EXCLUDED.target3_price,
        sector = EXCLUDED.sector,
        quality_grade = EXCLUDED.quality_grade,
        model_version = EXCLUDED.model_version
    WHERE signal_ledger.status = 'open'
      AND signal_ledger.last_evaluated_date = signal_ledger.signal_date
"""


def _resolve_default_strategy(db) -> Tuple[int, str]:
    """Looks up the single seeded strategy row (id, strategy_version). Release A ships exactly
    one strategy; per-signal strategy selection is a later release's concern."""
    rows = db.execute_dict_query(
        "SELECT id, strategy_version FROM strategies WHERE strategy_key = %s AND strategy_version = %s",
        (DEFAULT_STRATEGY_KEY, DEFAULT_STRATEGY_VERSION))
    if not rows:
        raise RuntimeError(
            f"strategies row ({DEFAULT_STRATEGY_KEY}, {DEFAULT_STRATEGY_VERSION}) is missing -- "
            "has mechanism/add_strategy_identity_release_a.sql been applied?")
    return rows[0]["id"], rows[0]["strategy_version"]


def _has_open_position(db, symbol: str, strategy_id: int, direction: int, session_date: date) -> bool:
    """True if this symbol/strategy/direction already has an open row from a DIFFERENT (earlier)
    session -- excludes today's own date so a same-day re-run still reaches the upsert's refresh
    path (ON CONFLICT ... DO UPDATE) instead of being mistaken for the position invariant."""
    return bool(db.execute_dict_query(
        OPEN_POSITION_EXISTS_SQL, (symbol, strategy_id, direction, session_date)))


def write_todays_signals(db, signals: List[Dict], session_date: date) -> int:
    """Upsert one signal_ledger row per real breakout in `signals` for `session_date`.
    Returns the number of rows attempted (not necessarily changed -- a same-day re-run
    after the evaluator has already advanced a row is a deliberate no-op, not an error;
    a signal for a symbol/direction that already has an open position is also skipped,
    not an error -- see the position-invariant note above).
    Never raises: a single bad signal is logged and skipped, not fatal to the batch."""
    try:
        strategy_id, strategy_version = _resolve_default_strategy(db)
    except Exception as e:  # noqa: BLE001 -- the whole batch is meaningless without a strategy row
        logger.error(f"signal_ledger_writer: cannot resolve default strategy, skipping batch: {e}")
        return 0

    written = 0
    for signal in signals:
        direction = TRACKED_SIGNAL_TYPES.get(signal.get("signal_type"))
        if direction is None:
            continue  # near-breakout or unrecognized -- not a trade plan, nothing to track

        symbol = signal.get("symbol")
        try:
            if _has_open_position(db, symbol, strategy_id, direction, session_date):
                continue  # position invariant: this symbol/strategy/direction is already open

            entry_price = float(signal["current_price"])
            atr = float(signal["atr_14"])
            levels = compute_levels(entry_price, atr, direction)
            model_version: Optional[str] = signal.get("ml_model_version")
            params = {
                "symbol": symbol,
                "signal_date": session_date,
                "direction": direction,
                "entry_price": entry_price,
                "atr": atr,
                "sector": signal.get("sector"),
                "quality_grade": signal.get("quality_grade"),
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "model_version": model_version,
                **levels,
            }
            db.execute_insert(UPSERT_SQL, params)
            written += 1
        except Exception as e:  # noqa: BLE001 -- one bad row must never abort the batch
            logger.error(f"signal_ledger_writer: failed to write {symbol} on {session_date}: {e}")

    logger.info(f"signal_ledger_writer: wrote {written}/{len(signals)} signals for {session_date}")
    return written
