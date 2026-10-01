"""Candidate capture: record, immutably, what the strategy saw and how each candidate moved through the funnel.

    candidate_observation  ->  feature_snapshot  ->  strategy decision  ->  signal_ledger

Called by the screener AFTER its own output is saved and BEFORE the ledger write (so ledger rows can carry the
lineage). The contract with the pipeline is absolute:

  * DEFAULT OFF. Nothing here runs unless RESEARCH_CAPTURE_ENABLED is truthy.
  * NEVER RAISES into the caller. Any failure is logged and returned as a failed CaptureResult; the screener's
    JSON output and the ledger write proceed exactly as they would without this module.
  * The session is explicit and required -- it is never derived from the candidates or the clock. A candidate
    whose own bar is not that session is skipped and counted, never relabelled.
  * First valid write wins. A re-run for the same session never overwrites: an identical payload is an
    `already_captured`, a different one is also left alone and counted as `hash_drift`.
  * No fabricated values. Snapshot values come from stock_prices (snapshot_builder), never the screener's
    defaults; a candidate whose inputs the screener filled in is flagged in `screener_defaults`.

Funnel stages are all recorded: the candidate (everything the strategy produced, near-breakouts included), the
guard verdict WITH reasons, the rank and ML status of survivors, and whether the ledger writer would take it.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from research import registry, repository, snapshot_builder as sb
from screeners.signal_ledger_writer import ObservationLink, StrategyRef, ledger_ineligibility
from shared.session_integrity import to_date

logger = logging.getLogger(__name__)

ENABLE_ENV = "RESEARCH_CAPTURE_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}

DIRECTION = {"bullish_breakout": 1, "near_bullish": 1, "bearish_breakout": -1, "near_bearish": -1}
TRIGGERED = {"bullish_breakout": True, "bearish_breakout": True, "near_bullish": False, "near_bearish": False}
PRICE_TOLERANCE = 1e-6


def is_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENABLE_ENV, "")).strip().lower() in _TRUTHY


@dataclass
class CaptureResult:
    status: str                       # complete | failed | disabled
    run_id: Optional[int] = None
    links: Dict[Tuple[str, int], ObservationLink] = field(default_factory=dict)
    counters: Dict[str, int] = field(default_factory=dict)
    skipped: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


def _num(x) -> Optional[float]:
    return sb._num(x)


def _capture_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(sb.canonical(payload).encode("utf-8")).hexdigest()


def _strategy_context(c: Mapping[str, Any]) -> Dict[str, Any]:
    weekly, monthly = c.get("weekly_context") or {}, c.get("monthly_context") or {}
    return {"urgency": c.get("urgency"), "donchian_high_20": _num(c.get("donchian_high")),
            "donchian_low_20": _num(c.get("donchian_low")), "weekly_trend": weekly.get("weekly_trend"),
            "monthly_trend": monthly.get("monthly_trend"),
            "weekly_asof": str(weekly["week_ending_date"]) if weekly.get("week_ending_date") else None,
            "monthly_asof": str(monthly["month_ending_date"]) if monthly.get("month_ending_date") else None}


def _funnel(c: Mapping[str, Any], session_date: date, final: Optional[Mapping[str, Any]],
            rank: Optional[int], decisions: Mapping[str, List[str]], guards_evaluated: bool) -> Dict[str, Any]:
    """Everything about where this candidate went after detection. Pure."""
    symbol = c["symbol"]
    passed_guard: Optional[bool] = None
    guard_reasons: Optional[List[str]] = None
    if guards_evaluated and symbol in decisions:
        reasons = list(decisions[symbol])
        passed_guard = not reasons
        guard_reasons = reasons or None

    scored = bool(final and final.get("ml_prediction_available") and _num(final.get("ml_momentum_probability")) is not None)
    return {
        "passed_guard": passed_guard,
        "guard_reasons": guard_reasons,
        "alignment_score": _num(c.get("alignment_score")),
        "quality_grade": c.get("alignment_grade"),
        "combined_score": _num(final.get("combined_score")) if final else None,
        "session_rank": rank,
        "ml_status": "scored" if scored else "not_processed",
        "ml_score": _num(final.get("ml_momentum_probability")) if scored else None,
        "ml_confidence": final.get("ml_confidence") if scored else None,
        "ml_model_version": final.get("ml_model_version") if scored else None,
        # the ONE predicate the ledger writer uses; a candidate the guards dropped has no `final` and so is False
        "tracked_intent": bool(final) and ledger_ineligibility(final, session_date) is None,
    }


def build_observation(c: Mapping[str, Any], session_date: date, strategy: StrategyRef, snapshot: "sb.Snapshot",
                      snapshot_id: int, run_id: int, final: Optional[Mapping[str, Any]], rank: Optional[int],
                      decisions: Mapping[str, List[str]], guards_evaluated: bool
                      ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(row, None) or (None, why-invalid). Pure."""
    signal_type = c.get("signal_type")
    if signal_type not in DIRECTION:
        return None, f"unknown_signal_type:{signal_type}"
    direction = DIRECTION[signal_type]
    entry = _num(c.get("current_price"))
    high_prev, low_prev = _num(c.get("prev_donchian_high")), _num(c.get("prev_donchian_low"))
    distance = _num(c.get("distance_to_breakout"))
    if None in (entry, high_prev, low_prev, distance):
        return None, "missing_channel_or_price"
    if abs(entry - snapshot.close) > PRICE_TOLERANCE * max(1.0, abs(snapshot.close)):
        return None, f"price_mismatch:{entry}!={snapshot.close}"

    atr = snapshot.features.get("atr_14")
    dist_atr: Optional[float] = None
    if atr is not None and atr > 0:
        dist_atr = ((snapshot.close - high_prev) if direction == 1 else (low_prev - snapshot.close)) / atr
        dist_atr = dist_atr if math.isfinite(dist_atr) else None

    funnel = _funnel(c, session_date, final, rank, decisions, guards_evaluated)
    defaults = sorted(set(c.get("screener_defaults") or ()))
    row: Dict[str, Any] = {
        "strategy_id": strategy.id, "strategy_version": strategy.version, "symbol": c["symbol"],
        "session_date": session_date, "direction": direction, "bar_date": snapshot.bar_date,
        "session_source": "explicit", "signal_type": signal_type, "triggered": TRIGGERED[signal_type],
        "entry_close": entry, "channel_high_prev": high_prev, "channel_low_prev": low_prev,
        "breakout_dist_atr": dist_atr, "distance_to_channel_pct": distance, **funnel,
        "screener_defaults": defaults, "strategy_context": _strategy_context(c),
    }
    row["capture_hash"] = _capture_hash(row)
    row.update(snapshot_id=snapshot_id, capture_run_id=run_id, code_ref=registry.code_ref())
    return row, None


