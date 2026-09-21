# ml_training/features/price_features.py
"""
Single source of truth for everything the ML layer derives from price bars:
Donchian breakout detection, causal indicators, model features, price-integrity
rules, and trade-plan outcome labels.

Why this module exists (see CLAUDE.md, ML audit 2026-09-20):
  * Training read indicators from `technical_indicators` and entry prices from
    `breakouts`, both computed on an older price basis than the `stock_prices`
    rows that Tiingo later overwrote -- ~24% of 2023-2025 indicator rows
    disagreed with current prices by >0.5%, which biased the labels. Here every
    number is computed from ONE consistent OHLCV frame, so stale derived tables
    cannot leak in.
  * Training and live inference had separate feature implementations that
    silently disagreed (bollinger_position, turnaround_volume, piotroski
    defaults). Both now call `build_breakout_features()`; every indicator is
    causal (row t depends only on rows <= t), so features computed on a full
    history equal features computed on that history truncated at t. That
    property is what tests/test_price_features.py asserts.

Conventions
  * `px` is a DataFrame with columns date, open, high, low, close, volume,
    sorted ascending by date, one row per trading day.
  * Missing data stays NaN (XGBoost handles NaN natively); nothing is defaulted.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

FEATURE_SET_VERSION = "price_v1"

# --- Price-integrity rules ---------------------------------------------------
# A one-day close-to-close move beyond these ratios is treated as a data
# discontinuity (unadjusted reverse split, ticker reuse, bad vendor bar), not a
# trade. It is never "repaired" -- inventing an adjustment factor would fabricate
# history. Samples whose feature lookback or label window cross one are excluded.
MAX_DAY_UP_RATIO = 3.0
MAX_DAY_DOWN_RATIO = 1.0 / 3.0

# Tradability floor: 20-day average dollar volume. Dollar volume is invariant to split adjustment
# (adjusted price x inversely-adjusted volume), so it also exposes series like PARA that are
# consistent but astronomically back-adjusted with ~0 shares traded -- not a market anyone can trade.
MIN_DOLLAR_VOLUME_20 = 1_000_000.0

# Longest feature lookback (52-week range / SMA200) plus a safety bar.
LOOKBACK_BARS = 253
# Trade-plan horizon (bars) -- also the forward window a discontinuity can poison.
PLAN_HORIZON = 20
STOP_ATR = 2.0          # 1R = STOP_ATR * ATR, identical to the screener's stop_loss_price
LEGACY_LABEL_WINDOW_DAYS = 25  # ml_config.MOMENTUM_DAYS_FORWARD used by momentum_labeler.py

SECTORS = [
    "Technology", "Healthcare", "Financial Services", "Consumer Cyclical", "Industrials",
    "Communication Services", "Consumer Defensive", "Energy", "Basic Materials",
    "Real Estate", "Utilities",
]

FEATURE_NAMES: List[str] = [
    "is_bullish", "breakout_dist_atr", "chan_width_pct", "chan_squeeze_pctile",
    "atr_pct", "rsi_14", "vol_ratio_10", "vol_ratio_50", "log_dollar_vol_20",
    "ret_5d", "ret_20d", "ret_60d", "sma10_vs_20", "dist_sma50_pct", "dist_sma200_pct",
    "macd_norm", "bollinger_pos", "gap_pct", "bar_range_atr", "close_location",
    "pct_from_52w_high", "pct_from_52w_low",
] + [f"sector_{s.lower().replace(' ', '_')}" for s in SECTORS]

OHLCV = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------
def find_discontinuities(px: pd.DataFrame) -> pd.DataFrame:
    """Rows of px (by position) where the series is not a trustworthy continuous price path."""
    c = px["close"].to_numpy(dtype=float)
    prev = np.r_[np.nan, c[:-1]]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = c / prev
    nonpos = (px[["open", "high", "low", "close"]].to_numpy(dtype=float) <= 0).any(axis=1)
    bad_ohlc = (px["high"] < px["low"]).to_numpy() | (px["close"] > px["high"] * 1.0001).to_numpy() \
               | (px["close"] < px["low"] * 0.9999).to_numpy()
    kind = np.full(len(px), "", dtype=object)
    kind[ratio > MAX_DAY_UP_RATIO] = "jump_up"
    kind[ratio < MAX_DAY_DOWN_RATIO] = "jump_down"
    kind[bad_ohlc] = "ohlc_inconsistent"
    kind[nonpos] = "nonpositive"
    idx = np.flatnonzero(kind != "")
    return pd.DataFrame({
        "pos": idx,
        "date": px["date"].to_numpy()[idx],
        "kind": kind[idx],
        "prev_close": prev[idx],
        "close": c[idx],
        "ratio": ratio[idx],
    })


def contaminated_mask(n: int, disc_pos: Iterable[int],
                      lookback: int = LOOKBACK_BARS, forward: int = PLAN_HORIZON) -> np.ndarray:
    """True for bars t whose feature lookback or forward window includes a discontinuity."""
    bad = np.zeros(n, dtype=bool)
    for d in disc_pos:
        bad[max(0, d - forward): min(n, d + lookback)] = True
    return bad


# ---------------------------------------------------------------------------
# Indicators (all causal)
# ---------------------------------------------------------------------------
def _wilder(x: np.ndarray, period: int) -> np.ndarray:
    """Wilder smoothing seeded with the SMA of x[1:period+1] at index `period`
    (identical to EnhancedDailyDataUpdater.calculate_atr / calculate_rsi_fixed)."""
    out = np.full(len(x), np.nan)
    if len(x) <= period:
        return out
    cur = np.nanmean(x[1:period + 1])
    out[period] = cur
    a = 1.0 / period
    for i in range(period + 1, len(x)):
        v = x[i]
        if not np.isnan(v):
            cur = a * v + (1 - a) * cur
        out[i] = cur
    return out


def compute_indicators(px: pd.DataFrame) -> pd.DataFrame:
    """Causal indicator frame aligned row-for-row with px."""
    o, h, l, c, v = (px[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    s = lambda a: pd.Series(a)  # noqa: E731
    cs, hs, ls, vs = s(c), s(h), s(l), s(v)

    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev_c), np.abs(l - prev_c)]), axis=0)
    tr[0] = np.nan
    atr = _wilder(tr, 14)

    delta = np.r_[np.nan, np.diff(c)]
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain[0] = loss[0] = np.nan
    ag, al = _wilder(gain, 14), _wilder(loss, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = np.where(al == 0, 100.0, 100.0 - 100.0 / (1.0 + ag / al))
    rsi[np.isnan(ag) | np.isnan(al)] = np.nan

    dh = hs.rolling(20, min_periods=20).max().to_numpy()
    dl = ls.rolling(20, min_periods=20).min().to_numpy()
    sma = {n: cs.rolling(n, min_periods=n).mean().to_numpy() for n in (10, 20, 50, 200)}
    sd20 = cs.rolling(20, min_periods=20).std(ddof=0).to_numpy()
    ema12 = cs.ewm(span=12, adjust=False, min_periods=26).mean().to_numpy()
    ema26 = cs.ewm(span=26, adjust=False, min_periods=26).mean().to_numpy()

    with np.errstate(divide="ignore", invalid="ignore"):
        # Width of the channel as it stood at the previous close, i.e. the channel being broken.
        chan_w_prev = np.r_[np.nan, (dh[:-1] - dl[:-1]) / c[:-1] * 100]
    squeeze = s(chan_w_prev).rolling(120, min_periods=20).rank(pct=True, method="max").to_numpy() * 100

    return pd.DataFrame({
        "date": px["date"].to_numpy(), "open": o, "high": h, "low": l, "close": c, "volume": v,
        "atr": atr, "rsi": rsi, "dh": dh, "dl": dl,
        "sma10": sma[10], "sma20": sma[20], "sma50": sma[50], "sma200": sma[200],
        "sd20": sd20, "macd": ema12 - ema26,
        "vol_sma10": vs.rolling(10, min_periods=10).mean().to_numpy(),
        "vol_sma50": vs.rolling(50, min_periods=50).mean().to_numpy(),
        "dollar_vol_20": (cs * vs).rolling(20, min_periods=20).mean().to_numpy(),
        "hi252": hs.rolling(252, min_periods=126).max().to_numpy(),
        "lo252": ls.rolling(252, min_periods=126).min().to_numpy(),
        "chan_w_prev": chan_w_prev, "squeeze": squeeze,
    })


def detect_breakouts(ind: pd.DataFrame) -> np.ndarray:
    """+1 bullish / -1 bearish / 0 none per bar. Same rule as the live screener and the
    historical generator: close crosses the PREVIOUS bar's 20-bar channel while the
    previous close was still inside it."""
    c = ind["close"].to_numpy()
    pc = np.r_[np.nan, c[:-1]]
    pdh = np.r_[np.nan, ind["dh"].to_numpy()[:-1]]
    pdl = np.r_[np.nan, ind["dl"].to_numpy()[:-1]]
    with np.errstate(invalid="ignore"):
        bull = (c > pdh) & (pc <= pdh)
        bear = (c < pdl) & (pc >= pdl)
    return np.where(bull, 1, np.where(bear, -1, 0))


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
def sector_features(sector: Optional[str]) -> Dict[str, float]:
    """One-hot over the 11 GICS sectors; unknown/unmapped sector -> all NaN (missing, not 'none')."""
    names = [f"sector_{s.lower().replace(' ', '_')}" for s in SECTORS]
    if not sector or sector not in SECTORS:
        return {n: np.nan for n in names}
    return {n: float(n == f"sector_{sector.lower().replace(' ', '_')}") for n in names}


def build_breakout_features(ind: pd.DataFrame, pos: np.ndarray, direction: np.ndarray,
                            sector: Optional[str] = None) -> pd.DataFrame:
    """Feature rows (columns == FEATURE_NAMES) for bars `pos` with breakout `direction` (+1/-1)."""
    pos = np.asarray(pos, dtype=int)
    d = np.asarray(direction, dtype=float)
    g = lambda col: ind[col].to_numpy()[pos]  # noqa: E731
    gp = lambda col, k: ind[col].to_numpy()[np.clip(pos - k, 0, None)]  # noqa: E731
    c, atr = g("close"), g("atr")
    prev_dh, prev_dl = ind["dh"].to_numpy()[np.clip(pos - 1, 0, None)], ind["dl"].to_numpy()[np.clip(pos - 1, 0, None)]
    hi, lo = g("high"), g("low")
    with np.errstate(divide="ignore", invalid="ignore"):
        dist = np.where(d > 0, c - prev_dh, prev_dl - c) / atr
        rng = hi - lo
        bb_up, bb_lo = g("sma20") + 2 * g("sd20"), g("sma20") - 2 * g("sd20")
        out = {
            "is_bullish": (d > 0).astype(float),
            "breakout_dist_atr": dist,
            "chan_width_pct": g("chan_w_prev"),
            "chan_squeeze_pctile": g("squeeze"),
            "atr_pct": atr / c * 100,
            "rsi_14": g("rsi"),
            "vol_ratio_10": g("volume") / g("vol_sma10"),
            "vol_ratio_50": g("volume") / g("vol_sma50"),
            "log_dollar_vol_20": np.log10(np.where(g("dollar_vol_20") > 0, g("dollar_vol_20"), np.nan)),
            "ret_5d": (c / gp("close", 5) - 1) * 100,
            "ret_20d": (c / gp("close", 20) - 1) * 100,
            "ret_60d": (c / gp("close", 60) - 1) * 100,
            "sma10_vs_20": (g("sma10") / g("sma20") - 1) * 100,
            "dist_sma50_pct": (c / g("sma50") - 1) * 100,
            "dist_sma200_pct": (c / g("sma200") - 1) * 100,
            "macd_norm": g("macd") / c * 100,
            "bollinger_pos": (c - bb_lo) / (bb_up - bb_lo) * 100,
            "gap_pct": (g("open") / gp("close", 1) - 1) * 100,
            "bar_range_atr": rng / atr,
            "close_location": np.where(rng > 0, (c - lo) / rng, np.nan),
            "pct_from_52w_high": (c / g("hi252") - 1) * 100,
            "pct_from_52w_low": (c / g("lo252") - 1) * 100,
        }
    out["ret_5d"][pos < 5] = np.nan
    out["ret_20d"][pos < 20] = np.nan
    out["ret_60d"][pos < 60] = np.nan
    out["gap_pct"][pos < 1] = np.nan
    df = pd.DataFrame(out)
    for k, v in sector_features(sector).items():
        df[k] = v
    return df[FEATURE_NAMES].replace([np.inf, -np.inf], np.nan)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def plan_outcomes(ind: pd.DataFrame, pos: np.ndarray, direction: np.ndarray,
                  horizon: int = PLAN_HORIZON) -> pd.DataFrame:
    """Outcome of the exact plan the UI shows: enter at the breakout close, stop = 2*ATR
    (=1R), targets at 2/4/6*ATR (=1R/2R/3R), 20-bar time exit. Conservative tie-break:
    if stop and target are both inside one bar's range, the stop is taken first. No
    slippage/gap modelling (stop fills at the stop price). Bars without a full forward
    window get NaN. `plan_r` = mean of three equal tranches (all share the stop)."""
    pos = np.asarray(pos, dtype=int)
    d = np.asarray(direction, dtype=float)
    n = len(ind)
    H, Lw, C = ind["high"].to_numpy(), ind["low"].to_numpy(), ind["close"].to_numpy()
    res = {k: np.full(len(pos), np.nan) for k in ("plan_r", "r_single", "stopped", "tp3_hit", "mae_r")}
    ok = (pos + horizon < n) & np.isfinite(ind["atr"].to_numpy()[pos]) & (ind["atr"].to_numpy()[pos] > 0)
    if ok.any():
        p, dd = pos[ok], d[ok]
        e, R = C[p], STOP_ATR * ind["atr"].to_numpy()[p]
        win = np.arange(1, horizon + 1)[None, :] + p[:, None]
        fh, fl, fc = H[win], Lw[win], C[win]
        adverse = np.where(dd[:, None] > 0, e[:, None] - fl, fh - e[:, None])
        favour = np.where(dd[:, None] > 0, fh - e[:, None], e[:, None] - fl)
        big = horizon + 1

        def first(mask):
            return np.where(mask.any(axis=1), mask.argmax(axis=1), big)

        ds = first(adverse >= R[:, None])
        mtm = dd * (fc[:, -1] - e) / R
        stopped = ds < big
        rs = []
        for lvl in (1, 2, 3):
            dk = first(favour >= lvl * R[:, None])
            rs.append(np.where(dk < ds, float(lvl), np.where(stopped, -1.0, mtm)))
        res["plan_r"][ok] = np.mean(rs, axis=0)
        res["r_single"][ok] = rs[2]
        res["stopped"][ok] = stopped.astype(float)
        res["tp3_hit"][ok] = (first(favour >= 3 * R[:, None]) < ds).astype(float)
        res["mae_r"][ok] = adverse.max(axis=1) / R
    return pd.DataFrame(res)


def legacy_momentum_score(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                          volume: np.ndarray, entry: float, is_bullish: bool) -> int:
    """The original composite (momentum_labeler.calculate_momentum_score), extracted verbatim so the
    old target can still be evaluated on consistent prices. Known quirks are intentionally NOT changed
    here (abs() in the speed term, negative volume points, unsigned direction) -- fixing the label
    definition is a separate, explicit decision."""
    if len(close) < 5:
        return 0
    prices, highs, lows, volumes = close, high, low, volume
    score = 0.0
    trend_days = np.sum(prices >= entry) if is_bullish else np.sum(prices <= entry)
    score += min(30, trend_days / len(prices) * 30)

    if is_bullish:
        new_ext = int(np.sum(highs > entry))
        prog, mx = 0, entry
        for x in highs:
            if x > mx:
                prog += 1
                mx = x
    else:
        new_ext = int(np.sum(lows < entry))
        prog, mn = 0, entry
        for x in lows:
            if x < mn:
                prog += 1
                mn = x
    score += min(25, (new_ext + prog) / len(prices) * 25)

    rets = np.diff(prices) / prices[:-1]
    if len(rets):
        cons = np.sum(rets > 0) / len(rets) if is_bullish else np.sum(rets < 0) / len(rets)
        score += cons * 20

    avg_vol = np.mean(volumes) if len(volumes) else 1
    vs = min(2.0, volumes[0] / avg_vol) if avg_vol > 0 else 1.0
    score += (vs - 1.0) * 15
    score += min(10, abs(prices[-1] - entry) / entry * 100 * 1.0) if len(prices) > 1 else 0
    return int(round(min(100, score)))


def legacy_labels(ind: pd.DataFrame, pos: np.ndarray, direction: np.ndarray,
                  window_days: int = LEGACY_LABEL_WINDOW_DAYS) -> pd.DataFrame:
    """momentum_score / target_binary on the legacy definition, computed from `ind` only.
    Window = bars with date in [breakout date, +window_days calendar days], entry = breakout close.
    Immature windows (series ends before the window closes) -> NaN."""
    dates = pd.to_datetime(ind["date"]).to_numpy()
    last = dates[-1]
    span = np.timedelta64(window_days, "D")
    score = np.full(len(pos), np.nan)
    C, H, L, V = (ind[k].to_numpy() for k in ("close", "high", "low", "volume"))
    for j, (p, d) in enumerate(zip(pos, direction)):
        if dates[p] + span > last:
            continue
        end = np.searchsorted(dates, dates[p] + span, side="right")
        score[j] = legacy_momentum_score(C[p:end], H[p:end], L[p:end], V[p:end], C[p], d > 0)
    out = pd.DataFrame({"momentum_score": score})
    out["target_binary"] = np.where(np.isnan(score), np.nan, (score >= 65).astype(float))
    return out
