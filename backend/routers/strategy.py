# File: backend/routers/strategy.py
"""
Trading Strategy API Router - Entry/stop/take-profit plans ranked by a
risk-adjusted strategy score (momentum + fundamentals + alignment, weighted
against volatility-based risk).
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from utils import load_ml_enhanced_data_cached
from routers.alpha import _find_previous_day_signals, CONFIDENCE_RANK
from services.strategy_calc import compute_strategy_plan

logger = logging.getLogger(__name__)

strategy_router = APIRouter(
    prefix="/api/strategy",
    tags=["strategy"],
    responses={404: {"description": "Not found"}},
)

DIRECTION_TO_SIGNAL_TYPE = {
    "long": "bullish_breakout",
    "short": "bearish_breakout",
}


@strategy_router.get("/rank")
def get_strategy_rank(
        direction: Optional[str] = Query("all", description="all | long | short"),
        min_confidence: Optional[str] = Query(None, description="very_low | low | medium | high | very_high"),
        limit: int = Query(50, ge=1, le=200),
):
    """
    Ranked entry/stop/take-profit plan for every stock with an active breakout
    signal today, sorted by a risk-adjusted strategy score.
    """
    try:
        ml_data = load_ml_enhanced_data_cached()
        if not ml_data:
            return {"generated_at": None, "total": 0, "results": []}

        signals = ml_data.get("ai_insights", {}).get("ml_enhanced_signals", [])
        previous_symbols = _find_previous_day_signals()

        min_rank = CONFIDENCE_RANK.get(min_confidence, -1) if min_confidence else -1
        wanted_signal_types = {"bullish_breakout", "bearish_breakout"}
        if direction in DIRECTION_TO_SIGNAL_TYPE:
            wanted_signal_types = {DIRECTION_TO_SIGNAL_TYPE[direction]}

        results = []
        for s in signals:
            signal_type = s.get("signal_type")
            if signal_type not in wanted_signal_types:
                continue

            conf_rank = CONFIDENCE_RANK.get(s.get("ml_confidence"), -1)
            if conf_rank < min_rank:
                continue

            plan = compute_strategy_plan(s)
            if plan is None:
                continue

            results.append({
                "symbol": s.get("symbol"),
                "sector": s.get("sector"),
                "direction": plan["direction"],
                "current_price": plan["entry_price"],
                "price_change_pct": s.get("price_change_pct"),
                "stop_loss_price": plan["stop_loss_price"],
                "risk_pct": plan["risk_pct"],
                "tp1": plan["tp1"],
                "tp2": plan["tp2"],
                "tp3": plan["tp3"],
                "reward_pct_tp1": plan["reward_pct_tp1"],
                "reward_pct_tp2": plan["reward_pct_tp2"],
                "reward_pct_tp3": plan["reward_pct_tp3"],
                "momentum_score": plan["momentum_score"],
                "fundamentals_score": plan["fundamentals_score"],
                "alignment_score": plan["alignment_score"],
                "model_risk_score": plan["model_risk_score"],
                "risk_bonus": plan["risk_bonus"],
                "strategy_score": plan["strategy_score"],
                "quality_grade": s.get("quality_grade"),
                "ml_confidence": s.get("ml_confidence"),
                "summary_text": s.get("summary_text"),
                "is_new": s.get("symbol") not in previous_symbols,
            })

        results.sort(key=lambda r: r["strategy_score"], reverse=True)
        results = results[:limit]

        return {
            "generated_at": ml_data.get("metadata", {}).get("generated_at"),
            "total": len(results),
            "results": results,
        }

    except Exception as e:
        logger.error(f"Error getting strategy rank: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving strategy rank: {str(e)}")