def capture_session(*, candidates: List[Mapping[str, Any]], final_signals: List[Mapping[str, Any]],
                    guard_decisions: Mapping[str, List[str]], guards_evaluated: bool, session_date: Optional[date],
                    strategy: StrategyRef, connect: Callable[[], AbstractContextManager],
                    universe_size: Optional[int] = None) -> CaptureResult:
    """`candidates`: every signal the strategy produced for the session, BEFORE the guards. `final_signals`: the
    post-guard, post-ML list in its final sorted order (rank = position in it). `connect`: a context-manager
    factory yielding a psycopg2 connection."""
    run_id: Optional[int] = None
    counters = {c: 0 for c in repository.COUNTER_COLUMNS}
    skipped: Dict[str, str] = {}
    links: Dict[Tuple[str, int], ObservationLink] = {}
    try:
        if not isinstance(session_date, date):
            raise ValueError("an explicit session_date is required; it is never inferred")
        counters["candidates"] = len(candidates)
        final_by_key = {(s["symbol"], s["signal_type"]): s for s in final_signals}
        rank_by_key = {(s["symbol"], s["signal_type"]): i + 1 for i, s in enumerate(final_signals)}

        with connect() as conn:
            try:
                registry.ensure_registered(conn)
                conn.commit()
            except registry.ManifestMismatch as e:
                conn.rollback()
                run_id = repository.start_run(conn, strategy.id, strategy.version, session_date, registry.T0_V1,
                                              registry.code_ref(), universe_size)
                repository.finish_run(conn, run_id, "failed", counters, skipped, str(e))
                logger.error(f"research capture REFUSED: {e}")
                return CaptureResult("failed", run_id, error=str(e), counters=counters)

            run_id = repository.start_run(conn, strategy.id, strategy.version, session_date, registry.T0_V1,
                                          registry.code_ref(), universe_size)

            usable: List[Mapping[str, Any]] = []
            for c in candidates:
                symbol = c.get("symbol")
                if not symbol:
                    counters["invalid_skipped"] += 1
                    continue
                if to_date(c.get("screening_date")) != session_date:
                    counters["stale_skipped"] += 1
                    skipped[symbol] = f"stale_bar:{c.get('screening_date')}"
                    continue
                usable.append(c)

            symbols = [c["symbol"] for c in usable]
            prices = repository.fetch_price_rows(conn, symbols, session_date)
            sectors = repository.fetch_sectors(conn, symbols, session_date)

            for c in usable:
                symbol = c["symbol"]
                try:
                    _capture_one(conn, c, session_date, strategy, run_id, prices.get(symbol, []),
                                 sectors.get(symbol), final_by_key, rank_by_key, guard_decisions, guards_evaluated,
                                 counters, skipped, links)
                except Exception as e:  # noqa: BLE001 -- one bad candidate must not stop the session
                    conn.rollback()
                    counters["invalid_skipped"] += 1
                    skipped[symbol] = f"error:{type(e).__name__}:{e}"[:200]
                    logger.error(f"research capture: {symbol} failed: {e}")

            repository.finish_run(conn, run_id, "complete", counters, skipped, None)
        logger.info(f"research capture {session_date}: {counters}")
        return CaptureResult("complete", run_id, links, counters, skipped)
    except Exception as e:  # noqa: BLE001 -- the pipeline must never feel this
        logger.error(f"research capture failed (non-fatal): {type(e).__name__}: {e}")
        if run_id is not None:
            try:
                with connect() as conn:
                    repository.finish_run(conn, run_id, "failed", counters, skipped, f"{type(e).__name__}: {e}"[:500])
            except Exception:  # noqa: BLE001
                logger.error("research capture: could not even record the failed run")
        return CaptureResult("failed", run_id, links, counters, skipped, f"{type(e).__name__}: {e}")


