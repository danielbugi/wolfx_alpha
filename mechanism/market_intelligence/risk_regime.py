"""risk_regime_v1 -- a descriptive, versioned, deterministic daily state of the US market.

THIS IS A V1 HEURISTIC, NOT A VALIDATED PREDICTIVE MODEL. The weights and thresholds are declarations, not estimates; they were not
fitted to outcomes. It is context/research information only: never a Donchian filter, never an eligibility rule, never a screener score input.

Pure: no database, no network, no wall clock. The state for session `t` is a function of (index closes <= t, the stock panel <= t, this
module's constants). A component whose inputs are not on session `t` is MISSING (score None), never carried forward and never 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

MODEL_VERSION = "risk_regime_v1"

RISK_ON, RISK_OFF, NEUTRAL, UNAVAILABLE = "RISK_ON", "RISK_OFF", "NEUTRAL", "UNAVAILABLE"
STATES = (RISK_ON, RISK_OFF, NEUTRAL, UNAVAILABLE)

THRESHOLD = 0.30                 # score >= +THRESHOLD -> RISK_ON, <= -THRESHOLD -> RISK_OFF (both inclusive, compared at 4 dp)
MIN_PRESENT_WEIGHT = 0.70        # below this the state is UNAVAILABLE and the score NULL
MIN_BREADTH_STOCKS = 1_000       # a breadth component needs at least this many valid stocks
REAL_SESSION_SHARE = 0.5         # a date needs prices for >= half the busiest date's symbols to count as a session (market_stats)
YEAR_SESSIONS = 252
MIN_HISTORY_YEAR = 200
STRENGTH_MODERATE = 0.30
STRENGTH_STRONG = 0.60

SPX, VIX, RUT = "^GSPC", "^VIX", "^RUT"

# (key, label, weight). Order is part of the definition.
COMPONENTS = (
    ("c1_index_trend", "S&P 500 trend structure", 0.25),
    ("c2_index_momentum", "S&P 500 20-session change", 0.10),
    ("c3_breadth_sma50", "Stocks above 50-day average", 0.20),
    ("c4_breadth_sma200", "Stocks above 200-day average", 0.10),
    ("c5_net_highs_lows", "Net 52-week highs minus lows", 0.10),
    ("c6_volatility", "VIX level", 0.15),
    ("c7_risk_appetite", "Russell 2000 vs S&P 500, 20 sessions", 0.10),
)
WEIGHTS: Dict[str, float] = {k: w for k, _, w in COMPONENTS}
LABELS: Dict[str, str] = {k: label for k, label, _ in COMPONENTS}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-12


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


@dataclass
class Component:
    key: str
    label: str
    weight: float
    present: bool
    raw: Dict[str, Any]                # every raw input, as computed
    value: Optional[float]             # the primary raw value that is normalised (None when missing)
    score: Optional[float]             # normalised s in [-1, +1]; None when missing, never 0
    missing_reason: Optional[str] = None

    def to_record(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "weight": self.weight, "present": self.present, "raw": self.raw,
                "value": self.value, "score": self.score, "missing_reason": self.missing_reason}


@dataclass
class RiskRegime:
    session_date: date
    model_version: str
    components: List[Component]
    present_weight: float
    score: Optional[float]
    state: str
    strength: Optional[float]
    strength_label: Optional[str]
    agreement: Optional[float]
    reasons: List[str] = field(default_factory=list)

    def to_record(self) -> Dict[str, Any]:
        """Flat, JSON-safe, everything needed to audit the number: raw + normalised components, availability, present weight, score,
        state, strength, agreement, version. Provenance / source are added by the store, which knows where the inputs came from."""
        return {
            "model_version": self.model_version, "session_date": self.session_date.isoformat(),
            "components": {c.key: c.to_record() for c in self.components},
            "available": {c.key: c.present for c in self.components},
            "present_weight": self.present_weight, "score": self.score, "state": self.state,
            "strength": self.strength, "strength_label": self.strength_label, "agreement": self.agreement,
            "reasons": list(self.reasons),
        }


# ------------------------------------------------------------------ scoring (pure function of the normalised component scores)
def classify(score: Optional[float]) -> str:
    if score is None:
        return UNAVAILABLE
    if score >= THRESHOLD:
        return RISK_ON
    if score <= -THRESHOLD:
        return RISK_OFF
    return NEUTRAL


def strength_label(strength: Optional[float]) -> Optional[str]:
    if strength is None:
        return None
    if strength >= STRENGTH_STRONG:
        return "strong"
    if strength >= STRENGTH_MODERATE:
        return "moderate"
    return "mixed"


def _reason(c: Component) -> str:
    v = c.value
    if c.key == "c1_index_trend":
        return f"{c.label}: {c.raw.get('n_conditions_true')} of 3 conditions true"
    if c.key == "c2_index_momentum":
        return f"{c.label}: {v:+.1f}%"
    if c.key in ("c3_breadth_sma50", "c4_breadth_sma200"):
        return f"{c.label}: {v:.0f}%"
    if c.key == "c5_net_highs_lows":
        return f"{c.label}: {v:+.1f}% of stocks"
    if c.key == "c6_volatility":
        return f"{c.label}: {v:.1f}"
    return f"{c.label}: {v:+.1f} pp"


def combine(components: Sequence[Component], session_date: date) -> RiskRegime:
    """Weights, coverage rule, score, state, strength, agreement, reasons. Uses only the components' weights and scores."""
    present = [c for c in components if c.present and c.score is not None]
    p = round(sum(c.weight for c in present), 10)
    if p < MIN_PRESENT_WEIGHT - 1e-9:
        return RiskRegime(session_date, MODEL_VERSION, list(components), p, None, UNAVAILABLE, None, None, None, [])
    score = round(sum(c.weight * c.score for c in present) / p, 4)
    w_pos = sum(c.weight for c in present if c.score > 0)
    w_neg = sum(c.weight for c in present if c.score < 0)
    agreement = round(max(w_pos, w_neg) / (w_pos + w_neg), 4) if (w_pos + w_neg) > 0 else None
    strength = round(abs(score), 4)
    ranked = sorted(present, key=lambda c: (-abs(c.weight * c.score), c.key))
    reasons = [_reason(c) for c in ranked[:3] if c.score != 0 and c.value is not None]
    return RiskRegime(session_date, MODEL_VERSION, list(components), p, score, classify(score), strength,
                      strength_label(strength), agreement, reasons)


