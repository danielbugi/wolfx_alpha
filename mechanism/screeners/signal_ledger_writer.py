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
one, the `model_version` of the model that actually scored the signal (NULL when no validated model did --
shared/model_provenance.py; never a placeholder like 'unknown'). When the research observer captured the candidate
(mechanism/research/observer.py), the row also carries the lineage -- `observation_id`,
`feature_snapshot_id`, `feature_set_version` -- passed in as `links`; with no link (capture off or failed)
those stay NULL exactly as before, and the ledger write is never conditional on them.

A signal whose ATR was not real (the screener filled `atr = 2% of price`, flagged in `screener_defaults`)
is REFUSED, not written: the stop and all three targets derive from ATR, so a fabricated ATR would put an
invented trade plan into the track record.

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
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Mapping, Optional, Tuple

from shared.model_provenance import scoring_model_version
from shared.session_integrity import partition_by_session, summarize_rejections, to_date
from shared.trade_plan import compute_levels

logger = logging.getLogger(__name__)

TRACKED_SIGNAL_TYPES = {"bullish_breakout": 1, "bearish_breakout": -1}

DEFAULT_STRATEGY_KEY = "donchian_breakout"
DEFAULT_STRATEGY_VERSION = "v1"



@dataclass(frozen=True)
class StrategyRef:
    id: int
    key: str
    version: str


@dataclass(frozen=True)
class ObservationLink:
    observation_id: int
    feature_snapshot_id: int
    feature_set_version: str


Links = Mapping[Tuple[str, int], ObservationLink]  # (symbol, direction) -> lineage of that candidate


def ledger_ineligibility(signal: Dict, session_date: date) -> Optional[str]:
    """None when the ledger writer would take this signal (before the stateful open-position check), else the
    reason it would not. The ONE predicate shared with the research observer's `tracked_intent`, so the two can
    never disagree about what the track record is built from."""
    if signal.get("signal_type") not in TRACKED_SIGNAL_TYPES:
        return "not_a_tracked_type"
    if to_date(signal.get("screening_date")) != session_date:
        return "not_the_session_bar"
    if "atr_14" in (signal.get("screener_defaults") or ()):
        return "defaulted_atr"
    try:
        entry, atr = float(signal["current_price"]), float(signal["atr_14"])
    except (KeyError, TypeError, ValueError):
        return "missing_price_or_atr"
    if not (entry > 0 and atr > 0):
        return "non_positive_price_or_atr"
    return None


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
         strategy_id, strategy_version, model_version,
         observation_id, feature_snapshot_id, feature_set_version)
    VALUES
        (%(symbol)s, %(signal_date)s, %(direction)s, %(entry_price)s, %(atr)s,
         %(stop_price)s, %(target1_price)s, %(target2_price)s, %(target3_price)s,
         %(sector)s, %(quality_grade)s, %(signal_date)s,
         %(strategy_id)s, %(strategy_version)s, %(model_version)s,
         %(observation_id)s, %(feature_snapshot_id)s, %(feature_set_version)s)
    ON CONFLICT (symbol, strategy_id, direction, signal_date) DO UPDATE SET
        entry_price = EXCLUDED.entry_price,
        atr = EXCLUDED.atr,
        stop_price = EXCLUDED.stop_price,
        target1_price = EXCLUDED.target1_price,
        target2_price = EXCLUDED.target2_price,
        target3_price = EXCLUDED.target3_price,
        sector = EXCLUDED.sector,
        quality_grade = EXCLUDED.quality_grade,
        model_version = EXCLUDED.model_version,
        observation_id = COALESCE(signal_ledger.observation_id, EXCLUDED.observation_id),
        feature_snapshot_id = COALESCE(signal_ledger.feature_snapshot_id, EXCLUDED.feature_snapshot_id),
        feature_set_version = COALESCE(signal_ledger.feature_set_version, EXCLUDED.feature_set_version)
    WHERE signal_ledger.status = 'open'
      AND signal_ledger.last_evaluated_date = signal_ledger.signal_date
