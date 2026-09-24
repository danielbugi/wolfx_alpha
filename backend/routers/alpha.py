# File: backend/routers/alpha.py
"""
Alpha Finder API Router - A single ranked, filterable view of today's best
ML-enhanced signals, combining confidence + alignment + quality into one list.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.dependencies import require_authenticated_user
from typing import List, Dict, Any, Optional
import glob
import os
import re
import time
import logging

from utils import load_ml_enhanced_data_cached, find_project_root

logger = logging.getLogger(__name__)

alpha_router = APIRouter(
    prefix="/api/alpha",
    tags=["alpha"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(require_authenticated_user)],
)

CONFIDENCE_RANK = {"very_high": 4, "high": 3, "medium": 2, "low": 1, "very_low": 0}

# "Yesterday's signals" only changes once per day (when a new screener run
# lands), but this was previously re-globbed/re-read/re-parsed from disk on
# every single /finder request (strategy.py's /rank shares this function too)
# -- cache it for 30 minutes.
_PREV_SIGNALS_CACHE_TTL_SECONDS = 1800
_prev_signals_cache: Dict[str, Any] = {"symbols": None, "ts": 0.0}


def _file_date(path: str) -> Optional[str]:
    match = re.search(r"(\d{8})_\d+\.json$", os.path.basename(path))
    return match.group(1) if match else None


def _find_previous_day_signals() -> set:
    """Find the most recent screener run from an earlier calendar day than the
    latest run, and return the set of symbols that had any signal in it."""
    now = time.time()
    if _prev_signals_cache["symbols"] is not None and (now - _prev_signals_cache["ts"]) < _PREV_SIGNALS_CACHE_TTL_SECONDS:
        return _prev_signals_cache["symbols"]

    symbols = _compute_previous_day_signals()
    _prev_signals_cache["symbols"] = symbols
    _prev_signals_cache["ts"] = now
    return symbols


def _compute_previous_day_signals() -> set:
    try:
        project_root = find_project_root()
        pattern = str(project_root / "breakout_results" / "multi_timeframe_ml_enhanced_*.json")
        files = glob.glob(pattern)
        if not files:
            return set()

        files_with_dates = [(f, _file_date(f)) for f in files]
        files_with_dates = [(f, d) for f, d in files_with_dates if d]
        if not files_with_dates:
            return set()

        latest_date = max(d for _, d in files_with_dates)
        earlier_files = [f for f, d in files_with_dates if d < latest_date]
        if not earlier_files:
            return set()

        # Most recent file among the earlier days
        prev_file = max(earlier_files, key=lambda f: os.path.basename(f))

        import json
        with open(prev_file, "r", encoding="utf-8") as fh:
            prev_data = json.load(fh)

        symbols = set()
        for bucket in ["bullish_breakout", "bearish_breakout", "near_bullish", "near_bearish"]:
            for entry in prev_data.get("signals", {}).get(bucket, []):
                if entry.get("symbol"):
                    symbols.add(entry["symbol"])
        return symbols
    except Exception as e:
        logger.warning(f"Could not compute previous-day signals for 'new today' badges: {e}")
        return set()


@alpha_router.get("/finder")
def get_alpha_finder(
        signal_type: Optional[str] = Query(None, description="bullish_breakout | bearish_breakout | near_bullish | near_bearish"),
        min_confidence: Optional[str] = Query(None, description="very_low | low | medium | high | very_high"),
        sector: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
):
    """
    Ranked list of today's ML-enhanced signals, sorted by combined_score
    (alignment + ML confidence + quality), with sector/type/confidence filters
    and a "new today" flag vs. the previous day's run.
    """
    try:
        ml_data = load_ml_enhanced_data_cached()
        if not ml_data:
            return {"generated_at": None, "total": 0, "results": []}

        signals = ml_data.get("ai_insights", {}).get("ml_enhanced_signals", [])
        previous_symbols = _find_previous_day_signals()

        min_rank = CONFIDENCE_RANK.get(min_confidence, -1) if min_confidence else -1

        results = []
        for s in signals:
            if signal_type and s.get("signal_type") != signal_type:
                continue
            if sector and s.get("sector") != sector:
                continue
            conf_rank = CONFIDENCE_RANK.get(s.get("ml_confidence"), -1)
            if conf_rank < min_rank:
                continue

            results.append({
                "symbol": s.get("symbol"),
                "current_price": s.get("current_price"),
                "price_change_pct": s.get("price_change_pct"),
                "signal_type": s.get("signal_type"),
                "sector": s.get("sector"),
                "quality_grade": s.get("quality_grade"),
                "overall_quality_score": s.get("overall_quality_score"),
                "ml_confidence": s.get("ml_confidence"),
                "ml_momentum_probability": s.get("ml_momentum_probability"),
                "alignment_score": s.get("alignment_score"),
                "alignment_grade": s.get("alignment_grade"),
                "combined_score": s.get("combined_score"),
                "stop_loss_price": s.get("stop_loss_price"),
                "target_price": s.get("target_price"),
                "reward_risk_ratio": s.get("reward_risk_ratio"),
                "summary_text": s.get("summary_text"),
                "is_new": s.get("symbol") not in previous_symbols,
            })

        results.sort(key=lambda r: r.get("combined_score") or 0, reverse=True)
        results = results[:limit]

        return {
            "generated_at": ml_data.get("metadata", {}).get("generated_at"),
            "total": len(results),
            "results": results,
        }

    except Exception as e:
        logger.error(f"Error getting alpha finder results: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving alpha finder results: {str(e)}")