def _missing(key: str, reason: str, raw: Optional[Dict[str, Any]] = None) -> Component:
    return Component(key, LABELS[key], WEIGHTS[key], False, raw or {}, None, None, reason)


def _present(key: str, raw: Dict[str, Any], value: float, score: float) -> Component:
    return Component(key, LABELS[key], WEIGHTS[key], True, raw, float(value), float(score))


# ------------------------------------------------------------------ index components
def _series_upto(s: Optional[pd.Series], t: pd.Timestamp) -> Optional[pd.Series]:
    if s is None:
        return None
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index)
    return s.sort_index().loc[:t]


def _on_session(s: Optional[pd.Series], t: pd.Timestamp) -> bool:
    return s is not None and len(s) > 0 and s.index[-1] == t


def c1_index_trend(spx: Optional[pd.Series], t: pd.Timestamp) -> Component:
    key = "c1_index_trend"
    if spx is None or len(spx) < 200 or not _on_session(spx, t):
        return _missing(key, "fewer than 200 ^GSPC bars on or before the session, or no bar on the session",
                        {"bars": 0 if spx is None else int(len(spx))})
    close = float(spx.iloc[-1])
    sma50, sma200 = float(spx.iloc[-50:].mean()), float(spx.iloc[-200:].mean())
    a, b, c = close > sma50, close > sma200, sma50 > sma200
    n = int(a) + int(b) + int(c)
    raw = {"close": close, "sma50": sma50, "sma200": sma200, "close_above_sma50": a, "close_above_sma200": b,
           "sma50_above_sma200": c, "n_conditions_true": n}
    return _present(key, raw, n, (n - 1.5) / 1.5)


def _ret20(s: Optional[pd.Series], t: pd.Timestamp) -> Optional[Dict[str, float]]:
    if s is None or len(s) < 21 or not _on_session(s, t):
        return None
    c_t, c_0 = float(s.iloc[-1]), float(s.iloc[-21])
    if not c_0 > 0:
        return None
    return {"close": c_t, "close_20_sessions_ago": c_0, "ret20_pct": (c_t / c_0 - 1) * 100}


def c2_index_momentum(spx: Optional[pd.Series], t: pd.Timestamp) -> Component:
    key = "c2_index_momentum"
    r = _ret20(spx, t)
    if r is None:
        return _missing(key, "fewer than 21 ^GSPC bars, or no bar on the session")
    return _present(key, r, r["ret20_pct"], _clip(r["ret20_pct"] / 5.0))


def c6_volatility(vix: Optional[pd.Series], t: pd.Timestamp) -> Component:
    key = "c6_volatility"
    if vix is None or not _on_session(vix, t):
        return _missing(key, "no ^VIX bar on the session")
    v = float(vix.iloc[-1])
    return _present(key, {"vix_close": v}, v, _clip((20.0 - v) / 10.0))


