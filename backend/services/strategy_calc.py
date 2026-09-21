# File: backend/services/strategy_calc.py
"""
Shared risk/reward strategy-plan math, used by both the Trading Strategy
ranking endpoint (routers/strategy.py) and the per-stock detail endpoint
(routers/stock.py) so the two pages always agree on the numbers.

The screener's own `stop_loss_price` / `target_price` / `reward_risk_ratio`
fields are not usable for this: reward_risk_ratio is a hardcoded 3.0 for every
signal (stop = entry ∓ 2×ATR, target = entry ± 6×ATR, so the ratio is always
6/2=3 algebraically). Real differentiation comes from ATR relative to price
(risk_pct) and the already-computed ML fields (momentum, quality, alignment).
"""

from typing import Any, Dict, Optional


def compute_strategy_plan(signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build a tiered entry/stop/take-profit plan + risk-adjusted strategy score
    from one ML-enhanced signal dict. Returns None if the signal isn't a
    bullish/bearish setup or is missing the fields needed to compute it.
    """
    signal_type = signal.get("signal_type") or ""
    if "bullish" not in signal_type and "bearish" not in signal_type:
        return None

    entry = signal.get("current_price")
    atr = signal.get("atr_14")
    stop_loss = signal.get("stop_loss_price")
    target = signal.get("target_price")
    if entry is None or atr is None or stop_loss is None or target is None or entry == 0:
        return None

    is_long = "bullish" in signal_type
    sign = 1 if is_long else -1

    tp1 = round(entry + sign * 2 * atr, 2)
    tp2 = round(entry + sign * 4 * atr, 2)
    tp3 = round(target, 2)

    risk_pct = abs(entry - stop_loss) / entry * 100
    reward_pct_tp1 = abs(tp1 - entry) / entry * 100
    reward_pct_tp2 = abs(tp2 - entry) / entry * 100
    reward_pct_tp3 = abs(tp3 - entry) / entry * 100

    # `None` = the component is unavailable for this signal. It is NOT scored as 0: the weighted
    # score is renormalised over the components that exist, so an unscored signal is neither
    # penalised nor inflated by an invented value.
    momentum_score = signal.get("ml_momentum_probability")
    fundamentals_score = signal.get("overall_quality_score")
    alignment_score = signal.get("alignment_score")
    model_risk_score = signal.get("ml_risk_score")

    # Risk bonus shrinks as stop-risk % grows; ~0 once risk_pct hits ~12.5%
    risk_bonus = max(0.0, 100.0 - risk_pct * 8.0)

    components = [(0.40, momentum_score), (0.25, fundamentals_score), (0.20, alignment_score), (0.15, risk_bonus)]
    available = [(w, v) for w, v in components if v is not None]
    strategy_score = round(sum(w * v for w, v in available) / sum(w for w, _ in available), 1)

    return {
        "direction": "long" if is_long else "short",
        "entry_price": entry,
        "stop_loss_price": round(stop_loss, 2),
        "risk_pct": round(risk_pct, 2),
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "reward_pct_tp1": round(reward_pct_tp1, 2),
        "reward_pct_tp2": round(reward_pct_tp2, 2),
        "reward_pct_tp3": round(reward_pct_tp3, 2),
        "momentum_score": round(momentum_score, 1) if momentum_score is not None else None,
        "fundamentals_score": round(fundamentals_score, 1) if fundamentals_score is not None else None,
        "alignment_score": alignment_score,
        "model_risk_score": model_risk_score,
        "risk_bonus": round(risk_bonus, 1),
        "strategy_score": strategy_score,
    }
