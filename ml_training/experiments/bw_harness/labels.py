"""The canonical Big Winner label. ONE definition, used everywhere in the harness.

  * decision time  the close of the signal bar i (a Donchian breakout);
  * entry          the OPEN of bar i+1 (the next session);
  * 1R             2 x ATR14 of the signal bar (a decision-time number);
  * window         bars i+1 .. i+H, the entry bar included (exactly `mechanism/research/opportunity.favourable_excursion`: `entry_idx .. entry_idx + H`), H in 20 / 60 / 120;
  * MFE            the best price the position could have seen: the highest high (long) / lowest low (short) of the window, as R = d * (best - entry) / (2 x ATR14) and as a percentage of the entry;
  * tiers          3R, 5R and 10R are SEPARATE indicators of the same MFE, never merged;
  * terminal       d * (close of bar i+H - entry) / (2 x ATR14): where the position stood at the end of the window with NO exit rule. It is not a strategy return.

MFE and terminal values are properties of the price path. They are NOT realised strategy returns (those come from an exit rule and live in `realised_*` columns merged from the experiment outputs).

A window that is not completely inside the data is IMMATURE and every value of that horizon is NaN with status "immature": it is never a zero, never a "no", and never counted in a rate. A window that crosses a price
discontinuity (a split-like jump, a non-positive price) is status "discontinuity" and is excluded the same way.
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .config import Config

STATUS_OK, STATUS_IMMATURE, STATUS_DISCONTINUITY = "ok", "immature", "discontinuity"


def realised_vol(c: np.ndarray, window: int = 60) -> np.ndarray:
    """Std of daily log returns over the `window` bars ENDING at each bar (causal); NaN until the window is full."""
    lr = np.r_[np.nan, np.log(c[1:] / c[:-1])]
    return pd.Series(lr).rolling(window, min_periods=window).std(ddof=0).to_numpy()


def signal_labels(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray, idx: np.ndarray, direction: np.ndarray, cfg: Config,
                  discontinuity_in_window: Optional[Mapping[int, np.ndarray]] = None) -> Dict[str, np.ndarray]:
    """Labels for the signals at bar positions `idx` (direction +1 / -1). `discontinuity_in_window[H][i]` is True when the lookback or forward window of bar i touches a discontinuity.
    Returns equal-length arrays; see the module docstring for the semantics."""
    idx = np.asarray(idx, dtype=int)
    d = np.asarray(direction, dtype=float)
    n = len(c)
    out: Dict[str, np.ndarray] = {}
    nxt = idx + 1
    has_next = nxt < n
    entry = np.full(len(idx), np.nan)
    entry[has_next] = o[nxt[has_next]]
    a = atr[idx]
    unit = cfg.unit_atr * a
    valid = has_next & np.isfinite(entry) & (entry > 0) & np.isfinite(unit) & (unit > 0)
    out["entry_px"] = entry
    out["unit_r"] = unit
    sig60 = realised_vol(c)[idx]
    out["sigma60"] = sig60
    hs, ls = pd.Series(h), pd.Series(l)
    for H in cfg.horizons:
        fmax = hs.rolling(H).max().shift(-H).to_numpy()           # max high of bars i+1 .. i+H (NaN when the window is not complete)
        fmin = ls.rolling(H).min().shift(-H).to_numpy()
        endc = np.full(n, np.nan)
        if n > H:
            endc[: n - H] = c[H:]                                  # close of bar i+H
        best = np.where(d > 0, fmax[idx], fmin[idx])
        window_ok = valid & np.isfinite(best) & np.isfinite(endc[idx])
        bad = np.zeros(len(idx), bool) if discontinuity_in_window is None else np.asarray(discontinuity_in_window[H])[idx]
        status = np.where(~window_ok, STATUS_IMMATURE, np.where(bad, STATUS_DISCONTINUITY, STATUS_OK)).astype(object)
        ok = status == STATUS_OK
        fav = np.where(d > 0, best - entry, entry - best)
        mfe_r = np.where(ok, fav / unit, np.nan)
        mfe_pct = np.where(ok, fav / entry * 100.0, np.nan)
        term = np.where(ok, d * (endc[idx] - entry) / unit, np.nan)
        out[f"status_{H}"] = status
        out[f"mfe_r_{H}"] = mfe_r
        out[f"mfe_pct_{H}"] = mfe_pct
        out[f"terminal_r_{H}"] = term
        for t in cfg.tiers_r:
            out[f"ge{int(t)}r_{H}"] = np.where(ok, (mfe_r >= t).astype(float), np.nan)
        thr = np.where(d > 0, cfg.return_label_long_pct, cfg.return_label_short_pct)
        out[f"ret_label_{H}"] = np.where(ok, (mfe_pct >= thr).astype(float), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            z = mfe_pct / 100.0 / (sig60 * np.sqrt(H))
        out[f"vn_z_{H}"] = np.where(ok & np.isfinite(z), z, np.nan)
        out[f"vn_label_{H}"] = np.where(np.isfinite(out[f"vn_z_{H}"]), (out[f"vn_z_{H}"] >= cfg.vol_neutral_threshold).astype(float), np.nan)
        out[f"persist_{H}"] = np.where(ok & (mfe_r > 0), (term >= cfg.persistence_fraction * mfe_r).astype(float), np.nan)
    return out


def tier_of(mfe_r: float, tiers: Sequence[float] = (3.0, 5.0, 10.0)) -> int:
    """0 = below the lowest tier, 1 = reached 3R, 2 = 5R, 3 = 10R (the dose-response scale); NaN stays NaN."""
    if mfe_r is None or not np.isfinite(mfe_r):
        return -1
    return int(sum(mfe_r >= t for t in tiers))