def c7_risk_appetite(rut: Optional[pd.Series], spx: Optional[pd.Series], t: pd.Timestamp) -> Component:
    key = "c7_risk_appetite"
    r, s = _ret20(rut, t), _ret20(spx, t)
    if r is None or s is None:
        return _missing(key, "^RUT or ^GSPC lacks 21 bars or a bar on the session")
    d = r["ret20_pct"] - s["ret20_pct"]
    return _present(key, {"rut_ret20_pct": r["ret20_pct"], "spx_ret20_pct": s["ret20_pct"], "difference_pp": d}, d, _clip(d / 3.0))


# ------------------------------------------------------------------ breadth components (stock panel)
def real_sessions_only(close: pd.DataFrame, *others: pd.DataFrame):
    counts = close.notna().sum(axis=1)
    real = counts >= REAL_SESSION_SHARE * counts.max()
    return (close.loc[real], *(o.loc[real] for o in others))


def breadth_components(close: Optional[pd.DataFrame], high: Optional[pd.DataFrame], low: Optional[pd.DataFrame],
                       t: pd.Timestamp) -> List[Component]:
    keys = ("c3_breadth_sma50", "c4_breadth_sma200", "c5_net_highs_lows")
    if close is None or high is None or low is None or len(close) == 0:
        return [_missing(k, "no stock panel") for k in keys]
    close, high, low = real_sessions_only(close.loc[:t], high.loc[:t], low.loc[:t])
    if len(close) == 0 or close.index[-1] != t:
        return [_missing(k, "the session is not a real session in the stock panel (no or partial load)") for k in keys]
    out: List[Component] = []
    for key, n, pct_key in (("c3_breadth_sma50", 50, "p50"), ("c4_breadth_sma200", 200, "p200")):
        sma = close.rolling(n, min_periods=n).mean().iloc[-1]
        c_t = close.iloc[-1]
        valid = sma.notna() & c_t.notna()
        n_valid = int(valid.sum())
        if n_valid < MIN_BREADTH_STOCKS:
            out.append(_missing(key, f"valid universe {n_valid} < {MIN_BREADTH_STOCKS}", {"n_valid": n_valid}))
            continue
        n_above = int(((c_t > sma) & valid).sum())
        pct = 100.0 * n_above / n_valid
        out.append(_present(key, {"n_valid": n_valid, "n_above": n_above, pct_key: pct}, pct, _clip((pct - 50.0) / 25.0)))
    hi = high.rolling(YEAR_SESSIONS, min_periods=MIN_HISTORY_YEAR).max().iloc[-1]
    lo = low.rolling(YEAR_SESSIONS, min_periods=MIN_HISTORY_YEAR).min().iloc[-1]
    hi_t, lo_t = high.iloc[-1], low.iloc[-1]
    ok = hi.notna() & hi_t.notna() & lo.notna() & lo_t.notna()
    r = int(ok.sum())
    if r < MIN_BREADTH_STOCKS:
        out.append(_missing("c5_net_highs_lows", f"stocks with a usable 52-week range {r} < {MIN_BREADTH_STOCKS}", {"n_range": r}))
    else:
        n_hi, n_lo = int(((hi_t >= hi) & ok).sum()), int(((lo_t <= lo) & ok).sum())
        net = (n_hi - n_lo) / r * 100.0
        out.append(_present("c5_net_highs_lows", {"n_range": r, "n_new_highs": n_hi, "n_new_lows": n_lo, "net_pct": net},
                            net, _clip(net / 5.0)))
    return out


# ------------------------------------------------------------------ entry points
def compute(session_date: date, index_closes: Dict[str, pd.Series], close: Optional[pd.DataFrame] = None,
            high: Optional[pd.DataFrame] = None, low: Optional[pd.DataFrame] = None) -> RiskRegime:
    """`index_closes`: {'^GSPC'|'^VIX'|'^RUT': close series indexed by date}. `close/high/low`: date x symbol matrices (as
    `alerts.market_stats.to_wide` builds them). `session_date` is explicit; bars after it are ignored."""
    t = pd.Timestamp(session_date)
    spx = _series_upto(index_closes.get(SPX), t)
    vix = _series_upto(index_closes.get(VIX), t)
    rut = _series_upto(index_closes.get(RUT), t)
    b = {c.key: c for c in breadth_components(close, high, low, t)}
    comps = [c1_index_trend(spx, t), c2_index_momentum(spx, t), b["c3_breadth_sma50"], b["c4_breadth_sma200"],
             b["c5_net_highs_lows"], c6_volatility(vix, t), c7_risk_appetite(rut, spx, t)]
    return combine(comps, session_date)


def from_scores(session_date: date, scores: Dict[str, Optional[float]]) -> RiskRegime:
    """Combine already-normalised scores (documentation / tests). A key absent or None is a missing component."""
    comps = [Component(key, label, w, scores.get(key) is not None, {}, None, scores.get(key), None if scores.get(key) is not None else "not supplied")
             for key, label, w in COMPONENTS]
    return combine(comps, session_date)