def _capture_one(conn, c, session_date, strategy, run_id, price_rows, sector_info, final_by_key, rank_by_key,
                 decisions, guards_evaluated, counters, skipped, links) -> None:
    symbol = c["symbol"]
    sector, sector_asof = sector_info if sector_info else (None, None)
    snap = sb.build_t0_v1(symbol, session_date, price_rows, sector=sector,
                          sector_source="daily_fundamentals" if sector else None,
                          sector_asof=sector_asof if sector else None)
    if isinstance(snap, sb.SnapshotSkip):
        counters["stale_skipped" if snap.reason == "stale_bar" else "snapshot_skipped"] += 1
        skipped[symbol] = f"{snap.reason}:{snap.detail}"[:200]
        return

    key = (symbol, c.get("signal_type"))
    final = final_by_key.get(key)
    cur = conn.cursor()
    snap_w = repository.insert_snapshot(cur, snap)
    row, why = build_observation(c, session_date, strategy, snap, snap_w.id, run_id, final, rank_by_key.get(key),
                                 decisions, guards_evaluated)
    if row is None:
        conn.rollback()
        counters["invalid_skipped"] += 1
        skipped[symbol] = why
        return
    obs_w = repository.insert_observation(cur, row)
    conn.commit()

    if snap_w.drifted(snap.content_hash):
        counters["snapshot_drift"] += 1
    if obs_w.inserted:
        counters["captured"] += 1
        if row["screener_defaults"]:
            counters["defaulted_flagged"] += 1
    else:
        counters["already_captured"] += 1
        if obs_w.drifted(row["capture_hash"]):
            counters["hash_drift"] += 1
    if row["passed_guard"] is False:
        counters["guard_rejected"] += 1
    elif row["passed_guard"] is None:
        counters["guard_not_evaluated"] += 1
    links[(symbol, row["direction"])] = ObservationLink(obs_w.id, snap_w.id, snap.feature_set_version)