"""


def _resolve_default_strategy(db) -> StrategyRef:
    """Looks up the single seeded strategy row. The pipeline runs exactly one strategy today; a second one
    passes its own StrategyRef to write_signals()."""
    rows = db.execute_dict_query(
        "SELECT id, strategy_key, strategy_version FROM strategies WHERE strategy_key = %s AND strategy_version = %s",
        (DEFAULT_STRATEGY_KEY, DEFAULT_STRATEGY_VERSION))
    if not rows:
        raise RuntimeError(
            f"strategies row ({DEFAULT_STRATEGY_KEY}, {DEFAULT_STRATEGY_VERSION}) is missing -- "
            "has mechanism/add_strategy_identity_release_a.sql been applied?")
    return StrategyRef(rows[0]["id"], rows[0]["strategy_key"], rows[0]["strategy_version"])


def _has_open_position(db, symbol: str, strategy_id: int, direction: int, session_date: date) -> bool:
    """True if this symbol/strategy/direction already has an open row from a DIFFERENT (earlier)
    session -- excludes today's own date so a same-day re-run still reaches the upsert's refresh
    path (ON CONFLICT ... DO UPDATE) instead of being mistaken for the position invariant."""
    return bool(db.execute_dict_query(
        OPEN_POSITION_EXISTS_SQL, (symbol, strategy_id, direction, session_date)))


def write_todays_signals(db, signals: List[Dict], session_date: date, links: Optional[Links] = None,
                         strict: bool = True) -> int:
    """The pipeline's call: the default strategy for `session_date`. See write_signals."""
    try:
        strategy = _resolve_default_strategy(db)
    except Exception as e:  # noqa: BLE001 -- the whole batch is meaningless without a strategy row
        logger.error(f"signal_ledger_writer: cannot resolve default strategy, skipping batch: {e}")
        return 0
    return write_signals(db, signals, session_date, strategy, links, strict)


def write_signals(db, signals: List[Dict], session_date: date, strategy: StrategyRef,
                  links: Optional[Links] = None, strict: bool = True) -> int:
    """Upsert one signal_ledger row per real breakout in `signals` for `session_date` under `strategy`.
    Returns the number of rows attempted (not necessarily changed -- a same-day re-run
    after the evaluator has already advanced a row is a deliberate no-op, not an error;
    a signal for a symbol/direction that already has an open position is also skipped,
    not an error -- see the position-invariant note above).
    Never raises: a single bad signal is logged and skipped, not fatal to the batch.

    SESSION INTEGRITY (defense in depth, 2026-10-01): `session_date` is the session the pipeline told the
    screener to process. A tracked signal whose own `screening_date` is not exactly that session (a
    symbol whose newest bar is stale, or from a later session) is REJECTED with a logged reason -- never
    written under `session_date`, which would stamp one day's bar with another day's signal_date.

    `links`: (symbol, direction) -> ObservationLink from the research observer; absent entries write NULL
    lineage (never a guess).

    `strict` (default True): refuse a signal whose ATR was a screener default or whose price/ATR is unusable (see
    `ledger_ineligibility`). The screener passes strict=False for a session BEFORE the GUARDS_EFFECTIVE_FROM boundary,
    which reproduces the pre-boundary writer exactly (no such refusal), so deploying the image changes no ledger row."""
    links = links or {}
    tracked = [sg for sg in signals if sg.get("signal_type") in TRACKED_SIGNAL_TYPES]
    _, rejected = partition_by_session(tracked, session_date, "screening_date")
    if rejected:
        logger.error(f"signal_ledger_writer: REJECTED {len(rejected)} signal(s) whose screening_date != "
                     f"session {session_date}: {summarize_rejections(rejected)}")
        rejected_ids = {id(sg) for sg, _ in rejected}
        signals = [sg for sg in signals if id(sg) not in rejected_ids]

    written = 0
    for signal in signals:
        direction = TRACKED_SIGNAL_TYPES.get(signal.get("signal_type"))
        if direction is None:
            continue  # near-breakout or unrecognized -- not a trade plan, nothing to track

        symbol = signal.get("symbol")
        try:
            reason = ledger_ineligibility(signal, session_date) if strict else None
            if reason == "defaulted_atr":
                logger.warning(f"signal_ledger_writer: REFUSED {symbol} on {session_date}: its ATR was a screener "
                               f"default, not a measured value -- no invented trade plan is recorded")
                continue
            if reason is not None:
                logger.error(f"signal_ledger_writer: REFUSED {symbol} on {session_date}: {reason}")
                continue
            if _has_open_position(db, symbol, strategy.id, direction, session_date):
                continue  # position invariant: this symbol/strategy/direction is already open

            entry_price = float(signal["current_price"])
            atr = float(signal["atr_14"])
            levels = compute_levels(entry_price, atr, direction)
            model_version: Optional[str] = scoring_model_version(signal)  # NULL unless a validated model scored it
            link = links.get((symbol, direction))
            params = {
                "symbol": symbol,
                "signal_date": session_date,
                "direction": direction,
                "entry_price": entry_price,
                "atr": atr,
                "sector": signal.get("sector"),
                "quality_grade": signal.get("quality_grade"),
                "strategy_id": strategy.id,
                "strategy_version": strategy.version,
                "model_version": model_version,
                "observation_id": link.observation_id if link else None,
                "feature_snapshot_id": link.feature_snapshot_id if link else None,
                "feature_set_version": link.feature_set_version if link else None,
                **levels,
            }
            db.execute_insert(UPSERT_SQL, params)
            written += 1
        except Exception as e:  # noqa: BLE001 -- one bad row must never abort the batch
            logger.error(f"signal_ledger_writer: failed to write {symbol} on {session_date}: {e}")

    logger.info(f"signal_ledger_writer: wrote {written}/{len(signals)} signals for {session_date}")
    return written
